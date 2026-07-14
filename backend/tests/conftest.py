"""测试公共 fixtures：同步 SQLite Session + 异步 HTTP 客户端。"""

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.session import get_db
from app.main import create_app
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

    app.dependency_overrides[get_db] = _override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()
