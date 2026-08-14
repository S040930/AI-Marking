"""真实 PostgreSQL 行锁集成测试。

默认跳过；设置 ``RUN_POSTGRES_TESTS=1`` 后使用 ``DATABASE_URL``。
"""

import os
import threading

import pytest
from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.background_job import BackgroundJob
from app.models.question import Question, QuestionStatus
from app.models.submission import Submission, SubmissionStatus
from app.services.queue import claim_next_job, new_question_ocr_job

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_POSTGRES_TESTS") != "1",
    reason="需要显式启用本机 PostgreSQL 集成测试",
)


def test_skip_locked_prevents_duplicate_claim():
    question_id = None
    with SessionLocal() as setup:
        question = Question(
            name="queue-integration-test",
            original_filename="integration.pdf",
            file_path="/tmp/integration.pdf",
            status=QuestionStatus.pending,
        )
        setup.add(question)
        setup.flush()
        question_id = question.id
        setup.add(new_question_ocr_job(question.id))
        setup.commit()

    try:
        with SessionLocal() as locker, SessionLocal() as contender:
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

        with SessionLocal() as claimant:
            claimed = claim_next_job(claimant, worker_id="claimant", lease_seconds=90)
            assert claimed is not None
            assert claimed.question_id == question_id
    finally:
        with SessionLocal() as cleanup:
            question = cleanup.get(Question, question_id)
            if question is not None:
                cleanup.delete(question)
                cleanup.commit()


def test_finalize_row_lock_serializes_concurrent_updates():
    """P1: finalize 加 ``with_for_update`` 后,并发事务被串行化。

    第二个事务的 ``SELECT ... FOR UPDATE`` 阻塞到第一个事务提交,
    随后读到的必须是 ``reviewed`` 而非过期的 ``ready_for_review``,
    从而杜绝并发 finalize 的重复审阅/丢失更新。
    """
    submission_id = None
    with SessionLocal() as setup:
        sub = Submission(
            original_filename="finalize-race.pdf",
            file_path="/tmp/finalize-race.pdf",
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
        with SessionLocal() as db:
            db.execute(
                select(Submission)
                .where(Submission.id == submission_id)
                .with_for_update()
            )
            started.set()
            assert release.wait(timeout=10), "release 信号未触发"
            row = db.get(Submission, submission_id)
            row.status = SubmissionStatus.reviewed
            db.commit()
            results["first"] = "ok"

    def second():
        # 该 SELECT FOR UPDATE 在 first 提交前不会返回
        with SessionLocal() as db:
            db.execute(
                select(Submission)
                .where(Submission.id == submission_id)
                .with_for_update()
            )
            row = db.get(Submission, submission_id)
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
        with SessionLocal() as cleanup:
            sub = cleanup.get(Submission, submission_id)
            if sub is not None:
                cleanup.delete(sub)
                cleanup.commit()
