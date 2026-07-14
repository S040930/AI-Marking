"""可复用作业题目 ORM 模型。"""

import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.time import utc_now_naive
from app.db.base import Base


class QuestionStatus(str, enum.Enum):
    pending = "pending"
    ocr_processing = "ocr_processing"
    ready = "ready"
    failed = "failed"


class QuestionReplacementStatus(str, enum.Enum):
    pending = "pending"
    processing = "processing"
    failed = "failed"


class Question(Base):
    __tablename__ = "questions"
    __table_args__ = (
        Index("ix_questions_name", "name"),
        Index("ix_questions_last_used_at", "last_used_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    file_path: Mapped[str] = mapped_column(String(512), nullable=False)
    ocr_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[QuestionStatus] = mapped_column(
        Enum(QuestionStatus, name="question_status"),
        default=QuestionStatus.pending,
        server_default="pending",
        nullable=False,
    )
    error_message: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    replacement_status: Mapped[QuestionReplacementStatus | None] = mapped_column(
        Enum(QuestionReplacementStatus, name="question_replacement_status"),
        nullable=True,
    )
    replacement_file_path: Mapped[str | None] = mapped_column(
        String(512), nullable=True
    )
    replacement_original_filename: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )
    replacement_error_message: Mapped[str | None] = mapped_column(
        String(1024), nullable=True
    )
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
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    submissions = relationship("Submission", back_populates="question")
