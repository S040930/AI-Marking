"""Generate, approve, execute, and snapshot question-scoped code tests."""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any, Callable

from langgraph.types import interrupt
from sqlalchemy import select
from sqlalchemy.orm import selectinload, sessionmaker

from app.agent import run_store as run_repo
from app.agent.graph.budget import ConcurrencyGate, ProviderPool, RunBudget
from app.agent.graph.node_runtime import (
    build_model_gateway,
    graph_event_sink,
    run_model_tool_loop,
)
from app.agent.graph.package import PackageRuntime, ensure_package_artifact
from app.agent.graph.state import GradingState
from app.agent.models import ToolResult, ToolSpec, user_message
from app.agent.prompts.loader import code_test_instructions
from app.agent.tools import ToolRegistry
from app.application import grading_package as grading_package
from app.models.code_test_suite import CodeTestSuite
from app.models.grading_run import GradingRun
from app.models.submission import Submission
from app.services import code_runner, code_testing
from app.services.code_runner_errors import (
    CodeRunnerCancelledError,
    CodeRunnerInfrastructureError,
)

TOOL_SUBMIT_CODE_TEST_SUITE = "submit_code_test_suite"


def route_after_code_test_prepare(state: GradingState) -> str:
    if state.get("errors"):
        return "fail"
    if state.get("regenerate_code_tests"):
        return "code_test_generate"
    suite = state.get("code_test_suite") or {}
    if suite.get("status") == "approved":
        return "code_test_run"
    if suite.get("status") == "draft":
        return "code_test_ask"
    return "code_test_generate"


def route_after_code_test_generate(state: GradingState) -> str:
    if state.get("final"):
        return "finalize"
    return "fail" if state.get("errors") else "code_test_ask"


def route_after_code_test_ask(state: GradingState) -> str:
    return "fail" if state.get("errors") else "code_test_wait"


def route_after_code_test_apply(state: GradingState) -> str:
    if state.get("errors"):
        return "fail"
    return "code_test_generate" if state.get("code_test_action") == "regenerate_test_suite" else "code_test_run"


def route_after_code_test_run(state: GradingState) -> str:
    if state.get("final"):
        return "finalize"
    return "fail" if state.get("errors") else "teacher_ask"


def _tests_tool() -> ToolSpec:
    case = {
        "type": "object",
        "properties": {
            "question_number": {"type": "integer", "minimum": 1},
            "title": {"type": "string", "minLength": 1, "maxLength": 160},
            "mode": {"type": "string", "enum": ["program_io"]},
            "language": {"type": "string", "enum": ["any", "python", "r", "java"]},
            "input": {"type": ["string", "null"], "maxLength": 8000},
            "expected_stdout": {"type": ["string", "null"], "maxLength": 8000},
        },
        "required": ["question_number", "title", "mode", "language"],
        "additionalProperties": False,
    }
    return ToolSpec(
        name=TOOL_SUBMIT_CODE_TEST_SUITE,
        description=(
            "提交题目级测试集，1–20 项。Python、R 和 Java 使用 program_io，提供 input/expected_stdout。"
            "Notebook 暂不自动执行。服务端会校验所有字段。"
        ),
        parameters={
            "type": "object",
            "properties": {"tests": {"type": "array", "minItems": 1, "maxItems": 20, "items": case}},
            "required": ["tests"],
            "additionalProperties": False,
        },
    )


def _validate_generated_cases(args: dict[str, Any]) -> list[code_testing.CodeTestCase]:
    raw = args.get("tests")
    if not isinstance(raw, list) or not 1 <= len(raw) <= code_testing.MAX_CODE_TESTS:
        raise ValueError("测试项数量必须在 1 到 20 之间")
    cases = [code_testing.CodeTestCase.model_validate(item) for item in raw]
    return cases


def _parse_generated_cases(items: list[dict[str, Any]]) -> list[code_testing.CodeTestCase] | None:
    for item in reversed(items):
        if item.get("type") != "function_call" or item.get("name") != TOOL_SUBMIT_CODE_TEST_SUITE:
            continue
        try:
            args = json.loads(item.get("arguments") or "{}")
            return _validate_generated_cases(args)
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
    return None


def build_code_test_nodes(
    *,
    session_factory: sessionmaker,
    run_id: int,
    submission_id: int,
    claim_token: str | None,
    provider_pool: ProviderPool,
    gate: ConcurrencyGate,
    budget: RunBudget,
    runtime: PackageRuntime,
    model: str | None,
    reasoning_effort: str | None,
    should_cancel: Callable[[], bool] | None,
    config_getter: Callable[[], tuple[str | None, str | None]] | None,
):
    instructions = code_test_instructions()

    emit = graph_event_sink(
        session_factory, run_id, agent="code_test_generator"
    )

    def _ensure_artifact() -> dict[str, Any] | None:
        return ensure_package_artifact(
            session_factory,
            submission_id,
            run_id,
            runtime,
            allow_missing_rubric=False,
        )

    def prepare(state: GradingState) -> dict[str, Any]:
        artifact = _ensure_artifact()
        if artifact is None:
            return {"errors": ["评分包未就绪，无法准备代码测试"]}
        rubric = artifact.get("rubric") or {}
        if not rubric.get("items") or not rubric.get("snapshot_id"):
            return {"errors": ["缺少可信 rubric，无法生成代码测试"]}
        with session_factory() as db:
            sub = db.get(Submission, submission_id)
            if sub is None or sub.question is None:
                return {"errors": ["提交或题目不存在"]}
            fingerprint = code_testing.source_fingerprint(sub.question, str(rubric["snapshot_id"]))
            suite = code_testing.get_current_suite(db, sub.question_id, fingerprint)
            run = db.get(GradingRun, run_id)
            checkpoint_answer = (run.checkpoint_answer or {}) if run else {}
            checkpoint_action = checkpoint_answer.get("action") if checkpoint_answer.get("type") == "code_test_suite_approval" else None
            if checkpoint_action:
                answered_suite = db.get(CodeTestSuite, checkpoint_answer.get("suite_id"))
                if (
                    answered_suite is None
                    or answered_suite.question_id != sub.question_id
                    or answered_suite.version != checkpoint_answer.get("version")
                    or answered_suite.source_fingerprint != fingerprint
                ):
                    return {"errors": ["测试集已失效或题目已变化，请重新生成"]}
                if checkpoint_action == "approve_test_suite":
                    try:
                        approved = code_testing.approve_suite(
                            db,
                            answered_suite.id,
                            question_id=sub.question_id,
                            fingerprint=fingerprint,
                        )
                        snapshot = code_testing.persist_run_snapshot(db, run_id, approved)
                    except ValueError as exc:
                        db.rollback()
                        return {"errors": [str(exc)]}
                    run.checkpoint_answer = None
                    run.regenerate_code_tests = False
                    db.commit()
                    return {
                        "code_test_fingerprint": fingerprint,
                        "code_test_suite": snapshot,
                        "phase": "code_tests",
                    }
                if checkpoint_action == "regenerate_test_suite":
                    if answered_suite.status != "draft":
                        return {"errors": ["待重新生成的测试集已失效"]}
                    answered_suite.status = "superseded"
                    run.checkpoint_answer = None
                    run.regenerate_code_tests = False
                    db.commit()
                    return {
                        "code_test_fingerprint": fingerprint,
                        "regenerate_code_tests": True,
                        "phase": "code_tests",
                    }
                return {"errors": ["测试集检查点答复无效"]}
            force_regenerate = bool(run and run.regenerate_code_tests)
            if force_regenerate and run is not None:
                run.regenerate_code_tests = False
            db.commit()
            if force_regenerate:
                return {
                    "code_test_fingerprint": fingerprint,
                    "regenerate_code_tests": True,
                    "phase": "code_tests",
                }
            if suite is None:
                return {"code_test_fingerprint": fingerprint, "phase": "code_tests"}
            suite_data = {
                "id": suite.id,
                "version": suite.version,
                "status": suite.status,
                "source_fingerprint": suite.source_fingerprint,
                "tests": suite.tests,
            }
        return {"code_test_fingerprint": fingerprint, "code_test_suite": suite_data, "phase": "code_tests"}

    async def generate(state: GradingState) -> dict[str, Any]:
        if state.get("final"):
            return {}
        if should_cancel and should_cancel():
            return {"phase": "done", "final": {"status": "cancelled"}}
        budget_error = budget.error()
        if budget_error:
            return {"errors": [budget_error]}
        artifact = _ensure_artifact()
        if artifact is None:
            return {"errors": ["评分包未就绪，无法生成代码测试"]}
        # Deliberately construct the only model input from question OCR and trusted rubric.
        generation_input = json.dumps(
            {
                "question_ocr": str((artifact.get("question") or {}).get("text") or ""),
                "trusted_rubric": {
                    "source": (artifact.get("rubric") or {}).get("source"),
                    "snapshot_id": (artifact.get("rubric") or {}).get("snapshot_id"),
                    "items": (artifact.get("rubric") or {}).get("items") or [],
                    "total_max_score": (artifact.get("rubric") or {}).get("total_max_score"),
                },
            },
            ensure_ascii=False,
        )
        registry = ToolRegistry()
        registry.register(
            _tests_tool(),
            lambda args: _test_tool_result(args),
        )
        agent = "code_test_generator"
        gateway = build_model_gateway(
            provider_pool,
            run_id=run_id,
            agent=agent,
            tools=registry,
            instructions=instructions,
            model=model,
            reasoning_effort=reasoning_effort,
            max_rounds=4,
            should_cancel=should_cancel,
            config_getter=config_getter,
            terminal_tool_names={TOOL_SUBMIT_CODE_TEST_SUITE},
        )
        result = await run_model_tool_loop(
            gateway,
            [user_message(generation_input)],
            emit,
            budget=budget,
            gate=gate,
        )
        update: dict[str, Any] = {
            "usage": result.usage,
            "provider_state": {agent: result.provider_state},
        }
        if result.status == "cancelled":
            return {**update, "phase": "done", "final": {"status": "cancelled"}}
        if result.status != "completed":
            return {**update, "errors": [result.error or "AI 测试集生成未完成"]}
        cases = _parse_generated_cases(result.items)
        if not cases:
            return {**update, "errors": [result.error or "AI 未生成有效代码测试集"]}
        with session_factory() as db:
            sub = db.execute(
                select(Submission)
                .where(Submission.id == submission_id)
                .options(
                    selectinload(Submission.code_files),
                    selectinload(Submission.code_input_files),
                )
            ).scalar_one_or_none()
            if sub is None or sub.question is None:
                return {**update, "errors": ["提交或题目不存在"]}
            rubric = artifact.get("rubric") or {}
            fingerprint = code_testing.source_fingerprint(sub.question, str(rubric.get("snapshot_id") or ""))
            if fingerprint != state.get("code_test_fingerprint"):
                return {**update, "errors": ["题目或 rubric 在测试生成期间变化，请重新发起批改"]}
            try:
                suite = code_testing.create_draft_suite(
                    db,
                    question_id=sub.question_id,
                    fingerprint=fingerprint,
                    cases=cases,
                    generated_by_run_id=run_id,
                )
            except ValueError as exc:
                return {**update, "errors": [str(exc)]}
            db.commit()
            suite_data = {
                "id": suite.id,
                "version": suite.version,
                "status": suite.status,
                "source_fingerprint": suite.source_fingerprint,
                "tests": suite.tests,
            }
        with session_factory() as db:
            run_repo.append_event(
                db,
                run_id,
                kind="code_test_suite_generated",
                payload={"suite_id": suite_data["id"], "version": suite_data["version"]},
            )
        return {**update, "code_test_suite": suite_data, "phase": "code_tests"}

    def ask(state: GradingState) -> dict[str, Any]:
        suite = state.get("code_test_suite") or {}
        if suite.get("status") != "draft" or not suite.get("tests"):
            return {"errors": ["待审核测试集不存在"]}
        with session_factory() as db:
            run = db.get(GradingRun, run_id)
            sub = db.get(Submission, submission_id)
            if run is None or sub is None:
                return {"errors": ["批改 run 或 submission 不存在"]}
            _, _, _, context_hash, _ = grading_package.build_context_parts(db, sub)
            checkpoint_id = uuid.uuid4().hex
            message = "请查看 AI 根据题目 OCR 和可信 rubric 生成的代码测试集。批准后同题作业会复用此版本；也可以重新生成。"
            run_repo.save_checkpoint(
                db,
                run,
                checkpoint={
                    "id": checkpoint_id,
                    "type": "code_test_suite_approval",
                    "message": message,
                    "preview": {
                        "suite_id": suite["id"],
                        "version": suite["version"],
                        "status": "draft",
                        "tests": suite["tests"],
                    },
                },
                dwell_seconds=run_repo.CHECKPOINT_DWELL_SECONDS,
                confirmation_revision=sub.grading_revision,
                confirmation_context_hash=context_hash,
                checkpoint_request_id=checkpoint_id,
                claim_token=claim_token,
            )
            run_repo.append_event(
                db,
                run_id,
                kind="checkpoint_requested",
                payload={"checkpoint_id": checkpoint_id, "type": "code_test_suite_approval", "message": message, "version": suite["version"]},
            )
        return {"code_test_checkpoint_id": checkpoint_id, "phase": "code_tests"}

    def wait(state: GradingState) -> dict[str, Any]:
        suite = state.get("code_test_suite") or {}
        decision = interrupt(
            {
                "type": "code_test_suite_approval",
                "checkpoint_id": state.get("code_test_checkpoint_id"),
                "message": "请批准测试集或重新生成。",
                "preview": {
                    "suite_id": suite.get("id"),
                    "version": suite.get("version"),
                    "status": "draft",
                    "tests": suite.get("tests"),
                },
            }
        )
        if isinstance(decision, dict):
            return {"code_test_decision": decision, "phase": "code_tests"}
        return {"errors": ["测试集检查点答复格式错误"]}

    def apply(state: GradingState) -> dict[str, Any]:
        decision = state.get("code_test_decision") or {}
        action = decision.get("action")
        suite = state.get("code_test_suite") or {}
        if action not in {"approve_test_suite", "regenerate_test_suite"}:
            return {"errors": ["测试集检查点答复无效"]}
        with session_factory() as db:
            run = db.get(GradingRun, run_id)
            sub = db.get(Submission, submission_id)
            if run is None or sub is None or run.checkpoint_answered_at is None:
                return {"errors": ["尚未收到测试集检查点答复"]}
            if decision.get("checkpoint_id") != run.checkpoint_request_id:
                return {"errors": ["测试集答复与当前检查点不匹配"]}
            if action == "regenerate_test_suite":
                draft = db.execute(
                    select(CodeTestSuite)
                    .where(CodeTestSuite.id == suite.get("id"), CodeTestSuite.status == "draft")
                    .with_for_update()
                ).scalar_one_or_none()
                if draft is None:
                    return {"errors": ["待重新生成的测试集已失效"]}
                draft.status = "superseded"
                run.checkpoint_answer = None
                run.regenerate_code_tests = False
                db.commit()
                return {"code_test_action": action, "phase": "code_tests"}
            rubric = (state.get("package") or {}).get("rubric_snapshot_id") or (runtime.artifact or {}).get("rubric", {}).get("snapshot_id")
            fingerprint = code_testing.source_fingerprint(sub.question, str(rubric or ""))
            try:
                approved = code_testing.approve_suite(
                    db,
                    int(suite.get("id")),
                    question_id=sub.question_id,
                    fingerprint=fingerprint,
                )
                snapshot = code_testing.persist_run_snapshot(db, run_id, approved)
                run.checkpoint_answer = None
                run.regenerate_code_tests = False
                db.commit()
            except (ValueError, TypeError) as exc:
                db.rollback()
                return {"errors": [str(exc)]}
        return {
            "code_test_action": action,
            "code_test_suite": snapshot,
            "phase": "code_tests",
        }

    async def run_tests(state: GradingState) -> dict[str, Any]:
        suite = state.get("code_test_suite") or {}
        if suite.get("status") != "approved":
            return {"errors": ["代码测试集尚未批准"]}
        with session_factory() as db:
            sub = db.execute(
                select(Submission)
                .where(Submission.id == submission_id)
                .options(
                    selectinload(Submission.code_files),
                    selectinload(Submission.code_input_files),
                )
            ).scalar_one_or_none()
            run = db.get(GradingRun, run_id)
            if sub is None or run is None:
                return {"errors": ["提交或批改 run 不存在"]}
            if not suite.get("source_fingerprint"):
                rubric_snapshot = (runtime.artifact or {}).get("rubric", {}).get("snapshot_id")
                suite["source_fingerprint"] = code_testing.source_fingerprint(sub.question, str(rubric_snapshot or ""))
            snapshot = code_testing.persist_run_snapshot(db, run_id, suite)
            db.commit()
        try:
            results = await asyncio.to_thread(
                code_runner.run_submission_tests,
                sub,
                suite.get("tests") or [],
                run_id=run_id,
                should_cancel=should_cancel,
            )
        except CodeRunnerCancelledError:
            return {"phase": "done", "final": {"status": "cancelled"}}
        except CodeRunnerInfrastructureError as exc:
            return {"errors": [f"代码测试运行失败：{exc}"]}
        with session_factory() as db:
            payload = code_testing.persist_run_results(db, run_id, snapshot, results)
            run_repo.append_event(
                db,
                run_id,
                kind="code_test_results",
                payload={"suite_id": payload["suite_id"], "version": payload["version"], "summary": payload["summary"]},
            )
            db.commit()
        if runtime.artifact is not None:
            _attach_test_results(runtime.artifact, payload)
        return {
            "code_test_results": payload,
            "phase": "code_tests",
        }

    return {
        "prepare": prepare,
        "generate": generate,
        "ask": ask,
        "wait": wait,
        "apply": apply,
        "run": run_tests,
    }


def _test_tool_result(args: dict[str, Any]) -> ToolResult:
    try:
        cases = _validate_generated_cases(args)
    except Exception as exc:  # noqa: BLE001 - validation error is a model correction hint
        return ToolResult(ok=False, error=f"测试集格式错误：{exc}")
    return ToolResult(ok=True, output={"accepted": True, "count": len(cases)})


def _attach_test_results(artifact: dict[str, Any], payload: dict[str, Any]) -> None:
    artifact["test_results"] = payload
    sections = artifact.setdefault("sections", [])
    sections[:] = [section for section in sections if section.get("name") != "test_results"]
    sections.append(
        {
            "name": "test_results",
            "text": json.dumps(payload, ensure_ascii=False),
            "chars": len(json.dumps(payload, ensure_ascii=False)),
        }
    )
