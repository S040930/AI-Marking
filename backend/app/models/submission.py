"""Submission ORM 模型与状态枚举。

单表存储完整批改流程数据:上传 PDF → OCR → LLM 批改 → 结果。
"""

import enum
from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, Enum, Float, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class SubmissionStatus(str, enum.Enum):
    """作业批改流程状态。

    继承 ``str`` 便于 Pydantic 序列化与 JSON 输出。
    """

    pending = "pending"
    ocr_processing = "ocr_processing"
    ocr_done = "ocr_done"
    llm_processing = "llm_processing"
    done = "done"
    failed = "failed"


class Submission(Base):
    """作业提交记录(单表存储完整批改流程数据)。"""

    __tablename__ = "submissions"

    id: Mapped[int] = mapped_column(primary_key=True)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    file_path: Mapped[str] = mapped_column(String(512), nullable=False)
    status: Mapped[SubmissionStatus] = mapped_column(
        Enum(SubmissionStatus, name="submission_status"),
        default=SubmissionStatus.pending,
        server_default="pending",
        nullable=False,
    )
    ocr_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    feedback: Mapped[str | None] = mapped_column(Text, nullable=True)
    details: Mapped[list | None] = mapped_column(JSON, nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
        nullable=False,
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    def __repr__(self) -> str:
        return (
            f"<Submission id={self.id} status={self.status!r} "
            f"filename={self.original_filename!r}>"
        )
