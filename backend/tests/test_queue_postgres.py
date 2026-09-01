"""真实 PostgreSQL 行锁集成测试。

默认跳过；启用后只允许连接数据库名以 ``_test`` 结尾的 ``TEST_DATABASE_URL``。
"""

import os
import threading
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from app.application.locking import lock_submission_after_question
from app.application.mcp_workflow import get_locked_submission_in_order
from app.application.uploads import (
    commit_question_replace,
    commit_submission_retry,
    preflight_submission_create,
)
from app.core.errors import ConflictError
from app.core.time import utc_now_naive
from app.models.background_job import BackgroundJob, BackgroundJobStatus
from app.models.config_profile import ConfigProfile
from app.models.question import Question, QuestionStatus
from app.models.submission import Submission, SubmissionStatus
from app.services.document_storage import StoredDocument
from app.services.queue import claim_next_job, new_question_ocr_job

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_POSTGRES_TESTS") != "1",
    reason="需要显式启用本机 PostgreSQL 集成测试",
)


def _build_test_session_factory():
    if os.environ.get("RUN_POSTGRES_TESTS") != "1":
        return None
    raw_url = os.environ.get("TEST_DATABASE_URL")
    if not raw_url:
        raise RuntimeError("RUN_POSTGRES_TESTS=1 时必须设置 TEST_DATABASE_URL")
    database = make_url(raw_url).database or ""
    if not database.endswith("_test"):
        raise RuntimeError("拒绝运行：TEST_DATABASE_URL 数据库名必须以 _test 结尾")
    return sessionmaker(
        bind=create_engine(raw_url, pool_pre_ping=True), expire_on_commit=False
    )


TestSessionLocal = _build_test_session_factory()


def _session():
    assert TestSessionLocal is not None
    return TestSessionLocal()


def _new_question(setup, prefix: str) -> Question:
    suffix = uuid4().hex[:10]
    profile = ConfigProfile(name=f"{prefix}-{suffix}", is_default=False)
    setup.add(profile)
    setup.flush()
    question = Question(
        id=f"{prefix}-{suffix}",
        config_profile_id=profile.id,
        name=f"{prefix}-{suffix}",
        original_filename=f"{prefix}-{suffix}.pdf",
        file_path=f"/tmp/{prefix}-{suffix}.pdf",
        status=QuestionStatus.ready,
    )
    setup.add(question)
    setup.flush()
    return question


def _cleanup(question_id: str | None, profile_id: int | None) -> None:
    with _session() as cleanup:
        question = cleanup.get(Question, question_id) if question_id else None
        if question is not None:
            cleanup.delete(question)
            cleanup.commit()
        profile = cleanup.get(ConfigProfile, profile_id) if profile_id else None
        if profile is not None:
            cleanup.delete(profile)
            cleanup.commit()


def test_skip_locked_prevents_duplicate_claim():
    question_id = None
    profile_id = None
    with _session() as setup:
        question = _new_question(setup, "queue-integration")
        question.status = QuestionStatus.pending
        question_id = question.id
        profile_id = question.config_profile_id
        setup.add(new_question_ocr_job(question.id))
        setup.commit()

    try:
        with _session() as locker, _session() as contender:
            locked = (
                locker.execute(
                    select(BackgroundJob)
                    .where(BackgroundJob.question_id == question_id)
                    .with_for_update()
                )
            ).scalar_one()
            assert locked is not None

            skipped = claim_next_job(contender, worker_id="contender", lease_seconds=90)
            assert skipped is None
            locker.rollback()

        with _session() as claimant:
            claimed = claim_next_job(claimant, worker_id="claimant", lease_seconds=90)
            assert claimed is not None
            assert claimed.question_id == question_id
    finally:
        _cleanup(question_id, profile_id)


def test_finalize_and_mcp_lock_paths_serialize():
    """Finalize and MCP writes share Question -> Submission serialization.

    第二个事务的 ``SELECT ... FOR UPDATE`` 阻塞到第一个事务提交,
    随后读到的必须是 ``reviewed`` 而非过期的 ``ready_for_review``,
    从而杜绝并发 finalize 的重复审阅/丢失更新。
    """
    submission_id = None
    question_id = None
    profile_id = None
    with _session() as setup:
        question = _new_question(setup, "finalize-race")
        question_id = question.id
        profile_id = question.config_profile_id
        sub = Submission(
            original_filename="finalize-race.pdf",
            file_path="/tmp/finalize-race.pdf",
            question_id=question.id,
            status=SubmissionStatus.ready_for_review,
        )
        setup.add(sub)
        setup.flush()
        submission_id = sub.id
        setup.commit()

    started = threading.Event()
    release = threading.Event()
    results: dict[str, str] = {}

    def first():
        with _session() as db:
            row = lock_submission_after_question(db, submission_id)
            assert row is not None
            started.set()
            assert release.wait(timeout=10), "release 信号未触发"
            row.status = SubmissionStatus.reviewed
            db.commit()
            results["first"] = "ok"

    def second():
        # 该 SELECT FOR UPDATE 在 first 提交前不会返回
        with _session() as db:
            row = get_locked_submission_in_order(db, submission_id)
            results["second_status"] = row.status.value

    try:
        t1 = threading.Thread(target=first)
        t2 = threading.Thread(target=second)
        t1.start()
        assert started.wait(timeout=10), "first 未能取得行锁"
        t2.start()
        threading.Event().wait(0.2)  # 让 second 阻塞在行锁上
        release.set()
        t1.join(timeout=10)
        t2.join(timeout=10)
        assert results.get("first") == "ok"
        assert results["second_status"] == SubmissionStatus.reviewed.value
    finally:
        _cleanup(question_id, profile_id)


def test_upload_preflight_does_not_hold_question_lock():
    question_id = None
    profile_id = None
    with _session() as setup:
        question = _new_question(setup, "upload-preflight")
        question.ocr_text = "ready"
        question_id = question.id
        profile_id = question.config_profile_id
        setup.commit()

    try:
        result = preflight_submission_create(TestSessionLocal, question_id)
        assert result.question_id == question_id
        # This represents the period while the HTTP body is still being streamed.
        # NOWAIT must succeed because preflight owns and closes its short session.
        with _session() as contender:
            locked = contender.execute(
                select(Question)
                .where(Question.id == question_id)
                .with_for_update(nowait=True)
            ).scalar_one()
            assert locked.id == question_id
    finally:
        _cleanup(question_id, profile_id)


def test_retry_and_question_replace_do_not_deadlock_and_only_one_wins(tmp_path):
    question_id = None
    profile_id = None
    submission_id = None
    source = tmp_path / "submission.pdf"
    source.write_bytes(b"%PDF-test")
    replacement = tmp_path / "replacement.pdf"
    replacement.write_bytes(b"%PDF-replacement")
    with _session() as setup:
        question = _new_question(setup, "retry-replace-race")
        question.ocr_text = "ready"
        question_id = question.id
        profile_id = question.config_profile_id
        sub = Submission(
            original_filename="submission.pdf",
            file_path=str(source),
            question_id=question.id,
            status=SubmissionStatus.failed,
        )
        setup.add(sub)
        setup.flush()
        submission_id = sub.id
        setup.commit()

    barrier = threading.Barrier(2)
    outcomes: dict[str, str] = {}

    def retry():
        barrier.wait(timeout=10)
        try:
            commit_submission_retry(
                TestSessionLocal,
                submission_id=submission_id,
                question_id=question_id,
                stored=None,
            )
            outcomes["retry"] = "ok"
        except ConflictError:
            outcomes["retry"] = "conflict"

    def replace():
        barrier.wait(timeout=10)
        try:
            commit_question_replace(
                TestSessionLocal,
                question_id=question_id,
                confirmation_name=question_id,
                acknowledge_deletion=True,
                stored=StoredDocument(
                    original_filename="replacement.pdf",
                    path=Path(replacement),
                    sha256="0" * 64,
                    created=True,
                ),
            )
            outcomes["replace"] = "ok"
        except ConflictError:
            outcomes["replace"] = "conflict"

    try:
        threads = [threading.Thread(target=retry), threading.Thread(target=replace)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
            assert not thread.is_alive(), "concurrent write deadlocked"
        assert sorted(outcomes.values()) == ["conflict", "ok"]
    finally:
        _cleanup(question_id, profile_id)


def test_expired_running_lease_is_reclaimed():
    question_id = None
    profile_id = None
    with _session() as setup:
        question = _new_question(setup, "expired-lease")
        question.status = QuestionStatus.pending
        question_id = question.id
        profile_id = question.config_profile_id
        setup.add(new_question_ocr_job(question.id))
        setup.commit()

    try:
        with _session() as first:
            claimed = claim_next_job(first, worker_id="first", lease_seconds=90)
        assert claimed is not None
        with _session() as expire:
            job = expire.get(BackgroundJob, claimed.id, with_for_update=True)
            job.status = BackgroundJobStatus.running
            job.lease_expires_at = utc_now_naive() - timedelta(seconds=1)
            expire.commit()
        with _session() as second:
            reclaimed = claim_next_job(second, worker_id="second", lease_seconds=90)
        assert reclaimed is not None
        assert reclaimed.id == claimed.id
        assert reclaimed.claim_token != claimed.claim_token
    finally:
        _cleanup(question_id, profile_id)
