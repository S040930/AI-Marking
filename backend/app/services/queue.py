"""PostgreSQL 持久化任务队列操作。"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import and_, delete, or_, select, update
from sqlalchemy.orm import Session

from app.core.time import utc_now_naive
from app.models.background_job import (
    BackgroundJob,
    BackgroundJobStatus,
    BackgroundJobType,
)

# 退避上界。base=5s、exponent=attempts-1,默认 MAX_ATTEMPTS=3 时仅 20s,
# 若运维调大重试次数,这里防止 available_at 滑到天/周量级令任务长期不可见。
_BACKOFF_BASE_SECONDS = 5
_MAX_BACKOFF_SECONDS = 3600


def _backoff_seconds(attempts: int) -> float:
    """指数退避(秒),带 1 小时上界。"""
    return min(
        _BACKOFF_BASE_SECONDS * (2 ** (max(1, attempts) - 1)),
        _MAX_BACKOFF_SECONDS,
    )


@dataclass(frozen=True)
class ClaimedJob:
    id: int
    job_type: BackgroundJobType
    question_id: int | None
    submission_id: int | None
    attempts: int
    claim_token: str


def new_question_ocr_job(question_id: int) -> BackgroundJob:
    return BackgroundJob(
        job_type=BackgroundJobType.question_ocr,
        question_id=question_id,
        status=BackgroundJobStatus.queued,
    )


def new_question_replace_job(question_id: int) -> BackgroundJob:
    return BackgroundJob(
        job_type=BackgroundJobType.question_replace,
        question_id=question_id,
        status=BackgroundJobStatus.queued,
    )


def new_submission_marking_job(submission_id: int) -> BackgroundJob:
    return BackgroundJob(
        job_type=BackgroundJobType.submission_marking,
        submission_id=submission_id,
        status=BackgroundJobStatus.queued,
    )


def reset_question_ocr_job(db: Session, question_id: int) -> None:
    """失败题目重新入队；已有死信任务复用同一行。"""
    job = (
        db.execute(
            select(BackgroundJob)
            .where(BackgroundJob.question_id == question_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if job is None:
        db.add(new_question_ocr_job(question_id))
        return
    job.job_type = BackgroundJobType.question_ocr
    job.status = BackgroundJobStatus.queued
    job.attempts = 0
    job.available_at = utc_now_naive()
    job.lease_expires_at = None
    job.worker_id = None
    job.claim_token = None
    job.last_error = None


def reset_question_replace_job(db: Session, question_id: int) -> None:
    """新版题目 OCR 入队；复用该题目的死信任务。"""
    job = (
        db.execute(
            select(BackgroundJob)
            .where(BackgroundJob.question_id == question_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if job is None:
        db.add(new_question_replace_job(question_id))
        return
    job.job_type = BackgroundJobType.question_replace
    job.status = BackgroundJobStatus.queued
    job.attempts = 0
    job.available_at = utc_now_naive()
    job.lease_expires_at = None
    job.worker_id = None
    job.claim_token = None
    job.last_error = None


def reset_submission_marking_job(db: Session, submission_id: int) -> None:
    """失败作业在原记录上重新入队。"""
    job = (
        db.execute(
            select(BackgroundJob)
            .where(BackgroundJob.submission_id == submission_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if job is None:
        db.add(new_submission_marking_job(submission_id))
        return
    job.job_type = BackgroundJobType.submission_marking
    job.status = BackgroundJobStatus.queued
    job.attempts = 0
    job.available_at = utc_now_naive()
    job.lease_expires_at = None
    job.worker_id = None
    job.claim_token = None
    job.last_error = None


def claim_next_job(
    db: Session, *, worker_id: str, lease_seconds: int
) -> ClaimedJob | None:
    """原子领取一个到期任务。

    PostgreSQL 使用 ``FOR UPDATE SKIP LOCKED``，即使误启动多个任务 worker，
    同一任务也只会被一个事务领取。
    """
    now = utc_now_naive()
    claimable = or_(
        and_(
            BackgroundJob.status == BackgroundJobStatus.queued,
            BackgroundJob.available_at <= now,
        ),
        and_(
            BackgroundJob.status == BackgroundJobStatus.running,
            BackgroundJob.lease_expires_at.is_not(None),
            BackgroundJob.lease_expires_at <= now,
        ),
    )
    job = (
        db.execute(
            select(BackgroundJob)
            .where(claimable)
            .order_by(BackgroundJob.available_at, BackgroundJob.created_at)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
    ).scalar_one_or_none()
    if job is None:
        db.rollback()
        return None

    token = str(uuid.uuid4())
    job.status = BackgroundJobStatus.running
    job.attempts += 1
    job.worker_id = worker_id
    job.claim_token = token
    job.lease_expires_at = now + timedelta(seconds=max(1, lease_seconds))
    job.updated_at = now
    db.commit()
    return ClaimedJob(
        id=job.id,
        job_type=job.job_type,
        question_id=job.question_id,
        submission_id=job.submission_id,
        attempts=job.attempts,
        claim_token=token,
    )


def renew_lease(
    db: Session, job_id: int, claim_token: str, lease_seconds: int
) -> bool:
    now = utc_now_naive()
    result = db.execute(
        update(BackgroundJob)
        .where(
            BackgroundJob.id == job_id,
            BackgroundJob.status == BackgroundJobStatus.running,
            BackgroundJob.claim_token == claim_token,
        )
        .values(
            lease_expires_at=now + timedelta(seconds=max(1, lease_seconds)),
            updated_at=now,
        )
    )
    db.commit()
    return result.rowcount == 1


def complete_job(db: Session, job_id: int, claim_token: str) -> bool:
    result = db.execute(
        delete(BackgroundJob).where(
            BackgroundJob.id == job_id,
            BackgroundJob.status == BackgroundJobStatus.running,
            BackgroundJob.claim_token == claim_token,
        )
    )
    db.commit()
    return result.rowcount == 1


def retry_or_dead_letter(
    db: Session,
    job: ClaimedJob,
    *,
    error: str,
    max_attempts: int,
) -> bool:
    """异常任务退避后重排；返回 True 表示已进入死信。"""
    row = db.get(BackgroundJob, job.id, with_for_update=True)
    if row is None or row.claim_token != job.claim_token:
        db.rollback()
        return False

    now = utc_now_naive()
    row.last_error = error[:4000]
    row.lease_expires_at = None
    row.worker_id = None
    row.claim_token = None
    row.updated_at = now
    is_dead = row.attempts >= max(1, max_attempts)
    if is_dead:
        row.status = BackgroundJobStatus.dead
    else:
        row.status = BackgroundJobStatus.queued
        row.available_at = now + timedelta(seconds=_backoff_seconds(row.attempts))
    db.commit()
    return is_dead


def requeue_worker_jobs(db: Session, worker_id: str) -> int:
    """优雅关闭时立即释放本 worker 的租约。"""
    now = utc_now_naive()
    result = db.execute(
        update(BackgroundJob)
        .where(
            BackgroundJob.status == BackgroundJobStatus.running,
            BackgroundJob.worker_id == worker_id,
        )
        .values(
            status=BackgroundJobStatus.queued,
            available_at=now,
            lease_expires_at=None,
            worker_id=None,
            claim_token=None,
            updated_at=now,
        )
    )
    db.commit()
    return result.rowcount
