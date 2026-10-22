"""教师检查点:答复落库与续跑恢复。

- ``answer_checkpoint``:把教师答复写入 run(绑定 submission 的
  revision 与 context hash;不匹配即失败并提示重新发起);
- 续跑的用户消息由图节点 ``graph.teacher`` 生成,决策由 ``interrupt`` 恢复,
  模型不再负责转述教师答复。
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.agent.run_store import _locked_run, _recover_run, transition_run
from app.core.time import utc_now_naive
from app.domain.grading_run import GradingRunStatus
from app.models.grading_run import GradingRun


def answer_checkpoint(
    db: Session,
    run: GradingRun,
    *,
    verdict: str | None = None,
    action: str | None = None,
    note: str | None = None,
) -> bool:
    """锁序 Question -> Submission -> Run，答复只绑定当前检查点。"""
    from app.application.grading_package import build_context_parts
    from app.application.locking import lock_submission_after_question

    sub = lock_submission_after_question(db, run.submission_id)
    run = _locked_run(db, run.id)
    if (
        run is None
        or sub is None
        or run.status != GradingRunStatus.waiting_for_teacher
        or run.checkpoint is None
    ):
        return False
    _, _, _, current_hash, resolved = build_context_parts(db, sub)
    if (
        run.confirmation_revision != sub.grading_revision
        or run.confirmation_context_hash != current_hash
    ):
        transition_run(
            db,
            run,
            GradingRunStatus.failed,
            error="评分上下文已变化，请重新发起批改",
        )
        raise ValueError("评分上下文已变化，请重新发起批改")
    if run.checkpoint.get("type") == "code_test_suite_approval":
        if action not in {"approve_test_suite", "regenerate_test_suite"}:
            return False
        if verdict is not None:
            return False
        from app.models.code_test_suite import CodeTestSuite
        from app.services.code_testing import source_fingerprint

        preview = run.checkpoint.get("preview") or {}
        suite = db.get(CodeTestSuite, preview.get("suite_id"))
        if (
            suite is None
            or suite.question_id != sub.question_id
            or suite.status != "draft"
            or suite.version != preview.get("version")
            or suite.source_fingerprint != source_fingerprint(sub.question, resolved.snapshot_id)
        ):
            raise ValueError("测试集已失效或题目已变化，请重新生成")
        run.checkpoint_answer = {
            "type": "code_test_suite_approval",
            "action": action,
            "note": note,
            "checkpoint_id": run.checkpoint_request_id,
            "suite_id": preview.get("suite_id"),
            "version": preview.get("version"),
        }
        run.checkpoint_answered_at = utc_now_naive()
        run.checkpoint = None
        run.checkpoint_expires_at = None
        if run.worker_id is None:
            _recover_run(db, run)
        db.commit()
        return True
    if verdict not in {"consistent", "mismatch"}:
        return False
    run.teacher_verdict = verdict
    run.teacher_note = note
    run.teacher_answered_at = utc_now_naive()
    run.checkpoint = None
    run.checkpoint_expires_at = None
    if run.worker_id is None:
        _recover_run(db, run)
    db.commit()
    return True
