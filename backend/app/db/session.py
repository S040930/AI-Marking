"""数据库引擎与 Session 工厂。"""

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings

engine = create_engine(settings.DATABASE_URL, pool_pre_ping=True)

SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


def get_db() -> Generator[Session, None, None]:
    """FastAPI 依赖:提供一个数据库 Session 并在结束后关闭。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
