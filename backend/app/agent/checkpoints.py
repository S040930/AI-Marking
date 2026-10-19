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
    db: Session, run: GradingRun, *, verdict: str, note: str | None
) -> bool:
    """锁序 Question -> Submission -> Run，答复只绑定当前检查点。"""
    from app.application.grading_workflow import build_context_parts
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
    _, _, _, current_hash, _ = build_context_parts(db, sub)
    if (
        run.confirmation_revision != sub.grading_revision
        or run.confirmation_context_hash != current_hash
    ):
        transition_run(
            db,
            run,
            GradingRunStatus.failed,
            error="评分上下文已变化，请重新发起代码一致性检查",
        )
        raise ValueError("评分上下文已变化，请重新发起代码一致性检查")
    run.teacher_verdict = verdict
    run.teacher_note = note
    run.teacher_answered_at = utc_now_naive()
    run.checkpoint = None
    run.checkpoint_expires_at = None
    if run.worker_id is None:
        _recover_run(db, run)
    db.commit()
    return True
