"""fan-out 逐项评分、服务端组装与证据预检。

设计要点：
- 每个评分分支只读（inspection tools + submit_item_score），结果通过
  function_call 参数回到节点，不写数据库；
- details 由 assemble 按 rubric 顺序组装，模型不能改分数网格；
- 缺项先 rescore 一次，证据错误先 evidence_verify 一次，仍失败则给出可读错误。
"""

from __future__ import annotations

import json
from typing import Any, Callable
from uuid import UUID

from sqlalchemy.orm import sessionmaker

from app.agent import run_store as run_repo
from app.agent.graph.budget import ConcurrencyGate, ProviderPool, RunBudget
from app.agent.graph.limits import (
    CHUNK_SIZE,
    FANOUT_ITEMS,
    HARD_ITEM_LIMIT,
    MAX_BRIEF_CHARS,
)
from app.agent.graph.model_gateway import ModelGateway, prompt_cache_key
from app.agent.graph.package import PackageRuntime, ingest_package
from app.agent.graph.state import GradingState
from app.agent.graph.tool_guard import build_node_tools
from app.agent.models import EngineEvent, user_message
from app.agent.prompts.loader import (
    evidence_instructions,
    item_scorer_instructions,
    supervisor_instructions,
)
from app.agent.tools.grading import GradingToolContext, build_grading_tools
from app.agent.tools.inspection import InspectionContext, build_inspection_tools
from app.agent.tools.scoring import (
    TOOL_SUBMIT_EVIDENCE_FIX,
    TOOL_SUBMIT_ITEM_SCORE,
    add_evidence_tool,
    add_scoring_tool,
)
from app.application.grading_workflow import (
    resolve_submission_rubric,
    validate_assessment_payload,
)
from app.core.errors import ValidationError
from app.models.submission import Submission
from app.schemas.grading import GradingAssessmentRequest

MAX_RESCORE_ROUNDS = 1
MAX_VERIFY_ROUNDS = 2


def _chunks(items: list[dict[str, Any]], size: int) -> list[list[dict[str, Any]]]:
    return [items[index : index + size] for index in range(0, len(items), size)]


def _group_items(items: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """按条数上限与 brief 字符预算分组；单条超预算则 fail-closed。"""
    if len(items) <= FANOUT_ITEMS:
        return [[item] for item in items]
    groups: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    current_chars = 0
    for item in items:
        item_chars = len(json.dumps(item, ensure_ascii=False))
        if current and (
            len(current) >= CHUNK_SIZE
            or current_chars + item_chars > MAX_BRIEF_CHARS - 1_500
        ):
            groups.append(current)
            current = []
            current_chars = 0
        current.append(item)
        current_chars += item_chars
    if current:
        groups.append(current)
    return groups


def _rubric_ids(items: list[dict[str, Any]]) -> list[str]:
    return [str(item.get("rubric_item_id")) for item in items]


def _build_brief(
    artifact: dict[str, Any],
    items: list[dict[str, Any]],
    *,
    extra: str | None = None,
) -> str:
    rubric = artifact.get("rubric") or {}
    sections = [
        section.get("name")
        for section in artifact.get("sections", [])
        if isinstance(section, dict)
    ]
    payload = {
        "rubric_items": items,
        "rubric_source": rubric.get("source"),
        "rubric_snapshot_id": rubric.get("snapshot_id"),
        "has_code": bool(artifact.get("has_code")),
        "sections": sections,
        "code_files": [
            {"filename": entry.get("filename"), "line_count": entry.get("line_count")}
            for entry in artifact.get("code_files", [])
            if isinstance(entry, dict)
        ],
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if extra:
        text += "\n\n" + extra
    if len(text) > MAX_BRIEF_CHARS:
        raise ValueError(
            f"单项评分说明超过 {MAX_BRIEF_CHARS} 字，请拆分 rubric 或缩减 details"
        )
    return text


def _summary_brief(state: GradingState) -> str:
    draft = state.get("assessment_draft") or {}
    details = draft.get("details") or []
    lines = [
        f"- {detail.get('rubric_item_id')}: {detail.get('score')} 分 — "
        f"{detail.get('comment', '')[:120]}"
        for detail in details
        if isinstance(detail, dict)
    ]
    total = sum(float(detail.get("score") or 0) for detail in details)
    return "服务端已组装评分明细（总分 %.2f）：\n%s" % (total, "\n".join(lines))


def _parse_submissions(
    items: list[dict[str, Any]], allowed_ids: set[str]
) -> dict[str, dict[str, Any]]:
    """从 function_call 参数收集评分；同一项后写覆盖。"""
    results: dict[str, dict[str, Any]] = {}
    for item in items:
        if item.get("type") != "function_call":
            continue
        if item.get("name") != TOOL_SUBMIT_ITEM_SCORE:
            continue
        try:
            args = json.loads(item.get("arguments") or "{}")
        except (TypeError, ValueError):
            continue
        if not isinstance(args, dict):
            continue
        item_id = str(args.get("rubric_item_id") or "")
        if item_id not in allowed_ids:
            continue
        try:
            score = float(args.get("score"))
        except (TypeError, ValueError):
            continue
        results[item_id] = {
            "rubric_item_id": item_id,
            "score": score,
            "comment": str(args.get("comment") or ""),
            "evidence_refs": args.get("evidence_refs")
            if isinstance(args.get("evidence_refs"), list)
            else [],
            "evidence_status": str(args.get("evidence_status") or "supported"),
        }
    return results


def _flatten_validation(exc: ValidationError) -> list[str]:
    detail = exc.detail
    if isinstance(detail, dict):
        evidence = detail.get("evidence")
        if isinstance(evidence, list):
            return [str(message) for message in evidence]
        return [str(detail.get("message") or detail)]
    if isinstance(detail, list):
        return [str(message) for message in detail]
    return [str(detail)]


def _failed_ids(messages: list[str], valid_ids: set[str]) -> list[str]:
    failed: list[str] = []
    for message in messages:
        prefix = message.split(":", 1)[0].strip()
        if prefix in valid_ids and prefix not in failed:
            failed.append(prefix)
    return failed


def _parse_evidence_fixes(
    items: list[dict[str, Any]], allowed_ids: set[str]
) -> dict[str, dict[str, Any]]:
    """从 function_call 参数收集证据修正；同一项后写覆盖。"""
    results: dict[str, dict[str, Any]] = {}
    for item in items:
        if item.get("type") != "function_call":
            continue
        if item.get("name") != TOOL_SUBMIT_EVIDENCE_FIX:
            continue
        try:
            args = json.loads(item.get("arguments") or "{}")
        except (TypeError, ValueError):
            continue
        if not isinstance(args, dict):
            continue
        item_id = str(args.get("rubric_item_id") or "")
        if item_id not in allowed_ids:
            continue
        refs = args.get("evidence_refs")
        results[item_id] = {
            "evidence_refs": refs if isinstance(refs, list) else [],
            "evidence_status": str(args.get("evidence_status") or "supported"),
        }
    return results


def _evidence_brief(
    items: list[dict[str, Any]],
    results: dict[str, dict[str, Any]],
    errors: list[str],
) -> str:
    lines = ["以下评分项的证据引用在服务端预检中无法定位，请为每一项修正证据引用："]
    for item in items:
        item_id = str(item.get("rubric_item_id"))
        current = results.get(item_id) or {}
        lines.append(
            json.dumps(
                {
                    "rubric_item_id": item_id,
                    "criterion": item.get("criterion"),
                    "max_score": item.get("max_score"),
                    "details": item.get("details"),
                    "current_score": current.get("score"),
                    "current_comment": current.get("comment"),
                    "current_evidence_refs": current.get("evidence_refs"),
                },
                ensure_ascii=False,
            )
        )
    if errors:
        lines.append("预检错误：")
        lines.extend(f"- {message}" for message in errors)
    return "\n".join(lines)


def _apply_evidence_hints(
    tasks: list[dict[str, Any]], fixes: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    """把独立复核得到的证据作为提示追加进该批评分说明，供重评使用。"""
    updated: list[dict[str, Any]] = []
    for task in tasks:
        hints: list[str] = []
        for item in task.get("items") or []:
            fix = fixes.get(str(item.get("rubric_item_id")))
            if fix is None:
                continue
            hints.append(
                f"- {item.get('rubric_item_id')}: evidence_status="
                f"{fix['evidence_status']}, evidence_refs="
                f"{json.dumps(fix['evidence_refs'], ensure_ascii=False)}"
            )
        if hints:
            extra = (
                "\n\n独立证据复核已给出可定位的证据，请据此重新提交该项"
                "（分数、评语与证据必须一致）：\n" + "\n".join(hints)
            )
            updated.append({**task, "brief": str(task.get("brief") or "") + extra})
        else:
            updated.append(task)
    return updated


def build_scoring_nodes(
    *,
    session_factory: sessionmaker,
    run_id: int,
    submission_id: int,
    provider_name: str,
    provider_pool: ProviderPool,
    gate: ConcurrencyGate,
    budget: RunBudget,
    runtime: PackageRuntime,
    model: str | None,
    reasoning_effort: str | None,
    should_cancel: Callable[[], bool] | None,
    config_getter: Callable[[], tuple[str | None, str | None]] | None,
):
    scorer_instructions = item_scorer_instructions()

    def _emit(agent: str):
        async def emit(event: EngineEvent) -> None:
            with session_factory() as db:
                run_repo.append_event(
                    db, run_id, kind=event.kind, payload=dict(event.payload)
                )

        return emit

    def _ensure_artifact() -> dict[str, Any] | None:
        """resume 后进程内 artifact 丢失时，重开评分包恢复。"""
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

    def prepare_briefs(state: GradingState) -> dict[str, Any]:
        artifact = _ensure_artifact()
        if artifact is None:
            return {"errors": ["评分包工件未就绪，无法拆分评分任务"]}
        items = (artifact.get("rubric") or {}).get("items") or []
        if not items:
            return {"errors": ["rubric 为空，无法评分"]}
        if len(items) > HARD_ITEM_LIMIT:
            return {
                "errors": [
                    f"rubric 共 {len(items)} 项，超过单次批改上限 "
                    f"{HARD_ITEM_LIMIT}; 请拆分为多次作业"
                ]
            }
        groups = _group_items(items)
        try:
            tasks = [
                {
                    "items": group,
                    "brief": _build_brief(artifact, group),
                    "attempt": 0,
                }
                for group in groups
            ]
        except ValueError as exc:
            return {"errors": [str(exc)]}
        return {
            "rubric_items": items,
            "score_tasks": tasks,
            "phase": "score",
        }

    async def score_item(task: dict[str, Any]) -> dict[str, Any]:
        items = task.get("items") or []
        item_ids = set(_rubric_ids(items))
        if not item_ids:
            return {"errors": ["评分任务缺少 rubric 项"]}
        budget_error = budget.error()
        if budget_error:
            return {"errors": [budget_error]}
        agent = "item:" + ",".join(sorted(item_ids))
        artifact = _ensure_artifact() or {}
        tools = add_scoring_tool(
            build_inspection_tools(
                InspectionContext(artifact=artifact, teacher=None)
            )
        )
        gateway = ModelGateway(
            provider_pool.get(agent),
            tools,
            instructions=scorer_instructions,
            agent=agent,
            model=model,
            reasoning_effort=reasoning_effort,
            max_rounds=4,
            should_cancel=should_cancel,
            config_getter=config_getter,
            cache_key=prompt_cache_key(run_id, scorer_instructions + agent),
        )
        async with gate.slot():
            result = await gateway.run_tool_loop(
                [user_message(task.get("brief") or "")],
                _emit(agent),
            )
        budget.record(result.usage)
        if result.status == "cancelled":
            return {"phase": "done", "final": {"status": "cancelled"}}
        parsed = _parse_submissions(result.items, item_ids)
        update: dict[str, Any] = {
            "item_results": parsed,
            "usage": result.usage,
            "provider_state": {agent: result.provider_state},
        }
        # 缺项交给 assemble 决定 rescore，而不是在分支里直接失败；
        # 只有分支本身失败且没有任何可用结果时才记录错误。
        if not parsed and result.status == "failed":
            update["errors"] = [result.error or "评分分支失败"]
        return update

    def assemble(state: GradingState) -> dict[str, Any]:
        if state.get("final"):
            return {}
        rubric_items = state.get("rubric_items") or []
        results = state.get("item_results") or {}
        ordered_ids = _rubric_ids(rubric_items)
        missing = [item_id for item_id in ordered_ids if item_id not in results]
        if missing:
            rounds = int((state.get("counters") or {}).get("rescore_rounds", 0))
            if rounds >= MAX_RESCORE_ROUNDS:
                return {
                    "errors": [f"缺少评分项: {', '.join(missing)}"],
                    "phase": "done",
                }
            items_by_id = {str(item.get("rubric_item_id")): item for item in rubric_items}
            missing_items = [items_by_id[item_id] for item_id in missing]
            artifact = _ensure_artifact() or {}
            return {
                "score_tasks": [
                    {
                        "items": [item],
                        "brief": _build_brief(
                            artifact,
                            [item],
                            extra="上一轮未提交该项，请务必调用 submit_item_score。",
                        ),
                        "attempt": rounds + 1,
                    }
                    for item in missing_items
                ],
                "counters": {"rescore_rounds": 1},
                "phase": "rescore",
            }
        details = []
        for item_id in ordered_ids:
            result = dict(results[item_id])
            result.setdefault("rubric_item_id", item_id)
            result.setdefault("comment", "")
            result.setdefault("evidence_refs", [])
            result.setdefault("evidence_status", "supported")
            details.append(result)
        return {
            "assessment_draft": {
                "details": details,
                "feedback": "",
                "confidence": 0,
            },
            "phase": "verify",
        }

    def verify_prepare(state: GradingState) -> dict[str, Any]:
        if state.get("final"):
            return {}
        draft = state.get("assessment_draft")
        if not draft or not draft.get("details"):
            return {"errors": ["评分明细为空，无法预检"]}
        try:
            payload = GradingAssessmentRequest(
                request_id=UUID(int=0),
                confidence=float(draft.get("confidence") or 0),
                feedback=str(draft.get("feedback") or "预检"),
                details=draft["details"],
            )
        except ValueError as exc:
            return {"errors": [f"评分明细格式错误: {exc}"]}
        messages: list[str] | None = None
        with session_factory() as db:
            sub = db.get(Submission, submission_id)
            if sub is None:
                return {"errors": ["提交记录不存在"]}
            try:
                resolved = resolve_submission_rubric(db, sub)
                validate_assessment_payload(sub, payload, resolved)
            except ValidationError as exc:
                messages = _flatten_validation(exc)
        if messages is None:
            return {"phase": "finalize"}
        valid_ids = set(_rubric_ids(state.get("rubric_items") or []))
        failed = _failed_ids(messages, valid_ids)
        rounds = int((state.get("counters") or {}).get("verify_rounds", 0))
        if failed and rounds < MAX_VERIFY_ROUNDS:
            items_by_id = {
                str(item.get("rubric_item_id")): item
                for item in (state.get("rubric_items") or [])
            }
            return {
                "score_tasks": [
                    {
                        "items": [items_by_id[item_id]],
                        "brief": _build_brief(
                            _ensure_artifact() or {},
                            [items_by_id[item_id]],
                            extra="上一轮证据预检失败，请修正后重新提交：\n"
                            + "\n".join(messages),
                        ),
                        "attempt": rounds + 1,
                    }
                    for item_id in failed
                ],
                "verification_errors": messages,
                "counters": {"verify_rounds": 1},
                "phase": "evidence",
            }
        return {
            "errors": messages,
            "verification_errors": messages,
            "phase": "done",
        }

    async def evidence_verify(state: GradingState) -> dict[str, Any]:
        """独立证据核查：只修证据引用，随后照旧 fan-out 回评分子 agent 重评。"""
        if state.get("final"):
            return {}
        tasks = state.get("score_tasks") or []
        failed_items = [
            item for task in tasks for item in (task.get("items") or [])
        ]
        allowed_ids = set(_rubric_ids(failed_items))
        if not allowed_ids:
            return {"phase": "score"}
        budget_error = budget.error()
        if budget_error:
            return {"errors": [budget_error]}
        artifact = _ensure_artifact() or {}
        results = state.get("item_results") or {}
        brief = _evidence_brief(
            failed_items, results, list(state.get("verification_errors") or [])
        )
        tools = add_evidence_tool(
            build_inspection_tools(
                InspectionContext(
                    artifact=artifact, teacher=state.get("teacher")
                )
            )
        )
        agent = "evidence"
        instructions = evidence_instructions()
        gateway = ModelGateway(
            provider_pool.get(agent),
            tools,
            instructions=instructions,
            agent=agent,
            model=model,
            reasoning_effort=reasoning_effort,
            max_rounds=3,
            should_cancel=should_cancel,
            config_getter=config_getter,
            cache_key=prompt_cache_key(run_id, instructions + agent),
        )
        async with gate.slot():
            result = await gateway.run_tool_loop(
                [user_message(brief)], _emit(agent)
            )
        budget.record(result.usage)
        update: dict[str, Any] = {
            "usage": result.usage,
            "provider_state": {agent: result.provider_state},
        }
        if result.status == "cancelled":
            update.update({"phase": "done", "final": {"status": "cancelled"}})
            return update
        fixes = _parse_evidence_fixes(result.items, allowed_ids)
        update["score_tasks"] = _apply_evidence_hints(tasks, fixes)
        update["phase"] = "score"
        return update

    async def supervisor_finalize(state: GradingState) -> dict[str, Any]:
        if state.get("final"):
            return {}
        budget_error = budget.error()
        if budget_error:
            return {"final": {"status": "failed", "error": budget_error}}
        if runtime.artifact is None or runtime.grading_handle is None:
            _ensure_artifact()
        if runtime.grading_handle is None:
            return {
                "final": {
                    "status": "failed",
                    "error": "评分句柄已失效且重新打开失败，请重新发起批改",
                }
            }
        draft = state.get("assessment_draft") or {}
        ctx = GradingToolContext(
            session_factory=session_factory,
            run_id=run_id,
            submission_id=submission_id,
            grading_handle=runtime.grading_handle,
            allow_write=True,
            assessment_draft=draft,
        )
        tools = build_node_tools(
            "supervisor_finalize", build_grading_tools(ctx)
        )
        agent = "supervisor"
        instructions = supervisor_instructions()
        gateway = ModelGateway(
            provider_pool.get(agent),
            tools,
            instructions=instructions,
            agent=agent,
            model=model,
            reasoning_effort=reasoning_effort,
            max_rounds=2,
            should_cancel=should_cancel,
            config_getter=config_getter,
            cache_key=prompt_cache_key(run_id, instructions),
        )
        result = await gateway.run_tool_loop(
            [user_message(_summary_brief(state))],
            _emit(agent),
        )
        budget.record(result.usage)
        update: dict[str, Any] = {
            "usage": result.usage,
            "provider_state": {agent: result.provider_state},
        }
        if result.status == "completed":
            update.update({"phase": "done", "final": {"status": "completed"}})
        elif result.status == "cancelled":
            update.update({"phase": "done", "final": {"status": "cancelled"}})
        else:
            update.update(
                {
                    "phase": "done",
                    "final": {
                        "status": "failed",
                        "error": result.error or "主管未保存评分建议",
                    },
                }
            )
        return update

    return {
        "prepare_briefs": prepare_briefs,
        "score_item": score_item,
        "assemble": assemble,
        "verify_prepare": verify_prepare,
        "evidence_verify": evidence_verify,
        "supervisor_finalize": supervisor_finalize,
    }
