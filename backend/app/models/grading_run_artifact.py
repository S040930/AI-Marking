"""批改 run 的结构化工件（评分包全文、代码文件索引等）。

评分包正文不进入 LangGraph checkpoint：checkpoint 只保存
``PackageDigest``（约 2KB），节点通过只读工具按需读取本表。
"""

from datetime import datetime

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.time import utc_now_naive
from app.db.base import Base


class GradingRunArtifact(Base):
    __tablename__ = "grading_run_artifacts"
    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "kind",
            "sha256",
            name="uq_grading_run_artifacts_run_kind_sha",
        ),
        Index("ix_grading_run_artifacts_run", "run_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(
        ForeignKey("grading_runs.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    meta: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utc_now_naive, server_default=func.now(), nullable=False
    )
