"""测试公共 fixtures。"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.session import get_db
from app.main import create_app
from app.services.config import invalidate_config_cache


@pytest.fixture(autouse=True)
def _clear_config_cache():
    """每个测试前后清除配置缓存,避免测试间互相污染。"""
    invalidate_config_cache()
    yield
    invalidate_config_cache()


@pytest.fixture
def db_session():
    """SQLite 内存测试数据库,每个测试独立。"""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    test_session_local = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    session = test_session_local()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)


@pytest.fixture
def client(db_session: Session):
    """FastAPI TestClient,数据库依赖注入覆盖为测试 SQLite。"""
    app = create_app()

    def _override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
