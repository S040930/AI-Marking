"""Conversation ORM 模型。

存储教师与 AI 就单个 submission 的多轮对话历史。
每条消息一个角色(user=教师,assistant=AI),按 created_at 升序展示。
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.time import utc_now_naive
from app.db.base import Base


class Conversation(Base):
    """教师-AI 对话消息。"""

    __tablename__ = "conversations"
    __table_args__ = (
        Index("ix_conversations_submission_id", "submission_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    submission_id: Mapped[int] = mapped_column(
        ForeignKey("submissions.id", ondelete="CASCADE"),
        nullable=False,
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=utc_now_naive,
        server_default=func.now(),
        nullable=False,
    )

    def __repr__(self) -> str:
        return (
            f"<Conversation id={self.id} submission_id={self.submission_id} "
            f"role={self.role!r}>"
        )
