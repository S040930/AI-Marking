"""订阅通道的进程级刷新锁。

refresh_token 轮换只能消费一次；同进程内多个 provider 实例（fan-out 分支）
必须串行刷新。锁按事件循环创建，避免在 import 期绑定到错误 loop。
"""

from __future__ import annotations

import asyncio
import weakref
from typing import Any

_locks: "weakref.WeakKeyDictionary[Any, asyncio.Lock]" = weakref.WeakKeyDictionary()


def shared_refresh_lock() -> asyncio.Lock:
    """返回当前事件循环共享的刷新锁。"""
    loop = asyncio.get_running_loop()
    lock = _locks.get(loop)
    if lock is None:
        lock = asyncio.Lock()
        _locks[loop] = lock
    return lock


def refresh_lock_or_new() -> asyncio.Lock:
    """在无运行循环的同步构造场景退化为独立锁。"""
    try:
        return shared_refresh_lock()
    except RuntimeError:
        return asyncio.Lock()
