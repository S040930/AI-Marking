"""批改流水线的关键路径测试。

覆盖性能优化引入的语义变化:
- OCR 阶段并行执行(题目与作业同时启动)
- 轻量状态接口 GET /submissions/{id}/status 返回的字段集合
- 上传请求写入持久化任务

测试将 marking.py 的同步 Session 工厂替换为测试数据库工厂。
"""

import asyncio

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.models.background_job import BackgroundJob, BackgroundJobStatus
from app.models.question import Question, QuestionStatus
from app.models.submission import Submission, SubmissionStatus
from app.services import marking
from app.services.errors import BusinessError

# ---------- helpers ----------


async def _make_submission(db_session, tmp_path, *, with_question: bool = True) -> Submission:
    question = None
    if with_question:
        question = Question(
            name="测试题目",
            original_filename="q.pdf",
            file_path=str(tmp_path / "q.pdf"),
            ocr_text="题目 OCR 文本",
            status=QuestionStatus.ready,
        )
    sub = Submission(
        original_filename="h.pdf",
        file_path=str(tmp_path / "h.pdf"),
        question=question,
        status=SubmissionStatus.pending,
    )
    db_session.add(sub)
    db_session.commit()
    db_session.refresh(sub)
    return sub


@pytest.fixture
def marking_session_factory(db_session):
    """让 ``marking.run_marking_pipeline`` 内部使用测试数据库 session。

    marking.py 通过 ``SessionLocal()`` 获取同步 session。
    """
    test_engine = db_session.bind
    factory = sessionmaker(
        bind=test_engine,
        autocommit=False,
        autoflush=False,
        expire_on_commit=False,
    )
    monkey = marking.SessionLocal
    marking.SessionLocal = factory
    try:
        yield factory
    finally:
        marking.SessionLocal = monkey


async def _fake_run_marking_agent(ocr_text, config, on_status=None, question_text=""):
    if on_status:
        # marking.py 的 on_status 现在是 async 函数,需要 await
        result = on_status(SubmissionStatus.agent_grading)
        if asyncio.iscoroutine(result):
            await result
    return {
        "outcome": "done",
        "draft": {
            "score": 80,
            "max_score": 100,
            "feedback": "总评",
            "details": [
                {
                    "criterion": "内容",
                    "score": 80,
                    "max_score": 100,
                    "comment": "评语",
                    "evidence": [],
                }
            ],
        },
        "critic": {"decision": "approve", "confidence": 0.9, "summary": "OK"},
        "trace": [],
        "review_reason": "",
    }


# ---------- 题目复用编排 ----------


@pytest.mark.asyncio
async def test_pipeline_reuses_question_ocr_and_only_ocr_student_submission(
    monkeypatch, tmp_path, db_session, marking_session_factory
):
    """题目 OCR 已缓存，批改时只调用一次学生作业 OCR。"""
    paths: list[str] = []

    async def fake_ocr(file_path: str, api_url: str, token: str) -> str:
        paths.append(file_path)
        return "OCR 文本"

    monkeypatch.setattr(marking, "ocr_pdf", fake_ocr)
    monkeypatch.setattr(marking, "run_marking_agent", _fake_run_marking_agent)

    sub = await _make_submission(db_session, tmp_path)

    await marking.run_marking_pipeline(sub.id)
    assert paths == [str(tmp_path / "h.pdf")]


@pytest.mark.asyncio
async def test_missing_cached_question_ocr_marks_submission_failed(
    monkeypatch, tmp_path, db_session, marking_session_factory
):
    sub = await _make_submission(db_session, tmp_path)
    sub.question.ocr_text = None
    db_session.commit()
    with pytest.raises(BusinessError, match="关联题目 OCR"):
        await marking.run_marking_pipeline(sub.id)

    db_session.refresh(sub)
    assert sub.status == SubmissionStatus.failed
    assert sub.error_message is not None and "题目 OCR" in sub.error_message


@pytest.mark.asyncio
async def test_submission_ocr_failure_marks_submission_failed(
    monkeypatch, tmp_path, db_session, marking_session_factory
):
    from app import worker
    from app.core.config import settings
    from app.services.queue import claim_next_job, new_submission_marking_job

    monkeypatch.setattr(settings, "TASK_MAX_ATTEMPTS", 1)

    async def fail_s(file_path: str, api_url: str, token: str) -> str:
        if file_path.endswith("q.pdf"):
            return "题目"
        raise RuntimeError("OCR service down")

    monkeypatch.setattr(marking, "ocr_pdf", fail_s)

    sub = await _make_submission(db_session, tmp_path)
    db_session.add(new_submission_marking_job(sub.id))
    db_session.commit()

    claimed = claim_next_job(db_session, worker_id="test-worker", lease_seconds=60)

    monkeypatch.setattr(worker, "SessionLocal", marking_session_factory)
    monkeypatch.setattr(marking, "SessionLocal", marking_session_factory)

    await worker._run_claimed(claimed)

    db_session.refresh(sub)
    assert sub.status == SubmissionStatus.failed
    assert sub.error_message is not None


@pytest.mark.asyncio
async def test_pipeline_marks_failed_immediately_on_ocr_error_without_dead_letter(
    monkeypatch, tmp_path, db_session, marking_session_factory
):
    """B1: 无论 worker 重试上限如何,OCR 抛异常时流水线自身应立即落库 failed。

    不依赖 worker 死信路径(那是 attempts 耗尽后的兜底),保证 UI 轮询在首次
    失败即可见 failed,而非停留在 ocr_processing 直到死信。
    """
    async def fail_submission(file_path: str, api_url: str, token: str) -> str:
        if file_path.endswith("q.pdf"):
            return "题目"
        raise RuntimeError("OCR service down")

    monkeypatch.setattr(marking, "ocr_pdf", fail_submission)

    sub = await _make_submission(db_session, tmp_path)

    # 流水线应 re-raise;此处只关心它已把 submission 标记为 failed
    with pytest.raises(RuntimeError, match="OCR service down"):
        await marking.run_marking_pipeline(sub.id)

    db_session.refresh(sub)
    assert sub.status == SubmissionStatus.failed
    assert sub.error_message is not None
    assert "OCR" in sub.error_message


@pytest.mark.asyncio
async def test_pipeline_marks_failed_immediately_on_agent_error(
    monkeypatch, tmp_path, db_session, marking_session_factory
):
    """B1: Agent 抛 AgentError 时流水线也应立即落库 failed。"""
    from app.services.agent import AgentError

    async def ok_ocr(file_path: str, api_url: str, token: str) -> str:
        return "作业"

    async def fail_agent(ocr_text, config, on_status=None, question_text=""):
        raise AgentError("LLM API 调用失败: 模拟错误")

    monkeypatch.setattr(marking, "ocr_pdf", ok_ocr)
    monkeypatch.setattr(marking, "run_marking_agent", fail_agent)

    sub = await _make_submission(db_session, tmp_path)

    with pytest.raises(BusinessError, match="LLM API 调用失败"):
        await marking.run_marking_pipeline(sub.id)

    db_session.refresh(sub)
    assert sub.status == SubmissionStatus.failed
    assert sub.error_message is not None
    assert "Agent" in sub.error_message


@pytest.mark.asyncio
async def test_pipeline_runs_without_question_pdf(
    monkeypatch, tmp_path, db_session, marking_session_factory
):
    """没有关联题目时必须失败，不能脱离评分依据继续批改。"""
    from app import worker
    from app.core.config import settings
    from app.services.queue import claim_next_job, new_submission_marking_job

    monkeypatch.setattr(settings, "TASK_MAX_ATTEMPTS", 1)

    async def only_submission(file_path, api_url, token):
        if file_path.endswith("q.pdf"):
            raise AssertionError("题目 PDF 不存在时不应触发 question OCR")
        return "作业"

    monkeypatch.setattr(marking, "ocr_pdf", only_submission)

    dummy_question = Question(
        name="测试题目",
        original_filename="q.pdf",
        file_path=str(tmp_path / "q.pdf"),
        ocr_text=None,
        status=QuestionStatus.failed,
    )
    sub = Submission(
        original_filename="h.pdf",
        file_path=str(tmp_path / "h.pdf"),
        question=dummy_question,
        status=SubmissionStatus.pending,
    )
    db_session.add(sub)
    db_session.flush()
    db_session.add(new_submission_marking_job(sub.id))
    db_session.commit()

    claimed = claim_next_job(db_session, worker_id="test-worker", lease_seconds=60)

    monkeypatch.setattr(worker, "SessionLocal", marking_session_factory)
    monkeypatch.setattr(marking, "SessionLocal", marking_session_factory)

    await worker._run_claimed(claimed)

    db_session.refresh(sub)
    assert sub.status == SubmissionStatus.failed


# ---------- /status 端点 ----------


@pytest.mark.asyncio
async def test_status_endpoint_returns_light_fields_only(client, db_session, tmp_path):
    """GET /submissions/{id}/status 仅返回轻量字段,不包含 ocr_text/ai_result。"""
    sub = Submission(
        original_filename="h.pdf",
        file_path=str(tmp_path / "h.pdf"),
        question=Question(
            name="状态题目",
            original_filename="q.pdf",
            file_path=str(tmp_path / "q.pdf"),
            ocr_text="题目",
            status=QuestionStatus.ready,
        ),
        status=SubmissionStatus.agent_grading,
        score=None,
        ocr_text="MG级的 ocr 文本不应该出现" * 100,
        ai_result={"score": 80},
    )
    db_session.add(sub)
    db_session.commit()
    db_session.refresh(sub)

    response = await client.get(f"/api/submissions/{sub.id}/status")
    assert response.status_code == 200
    payload = response.json()

    expected_keys = {
        "id",
        "status",
        "original_filename",
        "score",
        "max_score",
        "confidence",
        "uploaded_at",
        "completed_at",
        "error_message",
    }
    assert set(payload.keys()) == expected_keys
    for forbidden in (
        "ocr_text",
        "question_ocr_text",
        "ai_result",
        "details",
        "feedback",
        "file_path",
        "agent_trace",
    ):
        assert forbidden not in payload, (
            f"/status 端点不应返回 {forbidden}"
        )


@pytest.mark.asyncio
async def test_status_endpoint_404_when_missing(client):
    response = await client.get("/api/submissions/999999/status")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_status_endpoint_reflects_failure(client, db_session, tmp_path):
    sub = Submission(
        original_filename="h.pdf",
        file_path=str(tmp_path / "h.pdf"),
        question=Question(
            name="失败题目",
            original_filename="q.pdf",
            file_path=str(tmp_path / "q.pdf"),
            ocr_text="题目",
            status=QuestionStatus.ready,
        ),
        status=SubmissionStatus.failed,
        error_message="批改队列繁忙",
    )
    db_session.add(sub)
    db_session.commit()
    db_session.refresh(sub)

    response = await client.get(f"/api/submissions/{sub.id}/status")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "failed"
    assert data["error_message"] == "批改队列繁忙"


@pytest.mark.asyncio
async def test_upload_persists_job_instead_of_returning_503(client, db_session):
    """上传成功后持久排队，不因执行容量不足产生伪失败。"""
    question = Question(
        name="队列测试",
        original_filename="q.pdf",
        file_path="/tmp/q.pdf",
        ocr_text="题目",
        status=QuestionStatus.ready,
    )
    db_session.add(question)
    db_session.commit()
    response = await client.post(
        "/api/submissions",
        files=[("file", ("h.pdf", b"%PDF-1.4\n", "application/pdf"))],
        data={"question_id": str(question.id)},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "pending"
    job = (
        db_session.execute(
            select(BackgroundJob).where(
                BackgroundJob.submission_id == body["id"]
            )
        )
    ).scalar_one()
    assert job.status == BackgroundJobStatus.queued


@pytest.mark.asyncio
async def test_worker_marks_business_failure_without_retry(
    monkeypatch, tmp_path, db_session, marking_session_factory
):
    """B2: OCR 抛 BusinessError(业务失败)时,worker 直接标记终态并删除任务,
    不进入队列退避重试,也不产生死信。"""
    from app import worker
    from app.services.errors import BusinessError
    from app.services.queue import claim_next_job, new_submission_marking_job

    sub = await _make_submission(db_session, tmp_path)

    async def business_fail(file_path: str, api_url: str, token: str) -> str:
        raise BusinessError("PaddleOCR 地址配置错误: https://x.com/ocr")

    monkeypatch.setattr(marking, "ocr_pdf", business_fail)

    db_session.add(new_submission_marking_job(sub.id))
    db_session.commit()

    claimed = claim_next_job(
        db_session, worker_id="test-worker", lease_seconds=60
    )

    monkeypatch.setattr(worker, "SessionLocal", marking_session_factory)
    monkeypatch.setattr(marking, "SessionLocal", marking_session_factory)

    await worker._run_claimed(claimed)

    db_session.refresh(sub)
    assert sub.status == SubmissionStatus.failed
    assert sub.error_message is not None

    # 业务失败不应重试:任务行应被删除,而非回到 queued 或 dead
    remaining = (
        db_session.execute(
            select(BackgroundJob).where(
                BackgroundJob.submission_id == sub.id
            )
        )
        .scalars()
        .all()
    )
    assert remaining == []
