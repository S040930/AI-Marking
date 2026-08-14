"""同步 SQLAlchemy 引擎与 Session 工厂。"""

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings

engine = create_engine(
    settings.DATABASE_URL,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
    pool_timeout=30,
    pool_recycle=1800,
)

SessionLocal = sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
)


def get_db() -> Generator[Session, None, None]:
    """FastAPI 依赖：提供一个同步 Session 并在请求结束后关闭。"""
    with SessionLocal() as db:
        yield db


def get_session_factory() -> sessionmaker[Session]:
    """FastAPI 依赖：返回 Session 工厂。

    供需要在请求内多次开闭 Session 的路由使用(如 P0 改造后的 chat 路由:
    LLM 调用期间需释放连接,调用前后各开一个独立 Session)。测试时可通过
    ``app.dependency_overrides[get_session_factory]`` 替换为 SQLite 工厂,
    与 ``get_db`` 的 override 保持一致,避免直连生产 PostgreSQL。
    """
    return SessionLocal
