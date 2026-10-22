"""主图的条件路由与 fan-out。

``fan_out`` 返回 ``Send`` 列表；每个评分分支拿到独立 provider 实例与
独立 turn-state。分支数量由 prepare_briefs/rescore/verify 写入
``state.score_tasks``。
"""

from __future__ import annotations

from langgraph.types import Send

from app.agent.graph.state import GradingState


def route_after_plan(state: GradingState) -> str:
    return "fail" if state.get("errors") else "package_ingest"


def route_after_ingest(state: GradingState) -> str:
    if state.get("errors"):
        return "fail"
    if state.get("rubric_status") == "needs_extraction":
        return "rubric_extract"
    return "code_test_prepare" if state.get("has_code") else "ensure_handles"


def route_after_rubric(state: GradingState) -> str:
    """rubric 落库后重新摄包换新工件与评分句柄；失败即 fail-closed。"""
    return "fail" if state.get("errors") else "package_ingest"


def fan_out(state: GradingState) -> list[Send]:
    return [
        Send("score_item", task) for task in (state.get("score_tasks") or [])
    ]


def route_after_assemble(state: GradingState) -> str:
    if state.get("final"):
        return "finalize"
    if state.get("errors"):
        return "fail"
    if state.get("phase") == "rescore":
        return "rescore_missing"
    return "verify_prepare"


def route_after_verify(state: GradingState) -> str:
    if state.get("final"):
        return "finalize"
    if state.get("errors"):
        return "fail"
    if state.get("phase") == "evidence":
        return "evidence_verify"
    return "supervisor_finalize"
