"""阶段3+4验收:持久 run 状态机、租约与崩溃重排、教师检查点。

SQLite 内存库覆盖;PG 专属行为(部分唯一索引并发、NOTIFY)由
迁移与 SSE 通道复用既有实现。
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.acp import runs as run_repo
from app.acp.domain import (
    TERMINAL_RUN_STATUSES,
    AcpRunStatus,
    ensure_run_transition,
)
from app.acp.models import AcpRun
from app.db.base import Base
from app.models.question import Question, QuestionStatus
from app.models.submission import Submission, SubmissionStatus


@pytest.fixture
def factory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        db.add(
            Question(
                id="q1",
                name="题目1",
                original_filename="q1.pdf",
                file_path="uploads/q1.pdf",
                status=QuestionStatus.ready,
            )
        )
        db.add(
            Submission(
                original_filename="s1.pdf",
                file_path="uploads/s1.pdf",
                question_id="q1",
                status=SubmissionStatus.awaiting_mcp,
            )
        )
        db.commit()
    yield factory
    Base.metadata.drop_all(engine)
    engine.dispose()


def _submission_id(factory) -> int:
    with factory() as db:
        return db.query(Submission).first().id


def _make_run(factory, submission_id: int, **kw) -> int:
    with factory() as db:
        run = run_repo.create_run(
            db,
            submission_id=submission_id,
            agent_id="fake",
            agent_snapshot={
                "agent_id": "fake",
                "distribution": "binary",
                "package": "fake-agent",
                "version": "0.1.0",
                "command": "python3",
                "args": [],
                "env": {},
            },
            workspace_path="/tmp/ws",
            **kw,
        )
        db.commit()
        return run.id


# ---------------------------------------------------------------------------
# 创建与唯一活跃 run
# ---------------------------------------------------------------------------


def test_create_run(factory):
    sid = _submission_id(factory)
    run_id = _make_run(factory, sid)
    with factory() as db:
        run = db.get(AcpRun, run_id)
        assert run.status == AcpRunStatus.queued


def test_second_active_run_rejected(factory):
    sid = _submission_id(factory)
    _make_run(factory, sid)
    with pytest.raises(ValueError, match="已有进行中"):
        _make_run(factory, sid)


def test_new_run_allowed_after_terminal(factory):
    sid = _submission_id(factory)
    run_id = _make_run(factory, sid)
    with factory() as db:
        run = db.get(AcpRun, run_id)
        run_repo.transition_run(db, run, AcpRunStatus.cancelled)
    _make_run(factory, sid)  # 终态后允许新建


# ---------------------------------------------------------------------------
# 状态机
# ---------------------------------------------------------------------------


def test_transition_rejects_illegal(factory):
    with pytest.raises(ValueError):
        ensure_run_transition(AcpRunStatus.queued, AcpRunStatus.completed)


def test_transition_with_wrong_token(factory):
    sid = _submission_id(factory)
    run_id = _make_run(factory, sid)
    with factory() as db:
        run = db.get(AcpRun, run_id)
        assert run_repo.transition_run(
            db, run, AcpRunStatus.starting, claim_token="wrong"
        ) is False


def test_terminal_transition_clears_lease(factory):
    sid = _submission_id(factory)
    run_id = _make_run(factory, sid)
    with factory() as db:
        run = db.get(AcpRun, run_id)
        run.claim_token = "t"
        run.worker_id = "w"
        db.commit()
        run_repo.transition_run(db, run, AcpRunStatus.starting, claim_token="t")
        run_repo.transition_run(db, run, AcpRunStatus.failed, error="boom")
        assert run.status == AcpRunStatus.failed
        assert run.claim_token is None
        assert run.error_message == "boom"
        assert run.finished_at is not None


# ---------------------------------------------------------------------------
# 领取与租约
# ---------------------------------------------------------------------------


def test_claim_run(factory):
    sid = _submission_id(factory)
    _make_run(factory, sid)
    with factory() as db:
        run = run_repo.claim_next_run(
            db, worker_id="w1", lease_seconds=90, max_concurrent=1
        )
        assert run is not None
        assert run.status == AcpRunStatus.starting
        assert run.claim_token
        # 并发上限 1:已有 starting run 时不再领取
        assert (
            run_repo.claim_next_run(
                db, worker_id="w1", lease_seconds=90, max_concurrent=1
            )
            is None
        )


def test_renew_lease_requires_token(factory):
    sid = _submission_id(factory)
    run_id = _make_run(factory, sid)
    with factory() as db:
        run = run_repo.claim_next_run(db, worker_id="w1", lease_seconds=90, max_concurrent=1)
        token = run.claim_token
    with factory() as db:
        assert run_repo.renew_lease(db, run_id, token, 90) is True
        assert run_repo.renew_lease(db, run_id, "wrong", 90) is False


def test_requeue_expired_leases(factory):
    sid = _submission_id(factory)
    run_id = _make_run(factory, sid)
    with factory() as db:
        run = run_repo.claim_next_run(db, worker_id="w1", lease_seconds=90, max_concurrent=1)
        # 模拟租约过期
        from datetime import timedelta

        from app.core.time import utc_now_naive

        run.lease_expires_at = utc_now_naive() - timedelta(seconds=1)
        db.commit()

    with factory() as db:
        count = run_repo.requeue_expired_leases(db)
        assert count == 1
        run = db.get(AcpRun, run_id)
        assert run.status == AcpRunStatus.queued
        assert run.claim_token is None


def test_requeue_marks_completed_when_submission_graded(factory):
    sid = _submission_id(factory)
    run_id = _make_run(factory, sid)
    with factory() as db:
        run = run_repo.claim_next_run(db, worker_id="w1", lease_seconds=90, max_concurrent=1)
        from datetime import timedelta

        from app.core.time import utc_now_naive

        run.lease_expires_at = utc_now_naive() - timedelta(seconds=1)
        db.commit()
        # submission 已被教师侧确认
        sub = db.get(Submission, sid)
        sub.status = SubmissionStatus.ready_for_review
        db.commit()

    with factory() as db:
        run_repo.requeue_expired_leases(db)
        run = db.get(AcpRun, run_id)
        assert run.status == AcpRunStatus.completed


def test_requeue_worker_runs(factory):
    sid = _submission_id(factory)
    run_id = _make_run(factory, sid)
    with factory() as db:
        run = run_repo.claim_next_run(db, worker_id="w1", lease_seconds=90, max_concurrent=1)
    with factory() as db:
        assert run_repo.requeue_worker_runs(db, "w1") == 1
        run = db.get(AcpRun, run_id)
        assert run.status == AcpRunStatus.queued


# ---------------------------------------------------------------------------
# 事件流
# ---------------------------------------------------------------------------


def test_append_events_sequential(factory):
    sid = _submission_id(factory)
    run_id = _make_run(factory, sid)
    with factory() as db:
        s1 = run_repo.append_event(db, run_id, kind="notice", payload={"text": "a"})
        s2 = run_repo.append_event(db, run_id, kind="notice", payload={"text": "b"})
        assert s2 == s1 + 1
        events = run_repo.list_events_after(db, run_id, 0, 100)
        assert [e.seq for e in events] == [1, 2]
        assert run_repo.latest_event_seq(db, run_id) == 2


# ---------------------------------------------------------------------------
# 教师检查点
# ---------------------------------------------------------------------------


def test_checkpoint_roundtrip(factory):
    sid = _submission_id(factory)
    run_id = _make_run(factory, sid)
    with factory() as db:
        run = run_repo.claim_next_run(db, worker_id="w1", lease_seconds=90, max_concurrent=1)
        run_repo.transition_run(db, run, AcpRunStatus.running)
        run = db.get(AcpRun, run_id)
        sub = db.get(Submission, sid)
        from app.application.mcp_workflow import build_context_parts

        run_repo.save_checkpoint(
            db,
            run,
            checkpoint={"type": "code_consistency", "message": "一致?"},
            dwell_seconds=1800,
            confirmation_revision=sub.grading_revision,
            confirmation_context_hash=build_context_parts(db, sub)[3],
        )
        assert run.status == AcpRunStatus.waiting_for_teacher
        assert run.checkpoint_expires_at is not None

        assert run_repo.answer_checkpoint(
            db, run, verdict="consistent", note="看起来一致"
        )
        assert run.teacher_verdict == "consistent"
        assert run.teacher_note == "看起来一致"
        assert run.checkpoint is None

    # Worker closes the resident turn, releases ownership, then a new claim resumes it.
    with factory() as db:
        run = db.get(AcpRun, run_id)
        assert run_repo.release_waiting_run(db, run_id, "t") is False
        assert run.status == AcpRunStatus.waiting_for_teacher


def test_answer_checkpoint_requires_waiting(factory):
    sid = _submission_id(factory)
    run_id = _make_run(factory, sid)
    with factory() as db:
        run = db.get(AcpRun, run_id)
        assert (
            run_repo.answer_checkpoint(db, run, verdict="consistent", note=None)
            is False
        )


def test_terminal_statuses():
    assert TERMINAL_RUN_STATUSES == frozenset(
        {AcpRunStatus.completed, AcpRunStatus.failed, AcpRunStatus.cancelled}
    )
