"""P1 工具白名单：写工具只发给对应的 supervisor 节点。"""

from __future__ import annotations

from app.agent.graph.tool_guard import build_node_tools, write_tool_names
from app.agent.tools.grading import (
    TOOL_CONFIRM_VISUAL,
    TOOL_OPEN_PACKAGE,
    TOOL_SAVE_ASSESSMENT,
    TOOL_SAVE_RUBRIC,
    GradingToolContext,
    build_grading_tools,
)


def _registry():
    ctx = GradingToolContext(
        session_factory=None,  # type: ignore[arg-type]
        run_id=1,
        submission_id=1,
    )
    return build_grading_tools(ctx)


def test_read_only_nodes_never_see_write_tools() -> None:
    for node in ("score_item", "rubric_extract", "evidence_verify"):
        names = build_node_tools(node, _registry()).tool_names()
        assert names.isdisjoint(write_tool_names()), node
    names = build_node_tools("score_item", _registry()).tool_names()
    assert TOOL_OPEN_PACKAGE in names


def test_each_supervisor_only_gets_its_own_write_tool() -> None:
    rubric = build_node_tools("supervisor_rubric", _registry()).tool_names()
    confirm = build_node_tools("supervisor_confirm", _registry()).tool_names()
    finalize = build_node_tools("supervisor_finalize", _registry()).tool_names()

    assert TOOL_SAVE_RUBRIC in rubric
    assert TOOL_CONFIRM_VISUAL not in rubric
    assert TOOL_SAVE_ASSESSMENT not in rubric

    assert TOOL_CONFIRM_VISUAL in confirm
    assert TOOL_SAVE_RUBRIC not in confirm
    assert TOOL_SAVE_ASSESSMENT not in confirm

    assert TOOL_SAVE_ASSESSMENT in finalize
    assert TOOL_SAVE_RUBRIC not in finalize
    assert TOOL_CONFIRM_VISUAL not in finalize


def test_supervisor_keeps_read_tools() -> None:
    names = build_node_tools("supervisor_finalize", _registry()).tool_names()
    assert TOOL_OPEN_PACKAGE in names
