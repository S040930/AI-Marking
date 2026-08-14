"""死信任务管理路由(P4 可观测性)。

提供对进入 ``dead`` 状态的后台任务的运维操作:
- ``GET /api/admin/dead-jobs`` 列出所有死信任务
- ``POST /api/admin/dead-jobs/{id}/retry`` 重置 attempts 并重新入队
- ``DELETE /api/admin/dead-jobs/{id}`` 永久删除死信记录

MVP 内部工具,不做鉴权。前端管理页留待后续迭代,当前用 curl/SQL 验证。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.time import utc_now_naive
from app.db.session import get_db
from app.models.background_job import BackgroundJob, BackgroundJobStatus

router = APIRouter(prefix="/admin", tags=["admin"])


class DeadJobOut(BaseModel):
    """死信任务展示模型。"""

    id: int = Field(..., description="任务 ID")
    job_type: str = Field(..., description="任务类型")
    question_id: int | None = Field(None, description="关联题目 ID")
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
    """重试死信任务:重置 attempts 与 status,立即重新入队。

    注意:仅重置队列状态,不会自动恢复业务对象(Submission/Question)的状态。
    若死信已导致业务对象标记为 ``failed``,需走对应的业务重试接口。
    """
    job = db.get(BackgroundJob, job_id, with_for_update=True)
    if job is None or job.status != BackgroundJobStatus.dead:
        raise HTTPException(status_code=404, detail="死信任务不存在")
    job.status = BackgroundJobStatus.queued
    job.attempts = 0
    job.available_at = utc_now_naive()
    job.lease_expires_at = None
    job.worker_id = None
    job.claim_token = None
    job.last_error = None
    job.updated_at = utc_now_naive()
    db.commit()
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
