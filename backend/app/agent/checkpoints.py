"""教师检查点:答复落库与续跑注入。

- ``answer_checkpoint``:把教师答复写入 run(绑定 submission 的
  revision 与 context hash;不匹配即失败并提示重新发起);
- ``build_resume_prompt``:构造续跑注入的用户消息(含句柄失效提示)。
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
    from app.application.locking import lock_submission_after_question
    from app.application.mcp_workflow import build_context_parts

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


def build_resume_prompt(run: GradingRun) -> str:
    """教师答复后的续跑用户消息;显式提示句柄失效时的重开路径。"""
    note = (run.teacher_note or "").strip()
    verdict = run.teacher_verdict or ""
    lines = [
        f"[教师检查点答复] 运行结果与报告一致性: {verdict}。",
    ]
    if note:
        lines.append(f"教师说明: {note}")
    lines.append(
        "请基于该答复继续评分:若一致,调用 confirm_visual_review(verdict=\"consistent\")"
        "后继续;若不一致,调用 confirm_visual_review(verdict=\"mismatch\", "
        "note=教师说明),并且只把教师明确说明的差异纳入评分。"
    )
    lines.append(
        "若评分句柄已失效(context/token 错误),请重新调用 open_grading_package "
        "完整读取评分包后再保存。"
    )
    return "\n".join(lines)


def build_task_prompt(submission_id: int, question_name: str) -> str:
    """新建 run 的入口消息:只给身份标识,流程由系统指令提供。"""
    return (
        f"请批改 AI-Marking 作业《{question_name}》（submission_id={submission_id}）。"
        "这是已存在且已完成 OCR 的作业,请按系统指令中的批改流程执行,"
        "先完整读取评分包。最终成绩由教师在网页确认,不要自行定稿。"
    )
