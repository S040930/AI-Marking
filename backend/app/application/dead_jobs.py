"""死信任务用例：原子恢复目标状态并重新入队。

从 ``app/api/admin.py`` 迁入的写事务编排；API 层只做参数适配与序列化。
恢复使用全局锁序 ``Question -> Submission -> BackgroundJob``。
"""

from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy.orm import Session

from app.application.lifecycle import (
    transition_question,
    transition_question_replacement,
    transition_submission,
)
from app.core.errors import ConflictError, NotFoundError
from app.core.time import utc_now_naive
from app.models.background_job import (
    BackgroundJob,
    BackgroundJobStatus,
    BackgroundJobType,
)
from app.models.question import Question, QuestionReplacementStatus, QuestionStatus
from app.models.submission import Submission, SubmissionStatus
from app.services.events import notify_question_status, notify_submission_status

logger = logging.getLogger(__name__)


def retry_dead_job(db: Session, job_id: int) -> BackgroundJob:
    """原子恢复死信任务的目标状态并重新入队，返回已更新的任务行。

    - 任务不存在或不在 dead 状态时 404/409
    - 按任务类型校验源文件与目标状态，全部通过后才重置队列表字段
    - 全程持有 Question -> Submission -> BackgroundJob 锁
    """
    snapshot = db.get(BackgroundJob, job_id)
    if snapshot is None or snapshot.status != BackgroundJobStatus.dead:
        raise NotFoundError("死信任务不存在")

    submission_snapshot = (
        db.get(Submission, snapshot.submission_id)
        if snapshot.submission_id is not None
        else None
    )
    question_id = snapshot.question_id or (
        submission_snapshot.question_id if submission_snapshot is not None else None
    )
    question = (
        db.get(Question, question_id, with_for_update=True) if question_id else None
    )
    submission = (
        db.get(Submission, snapshot.submission_id, with_for_update=True)
        if snapshot.submission_id is not None
        else None
    )
    job = db.get(BackgroundJob, job_id, with_for_update=True)
    if job is None or job.status != BackgroundJobStatus.dead:
        raise ConflictError("死信任务状态已变化")

    if job.job_type == BackgroundJobType.question_ocr:
        if question is None:
            raise ConflictError("关联题目不存在")
        if not Path(question.file_path).is_file():
            raise ConflictError("题目源文件缺失，请删除题目后重新上传")
        if question.status != QuestionStatus.failed:
            raise ConflictError("题目当前状态不可恢复")
        transition_question(question, QuestionStatus.pending)
        question.error_message = None
        notify_question_status(db, question.id, question.status.value, None)
    elif job.job_type == BackgroundJobType.question_replace:
        if question is None:
            raise ConflictError("关联题目不存在")
        staged = question.replacement_file_path
        if not staged or not Path(staged).is_file():
            raise ConflictError(
                "题目替换暂存文件缺失，请从题目替换入口重新上传"
            )
        if question.replacement_status != QuestionReplacementStatus.failed:
            raise ConflictError("题目替换当前状态不可恢复")
        transition_question_replacement(question, QuestionReplacementStatus.pending)
        question.replacement_error_message = None
        notify_question_status(
            db,
            question.id,
            question.status.value,
            question.replacement_status.value,
        )
    elif job.job_type == BackgroundJobType.submission_ocr:
        if submission is None or question is None:
            raise ConflictError("关联作业或题目不存在")
        if not Path(submission.file_path).is_file():
            raise ConflictError("作业源文件缺失，请从作业重试入口重新上传")
        if submission.status != SubmissionStatus.failed:
            raise ConflictError("作业当前状态不可恢复")
        if (
            question.status != QuestionStatus.ready
            or question.replacement_status is not None
        ):
            raise ConflictError("关联题目当前不可用于批改")
        transition_submission(submission, SubmissionStatus.pending)
        submission.error_message = None
        notify_submission_status(db, submission.id, submission.status.value)
    else:
        raise ConflictError("不支持的任务类型")

    job.status = BackgroundJobStatus.queued
    job.attempts = 0
    job.available_at = utc_now_naive()
    job.lease_expires_at = None
    job.worker_id = None
    job.claim_token = None
    job.last_error = None
    job.updated_at = utc_now_naive()
    db.commit()
    return job
