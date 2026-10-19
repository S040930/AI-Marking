"""GradingRun 批改运行与事件 ORM 模型。

一个 run = 一次「agent 对单份 submission 的完整批改任务」:
- 通过部分唯一索引保证每份 submission 至多一个非终态 run;
- 事件表按 (run_id, seq) 自增,SSE 以 seq 续读;回放后再由
  PostgreSQL NOTIFY 推送增量;
- 续跑状态由 LangGraph checkpointer 按 thread 承载,``agent_state`` 为遗留列。
"""

from datetime import datetime

from sqlalchemy import (
    JSON,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.time import utc_now_naive
from app.db.base import Base
from app.domain.grading_run import GradingRunStatus


class GradingRun(Base):
    """一次内嵌 agent 批改运行。"""

    __tablename__ = "grading_runs"
    __table_args__ = (
        # 每份 submission 至多一个非终态 run
        Index(
            "uq_grading_runs_active_per_submission",
            "submission_id",
            unique=True,
            postgresql_where=text("status NOT IN ('completed','failed','cancelled')"),
            sqlite_where=text("status NOT IN ('completed','failed','cancelled')"),
        ),
        Index("ix_grading_runs_status_lease", "status", "lease_expires_at"),
        Index("ix_grading_runs_submission", "submission_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    submission_id: Mapped[int] = mapped_column(
        ForeignKey("submissions.id", ondelete="CASCADE"), nullable=False
    )
    # 模型通道与配置(desired;applied/pending 记录实际生效与排队)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    # 架构版本：1 = LangGraph 图执行；迁移会把旧 run 终止，plan 拒绝非 1。
    graph_version: Mapped[int] = mapped_column(
        Integer, default=1, server_default="1", nullable=False
    )
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    reasoning_effort: Mapped[str | None] = mapped_column(String(64), nullable=True)
    applied_config: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    pending_config: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # 遗留列:图架构下续跑状态由 LangGraph checkpointer 承载,本列已不再写入
    # (仅保留供旧数据读取,下一版迁移删除)
    agent_state: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # 跨回合累计 usage
    usage: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status: Mapped[GradingRunStatus] = mapped_column(
        Enum(GradingRunStatus, name="grading_run_status"),
        default=GradingRunStatus.queued,
        server_default="queued",
        nullable=False,
    )
    # worker 租约
    worker_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    claim_token: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # 教师检查点(等待答复时驻留)
    checkpoint: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    checkpoint_asked_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True
    )
    checkpoint_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True
    )
    # 检查点请求标识：教师答复会清空 checkpoint，但续跑仍需校验答复属于
    # 哪一次提问；与 checkpoint 的生命周期分离。
    checkpoint_request_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    # 教师答复:绑定 submission 的持久执行一致性确认
    teacher_verdict: Mapped[str | None] = mapped_column(String(16), nullable=True)
    teacher_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    teacher_answered_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True
    )
    # 失败信息
    error_message: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    attempts: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    # 事件转录合并去重游标
    transcript_bytes: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    next_event_seq: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    transcript_truncated: Mapped[bool] = mapped_column(
        nullable=False, default=False, server_default="false"
    )
    confirmation_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    confirmation_context_hash: Mapped[str | None] = mapped_column(
        String(71), nullable=True
    )
    cancel_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True
    )
    workspace_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
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
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class GradingRunEvent(Base):
    """run 事件流;SSE 先按 seq 回放,再由 NOTIFY 推送增量。"""

    __tablename__ = "grading_run_events"
    __table_args__ = (
        Index("ix_grading_run_events_run_seq", "run_id", "seq", unique=True),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(
        ForeignKey("grading_runs.id", ondelete="CASCADE"), nullable=False
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utc_now_naive, server_default=func.now(), nullable=False
    )
