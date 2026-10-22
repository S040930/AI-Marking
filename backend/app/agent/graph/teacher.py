"""教师检查点：ask（写库）→ wait（纯 interrupt）→ apply（校验/回填）。

ask 与 wait 必须分离：LangGraph 在 resume 时会从节点开头重跑，
任何写库动作都不能放在 ``interrupt()`` 之前。
"""

from __future__ import annotations

import uuid
from typing import Any

from langgraph.types import interrupt
from sqlalchemy.orm import sessionmaker

from app.agent import run_store as run_repo
from app.agent.graph.package import PackageRuntime, ingest_package
from app.agent.graph.state import GradingState
from app.application.grading_package import build_context_parts
from app.models.grading_run import GradingRun
from app.models.submission import Submission

CHECKPOINT_MESSAGE = (
    "请确认：本地运行学生代码的表现与报告描述是否一致？"
    "可在本地运行后确认，或直接确认；若存在不一致，请填写涉及题号、"
    "报告页/图或结果的说明。"
)


def build_teacher_nodes(
    *,
    session_factory: sessionmaker,
    run_id: int,
    submission_id: int,
    claim_token: str | None,
    runtime: PackageRuntime,
):
    def ask(state: GradingState) -> dict[str, Any]:
        with session_factory() as db:
            run = db.get(GradingRun, run_id)
            sub = db.get(Submission, submission_id)
            if run is None or sub is None:
                return {"errors": ["批改 run 或 submission 不存在"]}
            # 幂等：教师已答复则不再提问，避免 resume 重跑覆盖答复。
            if run.teacher_answered_at is not None:
                return {
                    "teacher": {
                        "verdict": run.teacher_verdict,
                        "note": run.teacher_note,
                        "checkpoint_id": run.checkpoint_request_id,
                    },
                    "teacher_checkpoint_id": run.checkpoint_request_id,
                    "phase": "teacher",
                }
            if run.checkpoint_request_id and run.checkpoint:
                return {
                    "teacher_checkpoint_id": run.checkpoint_request_id,
                    "phase": "teacher",
                }
            _, _, _, context_hash, _ = build_context_parts(db, sub)
            checkpoint_id = uuid.uuid4().hex
            run_repo.save_checkpoint(
                db,
                run,
                checkpoint={
                    "id": checkpoint_id,
                    "type": "code_consistency",
                    "message": CHECKPOINT_MESSAGE,
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
                payload={
                    "checkpoint_id": checkpoint_id,
                    "message": CHECKPOINT_MESSAGE,
                },
            )
        return {
            "teacher_checkpoint_id": checkpoint_id,
            "phase": "teacher",
        }

    def wait(state: GradingState) -> dict[str, Any]:
        # 纯暂停：不能有任何写库或外部副作用。
        decision = interrupt(
            {
                "type": "code_consistency",
                "checkpoint_id": state.get("teacher_checkpoint_id"),
                "message": CHECKPOINT_MESSAGE,
            }
        )
        if isinstance(decision, dict):
            return {"teacher": decision}
        return {"teacher": {"verdict": str(decision)}}

    def apply(state: GradingState) -> dict[str, Any]:
        if state.get("teacher", {}).get("verdict") in ("consistent", "mismatch"):
            return {"phase": "score"}
        with session_factory() as db:
            run = db.get(GradingRun, run_id)
        if run is None or run.teacher_answered_at is None:
            return {"errors": ["尚未收到教师检查点答复"]}
        if (
            state.get("teacher_checkpoint_id")
            and run.checkpoint_request_id
            and state["teacher_checkpoint_id"] != run.checkpoint_request_id
        ):
            return {"errors": ["教师答复与当前检查点不匹配，请重新发起"]}
        return {
            "teacher": {
                "verdict": run.teacher_verdict,
                "note": run.teacher_note,
                "checkpoint_id": run.checkpoint_request_id,
            },
            "phase": "score",
        }

    def ensure_handles(state: GradingState) -> dict[str, Any]:
        """resume 后进程内句柄已丢失：重开评分包换新句柄。"""
        if runtime.artifact is not None and runtime.grading_handle:
            return {"phase": "score"}
        with session_factory() as db:
            sub = db.get(Submission, submission_id)
            if sub is None:
                return {"errors": ["提交记录不存在"]}
            result = ingest_package(db, sub, run_id, runtime)
        if not result.get("ok"):
            return {"errors": [str(result.get("error") or "评分包重新打开失败")]}
        return {
            "phase": "score",
            "package": result["digest"],
            "has_code": bool(result["digest"].get("has_code")),
        }

    return ask, wait, apply, ensure_handles
