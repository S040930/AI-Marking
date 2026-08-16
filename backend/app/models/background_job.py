"""PostgreSQL 持久化后台任务模型。"""

import enum
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.time import utc_now_naive
from app.db.base import Base


class BackgroundJobType(str, enum.Enum):
    question_ocr = "question_ocr"
    question_replace = "question_replace"
    submission_ocr = "submission_ocr"


class BackgroundJobStatus(str, enum.Enum):
    queued = "queued"
    running = "running"
    dead = "dead"


class BackgroundJob(Base):
    """只保存尚未完成或进入死信状态的任务。成功任务会被删除。"""

    __tablename__ = "background_jobs"
    __table_args__ = (
        CheckConstraint(
            "(question_id IS NOT NULL AND submission_id IS NULL) OR "
            "(question_id IS NULL AND submission_id IS NOT NULL)",
            name="ck_background_jobs_single_target",
        ),
        Index(
            "ix_background_jobs_claim",
            "status",
            "available_at",
            "lease_expires_at",
            "created_at",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    job_type: Mapped[BackgroundJobType] = mapped_column(
        Enum(BackgroundJobType, name="background_job_type"), nullable=False
    )
    question_id: Mapped[int | None] = mapped_column(
        ForeignKey("questions.id", ondelete="CASCADE"),
        nullable=True,
        unique=True,
    )
    submission_id: Mapped[int | None] = mapped_column(
        ForeignKey("submissions.id", ondelete="CASCADE"),
        nullable=True,
        unique=True,
    )
    status: Mapped[BackgroundJobStatus] = mapped_column(
        Enum(BackgroundJobStatus, name="background_job_status"),
        default=BackgroundJobStatus.queued,
        server_default="queued",
        nullable=False,
    )
    attempts: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    available_at: Mapped[datetime] = mapped_column(
        DateTime, default=utc_now_naive, server_default=func.now(), nullable=False
    )
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    worker_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    claim_token: Mapped[str | None] = mapped_column(String(36), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utc_now_naive, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=utc_now_naive,
        onupdate=utc_now_naive,
        server_default=func.now(),
        nullable=False,
    )
