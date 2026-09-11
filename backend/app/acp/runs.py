"""AcpRun 数据访问与租约操作;纯持久化,无协议逻辑。"""

from __future__ import annotations

import json
import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.acp.domain import TERMINAL_RUN_STATUSES, AcpRunStatus
from app.acp.models import AcpRun, AcpRunEvent
from app.core.time import utc_now_naive

MAX_ATTEMPTS = 3
MAX_TRANSCRIPT_BYTES = 5 * 1024 * 1024
MAX_EVENT_BYTES = 64 * 1024


def create_run(
    db: Session,
    *,
    submission_id: int,
    agent_id: str,
    agent_snapshot: dict[str, Any],
    workspace_path: str,
    permission_mode: str = "ask",
    codex_config: dict[str, Any] | None = None,
) -> AcpRun:
    """创建 run;已有活跃 run 时返回 409 语义由调用方处理。"""
    run = AcpRun(
        submission_id=submission_id,
        agent_id=agent_id,
        agent_snapshot=agent_snapshot,
        permission_mode=permission_mode,
        codex_config=dict(codex_config or {}),
        status=AcpRunStatus.queued,
        workspace_path=workspace_path,
    )
    db.add(run)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise ValueError("该作业已有进行中的 ACP 批改运行") from exc
    return run


def get_run(db: Session, run_id: int) -> AcpRun | None:
    return db.get(AcpRun, run_id)


def get_active_run_for_submission(db: Session, submission_id: int) -> AcpRun | None:
    """返回该 submission 的非终态 run(至多一个,由部分唯一索引保证)。"""
    stmt = (
        select(AcpRun)
        .where(
            AcpRun.submission_id == submission_id,
            AcpRun.status.notin_([s.value for s in TERMINAL_RUN_STATUSES]),
        )
        .order_by(AcpRun.id.desc())
        .limit(1)
    )
    return db.execute(stmt).scalar_one_or_none()


def count_active_runs_for_agent(db: Session, agent_id: str) -> int:
    """统计使用该 agent 的非终态 run 数;卸载前用于保护进行中的会话。"""
    return int(
        db.execute(
            select(text("COUNT(*)")).select_from(
                select(AcpRun.id)
                .where(
                    AcpRun.agent_id == agent_id,
                    AcpRun.status.notin_([s.value for s in TERMINAL_RUN_STATUSES]),
                )
                .subquery()
            )
        ).scalar_one()
    )


def claim_next_run(
    db: Session,
    *,
    worker_id: str,
    lease_seconds: int,
    max_concurrent: int,
) -> AcpRun | None:
    """原子领取一个排队 run;租约过期且未达重试上限的也重新可领。

    ``max_concurrent`` 限制本 worker 同时持有的 run 数(默认并发 1)。
    使用 SQLite/PG 通用的事务 + 部分唯一约束串行化:以 ``FOR UPDATE``
    行锁(PG)或事务串行(SQLite)领取。
    """
    active_count = db.execute(
        select(text("COUNT(*)")).select_from(
            select(AcpRun.id)
            .where(
                AcpRun.worker_id == worker_id,
                AcpRun.status.in_([AcpRunStatus.starting, AcpRunStatus.running]),
            )
            .subquery()
        )
    ).scalar_one()
    if active_count >= max_concurrent:
        return None

    now = utc_now_naive()
    claimable = (
        select(AcpRun)
        .where(
            AcpRun.status == AcpRunStatus.queued,
            AcpRun.attempts < MAX_ATTEMPTS,
        )
        .order_by(AcpRun.id.asc())
    )
    run = db.execute(
        claimable.with_for_update(skip_locked=True).limit(1)
    ).scalar_one_or_none()
    if run is None:
        return None
    token = uuid.uuid4().hex
    run.status = AcpRunStatus.starting
    run.worker_id = worker_id
    run.claim_token = token
    run.lease_expires_at = now + timedelta(seconds=max(1, lease_seconds))
    run.heartbeat_at = now
    run.attempts += 1
    db.commit()
    return run


def renew_lease(db: Session, run_id: int, claim_token: str, lease_seconds: int) -> bool:
    now = utc_now_naive()
    result = db.execute(
        update(AcpRun)
        .where(
            AcpRun.id == run_id,
            AcpRun.claim_token == claim_token,
            AcpRun.status.in_([AcpRunStatus.starting, AcpRunStatus.running]),
        )
        .values(
            lease_expires_at=now + timedelta(seconds=max(1, lease_seconds)),
            heartbeat_at=now,
        )
    )
    db.commit()
    return result.rowcount == 1


def transition_run(
    db: Session,
    run: AcpRun,
    target: AcpRunStatus,
    *,
    claim_token: str | None = None,
    error: str | None = None,
) -> bool:
    """状态迁移;claim_token 不匹配或已终态时返回 False。"""
    from app.acp.domain import ensure_run_transition

    if claim_token is not None and run.claim_token != claim_token:
        return False
    try:
        ensure_run_transition(run.status, target)
    except ValueError:
        return False
    run.status = target
    if error is not None:
        run.error_message = error[:1024]
    if target in TERMINAL_RUN_STATUSES:
        run.finished_at = utc_now_naive()
        run.lease_expires_at = None
        run.claim_token = None
        run.worker_id = None
    db.commit()
    return True


def append_event(
    db: Session, run_id: int, *, kind: str, payload: dict[str, Any]
) -> int:
    """原子追加有界事件；达到 5MB 后仅保留一次截断提示。"""
    from app.services.events import notify_acp_run_event

    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )
    if len(encoded) > MAX_EVENT_BYTES:
        payload = {
            "text": encoded[: MAX_EVENT_BYTES - 128].decode("utf-8", errors="ignore")
            + "\n[事件已截断]"
        }
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    run = db.execute(
        select(AcpRun).where(AcpRun.id == run_id).with_for_update()
    ).scalar_one()
    if run.transcript_bytes + len(encoded) > MAX_TRANSCRIPT_BYTES:
        if run.transcript_truncated:
            db.rollback()
            return run.next_event_seq
        kind = "notice"
        payload = {"text": "转录已达到 5MB 上限，后续事件不再持久化"}
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        run.transcript_truncated = True
    run.next_event_seq += 1
    run.transcript_bytes += len(encoded)
    seq = run.next_event_seq
    db.add(AcpRunEvent(run_id=run_id, seq=seq, kind=kind, payload=payload))
    db.flush()
    notify_acp_run_event(db, run_id, seq, kind, payload)
    db.commit()
    return seq


def list_events_after(
    db: Session, run_id: int, after_seq: int, limit: int
) -> list[AcpRunEvent]:
    stmt = (
        select(AcpRunEvent)
        .where(AcpRunEvent.run_id == run_id, AcpRunEvent.seq > after_seq)
        .order_by(AcpRunEvent.seq.asc())
        .limit(limit)
    )
    return list(db.execute(stmt).scalars())


def latest_event_seq(db: Session, run_id: int) -> int:
    return (
        db.execute(
            select(AcpRunEvent.seq)
            .where(AcpRunEvent.run_id == run_id)
            .order_by(AcpRunEvent.seq.desc())
            .limit(1)
        ).scalar_one_or_none()
        or 0
    )


def requeue_expired_leases(db: Session) -> int:
    """崩溃重排:把租约过期的 starting/running run 重新排队。

    submission 已到 ready_for_review/reviewed 的 run 直接标记完成;
    其余在重试上限内重新排队(attempts 由调用方判定)。
    返回受影响行数。
    """
    from app.models.submission import Submission, SubmissionStatus

    now = utc_now_naive()
    expired = list(
        db.execute(
            select(AcpRun)
            .where(
                AcpRun.status.in_([AcpRunStatus.starting, AcpRunStatus.running]),
                AcpRun.lease_expires_at.is_not(None),
                AcpRun.lease_expires_at <= now,
            )
            .with_for_update(skip_locked=True)
        ).scalars()
    )
    count = 0
    for run in expired:
        sub = db.get(Submission, run.submission_id)
        if sub is not None and sub.status in (
            SubmissionStatus.ready_for_review,
            SubmissionStatus.reviewed,
        ):
            run.status = AcpRunStatus.completed
            run.finished_at = now
            run.error_message = None
        else:
            if run.attempts >= MAX_ATTEMPTS:
                run.status = AcpRunStatus.failed
                run.finished_at = now
                run.error_message = "ACP worker 多次中断，已达到重试上限"
            else:
                run.status = AcpRunStatus.queued
        run.lease_expires_at = None
        run.worker_id = None
        run.claim_token = None
        count += 1
    if count:
        db.commit()
    return count


def requeue_worker_runs(db: Session, worker_id: str) -> int:
    """优雅关闭:释放本 worker 的全部租约。"""
    now = utc_now_naive()
    result = db.execute(
        update(AcpRun)
        .where(
            AcpRun.worker_id == worker_id,
            AcpRun.status.in_([AcpRunStatus.starting, AcpRunStatus.running]),
        )
        .values(
            status=AcpRunStatus.queued,
            lease_expires_at=None,
            worker_id=None,
            claim_token=None,
            updated_at=now,
        )
    )
    db.commit()
    return result.rowcount


def save_checkpoint(
    db: Session,
    run: AcpRun,
    *,
    checkpoint: dict[str, Any],
    dwell_seconds: int,
    confirmation_revision: int,
    confirmation_context_hash: str,
) -> None:
    """进入教师检查点驻留;30 分钟无答复由 API 侧超时关闭会话。"""
    from app.acp.domain import ensure_run_transition

    now = utc_now_naive()
    ensure_run_transition(run.status, AcpRunStatus.waiting_for_teacher)
    run.status = AcpRunStatus.waiting_for_teacher
    run.checkpoint = checkpoint
    run.checkpoint_asked_at = now
    run.checkpoint_expires_at = now + timedelta(seconds=dwell_seconds)
    run.confirmation_revision = confirmation_revision
    run.confirmation_context_hash = confirmation_context_hash
    db.commit()


def request_cancel(db: Session, run: AcpRun) -> bool:
    """幂等请求取消；活跃会话由持有 claim 的 worker 完成回收。"""
    if run.status in TERMINAL_RUN_STATUSES:
        return True
    if run.status == AcpRunStatus.cancelling:
        return True
    if not transition_run(db, run, AcpRunStatus.cancelling):
        return False
    run = db.get(AcpRun, run.id)
    if run is not None:
        run.cancel_requested_at = utc_now_naive()
        db.commit()
    return True


def claim_control_state(db: Session, run_id: int, claim_token: str) -> str:
    row = db.execute(
        select(AcpRun.claim_token, AcpRun.status).where(AcpRun.id == run_id)
    ).one_or_none()
    if row is None or row.claim_token != claim_token:
        return "lost"
    return "cancelling" if row.status == AcpRunStatus.cancelling else "active"


def answer_checkpoint(
    db: Session, run: AcpRun, *, verdict: str, note: str | None
) -> bool:
    """持久教师答复;仅当前驻留检查点有效。"""
    if run.status != AcpRunStatus.waiting_for_teacher or run.checkpoint is None:
        return False
    from app.application.mcp_workflow import build_context_parts
    from app.models.submission import Submission

    sub = db.get(Submission, run.submission_id)
    if sub is None:
        return False
    _, _, _, current_hash, _ = build_context_parts(db, sub)
    if (
        run.confirmation_revision != sub.grading_revision
        or run.confirmation_context_hash != current_hash
    ):
        run.checkpoint = None
        run.checkpoint_expires_at = None
        db.commit()
        raise ValueError("评分上下文已变化，请重新发起代码一致性检查")
    now = utc_now_naive()
    run.teacher_verdict = verdict
    run.teacher_note = note
    run.teacher_answered_at = now
    run.checkpoint = None
    run.checkpoint_expires_at = None
    if run.worker_id is None:
        run.status = AcpRunStatus.queued
    db.commit()
    return True


def record_session(
    db: Session, run: AcpRun, *, session_id: str, resumable: bool
) -> None:
    run.acp_session_id = session_id
    run.session_resumable = resumable
    db.commit()


def queue_codex_configuration(
    db: Session,
    run: AcpRun,
    config: dict[str, Any],
    *,
    pending: bool,
) -> None:
    """Persist desired configuration without pretending it is applied."""
    run.codex_config = dict(config)
    if pending:
        run.pending_codex_config = dict(config)
    else:
        run.pending_codex_config = None
    db.commit()


def mark_codex_configuration_applied(
    db: Session, run: AcpRun, config: dict[str, Any]
) -> None:
    run.applied_codex_config = dict(config)
    run.pending_codex_config = None
    run.codex_config = dict(config)
    db.commit()


def mark_codex_configuration_failed(db: Session, run: AcpRun) -> None:
    """Clear a pending update while retaining the last applied snapshot."""
    run.pending_codex_config = None
    run.codex_config = dict(run.applied_codex_config or {})
    db.commit()


def release_waiting_run(db: Session, run_id: int, claim_token: str) -> bool:
    """会话已关闭后释放检查点所有权；已答复则重新排队。"""
    run = db.execute(
        select(AcpRun).where(AcpRun.id == run_id).with_for_update()
    ).scalar_one_or_none()
    if (
        run is None
        or run.claim_token != claim_token
        or run.status != AcpRunStatus.waiting_for_teacher
    ):
        return False
    run.status = (
        AcpRunStatus.queued
        if run.teacher_answered_at is not None
        else AcpRunStatus.waiting_for_teacher
    )
    run.worker_id = None
    run.claim_token = None
    run.lease_expires_at = None
    db.commit()
    return True
