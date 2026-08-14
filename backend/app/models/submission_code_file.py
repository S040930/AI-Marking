"""学生代码文件及其受控运行审计数据。"""

import enum
from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.time import utc_now_naive
from app.db.base import Base


class CodeExecutionStatus(str, enum.Enum):
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"


class SubmissionCodeFile(Base):
    """一个与小题对应的代码文件；每题恰有一个 entrypoint。

    ``execution_result`` 和 ``artifacts`` 只保存结构化审计摘要；原始文件和
    产物路径仍由应用层校验后提供，绝不把任意本机路径暴露给 MCP。
    """

    __tablename__ = "submission_code_files"
    __table_args__ = (
        UniqueConstraint(
            "submission_id", "original_filename", name="uq_submission_code_filename"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    submission_id: Mapped[int] = mapped_column(
        ForeignKey("submissions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    question_number: Mapped[int] = mapped_column(Integer, nullable=False)
    entrypoint: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="1")
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    file_path: Mapped[str] = mapped_column(String(512), nullable=False)
    file_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    source_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    source_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    execution_status: Mapped[CodeExecutionStatus] = mapped_column(
        Enum(CodeExecutionStatus, name="code_execution_status"),
        default=CodeExecutionStatus.pending,
        server_default="pending",
        nullable=False,
    )
    execution_result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    artifacts: Mapped[list | None] = mapped_column(JSON, nullable=True)
    visual_reviews: Mapped[list | None] = mapped_column(JSON, nullable=True)
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

    submission = relationship("Submission", back_populates="code_files")
