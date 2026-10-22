"""题目 rubric 提取：模型读草稿（rubric_extract）→ 确定性落库（supervisor_rubric）。

题目只有 OCR、还没有可信 rubric 时，图内闭环：模型只负责从 OCR 提取结构，提交
工具在服务端按“引用必须是 OCR 子串、分项和等于总分、条目不重复”预校验；只有
明确等级区间允许推断百分制总分；等级制原文会原样进入评分上下文，不做拆分或改写；
校验通过后
由 ``supervisor_rubric`` 调用 ``save_question_rubric`` 落库。写决定不交给模型，
任一环节失败都 fail-closed。
"""

from __future__ import annotations

import json
import math
from typing import Any, Callable

from sqlalchemy.orm import sessionmaker

from app.agent.graph.budget import ConcurrencyGate, ProviderPool, RunBudget
from app.agent.graph.node_runtime import (
    build_model_gateway,
    graph_event_sink,
    run_model_tool_loop,
)
from app.agent.graph.package import (
    PackageRuntime,
    ensure_package_artifact,
    ingest_package,
)
from app.agent.graph.state import GradingState
from app.agent.models import user_message
from app.agent.prompts.loader import rubric_instructions
from app.agent.tools.inspection import InspectionContext, build_inspection_tools
from app.agent.tools.scoring import (
    TOOL_SUBMIT_QUESTION_RUBRIC,
    add_rubric_tool,
)
from app.application import grading_workflow as workflow
from app.core.errors import ApplicationError
from app.models.submission import Submission

RUBRIC_BRIEF = (
    "题目还没有可信 rubric。请从题目 OCR 提取评分标准，逐项给出 "
    "criterion/max_score/details/source_quote，并用 submit_question_rubric 提交。"
)
OCR_PAGE_CHARS = 8_000
MIN_RUBRIC_ROUNDS = 8
MAX_RUBRIC_ROUNDS = 24


def _parse_rubric_draft(items: list[dict[str, Any]]) -> dict[str, Any] | None:
    """只收集服务端已校验接受的 rubric 提交。"""
    accepted_call_ids: set[str] = set()
    for item in items:
        if item.get("type") != "function_call_output":
            continue
        output = item.get("output")
        if isinstance(output, str):
            try:
                output = json.loads(output)
            except ValueError:
                continue
        if isinstance(output, dict) and output.get("accepted") is True:
            accepted_call_ids.add(str(item.get("call_id") or ""))

    for item in reversed(items):
        if item.get("type") != "function_call":
            continue
        if item.get("name") != TOOL_SUBMIT_QUESTION_RUBRIC:
            continue
        call_id = str(item.get("call_id") or "")
        if not call_id or call_id not in accepted_call_ids:
            continue
        raw_args = item.get("arguments")
        try:
            args = (
                raw_args if isinstance(raw_args, dict) else json.loads(raw_args or "{}")
            )
        except (TypeError, ValueError):
            continue
        if not isinstance(args, dict):
            continue
        status = str(args.get("status") or "")
        if status not in ("complete", "absent_or_ambiguous"):
            continue
        raw_items = args.get("items")
        return {
            "status": status,
            "items": raw_items if isinstance(raw_items, list) else [],
            "total_max_score": args.get("total_max_score"),
        }
    return None


def _rubric_max_rounds(ocr_chars: int) -> int:
    pages = math.ceil(max(0, ocr_chars) / OCR_PAGE_CHARS)
    return min(MAX_RUBRIC_ROUNDS, max(MIN_RUBRIC_ROUNDS, pages + 4))


def build_rubric_nodes(
    *,
    session_factory: sessionmaker,
    run_id: int,
    submission_id: int,
    provider_pool: ProviderPool,
    gate: ConcurrencyGate,
    budget: RunBudget,
    runtime: PackageRuntime,
    model: str | None,
    reasoning_effort: str | None,
    should_cancel: Callable[[], bool] | None,
    config_getter: Callable[[], tuple[str | None, str | None]] | None,
):
    instructions = rubric_instructions()

    emit = graph_event_sink(session_factory, run_id)

    def _ensure_artifact() -> dict[str, Any] | None:
        return ensure_package_artifact(
            session_factory, submission_id, run_id, runtime
        )

    async def rubric_extract(state: GradingState) -> dict[str, Any]:
        if state.get("final"):
            return {}
        budget_error = budget.error()
        if budget_error:
            return {"errors": [budget_error]}
        artifact = _ensure_artifact()
        if artifact is None:
            return {"errors": ["评分包工件未就绪，无法提取 rubric"]}
        ocr_text = str((artifact.get("question") or {}).get("text") or "")
        if not ocr_text:
            return {"errors": ["题目 OCR 为空，无法提取 rubric"]}
        tools = add_rubric_tool(
            build_inspection_tools(InspectionContext(artifact=artifact, teacher=None)),
            ocr_text=ocr_text,
        )
        agent = "rubric"
        gateway = build_model_gateway(
            provider_pool,
            run_id=run_id,
            agent=agent,
            tools=tools,
            instructions=instructions,
            model=model,
            reasoning_effort=reasoning_effort,
            max_rounds=_rubric_max_rounds(len(ocr_text)),
            should_cancel=should_cancel,
            config_getter=config_getter,
            terminal_tool_names={TOOL_SUBMIT_QUESTION_RUBRIC},
        )
        result = await run_model_tool_loop(
            gateway,
            [user_message(RUBRIC_BRIEF)],
            emit,
            budget=budget,
            gate=gate,
        )
        update: dict[str, Any] = {
            "usage": result.usage,
            "provider_state": {agent: result.provider_state},
        }
        if result.status == "cancelled":
            update.update({"phase": "done", "final": {"status": "cancelled"}})
            return update
        draft = _parse_rubric_draft(result.items)
        if draft is None:
            update["errors"] = [result.error or "rubric 提取未提交通过服务端校验的结果"]
            return update
        update.update(
            {
                "rubric_draft": draft,
                "rubric_status": "needs_extraction",
                "phase": "rubric",
            }
        )
        return update

    def supervisor_rubric(state: GradingState) -> dict[str, Any]:
        if state.get("final"):
            return {}
        draft = state.get("rubric_draft")
        if not draft:
            return {"errors": ["rubric 提取结果为空"]}
        with session_factory() as db:
            sub = db.get(Submission, submission_id)
            if sub is None or sub.question is None:
                return {"errors": ["提交或题目不存在"]}
            if not runtime.rubric_handle:
                # resume 后进程内提取句柄丢失：重开评分包换新句柄。
                ingest_package(db, sub, run_id, runtime)
            if not runtime.rubric_handle:
                return {"errors": ["rubric 提取句柄缺失，请重新发起批改"]}
            try:
                result = workflow.save_question_rubric(
                    db,
                    sub.question_id,
                    runtime.rubric_handle,
                    status_value=str(draft.get("status") or ""),
                    items=[
                        {
                            "criterion": item.get("criterion"),
                            "max_score": item.get("max_score"),
                            "details": item.get("details"),
                            "source_quote": item.get("source_quote"),
                        }
                        for item in (draft.get("items") or [])
                        if isinstance(item, dict)
                    ],
                    total_max_score=draft.get("total_max_score"),
                )
            except ApplicationError as exc:
                return {"errors": [str(exc.detail)]}
        runtime.rubric_handle = None
        status_value = str(result.get("status") or "")
        return {
            "phase": "ingest",
            "rubric_status": "ready" if status_value == "complete" else "absent",
        }

    return {
        "rubric_extract": rubric_extract,
        "supervisor_rubric": supervisor_rubric,
    }
