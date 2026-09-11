"""AcpChatSession 数据访问;纯持久化,无协议逻辑(镜像 runs.py)。"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.acp.domain import TERMINAL_CHAT_STATUSES, AcpChatStatus
from app.acp.models import AcpChatEvent, AcpChatSession
from app.core.time import utc_now_naive

MAX_TRANSCRIPT_BYTES = 5 * 1024 * 1024
MAX_EVENT_BYTES = 64 * 1024


class ChatSessionError(Exception):
    """对话会话操作失败(调用方转 4xx/5xx)。"""


def get_chat_session(db: Session, chat_id: int) -> AcpChatSession | None:
    return db.get(AcpChatSession, chat_id)


def list_chat_sessions(
    db: Session, submission_id: int, *, limit: int = 50
) -> list[AcpChatSession]:
    stmt = (
        select(AcpChatSession)
        .where(AcpChatSession.submission_id == submission_id)
        .order_by(AcpChatSession.updated_at.desc(), AcpChatSession.id.desc())
        .limit(limit)
    )
    return list(db.execute(stmt).scalars())


def create_chat_session(
    db: Session,
    *,
    submission_id: int,
    agent_id: str,
    workspace_path: str,
    host_pid: int,
    permission_mode: str = "ask",
    codex_config: dict[str, Any] | None = None,
) -> AcpChatSession:
    row = AcpChatSession(
        submission_id=submission_id,
        agent_id=agent_id,
        permission_mode=permission_mode,
        codex_config=dict(codex_config or {}),
        status=AcpChatStatus.idle,
        workspace_path=workspace_path,
        host_pid=host_pid,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def transition_chat(
    db: Session, row: AcpChatSession, target: AcpChatStatus, *, error: str | None = None
) -> bool:
    """状态迁移;非法迁移返回 False(不抛出,由调用方决定语义)。"""
    from app.acp.domain import ensure_chat_transition

    try:
        ensure_chat_transition(row.status, target)
    except ValueError:
        return False
    row.status = target
    if error is not None:
        row.last_error = error[:1024]
    if target in TERMINAL_CHAT_STATUSES:
        row.closed_at = utc_now_naive()
    db.commit()
    return True


def record_chat_session(
    db: Session, row: AcpChatSession, *, acp_session_id: str, resumable: bool
) -> None:
    row.acp_session_id = acp_session_id
    row.session_resumable = resumable
    db.commit()


def queue_chat_configuration(
    db: Session,
    row: AcpChatSession,
    config: dict[str, Any],
    *,
    pending: bool,
) -> None:
    row.codex_config = dict(config)
    row.pending_codex_config = dict(config) if pending else None
    db.commit()


def set_chat_permission_mode(db: Session, row: AcpChatSession, mode: str) -> None:
    """更新权限档位并落一条事件;agent 进程侧同步由 registry 负责。"""
    from app.services.events import notify_acp_chat_event

    row.permission_mode = mode  # type: ignore[assignment]
    seq = row.next_event_seq + 1
    row.next_event_seq = seq
    payload = {"permission_mode": mode}
    db.add(
        AcpChatEvent(
            session_id=row.id, seq=seq, kind="permission_mode_updated", payload=payload
        )
    )
    db.flush()
    notify_acp_chat_event(db, row.id, seq, "permission_mode_updated", payload)
    db.commit()


def mark_chat_configuration_applied(
    db: Session, row: AcpChatSession, config: dict[str, Any]
) -> None:
    row.codex_config = dict(config)
    row.applied_codex_config = dict(config)
    row.pending_codex_config = None
    db.commit()


def mark_chat_configuration_failed(db: Session, row: AcpChatSession) -> None:
    row.pending_codex_config = None
    row.codex_config = dict(row.applied_codex_config or {})
    db.commit()


def record_agent_pid(db: Session, chat_id: int, pid: int | None) -> None:
    row = db.get(AcpChatSession, chat_id)
    if row is not None:
        row.agent_pid = pid
        db.commit()


def delete_chat_session(db: Session, chat_id: int) -> bool:
    """永久删除会话行与其全部事件,返回是否存在。

    FK 虽定义 ON DELETE CASCADE,但测试用 SQLite 默认不启用外键约束,
    且 ORM 未声明 relationship cascade,因此显式先删事件再删行。
    """
    exists = db.get(AcpChatSession, chat_id) is not None
    if not exists:
        return False
    db.execute(
        delete(AcpChatEvent).where(AcpChatEvent.session_id == chat_id)
    )
    row = db.get(AcpChatSession, chat_id)
    if row is not None:
        db.delete(row)
    db.commit()
    return True


def append_chat_event(
    db: Session, chat_id: int, *, kind: str, payload: dict[str, Any]
) -> int:
    """原子追加有界事件;达到 5MB 后仅保留一次截断提示。"""
    from app.services.events import notify_acp_chat_event

    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )
    if len(encoded) > MAX_EVENT_BYTES:
        payload = {
            "text": encoded[: MAX_EVENT_BYTES - 128].decode("utf-8", errors="ignore")
            + "\n[事件已截断]"
        }
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    row = db.execute(
        select(AcpChatSession).where(AcpChatSession.id == chat_id).with_for_update()
    ).scalar_one()
    if row.transcript_bytes + len(encoded) > MAX_TRANSCRIPT_BYTES:
        if row.transcript_truncated:
            db.rollback()
            return row.next_event_seq
        kind = "notice"
        payload = {"text": "转录已达到 5MB 上限，后续事件不再持久化"}
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        row.transcript_truncated = True
    row.next_event_seq += 1
    row.transcript_bytes += len(encoded)
    seq = row.next_event_seq
    db.add(AcpChatEvent(session_id=chat_id, seq=seq, kind=kind, payload=payload))
    db.flush()
    notify_acp_chat_event(db, chat_id, seq, kind, payload)
    db.commit()
    return seq


def list_chat_events_after(
    db: Session, chat_id: int, after_seq: int, limit: int
) -> list[AcpChatEvent]:
    stmt = (
        select(AcpChatEvent)
        .where(AcpChatEvent.session_id == chat_id, AcpChatEvent.seq > after_seq)
        .order_by(AcpChatEvent.seq.asc())
        .limit(limit)
    )
    return list(db.execute(stmt).scalars())


def latest_chat_event_seq(db: Session, chat_id: int) -> int:
    return (
        db.execute(
            select(AcpChatEvent.seq)
            .where(AcpChatEvent.session_id == chat_id)
            .order_by(AcpChatEvent.seq.desc())
            .limit(1)
        ).scalar_one_or_none()
        or 0
    )
