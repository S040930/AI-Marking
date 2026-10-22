"""LangGraph 主图：plan → 读包（缺 rubric 时图内提取）→ 教师门 → fan-out
评分 → 预检 → 证据核查 → 提交。

P4 起每个评分分支拥有独立 provider 实例（订阅通道 turn-state 隔离）；
只有 ``supervisor_rubric`` / ``supervisor_confirm`` / ``supervisor_finalize``
能调用写工具。
"""

from __future__ import annotations

from typing import Any, Callable

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command
from sqlalchemy.orm import sessionmaker

from app.agent import run_store as run_repo
from app.agent.graph.budget import ProviderPool, RunBudget, gate_for
from app.agent.graph.checkpointer import create_memory_checkpointer, thread_id_for_run
from app.agent.graph.code_tests import (
    build_code_test_nodes,
    route_after_code_test_apply,
    route_after_code_test_ask,
    route_after_code_test_generate,
    route_after_code_test_prepare,
    route_after_code_test_run,
)
from app.agent.graph.finalize import build_finalize_node
from app.agent.graph.package import PackageRuntime, ingest_package
from app.agent.graph.routing import (
    fan_out,
    route_after_assemble,
    route_after_ingest,
    route_after_plan,
    route_after_rubric,
    route_after_verify,
)
from app.agent.graph.rubric import build_rubric_nodes
from app.agent.graph.scoring import build_scoring_nodes
from app.agent.graph.state import GradingState
from app.agent.graph.teacher import build_teacher_nodes
from app.domain.grading_run import GradingRunStatus
from app.models.grading_run import GradingRun
from app.models.submission import Submission, SubmissionStatus


def build_graph(
    *,
    session_factory: sessionmaker,
    run_id: int,
    submission_id: int,
    question_name: str,
    provider_pool: ProviderPool,
    provider_name: str,
    model: str | None,
    reasoning_effort: str | None,
    claim_token: str | None,
    should_cancel: Callable[[], bool] | None,
    config_getter: Callable[[], tuple[str | None, str | None]] | None,
    checkpointer,
):
    runtime = PackageRuntime()
    budget = RunBudget()
    gate = gate_for(provider_name)

    def plan(state: GradingState) -> dict[str, Any]:
        with session_factory() as db:
            run = db.get(GradingRun, run_id)
            sub = db.get(Submission, submission_id)
            has_code = bool(sub.code_files) if sub is not None else False
        if run is None or sub is None:
            return {"errors": ["批改 run 或 submission 不存在"]}
        if run.graph_version != 1:
            return {
                "errors": [
                    f"run 使用旧架构版本 {run.graph_version}，请重新发起批改"
                ]
            }
        if run.status in (
            GradingRunStatus.completed,
            GradingRunStatus.failed,
            GradingRunStatus.cancelled,
        ):
            return {"errors": [f"run 已处于终态 {run.status.value}"]}
        if sub.status not in (
            SubmissionStatus.awaiting_grading,
            SubmissionStatus.ready_for_review,
        ):
            return {"errors": [f"作业状态不可批改: {sub.status.value}"]}
        return {"phase": "ingest", "has_code": has_code}

    def package_ingest(state: GradingState) -> dict[str, Any]:
        with session_factory() as db:
            sub = db.get(Submission, submission_id)
            if sub is None:
                return {"errors": ["提交记录不存在"]}
            result = ingest_package(db, sub, run_id, runtime)
        if not result.get("ok"):
            return {"errors": [str(result.get("error") or "评分包读取失败")]}
        if result.get("needs_rubric"):
            return {"phase": "rubric", "rubric_status": "needs_extraction"}
        return {
            "phase": "score",
            "package": result["digest"],
            "has_code": bool(result["digest"].get("has_code")),
            "rubric_status": "ready",
        }

    teacher_ask, teacher_wait, teacher_apply, ensure_handles = build_teacher_nodes(
        session_factory=session_factory,
        run_id=run_id,
        submission_id=submission_id,
        claim_token=claim_token,
        runtime=runtime,
    )
    scoring = build_scoring_nodes(
        session_factory=session_factory,
        run_id=run_id,
        submission_id=submission_id,
        provider_name=provider_name,
        provider_pool=provider_pool,
        gate=gate,
        budget=budget,
        runtime=runtime,
        model=model,
        reasoning_effort=reasoning_effort,
        should_cancel=should_cancel,
        config_getter=config_getter,
    )
    finalize = build_finalize_node(
        session_factory=session_factory,
        run_id=run_id,
        claim_token=claim_token,
    )
    rubric = build_rubric_nodes(
        session_factory=session_factory,
        run_id=run_id,
        submission_id=submission_id,
        provider_pool=provider_pool,
        gate=gate,
        budget=budget,
        runtime=runtime,
        model=model,
        reasoning_effort=reasoning_effort,
        should_cancel=should_cancel,
        config_getter=config_getter,
    )
    code_tests = build_code_test_nodes(
        session_factory=session_factory,
        run_id=run_id,
        submission_id=submission_id,
        claim_token=claim_token,
        provider_pool=provider_pool,
        gate=gate,
        budget=budget,
        runtime=runtime,
        model=model,
        reasoning_effort=reasoning_effort,
        should_cancel=should_cancel,
        config_getter=config_getter,
    )

    def fail(state: GradingState) -> dict[str, Any]:
        error = "；".join(state.get("errors") or ["批改运行失败"])
        return {
            "phase": "done",
            "final": {"status": "failed", "error": error[:1024]},
        }

    def rescore_missing(state: GradingState) -> dict[str, Any]:
        # score_tasks 已由 assemble 写入；这里只标记阶段。
        return {"phase": "score"}

    builder = StateGraph(GradingState)
    builder.add_node("plan", plan)
    builder.add_node("package_ingest", package_ingest)
    builder.add_node("rubric_extract", rubric["rubric_extract"])
    builder.add_node("supervisor_rubric", rubric["supervisor_rubric"])
    builder.add_node("code_test_prepare", code_tests["prepare"])
    builder.add_node("code_test_generate", code_tests["generate"])
    builder.add_node("code_test_ask", code_tests["ask"])
    builder.add_node("code_test_wait", code_tests["wait"])
    builder.add_node("code_test_apply", code_tests["apply"])
    builder.add_node("code_test_run", code_tests["run"])
    builder.add_node("teacher_ask", teacher_ask)
    builder.add_node("teacher_wait", teacher_wait)
    builder.add_node("teacher_apply", teacher_apply)
    builder.add_node("ensure_handles", ensure_handles)
    builder.add_node("prepare_briefs", scoring["prepare_briefs"])
    builder.add_node("score_item", scoring["score_item"])
    builder.add_node("assemble", scoring["assemble"])
    builder.add_node("rescore_missing", rescore_missing)
    builder.add_node("verify_prepare", scoring["verify_prepare"])
    builder.add_node("evidence_verify", scoring["evidence_verify"])
    builder.add_node(
        "supervisor_finalize", scoring["supervisor_finalize"]
    )
    builder.add_node("fail", fail)
    builder.add_node("finalize", finalize)

    builder.add_edge(START, "plan")
    def add_routes(source: str, router, *destinations: str) -> None:
        builder.add_conditional_edges(
            source, router, {destination: destination for destination in destinations}
        )

    add_routes("plan", route_after_plan, "package_ingest", "fail")
    add_routes(
        "package_ingest",
        route_after_ingest,
        "rubric_extract",
        "code_test_prepare",
        "ensure_handles",
        "fail",
    )
    builder.add_edge("rubric_extract", "supervisor_rubric")
    add_routes("supervisor_rubric", route_after_rubric, "package_ingest", "fail")
    add_routes(
        "code_test_prepare",
        route_after_code_test_prepare,
        "code_test_generate",
        "code_test_ask",
        "code_test_run",
        "fail",
    )
    add_routes(
        "code_test_generate",
        route_after_code_test_generate,
        "code_test_ask",
        "fail",
        "finalize",
    )
    add_routes("code_test_ask", route_after_code_test_ask, "code_test_wait", "fail")
    builder.add_edge("code_test_wait", "code_test_apply")
    add_routes(
        "code_test_apply",
        route_after_code_test_apply,
        "code_test_generate",
        "code_test_run",
        "fail",
    )
    add_routes("code_test_run", route_after_code_test_run, "teacher_ask", "fail", "finalize")
    builder.add_edge("teacher_ask", "teacher_wait")
    builder.add_edge("teacher_wait", "teacher_apply")
    builder.add_edge("teacher_apply", "ensure_handles")
    builder.add_edge("ensure_handles", "prepare_briefs")
    builder.add_conditional_edges(
        "prepare_briefs", fan_out, ["score_item"]
    )
    builder.add_edge("score_item", "assemble")
    add_routes(
        "assemble",
        route_after_assemble,
        "rescore_missing",
        "verify_prepare",
        "finalize",
        "fail",
    )
    builder.add_conditional_edges(
        "rescore_missing", fan_out, ["score_item"]
    )
    add_routes(
        "verify_prepare",
        route_after_verify,
        "evidence_verify",
        "supervisor_finalize",
        "finalize",
        "fail",
    )
    builder.add_conditional_edges(
        "evidence_verify", fan_out, ["score_item"]
    )
    builder.add_edge("supervisor_finalize", "finalize")
    builder.add_edge("fail", "finalize")
    builder.add_edge("finalize", END)
    return builder.compile(checkpointer=checkpointer)


async def execute_graph(
    *,
    session_factory: sessionmaker,
    run_id: int,
    submission_id: int,
    question_name: str,
    provider=None,
    provider_factory: Callable[[str], Any] | None = None,
    provider_name: str = "openai_api",
    model: str | None,
    reasoning_effort: str | None,
    claim_token: str | None,
    should_cancel: Callable[[], bool] | None,
    config_getter: Callable[[], tuple[str | None, str | None]] | None,
    checkpointer=None,
) -> GradingRunStatus:
    if provider_factory is None:
        if provider is None:
            raise ValueError("execute_graph 需要 provider 或 provider_factory")
        provider_factory = lambda agent: provider  # noqa: E731
    pool = ProviderPool(provider_factory)
    saver = checkpointer or create_memory_checkpointer()
    graph = build_graph(
        session_factory=session_factory,
        run_id=run_id,
        submission_id=submission_id,
        question_name=question_name,
        provider_pool=pool,
        provider_name=provider_name,
        model=model,
        reasoning_effort=reasoning_effort,
        claim_token=claim_token,
        should_cancel=should_cancel,
        config_getter=config_getter,
        checkpointer=saver,
    )
    config = {
        "configurable": {"thread_id": thread_id_for_run(run_id)},
        "recursion_limit": 80,
    }
    initial = {
        "run_id": run_id,
        "submission_id": submission_id,
        "question_name": question_name,
        "phase": "plan",
    }

    def checkpoint_resume_payload() -> dict[str, Any]:
        with session_factory() as db:
            run = db.get(GradingRun, run_id)
        if run is None:
            return {}
        if run.checkpoint_answer is not None:
            return run.checkpoint_answer
        return {
            "verdict": run.teacher_verdict,
            "note": run.teacher_note,
            "checkpoint_id": run.checkpoint_request_id,
        }

    def teacher_answered() -> bool:
        with session_factory() as db:
            run = db.get(GradingRun, run_id)
        return run is not None and run.teacher_answered_at is not None

    def checkpoint_answered() -> bool:
        with session_factory() as db:
            run = db.get(GradingRun, run_id)
        return run is not None and run.checkpoint_answered_at is not None

    def is_teacher_checkpoint(snapshot) -> bool:
        return "teacher_wait" in snapshot.next

    def is_code_test_checkpoint(snapshot) -> bool:
        return "code_test_wait" in snapshot.next

    async def resume_if_answered(snapshot):
        if is_teacher_checkpoint(snapshot):
            if not teacher_answered():
                release_if_waiting()
                return None
            return await graph.ainvoke(Command(resume=checkpoint_resume_payload()), config)
        if is_code_test_checkpoint(snapshot):
            if not checkpoint_answered():
                release_if_waiting()
                return None
            return await graph.ainvoke(Command(resume=checkpoint_resume_payload()), config)
        return await graph.ainvoke(None, config)

    def release_if_waiting() -> bool:
        """检查点驻留：释放租约并保持 waiting_for_teacher。"""
        with session_factory() as db:
            ok = run_repo.release_waiting_run(db, run_id, claim_token or "")
        return ok

    try:
        snapshot = await graph.aget_state(config)
        if snapshot.next:
            result = await resume_if_answered(snapshot)
            if result is None:
                return GradingRunStatus.waiting_for_teacher
        elif snapshot.values:
            result = dict(snapshot.values)
        else:
            result = await graph.ainvoke(initial, config)
            snapshot = await graph.aget_state(config)
            if is_teacher_checkpoint(snapshot) or is_code_test_checkpoint(snapshot):
                resumed = await resume_if_answered(snapshot)
                if resumed is None:
                    return GradingRunStatus.waiting_for_teacher
                result = resumed
    except Exception as exc:  # noqa: BLE001 - 图边界统一转 failed
        with session_factory() as db:
            run = db.get(GradingRun, run_id)
            if run is not None:
                run_repo.transition_run(
                    db,
                    run,
                    GradingRunStatus.failed,
                    claim_token=claim_token,
                    error=f"{type(exc).__name__}: {exc}"[:1024],
                )
        return GradingRunStatus.failed
    finally:
        await pool.aclose()

    final = result.get("final") or {"status": "failed", "error": "图未产出终态"}
    return {
        "completed": GradingRunStatus.completed,
        "cancelled": GradingRunStatus.cancelled,
        "failed": GradingRunStatus.failed,
    }.get(final.get("status"), GradingRunStatus.failed)
