"""真实 PostgreSQL 行锁集成测试。

默认跳过；设置 ``RUN_POSTGRES_TESTS=1`` 后使用 ``DATABASE_URL``。
"""

import os

import pytest
from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.background_job import BackgroundJob
from app.models.question import Question, QuestionStatus
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

            skipped = claim_next_job(
                contender, worker_id="contender", lease_seconds=90
            )
            assert skipped is None
            locker.rollback()

        with SessionLocal() as claimant:
            claimed = claim_next_job(
                claimant, worker_id="claimant", lease_seconds=90
            )
            assert claimed is not None
            assert claimed.question_id == question_id
    finally:
        with SessionLocal() as cleanup:
            question = cleanup.get(Question, question_id)
            if question is not None:
                cleanup.delete(question)
                cleanup.commit()
