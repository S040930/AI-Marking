"""题目替换任务的关键路径测试。

覆盖 P3 修复:
- 执行期间状态被外部改写时,清理暂存 PDF 并抛 ``BusinessError``(确定性失败,
  worker 直接删除任务不重试,且不改写 question 行,保留新一轮 pending 状态)
- 缺少暂存 PDF 同样抛 ``BusinessError``
"""

from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app import worker
from app.application import question_replace
from app.core.errors import BusinessError
from app.models.background_job import BackgroundJob
from app.models.question import Question, QuestionReplacementStatus, QuestionStatus
from app.services.queue import claim_next_job, new_question_replace_job


@pytest.fixture
def replace_session_factory(db_session):
    """让 ``question_replace`` / ``worker`` 内部使用测试数据库 session。"""
    test_engine = db_session.bind
    factory = sessionmaker(
        bind=test_engine,
        autocommit=False,
        autoflush=False,
        expire_on_commit=False,
    )
    monkey = question_replace.SessionLocal
    question_replace.SessionLocal = factory
    try:
        yield factory
    finally:
        question_replace.SessionLocal = monkey


def _write_pdf(tmp_path, name: str) -> str:
    path = str(tmp_path / name)
    Path(path).write_bytes(b"%PDF-1.4\n")
    return path


def _ready_question(tmp_path, staged_path: str) -> Question:
    return Question(
        name="替换测试",
        original_filename="orig.pdf",
        file_path=str(tmp_path / "orig.pdf"),
        ocr_text="旧题目 OCR",
        status=QuestionStatus.ready,
        replacement_file_path=staged_path,
        replacement_original_filename="staged.pdf",
        replacement_status=QuestionReplacementStatus.processing,
    )


@pytest.mark.asyncio
async def test_state_changed_during_ocr_cleans_staging_and_raises_business_error(
    monkeypatch, tmp_path, db_session, replace_session_factory
):
    """P3: OCR 期间状态被改写(新一轮替换 pending + 新文件)→ 确定性失败。

    必须清理本任务的暂存 PDF、保留 question 的新一轮 pending 状态,且抛
    BusinessError 而非 RuntimeError(后者会被队列误重试 3 次)。
    """
    staged_path = _write_pdf(tmp_path, "staged.pdf")
    newer_path = _write_pdf(tmp_path, "newer.pdf")
    from app.core.config import settings

    monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path))

    question = _ready_question(tmp_path, staged_path)
    db_session.add(question)
    db_session.commit()
    db_session.refresh(question)
    question_id = question.id

    async def flaky_ocr(file_path: str, api_url: str, token: str) -> str:
        # 模拟 OCR 期间用户发起了新一轮替换
        fresh = db_session.get(Question, question_id)
        fresh.replacement_file_path = newer_path
        fresh.replacement_original_filename = "newer.pdf"
        fresh.replacement_status = QuestionReplacementStatus.pending
        db_session.commit()
        return "新 OCR 文本"

    monkeypatch.setattr(question_replace, "ocr_pdf", flaky_ocr)

    with pytest.raises(BusinessError, match="状态在执行期间发生变化"):
        await question_replace.run_question_replace(question_id)

    assert not Path(staged_path).exists(), "旧暂存 PDF 应被清理"
    assert Path(newer_path).exists(), "新一轮替换文件不应被删除"
    db_session.refresh(question)
    assert question.replacement_status == QuestionReplacementStatus.pending
    assert question.replacement_file_path == newer_path


@pytest.mark.asyncio
async def test_state_changed_job_deleted_without_retry(
    monkeypatch, tmp_path, db_session, replace_session_factory
):
    """P3: worker 收到 BusinessError 后直接删除任务,不重试、不进死信。"""
    staged_path = _write_pdf(tmp_path, "staged.pdf")
    newer_path = _write_pdf(tmp_path, "newer.pdf")
    from app.core.config import settings

    monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path))

    question = _ready_question(tmp_path, staged_path)
    db_session.add(question)
    db_session.commit()
    db_session.refresh(question)
    db_session.add(new_question_replace_job(question.id))
    db_session.commit()

    claimed = claim_next_job(db_session, worker_id="test-worker", lease_seconds=60)
    assert claimed is not None

    async def flaky_ocr(file_path: str, api_url: str, token: str) -> str:
        fresh = db_session.get(Question, question.id)
        fresh.replacement_file_path = newer_path
        fresh.replacement_status = QuestionReplacementStatus.pending
        db_session.commit()
        return "新 OCR 文本"

    monkeypatch.setattr(question_replace, "ocr_pdf", flaky_ocr)
    monkeypatch.setattr(worker, "SessionLocal", replace_session_factory)
    monkeypatch.setattr(question_replace, "SessionLocal", replace_session_factory)

    await worker._run_claimed(claimed)

    remaining = (
        db_session.execute(
            select(BackgroundJob).where(BackgroundJob.question_id == question.id)
        )
        .scalars()
        .all()
    )
    assert remaining == [], "业务失败任务应被删除而非重试/死信"
    assert not Path(staged_path).exists()
    db_session.refresh(question)
    assert question.replacement_status == QuestionReplacementStatus.pending


@pytest.mark.asyncio
async def test_missing_staged_pdf_job_deleted_without_finalize_overwrite(
    monkeypatch, tmp_path, db_session, replace_session_factory
):
    """P3: 缺少暂存 PDF 是确定性业务失败,worker 直接删除任务不重试。"""
    question = Question(
        name="替换测试",
        original_filename="orig.pdf",
        file_path=str(tmp_path / "orig.pdf"),
        ocr_text="旧题目 OCR",
        status=QuestionStatus.ready,
        replacement_file_path=None,
        replacement_original_filename=None,
        replacement_status=None,
    )
    db_session.add(question)
    db_session.commit()
    db_session.refresh(question)
    db_session.add(new_question_replace_job(question.id))
    db_session.commit()

    claimed = claim_next_job(db_session, worker_id="test-worker", lease_seconds=60)
    assert claimed is not None

    monkeypatch.setattr(worker, "SessionLocal", replace_session_factory)
    monkeypatch.setattr(question_replace, "SessionLocal", replace_session_factory)

    await worker._run_claimed(claimed)

    remaining = (
        db_session.execute(
            select(BackgroundJob).where(BackgroundJob.question_id == question.id)
        )
        .scalars()
        .all()
    )
    assert remaining == []
