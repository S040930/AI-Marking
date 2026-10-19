"""LangGraph checkpointer：Postgres 生产 saver + 测试用内存 saver。

边界：
- ``setup()`` 只在 ``scripts/setup_checkpointer.py`` / 启动脚本中调用一次，
  worker 不在每次启动时执行 DDL，避免多进程并发建表。
- 业务数据库用 psycopg2（SQLAlchemy），checkpointer 用 psycopg3；
  两者共用一个数据库，但连接池彼此独立。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from app.core.config import settings

_SQLALCHEMY_PREFIXES = (
    "postgresql+psycopg2://",
    "postgresql+psycopg://",
)


def dsn_for_psycopg(url: str) -> str:
    """把 SQLAlchemy DSN 转成 psycopg3 可用的 ``postgresql://``。"""
    for prefix in _SQLALCHEMY_PREFIXES:
        if url.startswith(prefix):
            return "postgresql://" + url[len(prefix) :]
    return url


def thread_id_for_run(run_id: int) -> str:
    """每个 run 一个 thread；长度远小于 Postgres 的 255 字符限制。"""
    return f"grading-run-{run_id}"


@dataclass
class CheckpointerHandle:
    """saver 与连接池的生命周期绑定；worker 关闭时调用 ``aclose``。"""

    saver: Any
    pool: AsyncConnectionPool | None = None

    async def aclose(self) -> None:
        if self.pool is not None:
            await self.pool.close()
            self.pool = None


async def create_postgres_checkpointer(
    database_url: str | None = None,
    *,
    max_size: int = 4,
) -> CheckpointerHandle:
    """建立 checkpointer 连接池；不执行 ``setup()``。"""
    dsn = dsn_for_psycopg(database_url or settings.DATABASE_URL)
    pool = AsyncConnectionPool(
        conninfo=dsn,
        min_size=1,
        max_size=max_size,
        open=False,
        kwargs={
            "autocommit": True,
            "prepare_threshold": 0,
            "row_factory": dict_row,
        },
    )
    await pool.open()
    # 数据库不可达时快速失败，避免启动脚本无限等待。
    await pool.wait(timeout=10.0)
    return CheckpointerHandle(saver=AsyncPostgresSaver(pool), pool=pool)


async def setup_postgres_checkpointer(
    database_url: str | None = None,
    *,
    max_size: int = 2,
) -> None:
    """建表/迁移 checkpointer schema；幂等，供部署脚本调用。"""
    handle = await create_postgres_checkpointer(database_url, max_size=max_size)
    try:
        await handle.saver.setup()
    finally:
        await handle.aclose()


def create_memory_checkpointer() -> InMemorySaver:
    """进程内 saver；只用于测试，不能跨 worker 重启。"""
    return InMemorySaver()
