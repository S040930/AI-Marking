"""Submission ORM 模型与状态枚举。

单表存储完整批改流程数据:上传 PDF → OCR → Agent 评分与复核 → 结果。
"""

import enum
from datetime import datetime
from pathlib import Path

from sqlalchemy import (
    JSON,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.time import utc_now_naive
from app.db.base import Base


class SubmissionStatus(str, enum.Enum):
    """作业批改流程状态。

    继承 ``str`` 便于 Pydantic 序列化与 JSON 输出。

    流程:pending → ocr_processing → agent_grading → agent_reviewing
    → (agent_revising) → ready_for_review → reviewed
    任何阶段失败:status=failed。
    """

    pending = "pending"
    ocr_processing = "ocr_processing"
    ocr_done = "ocr_done"
    agent_grading = "agent_grading"
    agent_reviewing = "agent_reviewing"
    agent_revising = "agent_revising"
    ready_for_review = "ready_for_review"
    reviewed = "reviewed"
    failed = "failed"


class Submission(Base):
    """作业提交记录及 Agent 审计、人工审核数据。"""

    __tablename__ = "submissions"
    __table_args__ = (
        Index("ix_submissions_uploaded_at", "uploaded_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    file_path: Mapped[str] = mapped_column(String(512), nullable=False)
    question_id: Mapped[int] = mapped_column(
        ForeignKey("questions.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    question = relationship("Question", back_populates="submissions", lazy="selectin")
    status: Mapped[SubmissionStatus] = mapped_column(
        Enum(SubmissionStatus, name="submission_status"),
        default=SubmissionStatus.pending,
        server_default="pending",
        nullable=False,
    )
    ocr_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    feedback: Mapped[str | None] = mapped_column(Text, nullable=True)
    details: Mapped[list | None] = mapped_column(JSON, nullable=True)
    ai_result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    agent_trace: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # Agent 完成时写入的完整建议分快照(含 score/max_score/feedback/details/confidence)
    # 与 ai_result 区别:ai_result 是 Agent 内部 draft,ai_suggestion 是面向教师展示的完整建议
    ai_suggestion: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    review_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    review_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=utc_now_naive,
        server_default=func.now(),
        nullable=False,
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    @property
    def question_original_filename(self) -> str | None:
        return self.question.original_filename if self.question else None

    @question_original_filename.setter
    def question_original_filename(self, value: str | None) -> None:
        """兼容旧测试/脚本构造方式；生产写入统一使用 question_id。"""
        if value is None:
            return
        if self.question is None:
            from app.models.question import Question

            self.question = Question(
                name=Path(value).stem,
                original_filename=value,
                file_path="",
            )
        else:
            self.question.original_filename = value

    @property
    def question_ocr_text(self) -> str | None:
        return self.question.ocr_text if self.question else None

    @question_ocr_text.setter
    def question_ocr_text(self, value: str | None) -> None:
        if self.question is not None:
            self.question.ocr_text = value

    @property
    def question_file_path(self) -> str | None:
        return self.question.file_path if self.question else None

    @question_file_path.setter
    def question_file_path(self, value: str | None) -> None:
        if value is None:
            return
        if self.question is None:
            from app.models.question import Question

            self.question = Question(
                name="历史题目",
                original_filename=Path(value).name,
                file_path=value,
            )
        else:
            self.question.file_path = value

    def __repr__(self) -> str:
        return (
            f"<Submission id={self.id} status={self.status!r} "
            f"filename={self.original_filename!r}>"
        )
