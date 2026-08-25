"""可复用作业题目 ORM 模型。"""

import enum
from datetime import datetime

from sqlalchemy import (
    JSON,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    Text,
    event,
    func,
)
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

    # id 为创建时 original_filename 去扩展名生成的稳定 slug(见
    # app/services/question_identity.py),替换/重试 OCR 不改 id。
    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    config_profile_id: Mapped[int] = mapped_column(
        ForeignKey("config_profiles.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    file_path: Mapped[str] = mapped_column(String(512), nullable=False)
    file_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    ocr_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 题目 OCR 阶段由服务端提取并校验的规范 rubric 文本；可信性还需同时满足
    # extracted_rubric_items、OCR hash、version 和时间字段。题目被替换时清空。
    extracted_rubric: Mapped[str | None] = mapped_column(Text, nullable=True)
    extracted_rubric_items: Mapped[list | None] = mapped_column(JSON, nullable=True)
    extracted_rubric_ocr_hash: Mapped[str | None] = mapped_column(
        String(71), nullable=True
    )
    extracted_rubric_version: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    extracted_rubric_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
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
    replacement_file_sha256: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
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


@event.listens_for(Question, "before_insert")
def _ensure_question_id(mapper, connection, target) -> None:  # noqa: ANN001
    """题目未显式指定 id 时,由 original_filename 自动生成 slug 主键。

    正常流程 ``create_question`` 已显式传 id 并在 API 层拒绝重名;此钩子
    仅兜底任何绕过显式指定(如测试/脚本直接建行)的题目,保证主键非空。
    同名冲突由数据库主键唯一性自然暴露。
    """
    if target.id:
        return
    from app.services.question_identity import build_question_id

    target.id = build_question_id(target.original_filename)
