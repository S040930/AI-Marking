"""作业域用例：批量删除与教师最终评分确认。

从 ``app/api/submissions.py`` 迁入的写事务编排；API 层只做参数适配与
序列化。所有写操作沿用既有锁序 ``Question -> Submission(s)``，数据库提交
成功后再清理磁盘文件。
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.application.lifecycle import transition_submission
from app.application.locking import lock_submission_after_question
from app.core.config import settings
from app.core.errors import ConflictError, NotFoundError
from app.core.time import utc_now_naive
from app.models.question import Question
from app.models.submission import Submission, SubmissionStatus
from app.services.document_storage import remove_document_if_unreferenced
from app.services.events import notify_submission_status

logger = logging.getLogger(__name__)


def batch_delete_submissions(db: Session, ids: list[int]) -> int:
    """批量删除提交记录，返回删除数量。

    - 仅终态记录允许删除；存在处理中记录时整批原子拒绝
    - 先提交数据库删除，成功后再清理磁盘 PDF 与代码产物目录
    - 不存在的 ID 安全跳过
    """
    from app.application.uploads import DELETABLE_SUBMISSION_STATUSES

    unique_ids = list(dict.fromkeys(ids))
    snapshots = db.execute(
        select(Submission.id, Submission.question_id).where(
            Submission.id.in_(unique_ids)
        )
    ).all()
    question_ids = sorted({row.question_id for row in snapshots})
    if question_ids:
        db.execute(
            select(Question.id)
            .where(Question.id.in_(question_ids))
            .order_by(Question.id)
            .with_for_update()
        ).all()
    stmt = (
        select(Submission)
        .options(
            selectinload(Submission.code_files),
            selectinload(Submission.code_input_files),
        )
        .where(Submission.id.in_(unique_ids))
        .order_by(Submission.id)
        .with_for_update()
    )
    subs = db.execute(stmt).scalars().all()

    blocked_ids = sorted(
        sub.id for sub in subs if sub.status not in DELETABLE_SUBMISSION_STATUSES
    )
    if blocked_ids:
        raise ConflictError(
            {
                "message": "正在处理的记录不可删除，请等待批改完成后重试",
                "blocked_ids": blocked_ids,
            }
        )

    file_paths = [sub.file_path for sub in subs if sub.file_path]
    code_paths = [
        code_file.file_path
        for sub in subs
        for code_file in sub.code_files
        if code_file.file_path
    ]
    input_paths = [
        input_file.file_path
        for sub in subs
        for input_file in sub.code_input_files
        if input_file.file_path
    ]
    artifact_roots = [
        Path(settings.UPLOAD_DIR).resolve() / "code-artifacts" / str(sub.id)
        for sub in subs
    ]

    for sub in subs:
        db.delete(sub)
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise

    # 数据库是删除结果的权威来源。提交成功后再清理文件,避免事务失败时
    # 出现“记录仍在但 PDF 已丢失”的不可恢复状态。
    for file_path in file_paths:
        try:
            remove_document_if_unreferenced(db, file_path, Path(settings.UPLOAD_DIR))
        except (OSError, ValueError) as exc:
            logger.warning("删除 submission PDF 失败 [%s]: %s", file_path, exc)
    for file_path in code_paths:
        try:
            Path(file_path).unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("删除 submission 代码文件失败 [%s]: %s", file_path, exc)
    for file_path in input_paths:
        try:
            Path(file_path).unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("删除 submission 数据文件失败 [%s]: %s", file_path, exc)
    for artifact_root in artifact_roots:
        shutil.rmtree(artifact_root, ignore_errors=True)

    return len(subs)


def finalize_submission(
    db: Session,
    submission_id: int,
    *,
    score: float,
    max_score: float,
    feedback: str | None,
    details: list[dict],
    reviewer_name: str,
) -> Submission:
    """教师确认最终评分（``reviewed`` 的唯一写入入口）。

    - 校验状态为 ready_for_review（已 reviewed 返回 409）
    - 写入 score/max_score/feedback/details/reviewed_by/reviewed_at
    - 状态置为 reviewed 并 NOTIFY
    """
    sub = lock_submission_after_question(db, submission_id)
    if sub is None:
        raise NotFoundError("提交记录不存在")
    if sub.status == SubmissionStatus.reviewed:
        raise ConflictError("该作业已审阅,不可重复提交")
    if sub.status != SubmissionStatus.ready_for_review:
        raise ConflictError("作业尚未准备好进行审阅")

    now = utc_now_naive()
    sub.score = score
    sub.max_score = max_score
    sub.feedback = feedback
    sub.details = details
    sub.reviewed_by = reviewer_name
    sub.reviewed_at = now
    sub.completed_at = now
    transition_submission(sub, SubmissionStatus.reviewed)
    notify_submission_status(db, submission_id, SubmissionStatus.reviewed.value)
    db.commit()
    db.refresh(sub)
    return sub
