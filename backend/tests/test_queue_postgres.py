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

from app.application.grading_workflow import get_locked_submission_in_order
from app.application.locking import lock_submission_after_question
from app.application.uploads import (
    commit_question_replace,
    commit_submission_retry,
    preflight_submission_create,
)
from app.core.errors import ConflictError
from app.core.time import utc_now_naive
from app.models.background_job import BackgroundJob, BackgroundJobStatus
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
    question = Question(
        id=f"{prefix}-{suffix}",
        name=f"{prefix}-{suffix}",
        original_filename=f"{prefix}-{suffix}.pdf",
        file_path=f"/tmp/{prefix}-{suffix}.pdf",
        status=QuestionStatus.ready,
    )
    setup.add(question)
    setup.flush()
    return question


def _cleanup(question_id: str | None) -> None:
    with _session() as cleanup:
        question = cleanup.get(Question, question_id) if question_id else None
        if question is not None:
            cleanup.delete(question)
            cleanup.commit()


def test_skip_locked_prevents_duplicate_claim():
    question_id = None
    with _session() as setup:
        question = _new_question(setup, "queue-integration")
        question.status = QuestionStatus.pending
        question_id = question.id
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
        _cleanup(question_id)


def test_finalize_and_mcp_lock_paths_serialize():
    """Finalize and MCP writes share Question -> Submission serialization.

    第二个事务的 ``SELECT ... FOR UPDATE`` 阻塞到第一个事务提交,
    随后读到的必须是 ``reviewed`` 而非过期的 ``ready_for_review``,
    从而杜绝并发 finalize 的重复审阅/丢失更新。
    """
    submission_id = None
    question_id = None
    with _session() as setup:
        question = _new_question(setup, "finalize-race")
        question_id = question.id
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
        _cleanup(question_id)


def test_upload_preflight_does_not_hold_question_lock():
    question_id = None
    with _session() as setup:
        question = _new_question(setup, "upload-preflight")
        question.ocr_text = "ready"
        question_id = question.id
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
        _cleanup(question_id)


def test_retry_and_question_replace_do_not_deadlock_and_only_one_wins(tmp_path):
    question_id = None
    submission_id = None
    source = tmp_path / "submission.pdf"
    source.write_bytes(b"%PDF-test")
    replacement = tmp_path / "replacement.pdf"
    replacement.write_bytes(b"%PDF-replacement")
    with _session() as setup:
        question = _new_question(setup, "retry-replace-race")
        question.ocr_text = "ready"
        question_id = question.id
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
        _cleanup(question_id)


def test_expired_running_lease_is_reclaimed():
    question_id = None
    with _session() as setup:
        question = _new_question(setup, "expired-lease")
        question.status = QuestionStatus.pending
        question_id = question.id
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
        _cleanup(question_id)


def _grading_fixture():
    from app.application.grading_package import (
        build_context_parts,
        create_workflow_handle,
    )
    from app.schemas.grading import GradingSaveAssessmentRequest

    with _session() as db:
        question = _new_question(db, "grading-version")
        sub = Submission(question_id=question.id, original_filename="answer.pdf", file_path="/tmp/answer.pdf",
                         ocr_text="Report", status=SubmissionStatus.awaiting_grading)
        db.add(sub)
        db.flush()
        context_hash, resolved = build_context_parts(db, sub)[3:]
        handles = [create_workflow_handle(db, {"kind": "grading", "submission_id": sub.id,
                   "context_hash": context_hash, "grading_revision": 0, "context_complete": True}) for _ in range(2)]
        requests = [GradingSaveAssessmentRequest(grading_handle=handle, assessment={
            "request_id": str(uuid4()), "feedback": "Missing content", "confidence": 0.8,
            "details": [{"rubric_item_id": item["rubric_item_id"], "score": 0,
                         "comment": "Not found", "evidence_status": "not_found"} for item in resolved.items],
        }) for handle in handles]
        db.commit()
        return question.id, sub.id, requests, resolved


def _race(first, second):
    """Both transactions preload their ORM state before either write starts."""
    barrier = threading.Barrier(2)
    results = {}

    def execute(name, operation):
        try:
            with _session() as db:
                results[name] = operation(db, barrier)
        except Exception as exc:
            results[name] = exc

    threads = [threading.Thread(target=execute, args=("first", first)),
               threading.Thread(target=execute, args=("second", second))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15)
        assert not thread.is_alive(), "transaction race did not terminate"
    return results


@pytest.mark.parametrize("identical", [False, True])
def test_concurrent_handle_versions_and_request_replay(identical):
    from app.application.grading_workflow import save_assessment

    question_id, sub_id, requests, _ = _grading_fixture()
    if identical:
        requests[1] = requests[0]

    def writer(request):
        def operation(db, barrier):
            assert db.get(Submission, sub_id).grading_revision == 0
            barrier.wait(timeout=10)
            return save_assessment(db, sub_id, request)
        return operation

    try:
        results = _race(writer(requests[0]), writer(requests[1]))
        if identical:
            assert all(not isinstance(value, Exception) for value in results.values()), results
            assert sorted(value.idempotent for value in results.values()) == [False, True]
        else:
            assert sum(isinstance(value, ConflictError) for value in results.values()) == 1, results
            assert sum(not isinstance(value, Exception) for value in results.values()) == 1, results
        with _session() as db:
            assert db.get(Submission, sub_id).grading_revision == 1
    finally:
        _cleanup(question_id)


def test_finalize_and_new_suggestion_cannot_overwrite_each_other():
    from app.application.grading_package import (
        build_context_parts,
        create_workflow_handle,
    )
    from app.application.grading_workflow import save_assessment
    from app.application.submissions import finalize_submission

    question_id, sub_id, requests, resolved = _grading_fixture()
    with _session() as db:
        save_assessment(db, sub_id, requests[0])
        sub = db.get(Submission, sub_id)
        request = requests[1]
        request.grading_handle = create_workflow_handle(db, {"kind": "grading", "submission_id": sub_id,
            "context_hash": build_context_parts(db, sub)[3], "grading_revision": 1, "context_complete": True})
        db.commit()

    def grade(db, barrier):
        assert db.get(Submission, sub_id).grading_revision == 1
        barrier.wait(timeout=10)
        return save_assessment(db, sub_id, request)

    def finalize(db, barrier):
        assert db.get(Submission, sub_id).grading_revision == 1
        barrier.wait(timeout=10)
        return finalize_submission(db, sub_id, expected_revision=1, reviewer_name="Teacher",
            score=0, max_score=resolved.total_max_score, feedback="Reviewed",
            details=[{"rubric_item_id": item["rubric_item_id"], "criterion": item["criterion"],
                      "max_score": item["max_score"], "score": 0, "comment": "Reviewed"} for item in resolved.items])

    try:
        results = _race(grade, finalize)
        assert sum(isinstance(value, ConflictError) for value in results.values()) == 1, results
        assert sum(not isinstance(value, Exception) for value in results.values()) == 1, results
        with _session() as db:
            sub = db.get(Submission, sub_id)
            assert (sub.status, sub.grading_revision) in [
                (SubmissionStatus.ready_for_review, 2), (SubmissionStatus.reviewed, 1)]
    finally:
        _cleanup(question_id)


def test_recovery_and_old_worker_result_serialize():
    from app.agent import run_store as runs
    from app.domain.grading_run import GradingRunStatus
    from app.models.grading_run import GradingRun

    question_id, sub_id, _, _ = _grading_fixture()
    with _session() as db:
        run = runs.create_run(db, submission_id=sub_id, provider="openai_api", workspace_path="/tmp/workspace")
        db.commit()
        run.status = GradingRunStatus.running
        run.worker_id = "race-owner"
        run.claim_token = uuid4().hex
        run.lease_expires_at = utc_now_naive() - timedelta(seconds=1)
        db.commit()
        run_id, token = run.id, run.claim_token

    def recover(db, barrier):
        db.get(GradingRun, run_id)
        barrier.wait(timeout=10)
        return runs.requeue_expired_leases(db)

    def result(db, barrier):
        stale = db.get(GradingRun, run_id)
        barrier.wait(timeout=10)
        return runs.transition_run(db, stale, GradingRunStatus.failed, claim_token=token, error="Worker result")

    try:
        results = _race(recover, result)
        assert all(not isinstance(value, Exception) for value in results.values()), results
        with _session() as db:
            run = db.get(GradingRun, run_id)
            assert run.status in (GradingRunStatus.queued, GradingRunStatus.failed)
            assert run.claim_token is None
            assert not runs.transition_run(db, run, GradingRunStatus.completed, claim_token=token)
    finally:
        _cleanup(question_id)


def test_checkpoint_reply_and_release_serialize():
    from app.agent import run_store as runs
    from app.application.grading_package import build_context_parts
    from app.domain.grading_run import GradingRunStatus
    from app.models.grading_run import GradingRun

    question_id, sub_id, _, _ = _grading_fixture()
    with _session() as db:
        run = runs.create_run(db, submission_id=sub_id, provider="openai_api", workspace_path="/tmp/workspace")
        db.commit()
        run.status = GradingRunStatus.running
        run.worker_id = "race-owner"
        run.claim_token = uuid4().hex
        run.attempts = 1
        db.commit()
        token, run_id = run.claim_token, run.id
        runs.save_checkpoint(db, run, checkpoint={"type": "code_consistency"}, dwell_seconds=1800,
            confirmation_revision=0, confirmation_context_hash=build_context_parts(db, db.get(Submission, sub_id))[3],
            claim_token=token)

    def reply(db, barrier):
        stale = db.get(GradingRun, run_id)
        barrier.wait(timeout=10)
        from app.agent import checkpoints as checkpoint_repo
        return checkpoint_repo.answer_checkpoint(db, stale, verdict="consistent", note=None)

    def release(db, barrier):
        db.get(GradingRun, run_id)
        barrier.wait(timeout=10)
        return runs.release_waiting_run(db, run_id, token)

    try:
        results = _race(reply, release)
        assert results == {"first": True, "second": True}, results
        with _session() as db:
            run = db.get(GradingRun, run_id)
            assert run.status == GradingRunStatus.queued
            assert run.worker_id is None
            assert run.teacher_verdict == "consistent"
    finally:
        _cleanup(question_id)
