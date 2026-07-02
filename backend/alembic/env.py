"""Alembic 迁移环境配置。

online 迁移使用 ``async_engine_from_config`` + ``connection.run_sync``
模式,与生产 ``asyncpg`` 驱动一致;offline 模式仅生成 SQL,保留同步。
"""

import asyncio
import sys
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

# 确保 alembic 目录的父目录(backend/)在 sys.path 中,以便 import app
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import settings  # noqa: E402
from app.db import base  # noqa: E402,F401  # 确保模型被加载
from app.db.base import Base  # noqa: E402

# Alembic 配置对象
config = context.config

# 从 settings 注入数据库 URL
config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)

# 日志配置
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# 目标元数据,用于 autogenerate
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """以 offline 模式运行迁移(只生成 SQL,不连接数据库)。"""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:
    """在同步上下文中执行迁移(由 ``connection.run_sync`` 调度)。"""
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online_async() -> None:
    """以 online 模式运行迁移(连接数据库执行,异步驱动)。"""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """online 迁移入口:把异步实现挂到事件循环执行。"""
    asyncio.run(run_migrations_online_async())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
