
from uuid import UUID

import pytest

from app.mcp import server
from app.mcp.errors import McpApiError
from app.schemas.mcp import McpSimpleAssessmentRequest


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
            "question_id": 2,
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
    assert "source" not in server._prepared_plans[result["submission_plan"]]

    q2 = tmp_path / "Q2.py"
    q2.write_text("import Q1", encoding="utf-8")
    with pytest.raises(McpApiError, match="每题代码必须独立"):
        await server.prepare_ai_marking_submission(
            "DTS208", str(report), [str(q1), str(q2)]
        )


def test_expired_prepared_plans_are_purged_and_capacity_is_bounded(monkeypatch):
    server._prepared_plans.clear()
    server._prepared_plans["expired"] = {"expires_at": 0}
    token = server._make_plan(
        {
            "question_id": 1,
            "question_name": "q",
            "report": {},
            "code_files": [],
            "code_manifest": [],
        }
    )
    assert "expired" not in server._prepared_plans
    assert token in server._prepared_plans
    server._prepared_plans.clear()


@pytest.mark.asyncio
async def test_open_waits_at_ten_second_intervals_until_package_ready(monkeypatch):
    responses = iter(
        [
            {"submission_id": 7, "status": "ocr_processing"},
            {
                "submission_id": 7,
                "status": "awaiting_codex",
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
    assert result["review_url"] == "http://localhost:5173/result/7"


@pytest.mark.asyncio
async def test_open_uses_the_same_tool_for_continuation(monkeypatch):
    captured = {}

    async def fake_call(_method, _path, **kwargs):
        captured.update(kwargs)
        return {"submission_id": 7, "status": "awaiting_codex", "content": "last", "context_complete": True}

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
    assessment = McpSimpleAssessmentRequest(
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
    assert result["review_url"] == "http://localhost:5173/result/13"


def test_grade_assignment_prompt_uses_only_new_workflow():
    prompt = server.grade_assignment(13)
    assert "open_ai_marking_assignment" in prompt
    assert "save_ai_marking_assessment" in prompt
    assert "grading_policy" in prompt
    assert "本地运行表现" in prompt
    assert "尚未检查、含糊或未回答时暂停" in prompt
    assert "本地运行表现" in prompt
    assert "不生成任何视觉比较或复核证据" in prompt
    assert "read_ai_marking_evidence_image" not in prompt
    assert "第二遍" in prompt
    assert "最终成绩" in prompt
    assert "wait_for_codex_assignment" not in prompt
