"""批改流水线的关键路径测试。

覆盖 MCP-only 收敛后的语义:
- 流水线只做作业 OCR,完成后停在 awaiting_mcp,不调用任何后端评分 Agent
- 轻量状态接口 GET /submissions/{id}/status 返回的字段集合
- 上传请求写入持久化任务

测试将 marking.py 的同步 Session 工厂替换为测试数据库工厂。
"""

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.application import marking
from app.core.errors import BusinessError
from app.models.background_job import BackgroundJob, BackgroundJobStatus
from app.models.question import Question, QuestionStatus
from app.models.submission import (
    Submission,
    SubmissionStatus,
)

# ---------- helpers ----------


async def _make_submission(
    db_session, tmp_path, *, with_question: bool = True
) -> Submission:
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


# ---------- MCP-only 流水线 ----------


@pytest.mark.asyncio
async def test_pipeline_reuses_question_ocr_and_only_ocr_student_submission(
    monkeypatch, tmp_path, db_session, marking_session_factory
):
    """题目 OCR 已缓存，批改时只调用一次学生作业 OCR，完成后停在 awaiting_mcp。"""
    paths: list[str] = []

    async def fake_ocr(file_path: str, api_url: str, token: str) -> str:
        paths.append(file_path)
        return "OCR 文本"

    monkeypatch.setattr(marking, "ocr_pdf", fake_ocr)

    sub = await _make_submission(db_session, tmp_path)

    await marking.run_marking_pipeline(sub.id)
    assert paths == [str(tmp_path / "h.pdf")]

    db_session.refresh(sub)
    assert sub.status == SubmissionStatus.awaiting_mcp
    assert sub.ocr_text == "OCR 文本"


@pytest.mark.asyncio
async def test_pipeline_uses_global_config(
    monkeypatch, tmp_path, db_session, marking_session_factory
):
    """批改流水线读取全局配置中的 OCR 参数。"""
    from app.models.question import Question, QuestionStatus
    from app.models.submission import Submission
    from app.services.config import upsert_config

    upsert_config(
        db_session,
        {
            "paddleocr_api_url": "https://custom.ocr/jobs",
            "paddleocr_token": "custom-token",
        },
    )

    question = Question(
        name="题目",
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

    seen: dict = {}

    async def fake_ocr(file_path: str, api_url: str, token: str) -> str:
        seen["ocr_url"] = api_url
        seen["ocr_token"] = token
        return "OCR 文本"

    monkeypatch.setattr(marking, "ocr_pdf", fake_ocr)

    await marking.run_marking_pipeline(sub.id)
    assert seen["ocr_url"] == "https://custom.ocr/jobs"
    assert seen["ocr_token"] == "custom-token"


@pytest.mark.asyncio
async def test_pipeline_stops_at_awaiting_mcp_without_backend_agent(
    monkeypatch, tmp_path, db_session, marking_session_factory
):
    """MCP-only: 流水线完成 OCR 后直接停在 awaiting_mcp，不调用任何后端评分 Agent。

    后端评分 Agent 已随收敛删除：marking 模块不再存在 run_marking_agent
    入口，流水线不可能再进入 agent 阶段。
    """
    assert not hasattr(marking, "run_marking_agent")

    async def fake_ocr(file_path: str, api_url: str, token: str) -> str:
        return "学生 OCR"

    monkeypatch.setattr(marking, "ocr_pdf", fake_ocr)

    sub = await _make_submission(db_session, tmp_path)

    await marking.run_marking_pipeline(sub.id)

    db_session.refresh(sub)
    assert sub.status == SubmissionStatus.awaiting_mcp
    assert sub.ocr_text == "学生 OCR"


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
    from app.services.queue import claim_next_job, new_submission_ocr_job

    monkeypatch.setattr(settings, "TASK_MAX_ATTEMPTS", 1)

    async def fail_s(file_path: str, api_url: str, token: str) -> str:
        if file_path.endswith("q.pdf"):
            return "题目"
        raise RuntimeError("OCR service down")

    monkeypatch.setattr(marking, "ocr_pdf", fail_s)

    sub = await _make_submission(db_session, tmp_path)
    db_session.add(new_submission_ocr_job(sub.id))
    db_session.commit()

    claimed = claim_next_job(db_session, worker_id="test-worker", lease_seconds=60)

    monkeypatch.setattr(worker, "SessionLocal", marking_session_factory)
    monkeypatch.setattr(marking, "SessionLocal", marking_session_factory)

    await worker._run_claimed(claimed)

    db_session.refresh(sub)
    assert sub.status == SubmissionStatus.failed
    assert sub.error_message is not None


@pytest.mark.asyncio
async def test_pipeline_leaves_system_failure_for_worker_retry(
    monkeypatch, tmp_path, db_session, marking_session_factory
):
    """系统异常不得提前落库 failed，否则下次队列重试会被状态守卫跳过。"""

    async def fail_submission(file_path: str, api_url: str, token: str) -> str:
        if file_path.endswith("q.pdf"):
            return "题目"
        raise RuntimeError("OCR service down")

    monkeypatch.setattr(marking, "ocr_pdf", fail_submission)

    sub = await _make_submission(db_session, tmp_path)

    with pytest.raises(RuntimeError, match="OCR service down"):
        await marking.run_marking_pipeline(sub.id)

    db_session.refresh(sub)
    assert sub.status == SubmissionStatus.ocr_processing
    assert sub.error_message is None


@pytest.mark.asyncio
async def test_pipeline_runs_without_question_pdf(
    monkeypatch, tmp_path, db_session, marking_session_factory
):
    """没有关联题目时必须失败，不能脱离评分依据继续批改。"""
    from app import worker
    from app.core.config import settings
    from app.services.queue import claim_next_job, new_submission_ocr_job

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
    db_session.add(new_submission_ocr_job(sub.id))
    db_session.commit()

    claimed = claim_next_job(db_session, worker_id="test-worker", lease_seconds=60)

    monkeypatch.setattr(worker, "SessionLocal", marking_session_factory)
    monkeypatch.setattr(marking, "SessionLocal", marking_session_factory)

    await worker._run_claimed(claimed)

    db_session.refresh(sub)
    assert sub.status == SubmissionStatus.failed


# ---------- 状态机守卫:重跑不得覆盖教师侧终态 ----------


@pytest.mark.asyncio
async def test_pipeline_skips_ready_for_review_submission(
    monkeypatch, tmp_path, db_session, marking_session_factory
):
    """B4: 提交已 ready_for_review(如 worker 崩溃后租约重认领)时,
    流水线直接返回,不再重跑覆盖已生成的建议。"""
    called = {"ocr": False}

    async def unexpected_ocr(file_path: str, api_url: str, token: str) -> str:
        called["ocr"] = True
        raise AssertionError("不应重新 OCR 已终态的提交")

    monkeypatch.setattr(marking, "ocr_pdf", unexpected_ocr)

    sub = await _make_submission(db_session, tmp_path)
    sub.status = SubmissionStatus.ready_for_review
    sub.score = 80
    sub.assessment_suggestion = {"score": 80}
    db_session.commit()

    await marking.run_marking_pipeline(sub.id)

    db_session.refresh(sub)
    assert not called["ocr"]
    assert sub.status == SubmissionStatus.ready_for_review
    assert sub.score == 80
    assert sub.assessment_suggestion == {"score": 80}


@pytest.mark.asyncio
async def test_pipeline_skips_reviewed_submission(
    monkeypatch, tmp_path, db_session, marking_session_factory
):
    """B4: 已 reviewed(教师已确认最终成绩)的提交同样禁止重跑覆盖。"""
    called = {"ocr": False}

    async def unexpected_ocr(file_path: str, api_url: str, token: str) -> str:
        called["ocr"] = True
        raise AssertionError("不应重新 OCR 已审阅的提交")

    monkeypatch.setattr(marking, "ocr_pdf", unexpected_ocr)

    sub = await _make_submission(db_session, tmp_path)
    sub.status = SubmissionStatus.reviewed
    sub.score = 95
    sub.reviewed_by = "Teacher"
    db_session.commit()

    await marking.run_marking_pipeline(sub.id)

    db_session.refresh(sub)
    assert not called["ocr"]
    assert sub.status == SubmissionStatus.reviewed
    assert sub.score == 95
    assert sub.reviewed_by == "Teacher"


@pytest.mark.asyncio
async def test_worker_dead_letter_skips_failed_when_confirmed(
    monkeypatch, tmp_path, db_session, marking_session_factory
):
    """B6: worker 死信路径(_mark_target_failed)不得把已 reviewed 的提交覆盖为 failed。"""
    from app import worker
    from app.services.queue import claim_next_job, new_submission_ocr_job

    sub = await _make_submission(db_session, tmp_path)
    sub.status = SubmissionStatus.reviewed
    sub.score = 90
    db_session.commit()

    db_session.add(new_submission_ocr_job(sub.id))
    db_session.commit()
    claimed = claim_next_job(db_session, worker_id="test-worker", lease_seconds=60)

    monkeypatch.setattr(worker, "SessionLocal", marking_session_factory)

    await worker._mark_target_failed(claimed, "模拟重试耗尽")

    db_session.refresh(sub)
    assert sub.status == SubmissionStatus.reviewed
    assert sub.score == 90


@pytest.mark.asyncio
async def test_worker_dead_letter_marks_failed_when_not_confirmed(
    monkeypatch, tmp_path, db_session, marking_session_factory
):
    """B6: 尚未进入教师侧终态的提交,死信路径仍正常标记 failed。"""
    from app import worker
    from app.services.queue import claim_next_job, new_submission_ocr_job

    sub = await _make_submission(db_session, tmp_path)
    sub.status = SubmissionStatus.ocr_processing
    db_session.commit()

    db_session.add(new_submission_ocr_job(sub.id))
    db_session.commit()
    claimed = claim_next_job(db_session, worker_id="test-worker", lease_seconds=60)

    monkeypatch.setattr(worker, "SessionLocal", marking_session_factory)

    await worker._mark_target_failed(claimed, "模拟重试耗尽")

    db_session.refresh(sub)
    assert sub.status == SubmissionStatus.failed
    assert sub.error_message is not None


# ---------- /status 端点 ----------

@pytest.mark.asyncio
async def test_status_endpoint_returns_light_fields_only(client, db_session, tmp_path):
    """GET /submissions/{id}/status 仅返回轻量字段,不包含 ocr_text/assessment_suggestion。"""
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
        status=SubmissionStatus.awaiting_mcp,
        score=None,
        ocr_text="MG级的 ocr 文本不应该出现" * 100,
        assessment_suggestion={"score": 80},
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
        "grading_mode",
        "grading_revision",
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
        "assessment_suggestion",
        "details",
        "feedback",
        "file_path",
        "agent_trace",
    ):
        assert forbidden not in payload, f"/status 端点不应返回 {forbidden}"


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
            select(BackgroundJob).where(BackgroundJob.submission_id == body["id"])
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
    from app.core.errors import BusinessError
    from app.services.queue import claim_next_job, new_submission_ocr_job

    sub = await _make_submission(db_session, tmp_path)

    async def business_fail(file_path: str, api_url: str, token: str) -> str:
        raise BusinessError("PaddleOCR 地址配置错误: https://x.com/ocr")

    monkeypatch.setattr(marking, "ocr_pdf", business_fail)

    db_session.add(new_submission_ocr_job(sub.id))
    db_session.commit()

    claimed = claim_next_job(db_session, worker_id="test-worker", lease_seconds=60)

    monkeypatch.setattr(worker, "SessionLocal", marking_session_factory)
    monkeypatch.setattr(marking, "SessionLocal", marking_session_factory)

    await worker._run_claimed(claimed)

    db_session.refresh(sub)
    assert sub.status == SubmissionStatus.failed
    assert sub.error_message is not None

    # 业务失败不应重试:任务行应被删除,而非回到 queued 或 dead
    remaining = (
        db_session.execute(
            select(BackgroundJob).where(BackgroundJob.submission_id == sub.id)
        )
        .scalars()
        .all()
    )
    assert remaining == []


# ---------- P2:流水线短会话(不长期持有数据库连接) ----------


def _counting_session_factory(base_factory):
    """包装 sessionmaker:返回公认可进入的上下文管理器,并跟踪活跃会话数。

    用法与 ``with SessionLocal() as db:`` 一致,返回的 ``db`` 仍是真正的
    Session,供 `marking.py` 各阶段使用。
    """

    from contextlib import contextmanager

    active = {"count": 0}

    @contextmanager
    def _managed():
        active["count"] += 1
        try:
            with base_factory() as session:
                yield session
        finally:
            active["count"] -= 1

    return _managed, active


@pytest.mark.asyncio
async def test_pipeline_releases_db_connection_during_ocr(
    monkeypatch, tmp_path, db_session, marking_session_factory
):
    """B3: 批改流水线在 OCR 调用期间必须已释放数据库连接。

    阶段化改造后每个阶段用短会话,OCR 期间活跃 Session 数应为 0,
    否则 TASK_CONCURRENCY 个并发流水线会长期占满连接池。
    """
    managed, active = _counting_session_factory(marking_session_factory)
    monkeypatch.setattr(marking, "SessionLocal", managed)

    async def fake_ocr(file_path: str, api_url: str, token: str) -> str:
        assert active["count"] == 0, "OCR 期间不应持有数据库连接"
        return "OCR 文本"

    monkeypatch.setattr(marking, "ocr_pdf", fake_ocr)

    sub = await _make_submission(db_session, tmp_path)

    await marking.run_marking_pipeline(sub.id)

    db_session.refresh(sub)
    assert sub.status == SubmissionStatus.awaiting_mcp
    assert sub.ocr_text == "OCR 文本"
