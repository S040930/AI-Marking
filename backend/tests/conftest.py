"""测试公共 fixtures(async 版本)。

使用 ``aiosqlite`` 异步 SQLite 内存库 + ``httpx.AsyncClient`` + ``ASGITransport``,
与生产 ``asyncpg`` + ``AsyncSession`` 异步栈一致。
"""

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.session import get_db
from app.main import create_app
from app.models.question import Question, QuestionStatus
from app.models.submission import Submission
from app.services.config import invalidate_config_cache
from app.services.ocr import reset_circuit_breaker


@pytest.fixture(autouse=True)
def _clear_config_cache():
    """每个测试前后清除配置缓存与 OCR 熔断状态,避免测试间互相污染。"""
    invalidate_config_cache()
    reset_circuit_breaker()
    yield
    invalidate_config_cache()
    reset_circuit_breaker()


@pytest_asyncio.fixture
async def db_session():
    """SQLite 内存测试数据库(异步),每个测试独立。"""
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    test_session_local = async_sessionmaker(
        bind=engine, class_=AsyncSession, autocommit=False, autoflush=False, expire_on_commit=False
    )
    session = test_session_local()

    def _attach_default_question(sync_session, flush_context, instances):
        """旧接口测试未关心题目时，也满足生产模型的必填外键约束。"""
        for obj in list(sync_session.new):
            if isinstance(obj, Submission) and obj.question is None:
                obj.question = Question(
                    name="测试题目",
                    original_filename="question.pdf",
                    file_path="/tmp/question.pdf",
                    ocr_text="测试题目内容",
                    status=QuestionStatus.ready,
                )

    event.listen(session.sync_session, "before_flush", _attach_default_question)
    try:
        yield session
    finally:
        event.remove(session.sync_session, "before_flush", _attach_default_question)
        await session.close()
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await engine.dispose()


@pytest_asyncio.fixture
async def client(db_session: AsyncSession, monkeypatch):
    """FastAPI AsyncClient,数据库依赖注入覆盖为测试 SQLite。"""
    # 测试环境禁用 uploads 清理后台任务,避免扫描真实文件系统
    async def _noop_cleanup() -> None:
        return

    monkeypatch.setattr("app.main.periodic_cleanup_loop", _noop_cleanup)

    app = create_app()

    async def _override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = _override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()
