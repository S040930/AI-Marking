"""批改流水线的并发闸门。

全局 ``asyncio.Semaphore`` 控制同时在跑的 ``run_marking_pipeline`` 数量,
防止突发上传把上游 OCR/LLM 服务打爆。

并发上限通过环境变量 ``PIPELINE_CONCURRENCY`` 覆盖,默认 4。
第一版只做并发上限;队列长度上限留作后续增强(当前总是立即拒绝而非排队)。
"""

from __future__ import annotations

import asyncio
import logging
import os

logger = logging.getLogger(__name__)

_DEFAULT_CONCURRENCY = 4
_semaphore: asyncio.Semaphore | None = None
_concurrency: int | None = None
_lock = asyncio.Lock()


def _resolve_concurrency() -> int:
    """读取运行时并发上限,带 fallback。"""
    raw = os.environ.get("PIPELINE_CONCURRENCY")
    if not raw:
        return _DEFAULT_CONCURRENCY
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        logger.warning(
            "PIPELINE_CONCURRENCY=%r 非法,回退默认值 %s",
            raw,
            _DEFAULT_CONCURRENCY,
        )
        return _DEFAULT_CONCURRENCY


def get_pipeline_semaphore() -> asyncio.Semaphore:
    """返回单例信号量,首次调用时初始化。"""
    global _semaphore, _concurrency
    if _semaphore is None:
        _concurrency = _resolve_concurrency()
        _semaphore = asyncio.Semaphore(_concurrency)
        logger.info("Pipeline concurrency limit set to %s", _concurrency)
    return _semaphore


def configure_concurrency(value: int) -> None:
    """运行时调整(便于测试重置)。下次 ``get_pipeline_semaphore`` 会重建。"""
    global _semaphore, _concurrency
    _concurrency = max(1, int(value))
    _semaphore = asyncio.Semaphore(_concurrency)


def get_current_concurrency() -> int:
    """返回当前配置的并发上限(便于日志/诊断)。"""
    global _concurrency
    if _concurrency is None:
        _concurrency = _resolve_concurrency()
    return _concurrency


def has_capacity() -> bool:
    """非阻塞地检查当前是否还有可用的流水线名额。

    返回 ``True`` 表示下一个 ``run_marking_pipeline`` 调用可以不必排队,
    返回 ``False`` 表示闸门已满,调用方应返回 503。
    """
    sem = get_pipeline_semaphore()
    # _value > 0 表示仍有未持有的计数;以该属性作为可靠的"还有多少空闲"
    # asyncio.Semaphore 内部的内部属性,CPython 实现稳定
    return getattr(sem, "_value", 0) > 0
