"""run 级预算、并发闸与分支 provider 池。

订阅通道的 ``turn_state`` 是实例级状态：fan-out 分支必须各自持有
provider 实例，不能共享同一个实例，否则两路会互相覆盖 turn-state。
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable

from app.agent.graph.limits import RUN_TOKEN_BUDGET, max_parallel_for


@dataclass
class RunBudget:
    token_limit: int = RUN_TOKEN_BUDGET
    used: int = 0

    def record(self, usage: dict[str, Any] | None) -> None:
        if not usage:
            return
        total = usage.get("total_tokens")
        if total is None:
            total = int(usage.get("input_tokens") or 0) + int(
                usage.get("output_tokens") or 0
            )
        try:
            self.used += max(0, int(total))
        except (TypeError, ValueError):
            return

    @property
    def exhausted(self) -> bool:
        return self.used >= self.token_limit

    def error(self) -> str | None:
        if not self.exhausted:
            return None
        return (
            f"本次批改 token 预算已用尽（{self.used}/{self.token_limit}），"
            "请拆分作业或减少 rubric 项后重试"
        )


class ConcurrencyGate:
    """按通道限制同时在跑的模型分支；超限排队而不是失败。"""

    def __init__(self, limit: int) -> None:
        self.limit = max(1, limit)
        self._sem = asyncio.Semaphore(self.limit)

    @asynccontextmanager
    async def slot(self) -> AsyncIterator[None]:
        await self._sem.acquire()
        try:
            yield
        finally:
            self._sem.release()


@dataclass
class ProviderPool:
    """每个 agent 分支一个 provider 实例，统一在 run 结束时关闭。"""

    factory: Callable[[str], Any]
    _providers: list[Any] = field(default_factory=list)

    def get(self, agent: str):
        provider = self.factory(agent)
        self._providers.append(provider)
        return provider

    async def aclose(self) -> None:
        for provider in self._providers:
            try:
                await provider.aclose()
            except Exception:  # noqa: BLE001 - 关闭失败不掩盖主错误
                pass
        self._providers.clear()


def gate_for(provider_name: str) -> ConcurrencyGate:
    return ConcurrencyGate(max_parallel_for(provider_name))
