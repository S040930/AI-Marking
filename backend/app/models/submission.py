"""Submission ORM 模型与状态枚举。

单表存储完整批改流程数据:上传 PDF → OCR → MCP 客户端评分 → 教师确认。
评分建议由 MCP 客户端写入,教师最终确认写入最终成绩。
"""

import enum
from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
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

    流程:pending → ocr_processing → ocr_done → awaiting_mcp
    → ready_for_review → reviewed
    任何阶段失败:status=failed。
    """

    pending = "pending"
    ocr_processing = "ocr_processing"
    ocr_done = "ocr_done"
    awaiting_mcp = "awaiting_mcp"
    ready_for_review = "ready_for_review"
    reviewed = "reviewed"
    failed = "failed"


class SubmissionGradingMode(str, enum.Enum):
    """评分来源。收敛为 MCP-only 后固定为 external_agent。"""

    external_agent = "external_agent"


class Submission(Base):
    """作业提交记录及 MCP 审计、人工审核数据。"""

    __tablename__ = "submissions"
    __table_args__ = (
        Index("ix_submissions_uploaded_at", "uploaded_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    file_path: Mapped[str] = mapped_column(String(512), nullable=False)
    file_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    question_id: Mapped[str] = mapped_column(
        ForeignKey("questions.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    question = relationship("Question", back_populates="submissions", lazy="selectin")
    status: Mapped[SubmissionStatus] = mapped_column(
        Enum(SubmissionStatus, name="submission_status"),
        default=SubmissionStatus.pending,
        server_default="pending",
        nullable=False,
    )
    grading_mode: Mapped[SubmissionGradingMode] = mapped_column(
        Enum(SubmissionGradingMode, name="submission_grading_mode"),
        default=SubmissionGradingMode.external_agent,
        server_default="external_agent",
        nullable=False,
    )
    # 是否要求 MCP 客户端双遍自检（固定必需）。保留配置项语义从"后端 critic
    # 开关"变为"MCP self_check 必需开关"。
    review_enabled: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    grading_revision: Mapped[int] = mapped_column(
        default=0,
        server_default="0",
        nullable=False,
    )
    graded_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    ocr_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    feedback: Mapped[str | None] = mapped_column(Text, nullable=True)
    details: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # MCP 客户端写入的完整建议分快照(含 score/max_score/feedback/details/confidence
    # 与 mcp_metadata),面向教师展示的建议
    assessment_suggestion: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # 独立复核任务对当前建议的复核结论(逐项 agree/disagree + 总结),
    # 绑定 reviewed_revision;建议更新后字段保留但视为过期
    assessment_review: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    reviewed_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=utc_now_naive,
        server_default=func.now(),
        nullable=False,
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    code_files = relationship(
        "SubmissionCodeFile",
        back_populates="submission",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="SubmissionCodeFile.question_number",
    )
    code_input_files = relationship(
        "SubmissionCodeInputFile",
        back_populates="submission",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="SubmissionCodeInputFile.original_filename",
    )

    @property
    def question_original_filename(self) -> str | None:
        return self.question.original_filename if self.question else None

    @property
    def question_ocr_text(self) -> str | None:
        return self.question.ocr_text if self.question else None

    @property
    def has_code(self) -> bool:
        return bool(self.code_files)

    def __repr__(self) -> str:
        return (
            f"<Submission id={self.id} status={self.status!r} "
            f"filename={self.original_filename!r}>"
        )
