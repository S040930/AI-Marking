"""P4 预算、并发闸与 provider 池。"""

from __future__ import annotations

import asyncio

from app.agent.graph.budget import ConcurrencyGate, ProviderPool, RunBudget, gate_for


def test_budget_accumulates_and_exhausts() -> None:
    budget = RunBudget(token_limit=100)
    budget.record({"input_tokens": 30, "output_tokens": 20})
    assert budget.used == 50 and not budget.exhausted
    budget.record({"total_tokens": 60})
    assert budget.used == 110
    assert budget.exhausted
    assert "预算已用尽" in (budget.error() or "")


def test_gate_limits_match_channels() -> None:
    assert gate_for("openai_api").limit == 3
    assert gate_for("chatgpt_sub").limit == 2
    assert gate_for("unknown").limit == 2


async def test_concurrency_gate_queues_instead_of_failing() -> None:
    gate = ConcurrencyGate(2)
    active = 0
    peak = 0

    async def worker() -> None:
        nonlocal active, peak
        async with gate.slot():
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.01)
            active -= 1

    await asyncio.gather(*(worker() for _ in range(6)))
    assert peak <= 2
    assert active == 0


async def test_provider_pool_creates_per_agent_and_closes() -> None:
    class FakeProvider:
        def __init__(self, agent: str) -> None:
            self.agent = agent
            self.closed = False

        async def aclose(self) -> None:
            self.closed = True

    providers: list[FakeProvider] = []

    def factory(agent: str) -> FakeProvider:
        provider = FakeProvider(agent)
        providers.append(provider)
        return provider

    pool = ProviderPool(factory)
    first = pool.get("item:a")
    second = pool.get("item:b")
    assert first is not second
    assert first.agent == "item:a"
    await pool.aclose()
    assert all(provider.closed for provider in providers)
