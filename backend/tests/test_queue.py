"""持久化任务队列的领取、租约、重试与去重测试。"""

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app import worker
from app.application import question_ocr
from app.core.time import utc_now_naive
from app.models.background_job import (
    BackgroundJob,
    BackgroundJobStatus,
    BackgroundJobType,
)
from app.models.question import (
    Question,
    QuestionReplacementStatus,
    QuestionStatus,
)
from app.services.ocr import OCRError
from app.services.queue import (
    ClaimedJob,
    claim_next_job,
    complete_job,
    new_question_ocr_job,
    renew_lease,
    reset_question_ocr_job,
    retry_or_dead_letter,
)


async def _queued_question_job(db_session):
    question = Question(
        name="队列题目",
        original_filename="q.pdf",
        file_path="/tmp/q.pdf",
        status=QuestionStatus.pending,
    )
    db_session.add(question)
    db_session.flush()
    db_session.add(new_question_ocr_job(question.id))
    db_session.commit()
    return question


async def test_claim_is_unique_and_completion_requires_current_token(db_session):
    question = await _queued_question_job(db_session)

    claimed = claim_next_job(db_session, worker_id="worker-a", lease_seconds=90)
    assert claimed is not None
    assert claimed.question_id == question.id
    assert claimed.attempts == 1

    assert (
        claim_next_job(db_session, worker_id="worker-b", lease_seconds=90)
        is None
    )
    assert not complete_job(db_session, claimed.id, "stale-token")
    assert renew_lease(db_session, claimed.id, claimed.claim_token, 90)
    assert complete_job(db_session, claimed.id, claimed.claim_token)
    assert db_session.get(BackgroundJob, claimed.id) is None


async def test_expired_lease_can_be_reclaimed(db_session):
    await _queued_question_job(db_session)
    first = claim_next_job(db_session, worker_id="worker-a", lease_seconds=90)
    row = db_session.get(BackgroundJob, first.id)
    row.lease_expires_at = utc_now_naive() - timedelta(seconds=1)
    db_session.commit()

    second = claim_next_job(db_session, worker_id="worker-b", lease_seconds=90)
    assert second.id == first.id
    assert second.claim_token != first.claim_token
    assert second.attempts == 2
    assert not complete_job(db_session, first.id, first.claim_token)


async def test_retry_then_dead_letter(db_session):
    await _queued_question_job(db_session)
    first = claim_next_job(db_session, worker_id="worker-a", lease_seconds=90)
    assert not retry_or_dead_letter(
        db_session, first, error="temporary", max_attempts=2
    )
    row = db_session.get(BackgroundJob, first.id)
    assert row.status == BackgroundJobStatus.queued
    row.available_at = utc_now_naive() - timedelta(seconds=1)
    db_session.commit()

    second = claim_next_job(db_session, worker_id="worker-a", lease_seconds=90)
    assert retry_or_dead_letter(
        db_session, second, error="permanent", max_attempts=2
    )
    row = db_session.get(BackgroundJob, first.id)
    assert row.status == BackgroundJobStatus.dead
    assert row.last_error == "permanent"


async def test_retry_backoff_is_bounded(db_session):
    """B2: 即便重试次数较大,退避 available_at 不应超过 _MAX_BACKOFF_SECONDS。"""
    from app.services.queue import _MAX_BACKOFF_SECONDS, _backoff_seconds

    # 直接验证上界函数
    assert _backoff_seconds(10) <= _MAX_BACKOFF_SECONDS
    assert _backoff_seconds(20) <= _MAX_BACKOFF_SECONDS
    assert _backoff_seconds(1) == 5  # 基线 5s 不被截断

    await _queued_question_job(db_session)
    job = claim_next_job(db_session, worker_id="worker-a", lease_seconds=90)
    # 模拟已重试多次,检查落库后的 available_at 不超过上界
    row = db_session.get(BackgroundJob, job.id)
    row.attempts = 12
    db_session.commit()

    retry_or_dead_letter(db_session, job, error="boom", max_attempts=99)
    db_session.refresh(row)

    assert row.status == BackgroundJobStatus.queued
    delay = (row.available_at - utc_now_naive()).total_seconds()
    assert delay <= _MAX_BACKOFF_SECONDS + 1  # +1s 容差
    assert delay > 0


async def test_question_retry_reuses_dead_job(db_session):
    question = await _queued_question_job(db_session)
    job = (
        db_session.execute(
            select(BackgroundJob).where(
                BackgroundJob.question_id == question.id
            )
        )
    ).scalar_one()
    job.status = BackgroundJobStatus.dead
    job.attempts = 3
    db_session.commit()

    reset_question_ocr_job(db_session, question.id)
    db_session.commit()
    jobs = (
        db_session.execute(
            select(BackgroundJob).where(
                BackgroundJob.question_id == question.id
            )
        )
    ).scalars().all()
    assert len(jobs) == 1
    assert jobs[0].job_type == BackgroundJobType.question_ocr
    assert jobs[0].status == BackgroundJobStatus.queued
    assert jobs[0].attempts == 0


async def test_dead_letter_marks_business_target_failed(db_session, monkeypatch):
    question = Question(
        name="死信题目",
        original_filename="dead.pdf",
        file_path="/tmp/dead.pdf",
        status=QuestionStatus.ocr_processing,
    )
    db_session.add(question)
    db_session.commit()
    factory = sessionmaker(
        bind=db_session.bind, expire_on_commit=False
    )
    monkeypatch.setattr(worker, "SessionLocal", factory)

    await worker._mark_target_failed(
        ClaimedJob(
            id=1,
            job_type=BackgroundJobType.question_ocr,
            question_id=question.id,
            submission_id=None,
            attempts=3,
            claim_token="token",
        ),
        "worker crashed",
    )
    db_session.refresh(question)
    assert question.status == QuestionStatus.failed
    assert question.error_message == "worker crashed"


async def test_transient_ocr_failure_remains_queued_for_retry(
    db_session, monkeypatch
):
    from app.core.config import settings
    monkeypatch.setattr(settings, "TASK_MAX_ATTEMPTS", 2)

    question = await _queued_question_job(db_session)
    factory = sessionmaker(bind=db_session.bind, expire_on_commit=False)
    monkeypatch.setattr(worker, "SessionLocal", factory)
    monkeypatch.setattr(question_ocr, "SessionLocal", factory)

    async def fail_ocr(*args, **kwargs):
        raise OCRError("OCR 服务不可用")

    monkeypatch.setattr(question_ocr, "ocr_pdf", fail_ocr)
    claimed = claim_next_job(
        db_session, worker_id="worker-a", lease_seconds=90
    )

    await worker._run_claimed(claimed)

    db_session.expire_all()
    job_in_db = db_session.get(BackgroundJob, claimed.id)
    assert job_in_db is not None
    assert job_in_db.status == BackgroundJobStatus.queued
    retrying_question = db_session.get(Question, question.id)
    assert retrying_question.status == QuestionStatus.ocr_processing
    assert retrying_question.error_message is None


async def test_transient_ocr_failure_marks_target_only_after_dead_letter(
    db_session, monkeypatch
):
    from app.core.config import settings

    monkeypatch.setattr(settings, "TASK_MAX_ATTEMPTS", 1)
    question = await _queued_question_job(db_session)
    factory = sessionmaker(bind=db_session.bind, expire_on_commit=False)
    monkeypatch.setattr(worker, "SessionLocal", factory)
    monkeypatch.setattr(question_ocr, "SessionLocal", factory)

    async def fail_ocr(*args, **kwargs):
        raise OCRError("OCR 服务不可用")

    monkeypatch.setattr(question_ocr, "ocr_pdf", fail_ocr)
    claimed = claim_next_job(
        db_session, worker_id="worker-a", lease_seconds=90
    )
    await worker._run_claimed(claimed)

    db_session.expire_all()
    dead_job = db_session.get(BackgroundJob, claimed.id)
    assert dead_job is not None
    assert dead_job.status == BackgroundJobStatus.dead
    failed_question = db_session.get(Question, question.id)
    assert failed_question.status == QuestionStatus.failed
    assert "OCRError" in failed_question.error_message


async def test_dead_question_replace_keeps_old_question_usable(
    db_session, monkeypatch, tmp_path
):
    staged = tmp_path / "staged.pdf"
    staged.write_bytes(b"%PDF-new")
    question = Question(
        name="旧题目",
        original_filename="old.pdf",
        file_path=str(tmp_path / "old.pdf"),
        ocr_text="旧题目内容",
        status=QuestionStatus.ready,
        replacement_status=QuestionReplacementStatus.processing,
        replacement_file_path=str(staged),
        replacement_original_filename="new.pdf",
    )
    db_session.add(question)
    db_session.commit()
    factory = sessionmaker(bind=db_session.bind, expire_on_commit=False)
    monkeypatch.setattr(worker, "SessionLocal", factory)

    await worker._mark_target_failed(
        ClaimedJob(
            id=2,
            job_type=BackgroundJobType.question_replace,
            question_id=question.id,
            submission_id=None,
            attempts=3,
            claim_token="token",
        ),
        "worker crashed",
    )

    db_session.refresh(question)
    assert question.status == QuestionStatus.ready
    assert question.ocr_text == "旧题目内容"
    assert question.replacement_status == QuestionReplacementStatus.failed
    assert question.replacement_file_path == str(staged)
    assert staged.exists()


async def test_dead_question_replace_does_not_clobber_completed_switch(
    db_session, monkeypatch, tmp_path
):
    """死信处理不得覆盖并发执行已完成切换的替换（replacement_status=None）。

    模拟旧任务已死信，但另一份任务已成功切换题目（replacement_status 被清为
    None、file_path 已指向新 PDF）。死信分支此时不得把题目标成 failed。
    """
    staged = tmp_path / "staged.pdf"
    staged.write_bytes(b"%PDF-new")
    question = Question(
        name="旧题目",
        original_filename="old.pdf",
        file_path=str(tmp_path / "old.pdf"),
        ocr_text="旧题目内容",
        status=QuestionStatus.ready,
        replacement_status=None,  # 并发成功切换后已复位
        replacement_file_path=None,
        replacement_original_filename=None,
    )
    db_session.add(question)
    db_session.commit()
    factory = sessionmaker(bind=db_session.bind, expire_on_commit=False)
    monkeypatch.setattr(worker, "SessionLocal", factory)

    await worker._mark_target_failed(
        ClaimedJob(
            id=3,
            job_type=BackgroundJobType.question_replace,
            question_id=question.id,
            submission_id=None,
            attempts=3,
            claim_token="token",
        ),
        "worker crashed",
    )

    db_session.refresh(question)
    assert question.status == QuestionStatus.ready
    assert question.ocr_text == "旧题目内容"
    assert question.replacement_status is None
    # 暂存 PDF 属于已完成的替换,不应被删除
    assert staged.exists()
