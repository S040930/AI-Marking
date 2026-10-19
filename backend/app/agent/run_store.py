"""GradingRun 数据访问与租约操作;纯持久化,无协议逻辑。

由旧运行骨架平移:表名/前缀改名,删除 agent 会话与
codex 专属配置(改 model/effort 语义),恢复策略保持统一。
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import and_, delete, or_, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.time import utc_now_naive
from app.domain.grading_run import TERMINAL_RUN_STATUSES, GradingRunStatus
from app.models.grading_run import GradingRun, GradingRunEvent
from app.models.grading_run_artifact import GradingRunArtifact

# 终态 run 的工件与 checkpointer thread 保留期(天)
ARTIFACT_RETENTION_DAYS = 7

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
MAX_TRANSCRIPT_BYTES = 5 * 1024 * 1024
MAX_EVENT_BYTES = 64 * 1024
# 检查点驻留上限 24h:教师可隔日答复;评分句柄 TTL 与之对齐。
CHECKPOINT_DWELL_SECONDS = 24 * 60 * 60


def create_run(
    db: Session,
    *,
    submission_id: int,
    provider: str,
    workspace_path: str = "",
    model: str | None = None,
    reasoning_effort: str | None = None,
) -> GradingRun:
    """创建 run;已有活跃 run 时返回 409 语义由调用方处理。"""
    run = GradingRun(
        submission_id=submission_id,
        provider=provider,
        graph_version=1,
        model=model,
        reasoning_effort=reasoning_effort,
        status=GradingRunStatus.queued,
        workspace_path=workspace_path,
    )
    db.add(run)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise ValueError("该作业已有进行中的批改运行") from exc
    return run


def get_run(db: Session, run_id: int) -> GradingRun | None:
    return db.get(GradingRun, run_id)


def get_active_run_for_submission(db: Session, submission_id: int) -> GradingRun | None:
    """返回该 submission 的非终态 run(至多一个,由部分唯一索引保证)。"""
    stmt = (
        select(GradingRun)
        .where(
            GradingRun.submission_id == submission_id,
            GradingRun.status.notin_([s.value for s in TERMINAL_RUN_STATUSES]),
        )
        .order_by(GradingRun.id.desc())
        .limit(1)
    )
    return db.execute(stmt).scalar_one_or_none()


def claim_next_run(
    db: Session,
    *,
    worker_id: str,
    lease_seconds: int,
    max_concurrent: int,
) -> GradingRun | None:
    """原子领取一个排队 run;租约过期且未达重试上限的也重新可领。

    ``max_concurrent`` 限制本 worker 同时持有的 run 数(默认并发 1)。
    使用 SQLite/PG 通用的事务 + 部分唯一约束串行化:以 ``FOR UPDATE``
    行锁(PG)或事务串行(SQLite)领取。
    """
    active_count = db.execute(
        select(text("COUNT(*)")).select_from(
            select(GradingRun.id)
            .where(
                GradingRun.worker_id == worker_id,
                GradingRun.status.in_(
                    [GradingRunStatus.starting, GradingRunStatus.running]
                ),
            )
            .subquery()
        )
    ).scalar_one()
    if active_count >= max_concurrent:
        return None

    now = utc_now_naive()
    claimable = (
        select(GradingRun)
        .where(
            GradingRun.status == GradingRunStatus.queued,
            GradingRun.attempts < MAX_ATTEMPTS,
        )
        .order_by(GradingRun.id.asc())
    )
    run = db.execute(
        claimable.with_for_update(skip_locked=True)
        .limit(1)
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if run is None:
        return None
    token = uuid.uuid4().hex
    run.status = GradingRunStatus.starting
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
        update(GradingRun)
        .where(
            GradingRun.id == run_id,
            GradingRun.claim_token == claim_token,
            GradingRun.status.in_(
                [GradingRunStatus.starting, GradingRunStatus.running]
            ),
        )
        .values(
            lease_expires_at=now + timedelta(seconds=max(1, lease_seconds)),
            heartbeat_at=now,
        )
    )
    db.commit()
    return result.rowcount == 1


def _locked_run(db: Session, run_id: int) -> GradingRun | None:
    return db.execute(
        select(GradingRun)
        .where(GradingRun.id == run_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()


def _worker_run(
    db: Session, run: GradingRun, claim_token: str | None
) -> GradingRun:
    current = _locked_run(db, run.id)
    if (
        current is None
        or current.status in TERMINAL_RUN_STATUSES
        or current.status == GradingRunStatus.cancelling
        or (claim_token is not None and current.claim_token != claim_token)
    ):
        raise ValueError("worker 已失去 run 所有权或运行已结束")
    return current


def _clear_owner(run: GradingRun) -> None:
    run.lease_expires_at = None
    run.claim_token = None
    run.worker_id = None


def transition_run(
    db: Session,
    run: GradingRun,
    target: GradingRunStatus,
    *,
    claim_token: str | None = None,
    error: str | None = None,
) -> bool:
    """持锁刷新后迁移，拒绝失效 worker 与终态覆盖。"""
    from app.domain.grading_run import ensure_run_transition

    run = _locked_run(db, run.id)
    if run is None or (claim_token is not None and run.claim_token != claim_token):
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
        run.checkpoint = None
        run.checkpoint_expires_at = None
        run.checkpoint_request_id = None
        _clear_owner(run)
    db.commit()
    return True


def append_event(
    db: Session,
    run_id: int,
    *,
    kind: str,
    payload: dict[str, Any],
    commit: bool = True,
) -> int:
    """原子追加有界事件；达到 5MB 后仅保留一次截断提示。"""
    from app.services.events import notify_grading_run_event

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
        select(GradingRun).where(GradingRun.id == run_id).with_for_update()
    ).scalar_one()
    if run.transcript_bytes + len(encoded) > MAX_TRANSCRIPT_BYTES:
        if run.transcript_truncated:
            if commit:
                db.rollback()
            return run.next_event_seq
        kind = "notice"
        payload = {"text": "转录已达到 5MB 上限，后续事件不再持久化"}
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        run.transcript_truncated = True
    run.next_event_seq += 1
    run.transcript_bytes += len(encoded)
    seq = run.next_event_seq
    db.add(GradingRunEvent(run_id=run_id, seq=seq, kind=kind, payload=payload))
    db.flush()
    notify_grading_run_event(db, run_id, seq, kind, payload)
    if commit:
        db.commit()
    return seq


def list_events_after(
    db: Session, run_id: int, after_seq: int, limit: int
) -> list[GradingRunEvent]:
    stmt = (
        select(GradingRunEvent)
        .where(GradingRunEvent.run_id == run_id, GradingRunEvent.seq > after_seq)
        .order_by(GradingRunEvent.seq.asc())
        .limit(limit)
    )
    return list(db.execute(stmt).scalars())


def latest_event_seq(db: Session, run_id: int) -> int:
    return (
        db.execute(
            select(GradingRunEvent.seq)
            .where(GradingRunEvent.run_id == run_id)
            .order_by(GradingRunEvent.seq.desc())
            .limit(1)
        ).scalar_one_or_none()
        or 0
    )


def _recover_run(db: Session, run: GradingRun) -> None:
    """Recovery policy shared by expiry, shutdown and checkpoint release.

    The caller holds the run lock; this function does not commit.
    """
    from app.models.submission import Submission, SubmissionStatus

    before = run.status
    if before == GradingRunStatus.cancelling:
        target = GradingRunStatus.cancelled
    elif before == GradingRunStatus.waiting_for_teacher and run.checkpoint is not None:
        target = GradingRunStatus.waiting_for_teacher
    elif before in (
        GradingRunStatus.starting,
        GradingRunStatus.running,
    ) and db.scalar(
        select(Submission.status).where(Submission.id == run.submission_id)
    ) in (SubmissionStatus.ready_for_review, SubmissionStatus.reviewed):
        target = GradingRunStatus.completed
    elif run.attempts >= MAX_ATTEMPTS:
        target = GradingRunStatus.failed
    else:
        target = GradingRunStatus.queued
    run.status = target
    _clear_owner(run)
    if target in TERMINAL_RUN_STATUSES:
        run.finished_at = utc_now_naive()
        run.checkpoint = None
        run.checkpoint_expires_at = None
        run.checkpoint_request_id = None
        run.error_message = (
            "批改 worker 多次中断，已达到重试上限"
            if target == GradingRunStatus.failed
            else None
        )
    logger.info(
        "批改 run 恢复 [run=%s, before=%s, after=%s]",
        run.id,
        before.value,
        target.value,
    )
    append_event(
        db,
        run.id,
        kind="notice",
        payload={"text": f"运行恢复为 {target.value}", "status": target.value},
        commit=False,
    )


def _recover_matching_runs(db: Session, condition) -> int:
    # Bound memory and lock duration, including repair of legacy stuck rows.
    count = 0
    while True:
        rows = list(
            db.execute(
                select(GradingRun)
                .where(condition)
                .order_by(GradingRun.id)
                .limit(100)
                .with_for_update(skip_locked=True)
                .execution_options(populate_existing=True)
            ).scalars()
        )
        if not rows:
            return count
        for run in rows:
            _recover_run(db, run)
        db.commit()
        count += len(rows)
        if len(rows) < 100:
            return count


def requeue_expired_leases(db: Session) -> int:
    """释放过期所有权，并终结无人处理的取消/重试耗尽记录。"""
    return _recover_matching_runs(
        db,
        or_(
            and_(
                GradingRun.status.in_(
                    [
                        GradingRunStatus.starting,
                        GradingRunStatus.running,
                        GradingRunStatus.waiting_for_teacher,
                        GradingRunStatus.cancelling,
                    ]
                ),
                GradingRun.lease_expires_at.is_not(None),
                GradingRun.lease_expires_at <= utc_now_naive(),
            ),
            and_(
                GradingRun.status == GradingRunStatus.cancelling,
                GradingRun.worker_id.is_(None),
            ),
            and_(
                GradingRun.status == GradingRunStatus.queued,
                GradingRun.attempts >= MAX_ATTEMPTS,
            ),
        ),
    )


def requeue_worker_runs(db: Session, worker_id: str) -> int:
    """进程已关闭后按同一恢复规则释放该 worker 的所有活跃运行。"""
    return _recover_matching_runs(
        db,
        and_(
            GradingRun.worker_id == worker_id,
            GradingRun.status.notin_(list(TERMINAL_RUN_STATUSES)),
        ),
    )


def save_checkpoint(
    db: Session,
    run: GradingRun,
    *,
    checkpoint: dict[str, Any],
    dwell_seconds: int,
    confirmation_revision: int,
    confirmation_context_hash: str,
    agent_state: dict[str, Any] | None = None,
    claim_token: str | None = None,
    checkpoint_request_id: str | None = None,
) -> None:
    """进入教师检查点驻留。

    续跑状态不再写 ``agent_state``:对话与句柄由 LangGraph checkpointer
    按 thread 持久化(见 ``graph/checkpointer.py``)。``agent_state`` 参数仅为
    过渡期接口保留,图中调用一律不传。
    """
    from app.domain.grading_run import ensure_run_transition

    run = _worker_run(db, run, claim_token)
    now = utc_now_naive()
    ensure_run_transition(run.status, GradingRunStatus.waiting_for_teacher)
    run.status = GradingRunStatus.waiting_for_teacher
    run.teacher_verdict = None
    run.teacher_note = None
    run.teacher_answered_at = None
    run.checkpoint = checkpoint
    run.checkpoint_asked_at = now
    run.checkpoint_expires_at = now + timedelta(seconds=dwell_seconds)
    run.confirmation_revision = confirmation_revision
    run.confirmation_context_hash = confirmation_context_hash
    if checkpoint_request_id is not None:
        run.checkpoint_request_id = checkpoint_request_id
    if agent_state is not None:
        run.agent_state = agent_state
    db.commit()


def request_cancel(db: Session, run: GradingRun) -> bool:
    """幂等请求取消；活跃会话由持有 claim 的 worker 完成回收。"""
    run = _locked_run(db, run.id)
    if run is None:
        return False
    if run.status in TERMINAL_RUN_STATUSES:
        return True
    if run.status == GradingRunStatus.cancelling:
        return True
    if not transition_run(db, run, GradingRunStatus.cancelling):
        return False
    run = db.get(GradingRun, run.id)
    if run is not None:
        run.cancel_requested_at = utc_now_naive()
        db.commit()
    return True


def claim_control_state(db: Session, run_id: int, claim_token: str) -> str:
    row = db.execute(
        select(GradingRun.claim_token, GradingRun.status).where(
            GradingRun.id == run_id
        )
    ).one_or_none()
    if row is None or row.claim_token != claim_token:
        return "lost"
    return "cancelling" if row.status == GradingRunStatus.cancelling else "active"


def queue_configuration(
    db: Session,
    run: GradingRun,
    config: dict[str, Any],
    *,
    pending: bool,
) -> None:
    """Persist desired configuration without pretending it is applied."""
    run.model = config.get("model")
    run.reasoning_effort = config.get("reasoning_effort")
    if pending:
        run.pending_config = dict(config)
    else:
        run.pending_config = None
    db.commit()


def mark_configuration_applied(
    db: Session,
    run: GradingRun,
    config: dict[str, Any],
    *,
    claim_token: str | None = None,
) -> None:
    run = _worker_run(db, run, claim_token)
    run.applied_config = dict(config)
    run.pending_config = None
    db.commit()


def mark_configuration_failed(
    db: Session, run: GradingRun, *, claim_token: str | None = None
) -> None:
    """Clear a pending update while retaining the last applied snapshot."""
    run = _worker_run(db, run, claim_token)
    run.pending_config = None
    db.commit()


def update_usage(db: Session, run_id: int, usage: dict[str, Any]) -> None:
    """持久化跨回合累计 usage(整字典覆盖,由 runner 维护累计)。"""
    run = db.get(GradingRun, run_id)
    if run is None:
        return
    run.usage = dict(usage)
    db.commit()


def release_waiting_run(db: Session, run_id: int, claim_token: str) -> bool:
    """检查点驻留后释放所有权；答复与重试耗尽采用统一规则。"""
    run = _locked_run(db, run_id)
    if (
        run is None
        or run.claim_token != claim_token
        or run.status != GradingRunStatus.waiting_for_teacher
    ):
        return False
    _recover_run(db, run)
    db.commit()
    return True


def expired_terminal_run_ids(
    db: Session,
    *,
    retention_days: int = ARTIFACT_RETENTION_DAYS,
    limit: int = 200,
) -> list[int]:
    """返回超过保留期的终态 run(按 ``updated_at`` 最早优先,分批处理)。"""
    cutoff = utc_now_naive() - timedelta(days=retention_days)
    rows = db.execute(
        select(GradingRun.id)
        .where(
            GradingRun.status.in_([s.value for s in TERMINAL_RUN_STATUSES]),
            GradingRun.updated_at <= cutoff,
        )
        .order_by(GradingRun.updated_at)
        .limit(limit)
    ).scalars()
    return list(rows)


def delete_run_artifacts(db: Session, run_ids: list[int]) -> int:
    """删除指定 run 的 ``grading_run_artifacts`` 行,返回删除条数。"""
    if not run_ids:
        return 0
    result = db.execute(
        delete(GradingRunArtifact).where(GradingRunArtifact.run_id.in_(run_ids))
    )
    db.commit()
    return int(result.rowcount or 0)
