"""数据库异步引擎与 AsyncSession 工厂。

使用 ``asyncpg`` 驱动 + SQLAlchemy 2.0 ``AsyncSession``。
后台流水线(``app.services.marking``)通过 ``AsyncSessionLocal()``
显式获取 session;API 层通过 ``get_db`` 依赖注入获取。
"""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import settings

# 显式配置连接池参数,避免默认 5+10 在并发批改 + 轮询场景下不够用
engine = create_async_engine(
    settings.DATABASE_URL,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
    pool_timeout=30,
    pool_recycle=1800,
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI 依赖:提供一个 AsyncSession 并在结束后关闭。"""
    async with AsyncSessionLocal() as db:
        yield db
