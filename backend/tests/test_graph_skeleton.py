"""P0 骨架测试：DSN/thread_id/reducer、checkpointer 与 interrupt 契约。

这些用例不依赖业务数据库：内存 saver 即可验证 LangGraph 的
``interrupt`` / ``Command(resume=...)`` 语义，防止升级时静默破坏。
"""

from __future__ import annotations

from typing import TypedDict

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from app.agent.graph.checkpointer import (
    create_memory_checkpointer,
    dsn_for_psycopg,
    thread_id_for_run,
)
from app.agent.graph.limits import max_parallel_for
from app.agent.graph.state import (
    merge_by_key,
    merge_item_results,
    merge_usage,
)


def test_dsn_for_psycopg_strips_sqlalchemy_driver() -> None:
    assert (
        dsn_for_psycopg("postgresql+psycopg2://u:p@localhost:5555/db")
        == "postgresql://u:p@localhost:5555/db"
    )
    assert (
        dsn_for_psycopg("postgresql+psycopg://u:p@localhost:5555/db")
        == "postgresql://u:p@localhost:5555/db"
    )
    assert (
        dsn_for_psycopg("postgresql://u:p@localhost:5555/db")
        == "postgresql://u:p@localhost:5555/db"
    )


def test_thread_id_is_stable_and_bounded() -> None:
    assert thread_id_for_run(42) == "grading-run-42"
    assert len(thread_id_for_run(2**31)) < 255


def test_reducers_merge_without_mutating() -> None:
    left = {"r1": {"score": 1}, "r2": {"score": 2}}
    right = {"r2": {"score": 3}, "r3": {"score": 4}}
    merged = merge_item_results(left, right)
    assert merged["r1"]["score"] == 1
    assert merged["r2"]["score"] == 3
    assert merged["r3"]["score"] == 4
    assert left["r2"]["score"] == 2  # 未原地修改

    usage = merge_usage({"input_tokens": 10}, {"input_tokens": 5, "model": "m"})
    assert usage == {"input_tokens": 15, "model": "m"}

    assert merge_by_key({"a": {"x": 1}}, {"b": {"y": 2}}) == {
        "a": {"x": 1},
        "b": {"y": 2},
    }


def test_parallel_limits_are_conservative_for_subscription() -> None:
    assert max_parallel_for("openai_api") == 3
    assert max_parallel_for("chatgpt_sub") == 2
    assert max_parallel_for("unknown") == 2


class _State(TypedDict, total=False):
    value: str
    approved: bool


async def _ask(state: _State) -> dict:
    answer = interrupt({"question": "approve?"})
    return {"approved": bool(answer)}


def _build_graph():
    builder = StateGraph(_State)
    builder.add_node("ask", _ask)
    builder.add_edge(START, "ask")
    builder.add_edge("ask", END)
    return builder.compile(checkpointer=create_memory_checkpointer())


async def test_interrupt_and_resume_roundtrip() -> None:
    graph = _build_graph()
    config = {"configurable": {"thread_id": "t-1"}}

    paused = await graph.ainvoke({"value": "x"}, config)
    assert "__interrupt__" in paused
    snapshot = await graph.aget_state(config)
    assert snapshot.next == ("ask",)

    resumed = await graph.ainvoke(Command(resume=True), config)
    assert resumed["approved"] is True
    final = await graph.aget_state(config)
    assert final.next == ()


async def test_memory_checkpointer_is_isolated_by_thread() -> None:
    graph = _build_graph()
    first = {"configurable": {"thread_id": "a"}}
    second = {"configurable": {"thread_id": "b"}}

    await graph.ainvoke({"value": "a"}, first)
    await graph.ainvoke({"value": "b"}, second)
    assert (await graph.aget_state(first)).values["value"] == "a"
    assert (await graph.aget_state(second)).values["value"] == "b"


def test_in_memory_saver_type() -> None:
    assert isinstance(create_memory_checkpointer(), InMemorySaver)


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__])
