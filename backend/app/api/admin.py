"""死信任务管理路由(P4 可观测性)。

提供对进入 ``dead`` 状态的后台任务的运维操作:
- ``GET /api/admin/dead-jobs`` 列出所有死信任务
- ``POST /api/admin/dead-jobs/{id}/retry`` 重置 attempts 并重新入队
- ``DELETE /api/admin/dead-jobs/{id}`` 永久删除死信记录

所有端点均受全局访问令牌中间件保护。前端管理页留待后续迭代。
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.application.dead_jobs import retry_dead_job as retry_dead_job_use_case
from app.core.time import utc_now_naive
from app.db.session import get_db
from app.models.background_job import (
    BackgroundJob,
    BackgroundJobStatus,
)
from app.models.question import (
    Question,
    QuestionReplacementStatus,
    QuestionStatus,
)
from app.models.submission import Submission, SubmissionStatus

router = APIRouter(prefix="/admin", tags=["admin"])


class DeadJobOut(BaseModel):
    """死信任务展示模型。"""

    id: int = Field(..., description="任务 ID")
    job_type: str = Field(..., description="任务类型")
    question_id: str | None = Field(None, description="关联题目 ID")
    submission_id: int | None = Field(None, description="关联提交 ID")
    attempts: int = Field(..., description="已重试次数")
    last_error: str | None = Field(None, description="最近一次错误信息")
    updated_at: str | None = Field(None, description="最近更新时间(ISO)")

    model_config = {"from_attributes": True}


class DeadJobRetryResponse(BaseModel):
    id: int
    status: str


class DeadJobDeleteResponse(BaseModel):
    deleted: int


class QueueRuntimeOut(BaseModel):
    status_counts: dict[str, int]
    expired_leases: int
    oldest_queued_at: datetime | None


class RuntimeOut(BaseModel):
    checked_at: datetime
    queue: QueueRuntimeOut
    question_status_counts: dict[str, int]
    replacement_status_counts: dict[str, int]
    submission_status_counts: dict[str, int]


def _enum_counts(db: Session, column, enum_type) -> dict[str, int]:
    rows = db.execute(select(column, func.count()).group_by(column)).all()
    counts = {item.value: 0 for item in enum_type}
    counts.update({status_.value: count for status_, count in rows if status_ is not None})
    return counts


@router.get("/runtime", response_model=RuntimeOut)
def runtime_summary(db: Session = Depends(get_db)) -> RuntimeOut:
    """Return bounded operational counts without filenames or student content."""
    now = utc_now_naive()
    queue_counts = _enum_counts(db, BackgroundJob.status, BackgroundJobStatus)
    expired = db.scalar(
        select(func.count(BackgroundJob.id)).where(
            BackgroundJob.status == BackgroundJobStatus.running,
            BackgroundJob.lease_expires_at.is_not(None),
            BackgroundJob.lease_expires_at <= now,
        )
    ) or 0
    oldest = db.scalar(
        select(func.min(BackgroundJob.available_at)).where(
            BackgroundJob.status == BackgroundJobStatus.queued
        )
    )
    return RuntimeOut(
        checked_at=now,
        queue=QueueRuntimeOut(
            status_counts=queue_counts,
            expired_leases=expired,
            oldest_queued_at=oldest,
        ),
        question_status_counts=_enum_counts(db, Question.status, QuestionStatus),
        replacement_status_counts=_enum_counts(
            db, Question.replacement_status, QuestionReplacementStatus
        ),
        submission_status_counts=_enum_counts(
            db, Submission.status, SubmissionStatus
        ),
    )


@router.get("/dead-jobs", response_model=list[DeadJobOut])
def list_dead_jobs(db: Session = Depends(get_db)) -> list[DeadJobOut]:
    """列出所有死信任务(按 ``updated_at`` 降序)。"""
    jobs = (
        db.execute(
            select(BackgroundJob)
            .where(BackgroundJob.status == BackgroundJobStatus.dead)
            .order_by(BackgroundJob.updated_at.desc())
        )
        .scalars()
        .all()
    )
    return [
        DeadJobOut(
            id=job.id,
            job_type=job.job_type.value,
            question_id=job.question_id,
            submission_id=job.submission_id,
            attempts=job.attempts,
            last_error=job.last_error,
            updated_at=job.updated_at.isoformat() if job.updated_at else None,
        )
        for job in jobs
    ]


@router.post(
    "/dead-jobs/{job_id}/retry",
    response_model=DeadJobRetryResponse,
    status_code=status.HTTP_200_OK,
)
def retry_dead_job(
    job_id: int, db: Session = Depends(get_db)
) -> DeadJobRetryResponse:
    """Atomically restore the target state and dead job using global lock order."""
    retry_dead_job_use_case(db, job_id)
    return DeadJobRetryResponse(id=job_id, status="queued")


@router.delete(
    "/dead-jobs/{job_id}",
    response_model=DeadJobDeleteResponse,
    status_code=status.HTTP_200_OK,
)
def delete_dead_job(
    job_id: int, db: Session = Depends(get_db)
) -> DeadJobDeleteResponse:
    """永久删除死信任务记录。"""
    job = db.get(BackgroundJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="死信任务不存在")
    db.delete(job)
    db.commit()
    return DeadJobDeleteResponse(deleted=job_id)
