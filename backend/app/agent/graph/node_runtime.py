"""Shared model-node setup and execution helpers."""

from __future__ import annotations

from typing import Any, Callable

from sqlalchemy.orm import sessionmaker

from app.agent import run_store as run_repo
from app.agent.graph.budget import ConcurrencyGate, ProviderPool, RunBudget
from app.agent.graph.model_gateway import ModelGateway, prompt_cache_key
from app.agent.models import EngineEvent


def graph_event_sink(
    session_factory: sessionmaker, run_id: int, *, agent: str | None = None
):
    async def emit(event: EngineEvent) -> None:
        with session_factory() as db:
            payload = (
                {**event.payload, "agent": agent}
                if agent is not None
                else dict(event.payload)
            )
            run_repo.append_event(
                db, run_id, kind=event.kind, payload=payload
            )

    return emit


def build_model_gateway(
    provider_pool: ProviderPool,
    *,
    run_id: int,
    agent: str,
    tools,
    instructions: str,
    model: str | None,
    reasoning_effort: str | None,
    should_cancel: Callable[[], bool] | None,
    config_getter: Callable[[], tuple[str | None, str | None]] | None,
    max_rounds: int,
    cache_scope: str | None = None,
    terminal_tool_names: set[str] | None = None,
) -> ModelGateway:
    cache_text = instructions + (agent if cache_scope is None else cache_scope)
    return ModelGateway(
        provider_pool.get(agent),
        tools,
        instructions=instructions,
        agent=agent,
        model=model,
        reasoning_effort=reasoning_effort,
        max_rounds=max_rounds,
        should_cancel=should_cancel,
        config_getter=config_getter,
        cache_key=prompt_cache_key(run_id, cache_text),
        terminal_tool_names=terminal_tool_names,
    )


async def run_model_tool_loop(
    gateway: ModelGateway,
    messages: list[dict[str, Any]],
    emit,
    *,
    budget: RunBudget,
    gate: ConcurrencyGate | None,
):
    if gate is None:
        result = await gateway.run_tool_loop(messages, emit)
    else:
        async with gate.slot():
            result = await gateway.run_tool_loop(messages, emit)
    budget.record(result.usage)
    return result
