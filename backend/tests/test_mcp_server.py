
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.mcp import plans, server
from app.mcp.errors import McpApiError
from app.schemas.mcp import McpAssessmentRequest


@pytest.mark.asyncio
async def test_prepare_returns_opaque_plan_and_enforces_independent_files(tmp_path, monkeypatch):
    report = tmp_path / "answer.pdf"
    report.write_bytes(b"%PDF-1.4\nreport")
    q1 = tmp_path / "Q1.py"
    q1.write_text("print('ok')", encoding="utf-8")

    async def fake_call(method, path, **_kwargs):
        if path == "/api/mcp/health":
            return {"status": "ok"}
        assert method == "POST"
        return {
            "status": "ready_to_submit",
            "question_id": "DTS208",
            "question_name": "DTS208",
            "code_manifest": [{"filename": "Q1.py", "question_number": 1}],
        }

    monkeypatch.setattr(server, "_call", fake_call)
    result = await server.prepare_ai_marking_submission(
        "DTS208", str(report), [str(q1)]
    )
    assert result["status"] == "ready_to_submit"
    assert result["submission_plan"]
    assert str(q1) not in result["submission_plan"]
    assert "source" not in plans._prepared_plans[result["submission_plan"]]

    q2 = tmp_path / "Q2.py"
    q2.write_text("import Q1", encoding="utf-8")
    with pytest.raises(McpApiError, match="每题代码必须独立"):
        await server.prepare_ai_marking_submission(
            "DTS208", str(report), [str(q1), str(q2)]
        )


def test_expired_prepared_plans_are_purged_and_capacity_is_bounded(monkeypatch):
    plans._prepared_plans.clear()
    plans._prepared_plans["expired"] = {"expires_at": 0}
    token = plans.make_plan(
        {
            "question_id": "q",
            "question_name": "q",
            "report": {},
            "code_files": [],
            "code_manifest": [],
        }
    )
    assert "expired" not in plans._prepared_plans
    assert token in plans._prepared_plans
    plans._prepared_plans.clear()


@pytest.mark.asyncio
async def test_open_waits_at_ten_second_intervals_until_package_ready(monkeypatch):
    responses = iter(
        [
            {"submission_id": 7, "status": "ocr_processing"},
            {
                "submission_id": 7,
                "status": "awaiting_mcp",
                "content": "package",
                "context_complete": True,
                "grading_handle": "handle",
            },
        ]
    )
    waits = []

    async def fake_call(*_args, **_kwargs):
        return next(responses)

    async def fake_sleep(value):
        waits.append(value)

    monkeypatch.setattr(server, "_call", fake_call)
    monkeypatch.setattr(server.asyncio, "sleep", fake_sleep)
    result = await server.open_ai_marking_assignment(7)
    assert result["context_complete"] is True
    assert waits == [10]
    assert result["review_url"] == "http://localhost:5173/review/7"


@pytest.mark.asyncio
async def test_open_returns_early_on_needs_rubric(monkeypatch):
    """needs_rubric 分支立即返回,提示客户端先提取题目 rubric 再重新打开。"""
    response = {
        "submission_id": 7,
        "status": "awaiting_mcp",
        "needs_rubric": True,
        "question_id": "期末作文",
        "question_ocr_text": "Task 1: 100 points",
        "rubric_handle": "rubric-handle",
    }
    calls = 0

    async def fake_call(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return response

    monkeypatch.setattr(server, "_call", fake_call)
    result = await server.open_ai_marking_assignment(7)
    assert result["needs_rubric"] is True
    assert calls == 1  # 不应轮询等待
    assert result["review_url"] == "http://localhost:5173/review/7"


@pytest.mark.asyncio
async def test_open_uses_the_same_tool_for_continuation(monkeypatch):
    captured = {}

    async def fake_call(_method, _path, **kwargs):
        captured.update(kwargs)
        return {"submission_id": 7, "status": "awaiting_mcp", "content": "last", "context_complete": True}

    monkeypatch.setattr(server, "_call", fake_call)
    result = await server.open_ai_marking_assignment(7, "opaque-cursor")
    assert captured["params"] == {"continuation_token": "opaque-cursor"}
    assert result["content"] == "last"


@pytest.mark.asyncio
async def test_save_hides_protocol_fields_and_returns_review_link(monkeypatch):
    captured = {}

    async def fake_call(*_args, **kwargs):
        captured.update(kwargs)
        return {"submission_id": 13, "status": "ready_for_review", "grading_revision": 1}

    monkeypatch.setattr(server, "_call", fake_call)
    assessment = McpAssessmentRequest(
        rubric_source="built_in_default",
        rubric_snapshot_id="builtin-default-v8",
        request_id=UUID("11111111-1111-4111-8111-111111111111"),
        score=80,
        max_score=100,
        confidence=0.8,
        feedback="整体良好。",
        details=[{"rubric_item_id": "rubric_item_1", "criterion": "完整性", "score": 80, "max_score": 100, "comment": "良好", "evidence": ["原文证据"]}],
    )
    result = await server.save_ai_marking_assessment(13, "grading-handle", assessment)
    assert "expected_revision" not in captured["json"]["assessment"]
    assert result["review_url"] == "http://localhost:5173/review/13"


@pytest.mark.asyncio
async def test_list_pending_passes_cursor_and_limit(monkeypatch):
    captured = {}

    async def fake_call(_method, path, **kwargs):
        assert path == "/api/mcp/pending-assignments"
        captured.update(kwargs)
        return {"items": [], "next_cursor": None}

    monkeypatch.setattr(server, "_call", fake_call)
    result = await server.list_pending_ai_marking_assignments(
        cursor="2026-08-16T00:00:00:5", limit=50
    )
    assert captured["params"] == {"cursor": "2026-08-16T00:00:00:5", "limit": 50}
    assert result["items"] == []


@pytest.mark.asyncio
async def test_list_pending_rejects_out_of_range_limit(monkeypatch):
    with pytest.raises(McpApiError, match="limit"):
        await server.list_pending_ai_marking_assignments(limit=101)


@pytest.mark.asyncio
async def test_save_question_rubric_posts_to_question_endpoint(monkeypatch):
    captured = {}

    async def fake_call(_method, path, **kwargs):
        captured.update(kwargs)
        return {
            "status": "complete",
            "question_id": "期末作文",
            "question_name": "期末作文",
            "rubric_snapshot_id": "rubric_abc123",
        }

    monkeypatch.setattr(server, "_call", fake_call)
    result = await server.save_ai_marking_question_rubric(
        question_id="期末作文",
        handle="rubric-extraction-handle",
        status="complete",
        items=[
            {
                "criterion": "内容理解",
                "max_score": 60,
                "details": "准确理解题目",
                "source_quote": "内容理解 60分",
            }
        ],
        total_max_score=60,
    )
    assert captured["json"]["handle"] == "rubric-extraction-handle"
    assert captured["json"]["status"] == "complete"
    assert result["rubric_snapshot_id"] == "rubric_abc123"


def _assessment(score: float, max_score: float, details_score: float):
    return dict(
        rubric_source="built_in_default",
        rubric_snapshot_id="builtin-default-v8",
        request_id=UUID("11111111-1111-4111-8111-111111111111"),
        score=score,
        max_score=max_score,
        confidence=0.8,
        feedback="整体良好。",
        details=[
            {
                "rubric_item_id": "rubric_item_1",
                "criterion": "完整性",
                "score": details_score,
                "max_score": max_score,
                "comment": "良好",
                "evidence": ["原文证据"],
            }
        ],
    )


def test_mcp_assessment_rejects_score_over_max():
    """C1: 编程助手提交的总分不得超过满分。"""
    with pytest.raises(ValidationError, match="总分不能超过满分"):
        McpAssessmentRequest(**_assessment(score=120, max_score=100, details_score=100))


def test_mcp_assessment_rejects_score_not_matching_detail_sum():
    """C1: 编程助手提交的总分必须等于各评分项得分之和。"""
    with pytest.raises(ValidationError, match="各评分项得分之和必须等于总分"):
        McpAssessmentRequest(**_assessment(score=95, max_score=100, details_score=80))


def test_mcp_assessment_accepts_consistent_totals():
    """C1: 总分与明细自洽的评分建议通过校验。"""
    model = McpAssessmentRequest(**_assessment(score=80, max_score=100, details_score=80))
    assert model.score == 80
