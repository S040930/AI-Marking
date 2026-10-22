"""持久 run 状态机、租约与崩溃重排、教师检查点。

SQLite 内存库覆盖;PG 专属行为(部分唯一索引并发、NOTIFY)由
迁移与 SSE 通道复用既有实现。
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.agent import checkpoints as checkpoint_repo
from app.agent import run_store as run_repo
from app.db.base import Base
from app.domain.grading_run import (
    TERMINAL_RUN_STATUSES,
    GradingRunStatus,
    ensure_run_transition,
)
from app.models.grading_run import GradingRun
from app.models.question import Question, QuestionStatus
from app.models.submission import Submission, SubmissionStatus

_FAKE_HASH = "sha256:" + "0" * 64


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
                status=SubmissionStatus.awaiting_grading,
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
            provider="openai_api",
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
        run = db.get(GradingRun, run_id)
        assert run.status == GradingRunStatus.queued
        assert run.provider == "openai_api"


def test_second_active_run_rejected(factory):
    sid = _submission_id(factory)
    _make_run(factory, sid)
    with pytest.raises(ValueError, match="已有进行中"):
        _make_run(factory, sid)


def test_new_run_allowed_after_terminal(factory):
    sid = _submission_id(factory)
    run_id = _make_run(factory, sid)
    with factory() as db:
        run = db.get(GradingRun, run_id)
        run_repo.transition_run(db, run, GradingRunStatus.cancelled)
    _make_run(factory, sid)  # 终态后允许新建


# ---------------------------------------------------------------------------
# 状态机
# ---------------------------------------------------------------------------


def test_transition_rejects_illegal(factory):
    with pytest.raises(ValueError):
        ensure_run_transition(GradingRunStatus.queued, GradingRunStatus.completed)


def test_transition_with_wrong_token(factory):
    sid = _submission_id(factory)
    run_id = _make_run(factory, sid)
    with factory() as db:
        run = db.get(GradingRun, run_id)
        assert (
            run_repo.transition_run(
                db, run, GradingRunStatus.starting, claim_token="wrong"
            )
            is False
        )


def test_terminal_transition_clears_lease(factory):
    sid = _submission_id(factory)
    run_id = _make_run(factory, sid)
    with factory() as db:
        run = db.get(GradingRun, run_id)
        run.claim_token = "t"
        run.worker_id = "w"
        db.commit()
        run_repo.transition_run(db, run, GradingRunStatus.starting, claim_token="t")
        run_repo.transition_run(db, run, GradingRunStatus.failed, error="boom")
        assert run.status == GradingRunStatus.failed
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
        assert run.status == GradingRunStatus.starting
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
        run = run_repo.claim_next_run(
            db, worker_id="w1", lease_seconds=90, max_concurrent=1
        )
        token = run.claim_token
    with factory() as db:
        assert run_repo.renew_lease(db, run_id, token, 90) is True
        assert run_repo.renew_lease(db, run_id, "wrong", 90) is False


def test_requeue_expired_leases(factory):
    sid = _submission_id(factory)
    run_id = _make_run(factory, sid)
    with factory() as db:
        run = run_repo.claim_next_run(
            db, worker_id="w1", lease_seconds=90, max_concurrent=1
        )
        # 模拟租约过期
        from datetime import timedelta

        from app.core.time import utc_now_naive

        run.lease_expires_at = utc_now_naive() - timedelta(seconds=1)
        db.commit()

    with factory() as db:
        count = run_repo.requeue_expired_leases(db)
        assert count == 1
        run = db.get(GradingRun, run_id)
        assert run.status == GradingRunStatus.queued
        assert run.claim_token is None


def test_requeue_marks_completed_when_submission_graded(factory):
    sid = _submission_id(factory)
    run_id = _make_run(factory, sid)
    with factory() as db:
        run = run_repo.claim_next_run(
            db, worker_id="w1", lease_seconds=90, max_concurrent=1
        )
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
        run = db.get(GradingRun, run_id)
        assert run.status == GradingRunStatus.completed


def test_requeue_worker_runs(factory):
    sid = _submission_id(factory)
    run_id = _make_run(factory, sid)
    with factory() as db:
        run_repo.claim_next_run(db, worker_id="w1", lease_seconds=90, max_concurrent=1)
    with factory() as db:
        assert run_repo.requeue_worker_runs(db, "w1") == 1
        run = db.get(GradingRun, run_id)
        assert run.status == GradingRunStatus.queued


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
        run = run_repo.claim_next_run(
            db, worker_id="w1", lease_seconds=90, max_concurrent=1
        )
        run_repo.transition_run(db, run, GradingRunStatus.running)
        run = db.get(GradingRun, run_id)
        sub = db.get(Submission, sid)
        from app.application.grading_package import build_context_parts

        run_repo.save_checkpoint(
            db,
            run,
            checkpoint={"type": "code_consistency", "message": "一致?"},
            dwell_seconds=1800,
            confirmation_revision=sub.grading_revision,
            confirmation_context_hash=build_context_parts(db, sub)[3],
            agent_state={"items": [{"role": "user", "content": "hi"}], "rounds": 1},
        )
        assert run.status == GradingRunStatus.waiting_for_teacher
        assert run.checkpoint_expires_at is not None
        assert run.agent_state["rounds"] == 1

        assert checkpoint_repo.answer_checkpoint(
            db, run, verdict="consistent", note="看起来一致"
        )
        assert run.teacher_verdict == "consistent"
        assert run.teacher_note == "看起来一致"
        assert run.checkpoint is None

    # Worker closes the resident turn, releases ownership, then a new claim resumes it.
    with factory() as db:
        run = db.get(GradingRun, run_id)
        assert run_repo.release_waiting_run(db, run_id, "t") is False
        assert run.status == GradingRunStatus.waiting_for_teacher


def test_answer_checkpoint_requires_waiting(factory):
    sid = _submission_id(factory)
    run_id = _make_run(factory, sid)
    with factory() as db:
        run = db.get(GradingRun, run_id)
        assert (
            checkpoint_repo.answer_checkpoint(
                db, run, verdict="consistent", note=None
            )
            is False
        )


@pytest.mark.parametrize("action", ["approve_test_suite", "regenerate_test_suite"])
def test_code_test_checkpoint_persists_teacher_action(factory, action):
    from app.application.grading_package import build_context_parts
    from app.models.code_test_suite import CodeTestSuite
    from app.services.code_testing import source_fingerprint

    sid = _submission_id(factory)
    run_id = _make_run(factory, sid)
    with factory() as db:
        run = db.get(GradingRun, run_id)
        run_repo.claim_next_run(db, worker_id="w1", lease_seconds=90, max_concurrent=1)
        run = db.get(GradingRun, run_id)
        run_repo.transition_run(db, run, GradingRunStatus.running)
        sub = db.get(Submission, sid)
        _, _, _, context_hash, resolved = build_context_parts(db, sub)
        suite = CodeTestSuite(
            question_id=sub.question_id,
            version=1,
            source_fingerprint=source_fingerprint(sub.question, resolved.snapshot_id),
            status="draft",
            tests=[{"id": "q1-t1"}],
            generated_by_run_id=run_id,
        )
        db.add(suite)
        db.flush()
        checkpoint_id = "checkpoint-tests-1"
        run_repo.save_checkpoint(
            db,
            run,
            checkpoint={
                "type": "code_test_suite_approval",
                "preview": {"suite_id": suite.id, "version": suite.version},
            },
            dwell_seconds=1800,
            confirmation_revision=sub.grading_revision,
            confirmation_context_hash=context_hash,
            checkpoint_request_id=checkpoint_id,
        )
        assert checkpoint_repo.answer_checkpoint(db, run, action=action)
        assert run.checkpoint_answer == {
            "type": "code_test_suite_approval",
            "action": action,
            "note": None,
            "checkpoint_id": checkpoint_id,
            "suite_id": suite.id,
            "version": suite.version,
        }
        assert run.checkpoint_answered_at is not None
        assert run.checkpoint is None
        assert suite.status == "draft"


def test_terminal_statuses():
    assert TERMINAL_RUN_STATUSES == frozenset(
        {
            GradingRunStatus.completed,
            GradingRunStatus.failed,
            GradingRunStatus.cancelled,
        }
    )


@pytest.mark.parametrize("source", ["expiry", "shutdown", "release"])
@pytest.mark.parametrize(
    "answered,attempts,target",
    [
        (False, 1, GradingRunStatus.waiting_for_teacher),
        (False, 3, GradingRunStatus.waiting_for_teacher),
        (True, 1, GradingRunStatus.queued),
        (True, 3, GradingRunStatus.failed),
    ],
)
def test_waiting_recovery_uses_one_policy(factory, source, answered, attempts, target):
    from datetime import timedelta

    from app.application.grading_package import build_context_parts
    from app.core.time import utc_now_naive

    sid = _submission_id(factory)
    run_id = _make_run(factory, sid)
    with factory() as db:
        run = run_repo.claim_next_run(
            db, worker_id="owner", lease_seconds=90, max_concurrent=1
        )
        token = run.claim_token
        run_repo.transition_run(db, run, GradingRunStatus.running, claim_token=token)
        sub = db.get(Submission, sid)
        run_repo.save_checkpoint(
            db,
            run,
            checkpoint={"type": "code_consistency"},
            dwell_seconds=1800,
            confirmation_revision=0,
            confirmation_context_hash=build_context_parts(db, sub)[3],
            claim_token=token,
        )
        run.attempts = attempts
        if answered:
            run.teacher_verdict = "consistent"
            run.teacher_answered_at = utc_now_naive()
            run.checkpoint = None
        run.lease_expires_at = utc_now_naive() - timedelta(seconds=1)
        db.commit()
        if source == "expiry":
            assert run_repo.requeue_expired_leases(db) == 1
        elif source == "shutdown":
            assert run_repo.requeue_worker_runs(db, "owner") == 1
        else:
            assert run_repo.release_waiting_run(db, run_id, token)
        assert run.status == target
        assert run.worker_id is None
        assert run.claim_token is None
        assert run.lease_expires_at is None
        if not answered:
            assert run.checkpoint is not None
            assert checkpoint_repo.answer_checkpoint(
                db, run, verdict="consistent", note=None
            )
            assert run.status == (
                GradingRunStatus.failed if attempts == 3 else GradingRunStatus.queued
            )
        if run.status == GradingRunStatus.failed:
            assert run.finished_at is not None
            assert run_repo.get_active_run_for_submission(db, sid) is None


@pytest.mark.parametrize("source", ["expiry", "shutdown"])
@pytest.mark.parametrize(
    "initial,attempts,scored,target",
    [
        (GradingRunStatus.cancelling, 1, False, GradingRunStatus.cancelled),
        (GradingRunStatus.running, 1, False, GradingRunStatus.queued),
        (GradingRunStatus.running, 3, False, GradingRunStatus.failed),
        (GradingRunStatus.starting, 3, True, GradingRunStatus.completed),
    ],
)
def test_active_recovery_and_old_worker_rejection(
    factory, source, initial, attempts, scored, target
):
    from datetime import timedelta

    from app.core.time import utc_now_naive

    sid = _submission_id(factory)
    _make_run(factory, sid)
    with factory() as db:
        run = run_repo.claim_next_run(
            db, worker_id="owner", lease_seconds=90, max_concurrent=1
        )
        old_token = run.claim_token
        run.status = initial
        run.attempts = attempts
        run.lease_expires_at = utc_now_naive() - timedelta(seconds=1)
        if scored:
            db.get(Submission, sid).status = SubmissionStatus.ready_for_review
        db.commit()
        if source == "expiry":
            assert run_repo.requeue_expired_leases(db) == 1
        else:
            assert run_repo.requeue_worker_runs(db, "owner") == 1
        assert run.status == target
        assert not run_repo.transition_run(
            db, run, GradingRunStatus.failed, claim_token=old_token
        )
        # 失效 worker 的 checkpoint 写入必须被拒绝
        with pytest.raises(ValueError, match="所有权"):
            run_repo.save_checkpoint(
                db,
                run,
                checkpoint={"type": "code_consistency"},
                dwell_seconds=1800,
                confirmation_revision=0,
                confirmation_context_hash=_FAKE_HASH,
                claim_token=old_token,
            )
        assert run.status == target
        if target in TERMINAL_RUN_STATUSES:
            assert run.finished_at is not None
        assert run_repo.requeue_expired_leases(db) == 0


def test_scan_repairs_exhausted_queue_and_ownerless_cancel(factory):
    sid = _submission_id(factory)
    run_id = _make_run(factory, sid)
    with factory() as db:
        run = db.get(GradingRun, run_id)
        run.attempts = run_repo.MAX_ATTEMPTS
        db.commit()
        assert run_repo.requeue_expired_leases(db) == 1
        assert run.status == GradingRunStatus.failed
        assert run_repo.get_active_run_for_submission(db, sid) is None
    run_id = _make_run(factory, sid)
    with factory() as db:
        run = db.get(GradingRun, run_id)
        run.status = GradingRunStatus.cancelling
        db.commit()
        assert run_repo.requeue_expired_leases(db) == 1
        assert run.status == GradingRunStatus.cancelled


def test_reply_rejects_changed_context_and_frees_run(factory):
    from app.application.grading_package import build_context_parts

    sid = _submission_id(factory)
    _make_run(factory, sid)
    with factory() as db:
        run = run_repo.claim_next_run(
            db, worker_id="owner", lease_seconds=90, max_concurrent=1
        )
        run_repo.transition_run(db, run, GradingRunStatus.running)
        sub = db.get(Submission, sid)
        run_repo.save_checkpoint(
            db,
            run,
            checkpoint={"type": "code_consistency"},
            dwell_seconds=1800,
            confirmation_revision=0,
            confirmation_context_hash=build_context_parts(db, sub)[3],
        )
        sub.grading_revision = 1
        db.commit()
        with pytest.raises(ValueError, match="评分上下文已变化"):
            checkpoint_repo.answer_checkpoint(
                db, run, verdict="consistent", note=None
            )
        assert run.status == GradingRunStatus.failed
        assert run.checkpoint is None
        assert run_repo.get_active_run_for_submission(db, sid) is None


def test_reply_then_release_and_release_then_reply_both_resume(factory):
    from app.application.grading_package import build_context_parts

    sid = _submission_id(factory)
    for reply_first in (True, False):
        run_id = _make_run(factory, sid)
        with factory() as db:
            run = run_repo.claim_next_run(
                db, worker_id="owner", lease_seconds=90, max_concurrent=1
            )
            token = run.claim_token
            run_repo.transition_run(db, run, GradingRunStatus.running)
            sub = db.get(Submission, sid)
            run_repo.save_checkpoint(
                db,
                run,
                checkpoint={"type": "code_consistency"},
                dwell_seconds=1800,
                confirmation_revision=0,
                confirmation_context_hash=build_context_parts(db, sub)[3],
            )
            if reply_first:
                assert checkpoint_repo.answer_checkpoint(
                    db, run, verdict="consistent", note=None
                )
                assert run_repo.release_waiting_run(db, run_id, token)
            else:
                assert run_repo.release_waiting_run(db, run_id, token)
                assert checkpoint_repo.answer_checkpoint(
                    db, run, verdict="consistent", note=None
                )
            assert run.status == GradingRunStatus.queued
            assert run_repo.claim_next_run(
                db, worker_id="new-owner", lease_seconds=90, max_concurrent=1
            )
            run_repo.transition_run(db, run, GradingRunStatus.cancelled)


def test_old_worker_cannot_renew_new_worker_lease(factory):
    from datetime import timedelta

    from app.core.time import utc_now_naive

    sid = _submission_id(factory)
    run_id = _make_run(factory, sid)
    with factory() as db:
        run = run_repo.claim_next_run(
            db, worker_id="old", lease_seconds=90, max_concurrent=1
        )
        old_token = run.claim_token
        run.lease_expires_at = utc_now_naive() - timedelta(seconds=1)
        db.commit()
        assert run_repo.requeue_expired_leases(db) == 1
        run = run_repo.claim_next_run(
            db, worker_id="new", lease_seconds=90, max_concurrent=1
        )
        new_expiry = run.lease_expires_at
    with factory() as db:
        assert run_repo.renew_lease(db, run_id, old_token, 90) is False
        assert db.get(GradingRun, run_id).lease_expires_at == new_expiry
