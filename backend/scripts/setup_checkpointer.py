#!/usr/bin/env python
"""初始化 LangGraph Postgres checkpointer 的表结构。

用法（在 ``backend/`` 下）::

    .venv/bin/python scripts/setup_checkpointer.py

必须在 ``alembic upgrade head`` 之后执行；脚本幂等，可重复运行。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# 允许 ``python scripts/setup_checkpointer.py`` 直接找到 app 包。
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent.graph.checkpointer import setup_postgres_checkpointer  # noqa: E402


def main() -> int:
    try:
        asyncio.run(setup_postgres_checkpointer())
    except Exception as exc:  # noqa: BLE001 - CLI 边界，打印可读原因
        print(
            f"checkpointer 初始化失败：{type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 1
    print("checkpointer schema 已就绪")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
