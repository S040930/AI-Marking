import pytest

from app.api.mcp import _grading_policy
from app.core.config import settings
from app.models.question import Question, QuestionStatus
from app.models.submission import Submission, SubmissionGradingMode, SubmissionStatus
from app.models.submission_code_file import SubmissionCodeFile
from app.services.rubric import resolve_rubric


def _headers():
    return {"X-AI-Marking-MCP-Token": "test-token"}


def test_grading_policy_requires_manual_visual_confirmation():
    policy = _grading_policy(resolve_rubric(None, {}))
    requirements = "\n".join(policy["requirements"])
    assert "提交含代码时" in requirements
    assert "已检查且一致" in requirements
    assert "存在不一致" in requirements
    assert "尚未检查、含糊回答或未回答时必须暂停" in requirements
    assert "读取视觉资产" in requirements
    assert "不得调用 read_ai_marking_evidence_image" not in requirements


@pytest.mark.asyncio
async def test_package_policy_covers_code_and_visual_assets_without_image_reading(
    client, db_session, monkeypatch
):
    monkeypatch.setattr(settings, "MCP_INTERNAL_TOKEN", "test-token")
    question = Question(
        name="图片核验题",
        original_filename="q.pdf",
        file_path="/tmp/q.pdf",
        ocr_text="Task 1: 100 points",
        status=QuestionStatus.ready,
    )
    submission = Submission(
        original_filename="answer.pdf",
        file_path="/tmp/a.pdf",
        question=question,
        ocr_text="报告文字",
        status=SubmissionStatus.awaiting_codex,
        grading_mode=SubmissionGradingMode.codex,
    )
    submission.code_files.append(
        SubmissionCodeFile(
            question_number=1,
            original_filename="Q1.py",
            file_path="/tmp/Q1.py",
            file_kind="py",
            source_sha256="b" * 64,
            source_text="print('ok')",
        )
    )
    db_session.add(submission)
    db_session.commit()

    response = await client.get(
        f"/api/mcp/submissions/{submission.id}/package", headers=_headers()
    )
    assert response.status_code == 200, response.text
    content = response.json()["content"]
    assert "运行表现" in content
    assert "execution" not in response.json()
    assert "visual_assets" not in response.json()


@pytest.mark.asyncio
async def test_legacy_mcp_routes_are_removed(client, monkeypatch):
    monkeypatch.setattr(settings, "MCP_INTERNAL_TOKEN", "test-token")
    for path in (
        "/api/mcp/questions",
        "/api/mcp/questions/1/code-requirements",
        "/api/mcp/submissions",
        "/api/mcp/submissions/1/status",
        "/api/mcp/submissions/1/manifest",
        "/api/mcp/submissions/1/context",
        "/api/mcp/submissions/1/assessment",
    ):
        response = await client.get(path, headers=_headers())
        assert response.status_code == 404, (path, response.text)

    response = await client.put(
        "/api/mcp/submissions/1/assessment",
        headers=_headers(),
        json={},
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_mcp_preflight_resolves_exact_question_and_maps_files(client, db_session, monkeypatch):
    monkeypatch.setattr(settings, "MCP_INTERNAL_TOKEN", "test-token")
    db_session.add(
        Question(
            name="DTS208TC_CW2_Paper",
            original_filename="cw2.pdf",
            file_path="/tmp/q.pdf",
            ocr_text="Task 1 uses task1.py and netflix_teaching_dataset.csv.",
            status=QuestionStatus.ready,
        )
    )
    db_session.commit()
    response = await client.post(
        "/api/mcp/submission-preflight",
        headers=_headers(),
        json={
            "question_name": "DTS208TC_CW2_Paper",
            "code_files": [{"filename": "task1.py", "question_number": 1}],
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "ready_to_submit"
    assert response.json()["code_manifest"] == [{"filename": "task1.py", "question_number": 1}]


@pytest.mark.asyncio
async def test_mcp_preflight_returns_candidates_for_ambiguous_name(client, db_session, monkeypatch):
    monkeypatch.setattr(settings, "MCP_INTERNAL_TOKEN", "test-token")
    for name in ("DTS208 CW1", "DTS208 CW2"):
        db_session.add(Question(name=name, original_filename=f"{name}.pdf", file_path="/tmp/q.pdf", ocr_text="text", status=QuestionStatus.ready))
    db_session.commit()
    response = await client.post("/api/mcp/submission-preflight", headers=_headers(), json={"question_name": "DTS208"})
    assert response.status_code == 200
    assert response.json()["status"] == "needs_question_choice"
    assert len(response.json()["candidates"]) == 2


@pytest.mark.asyncio
async def test_package_paginates_and_compact_save_enforces_handle(client, db_session, monkeypatch):
    monkeypatch.setattr(settings, "MCP_INTERNAL_TOKEN", "test-token")
    question = Question(name="评分题", original_filename="q.pdf", file_path="/tmp/q.pdf", ocr_text="Task 1: 100 points", status=QuestionStatus.ready)
    submission = Submission(
        original_filename="answer.pdf",
        file_path="/tmp/a.pdf",
        question=question,
        ocr_text="证据 " * 25_000,
        status=SubmissionStatus.awaiting_codex,
        grading_mode=SubmissionGradingMode.codex,
    )
    db_session.add(submission)
    db_session.commit()
    first = await client.get(f"/api/mcp/submissions/{submission.id}/package", headers=_headers())
    assert first.status_code == 200
    first_payload = first.json()
    assert first_payload["context_complete"] is False
    assert first_payload["grading_handle"] is None
    second = await client.get(
        f"/api/mcp/submissions/{submission.id}/package",
        params={"continuation_token": first_payload["continuation_token"]},
        headers=_headers(),
    )
    assert second.status_code == 200
    final_payload = second.json()
    assert final_payload["context_complete"] is True
    import json
    header = json.loads(first_payload["content"].split("\n", 1)[1].split("\n\n", 1)[0])
    resolved = header["grading_policy"]["resolved_rubric"]
    rubric_details = [
        {
            "rubric_item_id": item["rubric_item_id"],
            "criterion": item["criterion"],
            "score": 0,
            "max_score": item["max_score"],
            "comment": "待改进。",
            "evidence": ["证据"],
        }
        for item in resolved["items"]
    ]
    save = await client.put(
        f"/api/mcp/submissions/{submission.id}/assessment-v2",
        headers=_headers(),
        json={
                "grading_handle": final_payload["grading_handle"],
                "assessment": {
                    "request_id": "11111111-1111-4111-8111-111111111111",
                    "rubric_snapshot_id": resolved["snapshot_id"],
                    "rubric_source": "built_in_default",
                    "score": 0,
                    "max_score": resolved["total_max_score"],
                    "confidence": 0.8,
                    "feedback": "完成主要要求。",
                    "details": rubric_details,
                    "self_check": {"rubric_items_reviewed": ["默认 rubric"], "issues_found": ["无额外问题"], "changes_made": ["完成独立复核"], "second_pass_completed": True},
            },
        },
    )
    assert save.status_code == 200, save.text
    finalized = await client.post(
        f"/api/submissions/{submission.id}/finalize",
        json={
            "reviewer_name": "Teacher",
            "score": 0,
            "max_score": 100,
            "feedback": "完成主要要求。",
            "details": [
                {
                    "criterion": "Task 1",
                    "score": 0,
                    "max_score": 100,
                    "comment": "基本完成。",
                    "evidence": ["证据"],
                }
            ],
        },
    )
    assert finalized.status_code == 200
    assert finalized.json()["status"] == "reviewed"
    stale = await client.put(
        f"/api/mcp/submissions/{submission.id}/assessment-v2",
        headers=_headers(),
        json={"grading_handle": final_payload["grading_handle"], "assessment": {"request_id": "11111111-1111-4111-8111-111111111111", "rubric_snapshot_id": resolved["snapshot_id"], "rubric_source": "built_in_default", "score": 0, "max_score": resolved["total_max_score"], "confidence": 0.8, "feedback": "完成主要要求。", "details": rubric_details, "self_check": {"rubric_items_reviewed": [item["rubric_item_id"] for item in resolved["items"]], "second_pass_completed": True}}},
    )
    assert stale.status_code == 409


@pytest.mark.asyncio
async def test_package_hard_limit_returns_actionable_error(client, db_session, monkeypatch):
    monkeypatch.setattr(settings, "MCP_INTERNAL_TOKEN", "test-token")
    question = Question(
        name="小题",
        original_filename="q.pdf",
        file_path="/tmp/q.pdf",
        ocr_text="题目细则",
        status=QuestionStatus.ready,
    )
    submission = Submission(
        original_filename="answer.pdf",
        file_path="/tmp/a.pdf",
        question=question,
        ocr_text="学生内容",
        status=SubmissionStatus.awaiting_codex,
        grading_mode=SubmissionGradingMode.codex,
    )
    db_session.add(submission)
    db_session.commit()
    monkeypatch.setattr(settings, "MCP_MAX_GRADING_CONTEXT_CHARS", 1)

    response = await client.get(
        f"/api/mcp/submissions/{submission.id}/package", headers=_headers()
    )
    assert response.status_code == 422
    assert "拆分作业或缩减提交内容" in response.json()["detail"]
