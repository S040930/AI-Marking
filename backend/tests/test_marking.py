"""批改流水线的关键路径测试。

覆盖性能优化引入的语义变化:
- OCR 阶段并行执行(题目与作业同时启动)
- 轻量状态接口 GET /submissions/{id}/status 返回的字段集合
- 并发闸门容量检查

测试注意:marking.py 内部直接 ``AsyncSessionLocal()``,测试需要将其替换为
``db_session`` 绑定的 ``async_sessionmaker``,使流水线看到的提交与测试可见。
"""

import asyncio

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.question import Question, QuestionStatus
from app.models.submission import Submission, SubmissionStatus
from app.services import marking, queue

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
    await db_session.commit()
    await db_session.refresh(sub)
    return sub


@pytest.fixture
def marking_session_factory(db_session):
    """让 ``marking.run_marking_pipeline`` 内部使用测试数据库 session。

    marking.py 通过 ``AsyncSessionLocal()`` 拿 session,这里把测试 engine
    绑定的 ``async_sessionmaker`` 注入到 marking 模块。
    """
    test_engine = db_session.bind
    factory = async_sessionmaker(
        bind=test_engine,
        class_=type(db_session),
        autocommit=False,
        autoflush=False,
        expire_on_commit=False,
    )
    monkey = marking.AsyncSessionLocal
    marking.AsyncSessionLocal = factory
    try:
        yield factory
    finally:
        marking.AsyncSessionLocal = monkey


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
    await db_session.commit()
    await marking.run_marking_pipeline(sub.id)

    await db_session.refresh(sub)
    assert sub.status == SubmissionStatus.failed
    assert sub.error_message is not None and "题目 OCR" in sub.error_message


@pytest.mark.asyncio
async def test_submission_ocr_failure_marks_submission_failed(
    monkeypatch, tmp_path, db_session, marking_session_factory
):
    async def fail_s(file_path: str, api_url: str, token: str) -> str:
        if file_path.endswith("q.pdf"):
            return "题目"
        raise RuntimeError("OCR service down")

    monkeypatch.setattr(marking, "ocr_pdf", fail_s)

    sub = await _make_submission(db_session, tmp_path)
    await marking.run_marking_pipeline(sub.id)

    await db_session.refresh(sub)
    assert sub.status == SubmissionStatus.failed
    assert sub.error_message is not None


@pytest.mark.asyncio
async def test_pipeline_runs_without_question_pdf(
    monkeypatch, tmp_path, db_session, marking_session_factory
):
    """没有关联题目时必须失败，不能脱离评分依据继续批改。"""

    async def only_submission(file_path, api_url, token):
        if file_path.endswith("q.pdf"):
            raise AssertionError("题目 PDF 不存在时不应触发 question OCR")
        return "作业"

    monkeypatch.setattr(marking, "ocr_pdf", only_submission)
    sub = await _make_submission(db_session, tmp_path, with_question=False)
    await marking.run_marking_pipeline(sub.id)
    await db_session.refresh(sub)
    assert sub.status == SubmissionStatus.failed


# ---------- /status 端点 ----------


@pytest.mark.asyncio
async def test_status_endpoint_returns_light_fields_only(client, db_session, tmp_path):
    """GET /submissions/{id}/status 仅返回轻量字段,不包含 ocr_text/ai_result。"""
    sub = Submission(
        original_filename="h.pdf",
        file_path=str(tmp_path / "h.pdf"),
        status=SubmissionStatus.agent_grading,
        score=None,
        ocr_text="MG级的 ocr 文本不应该出现" * 100,
        ai_result={"score": 80},
    )
    db_session.add(sub)
    await db_session.commit()
    await db_session.refresh(sub)

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
        status=SubmissionStatus.failed,
        error_message="批改队列繁忙",
    )
    db_session.add(sub)
    await db_session.commit()
    await db_session.refresh(sub)

    response = await client.get(f"/api/submissions/{sub.id}/status")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "failed"
    assert data["error_message"] == "批改队列繁忙"


# ---------- 并发闸门 ----------


def test_pipeline_semaphore_has_capacity():
    queue.configure_concurrency(2)
    assert queue.has_capacity()


@pytest.mark.asyncio
async def test_pipeline_semaphore_full_when_held():
    queue.configure_concurrency(1)
    sem = queue.get_pipeline_semaphore()
    await sem.acquire()
    assert not queue.has_capacity()
    sem.release()
    assert queue.has_capacity()


@pytest.mark.asyncio
async def test_status_503_when_pipeline_queue_full(client, db_session):
    """闸门满载时 POST /submissions 返回 503 + Retry-After。"""
    question = Question(
        name="队列测试",
        original_filename="q.pdf",
        file_path="/tmp/q.pdf",
        ocr_text="题目",
        status=QuestionStatus.ready,
    )
    db_session.add(question)
    await db_session.commit()
    queue.configure_concurrency(1)
    sem = queue.get_pipeline_semaphore()
    await sem.acquire()
    try:
        assert not queue.has_capacity()

        response = await client.post(
            "/api/submissions",
                files=[
                    ("file", ("h.pdf", b"%PDF-1.4\n", "application/pdf")),
                ],
                data={"question_id": str(question.id)},
        )
        assert response.status_code == 503
        assert "Retry-After" in response.headers
        body = response.json()
        assert "队列繁忙" in body["detail"]
    finally:
        sem.release()
