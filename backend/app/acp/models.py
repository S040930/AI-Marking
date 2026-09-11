"""ACP 批改 run 与事件 ORM 模型。

一个 run = 一次「agent 对单份 submission 的完整批改任务」:
- 通过部分唯一索引保证每份 submission 至多一个非终态 run;
- 事件表按 (run_id, seq) 自增,SSE 以 seq 续读;回放后再由
  PostgreSQL NOTIFY 推送增量。
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

from app.acp.domain import AcpChatStatus, AcpRunStatus
from app.core.time import utc_now_naive
from app.db.base import Base


class AcpRun(Base):
    """一次 ACP agent 批改运行。"""

    __tablename__ = "acp_runs"
    __table_args__ = (
        # 每份 submission 至多一个非终态 run
        Index(
            "uq_acp_runs_active_per_submission",
            "submission_id",
            unique=True,
            postgresql_where=text("status NOT IN ('completed','failed','cancelled')"),
            sqlite_where=text("status NOT IN ('completed','failed','cancelled')"),
        ),
        Index("ix_acp_runs_status_lease", "status", "lease_expires_at"),
        Index("ix_acp_runs_submission", "submission_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    submission_id: Mapped[int] = mapped_column(
        ForeignKey("submissions.id", ondelete="CASCADE"), nullable=False
    )
    agent_id: Mapped[str] = mapped_column(String(64), nullable=False)
    agent_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False)
    permission_mode: Mapped[str] = mapped_column(
        String(16), nullable=False, default="ask", server_default="ask"
    )
    codex_config: Mapped[dict] = mapped_column(
        JSON, nullable=False, default=dict, server_default="{}"
    )
    applied_codex_config: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    pending_codex_config: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status: Mapped[AcpRunStatus] = mapped_column(
        Enum(AcpRunStatus, name="acp_run_status"),
        default=AcpRunStatus.queued,
        server_default="queued",
        nullable=False,
    )
    # worker 租约
    worker_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    claim_token: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # agent 会话恢复
    acp_session_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    session_resumable: Mapped[bool] = mapped_column(
        nullable=False, default=False, server_default="false"
    )
    # 教师检查点(等待答复时驻留)
    checkpoint: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    checkpoint_asked_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True
    )
    checkpoint_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True
    )
    # 教师答复:绑定 submission 的持久执行一致性确认,与 MCP 工具链共用语义
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


class AcpRunEvent(Base):
    """run 事件流;SSE 先按 seq 回放,再由 NOTIFY 推送增量。"""

    __tablename__ = "acp_run_events"
    __table_args__ = (Index("ix_acp_run_events_run_seq", "run_id", "seq", unique=True),)

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(
        ForeignKey("acp_runs.id", ondelete="CASCADE"), nullable=False
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utc_now_naive, server_default=func.now(), nullable=False
    )


class AcpChatSession(Base):
    """教师与 agent 的交互式多轮对话会话(绑定 submission 批改上下文)。

    - 进程由 API 进程内的 ``ChatSessionRegistry`` 持有;``host_pid``/
      ``agent_pid`` 用于重启后识别并清理孤儿行/进程;
    - 事件表 ``acp_chat_events`` 按 (session_id, seq) 自增,SSE 以
      seq 续读;回放后由 NOTIFY 推送增量。
    """

    __tablename__ = "acp_chat_sessions"
    __table_args__ = (
        Index("ix_acp_chat_sessions_submission", "submission_id"),
        Index("ix_acp_chat_sessions_status", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    submission_id: Mapped[int] = mapped_column(
        ForeignKey("submissions.id", ondelete="CASCADE"), nullable=False
    )
    agent_id: Mapped[str] = mapped_column(String(64), nullable=False)
    permission_mode: Mapped[str] = mapped_column(
        String(16), nullable=False, default="ask", server_default="ask"
    )
    codex_config: Mapped[dict] = mapped_column(
        JSON, nullable=False, default=dict, server_default="{}"
    )
    applied_codex_config: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    pending_codex_config: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status: Mapped[AcpChatStatus] = mapped_column(
        Enum(AcpChatStatus, name="acp_chat_status"),
        default=AcpChatStatus.idle,
        server_default="idle",
        nullable=False,
    )
    # agent 会话恢复
    acp_session_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    session_resumable: Mapped[bool] = mapped_column(
        nullable=False, default=False, server_default="false"
    )
    # 进程归属(孤儿清理)
    host_pid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    agent_pid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    workspace_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # 事件转录合并去重游标
    next_event_seq: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    transcript_bytes: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    transcript_truncated: Mapped[bool] = mapped_column(
        nullable=False, default=False, server_default="false"
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
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class AcpChatEvent(Base):
    """对话会话事件流;SSE 先按 seq 回放,再由 NOTIFY 推送增量。"""

    __tablename__ = "acp_chat_events"
    __table_args__ = (
        Index("ix_acp_chat_events_session_seq", "session_id", "seq", unique=True),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("acp_chat_sessions.id", ondelete="CASCADE"), nullable=False
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utc_now_naive, server_default=func.now(), nullable=False
    )


class AcpAgentInstallation(Base):
    """Agent 的机器级安装、连接诊断和版本绑定状态。"""

    __tablename__ = "acp_agent_installations"

    agent_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    distribution: Mapped[str] = mapped_column(String(16), nullable=False)
    launch_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False)
    is_default: Mapped[bool] = mapped_column(
        nullable=False, default=False, server_default="false"
    )
    connection_status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="unknown", server_default="unknown"
    )
    connection_detail: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    tested_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=utc_now_naive,
        onupdate=utc_now_naive,
        server_default=func.now(),
        nullable=False,
    )
