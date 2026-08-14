"""Durable, short-lived MCP workflow handles shared by all API workers."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.time import utc_now_naive
from app.db.base import Base


class McpWorkflowHandle(Base):
    __tablename__ = "mcp_workflow_handles"
    __table_args__ = (
        Index("ix_mcp_workflow_handles_expires_at", "expires_at"),
        Index("ix_mcp_workflow_handles_submission", "submission_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    submission_id: Mapped[int] = mapped_column(
        ForeignKey("submissions.id", ondelete="CASCADE"), nullable=False
    )
    context_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    grading_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    offset: Mapped[int | None] = mapped_column(Integer, nullable=True)
    context_complete: Mapped[bool] = mapped_column(
        nullable=False, default=False, server_default="false"
    )
    visual_confirmation: Mapped[str | None] = mapped_column(String(32), nullable=True)
    visual_confirmation_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    visual_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utc_now_naive, server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
