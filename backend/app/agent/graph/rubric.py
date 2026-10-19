"""题目 rubric 提取：模型读草稿（rubric_extract）→ 确定性落库（supervisor_rubric）。

题目只有 OCR、还没有可信 rubric 时，图内闭环：模型只负责从 OCR 逐字提取，提交
工具在服务端按“引用必须是 OCR 子串且含满分、分项和等于总分”预校验；校验通过后
由 ``supervisor_rubric`` 调用 ``save_question_rubric`` 落库。写决定不交给模型，
任一环节失败都 fail-closed。
"""

from __future__ import annotations

import json
from typing import Any, Callable

from sqlalchemy.orm import sessionmaker

from app.agent import run_store as run_repo
from app.agent.graph.budget import ConcurrencyGate, ProviderPool, RunBudget
from app.agent.graph.model_gateway import ModelGateway, prompt_cache_key
from app.agent.graph.package import PackageRuntime, ingest_package
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


def _parse_rubric_draft(items: list[dict[str, Any]]) -> dict[str, Any] | None:
    """从 function_call 参数收集 rubric 草稿；后写覆盖，取最后一次提交。"""
    for item in reversed(items):
        if item.get("type") != "function_call":
            continue
        if item.get("name") != TOOL_SUBMIT_QUESTION_RUBRIC:
            continue
        try:
            args = json.loads(item.get("arguments") or "{}")
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

    def _emit(agent: str):
        async def emit(event) -> None:
            with session_factory() as db:
                run_repo.append_event(
                    db, run_id, kind=event.kind, payload=dict(event.payload)
                )

        return emit

    def _ensure_artifact() -> dict[str, Any] | None:
        if runtime.artifact is not None:
            return runtime.artifact
        with session_factory() as db:
            sub = db.get(Submission, submission_id)
            if sub is None:
                return None
            result = ingest_package(db, sub, run_id, runtime)
        if not result.get("ok"):
            return None
        return runtime.artifact

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
            build_inspection_tools(
                InspectionContext(artifact=artifact, teacher=None)
            ),
            ocr_text=ocr_text,
        )
        agent = "rubric"
        gateway = ModelGateway(
            provider_pool.get(agent),
            tools,
            instructions=instructions,
            agent=agent,
            model=model,
            reasoning_effort=reasoning_effort,
            max_rounds=4,
            should_cancel=should_cancel,
            config_getter=config_getter,
            cache_key=prompt_cache_key(run_id, instructions + agent),
        )
        async with gate.slot():
            result = await gateway.run_tool_loop(
                [user_message(RUBRIC_BRIEF)], _emit(agent)
            )
        budget.record(result.usage)
        update: dict[str, Any] = {
            "usage": result.usage,
            "provider_state": {agent: result.provider_state},
        }
        if result.status == "cancelled":
            update.update({"phase": "done", "final": {"status": "cancelled"}})
            return update
        draft = _parse_rubric_draft(result.items)
        if draft is None:
            update["errors"] = [result.error or "rubric 提取未提交结果"]
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
