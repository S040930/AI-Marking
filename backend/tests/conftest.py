"""测试公共 fixtures：同步 SQLite Session + 异步 HTTP 客户端。"""

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.session import get_db, get_session_factory
from app.main import create_app
from app.models.config_profile import ConfigProfile
from app.services.config import invalidate_config_cache
from app.services.ocr import reset_circuit_breaker


@pytest.fixture(autouse=True)
def _clear_config_cache():
    invalidate_config_cache()
    reset_circuit_breaker()
    yield
    invalidate_config_cache()
    reset_circuit_breaker()


@pytest.fixture
def db_session():
    """每个测试使用独立的同步 SQLite 内存数据库。"""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = factory()
    # 初始化默认配置项目,保证题目绑定可回退、问题查询可用
    if (
        session.query(ConfigProfile).filter(ConfigProfile.is_default.is_(True)).first()
        is None
    ):
        session.add(ConfigProfile(name="默认配置", is_default=True))
        session.commit()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


@pytest_asyncio.fixture
async def client(db_session: Session):
    app = create_app()

    def _override_get_db():
        yield db_session

    # P0 改造后 chat 路由用 session_factory 在请求内多次开闭 Session,
    # 需 override 为绑定同一 SQLite 引擎的工厂,确保读写落到同一内存数据库。
    # 注意:返回的工厂创建的是新 Session,与 db_session 不是同一实例,
    # 但因为 SQLite StaticPool 共享同一连接,数据可见性没问题。
    sqlite_factory = sessionmaker(bind=db_session.bind, expire_on_commit=False)

    def _override_get_session_factory():
        return sqlite_factory

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_session_factory] = _override_get_session_factory
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()
