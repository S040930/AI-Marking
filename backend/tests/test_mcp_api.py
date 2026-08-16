import json

import pytest

from app.api.mcp import _grading_policy
from app.core.config import settings
from app.models.question import Question, QuestionStatus
from app.models.submission import Submission, SubmissionGradingMode, SubmissionStatus
from app.models.submission_code_file import SubmissionCodeFile
from app.services.rubric import resolve_rubric


def _headers():
    return {"X-AI-Marking-MCP-Token": "test-token"}


def _configure_default_rubric(db_session) -> None:
    """为默认配置项目写入结构化 rubric,使评分包走 configured 分支而非 needs_rubric。"""
    from app.services.config import upsert_config

    upsert_config(
        db_session,
        {
            "rubric_definition": json.dumps(
                {
                    "items": [
                        {
                            "criterion": "Task 1",
                            "max_score": 100,
                            "details": "完成度",
                        }
                    ],
                    "total_max_score": 100,
                },
                ensure_ascii=False,
            )
        },
    )
    db_session.commit()


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
        status=SubmissionStatus.awaiting_mcp,
        grading_mode=SubmissionGradingMode.external_agent,
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
    # 配置 rubric,避免触发 v10 needs_rubric 提前返回
    _configure_default_rubric(db_session)
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
    for name in ("DTS208 CW1", "DTS208 CW2"):
        db_session.add(Question(name=name, original_filename=f"{name}.pdf", file_path="/tmp/q.pdf", ocr_text="text", status=QuestionStatus.ready))
    db_session.commit()
    response = await client.post("/api/mcp/submission-preflight", headers=_headers(), json={"question_name": "DTS208"})
    assert response.status_code == 200
    assert response.json()["status"] == "needs_question_choice"
    assert len(response.json()["candidates"]) == 2


@pytest.mark.asyncio
async def test_package_paginates_and_compact_save_enforces_handle(client, db_session, monkeypatch):
    question = Question(name="评分题", original_filename="q.pdf", file_path="/tmp/q.pdf", ocr_text="Task 1: 100 points", status=QuestionStatus.ready)
    _configure_default_rubric(db_session)
    submission = Submission(
        original_filename="answer.pdf",
        file_path="/tmp/a.pdf",
        question=question,
        ocr_text="证据 " * 25_000,
        status=SubmissionStatus.awaiting_mcp,
        grading_mode=SubmissionGradingMode.external_agent,
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
                "client": "claude-code",
                "assessment": {
                    "request_id": "11111111-1111-4111-8111-111111111111",
                    "rubric_snapshot_id": resolved["snapshot_id"],
                    "rubric_source": "configured",
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
    db_session.expire_all()
    saved = db_session.get(Submission, submission.id)
    assert saved.assessment_suggestion["mcp_metadata"]["source"] == "mcp"
    assert saved.assessment_suggestion["mcp_metadata"]["client"] == "claude-code"
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
        status=SubmissionStatus.awaiting_mcp,
        grading_mode=SubmissionGradingMode.external_agent,
    )
    db_session.add(submission)
    # 配置 rubric,确保评分包走上下文上限校验而非 needs_rubric 提前返回
    _configure_default_rubric(db_session)
    db_session.commit()
    monkeypatch.setattr(settings, "MCP_MAX_GRADING_CONTEXT_CHARS", 1)

    response = await client.get(
        f"/api/mcp/submissions/{submission.id}/package", headers=_headers()
    )
    assert response.status_code == 422
    assert "拆分作业或缩减提交内容" in response.json()["detail"]


@pytest.mark.asyncio
async def test_save_rejects_inconsistent_score_totals(client, db_session, monkeypatch):
    """C1: 总分超过满分或与明细之和不一致的评分建议在保存时被 422 拒绝。"""
    question = Question(
        name="评分题",
        original_filename="q.pdf",
        file_path="/tmp/q.pdf",
        ocr_text="Task 1: 100 points",
        status=QuestionStatus.ready,
    )
    submission = Submission(
        original_filename="answer.pdf",
        file_path="/tmp/a.pdf",
        question=question,
        ocr_text="学生内容",
        status=SubmissionStatus.awaiting_mcp,
        grading_mode=SubmissionGradingMode.external_agent,
    )
    db_session.add(submission)
    # 配置 rubric,避免触发 v10 needs_rubric 提前返回
    _configure_default_rubric(db_session)
    db_session.commit()

    pkg = await client.get(
        f"/api/mcp/submissions/{submission.id}/package", headers=_headers()
    )
    payload = pkg.json()
    import json

    header = json.loads(
        payload["content"].split("\n", 1)[1].split("\n\n", 1)[0]
    )
    resolved = header["grading_policy"]["resolved_rubric"]
    total = resolved["total_max_score"]

    def _request(score: float, detail_scores: list[float]):
        return {
            "grading_handle": payload["grading_handle"],
            "assessment": {
                "request_id": "22222222-2222-4222-8222-222222222222",
                "rubric_snapshot_id": resolved["snapshot_id"],
                "rubric_source": "configured",
                "score": score,
                "max_score": total,
                "confidence": 0.8,
                "feedback": "完成主要要求。",
                "details": [
                    {
                        "rubric_item_id": item["rubric_item_id"],
                        "criterion": item["criterion"],
                        "score": s,
                        "max_score": item["max_score"],
                        "comment": "待改进。",
                        "evidence": ["学生内容"],
                    }
                    for item, s in zip(resolved["items"], detail_scores, strict=True)
                ],
                "self_check": {
                    "rubric_items_reviewed": [item["rubric_item_id"] for item in resolved["items"]],
                    "second_pass_completed": True,
                },
            },
        }

    items = resolved["items"]
    item_maxes = [item["max_score"] for item in items]

    # 总分超过满分
    over = await client.put(
        f"/api/mcp/submissions/{submission.id}/assessment-v2",
        headers=_headers(),
        json=_request(total + 20, item_maxes),
    )
    assert over.status_code == 422, over.text

    # 总分与明细之和不一致
    mismatch = await client.put(
        f"/api/mcp/submissions/{submission.id}/assessment-v2",
        headers=_headers(),
        json=_request(total - 10, item_maxes),
    )
    assert mismatch.status_code == 422, mismatch.text

    # 自洽的总分能正常保存
    ok = await client.put(
        f"/api/mcp/submissions/{submission.id}/assessment-v2",
        headers=_headers(),
        json=_request(total, item_maxes),
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["grading_revision"] == 1


# ---------- MCP v10:待办列表 / needs_rubric / 保存题目 rubric ----------


@pytest.mark.asyncio
async def test_pending_assignments_lists_only_awaiting_mcp(client, db_session):
    """待办列表只返回 awaiting_mcp 的作业，并按上传时间与 ID 升序分页。"""
    from datetime import timedelta

    from app.core.time import utc_now_naive

    q = Question(
        name="待办题", original_filename="q.pdf", file_path="/tmp/q.pdf",
        ocr_text="Task 1: 100 points", status=QuestionStatus.ready,
    )
    ready = Submission(
        original_filename="ready.pdf", file_path="/tmp/r.pdf",
        question=q, status=SubmissionStatus.ready_for_review,
    )
    waiting_a = Submission(
        original_filename="a.pdf", file_path="/tmp/a.pdf",
        question=q, status=SubmissionStatus.awaiting_mcp,
        uploaded_at=utc_now_naive() - timedelta(hours=3),
    )
    waiting_b = Submission(
        original_filename="b.pdf", file_path="/tmp/b.pdf",
        question=q, status=SubmissionStatus.awaiting_mcp,
        uploaded_at=utc_now_naive() - timedelta(hours=2),
    )
    waiting_c = Submission(
        original_filename="c.pdf", file_path="/tmp/c.pdf",
        question=q, status=SubmissionStatus.awaiting_mcp,
        uploaded_at=utc_now_naive() - timedelta(hours=1),
    )
    db_session.add_all([ready, waiting_a, waiting_b, waiting_c])
    db_session.commit()

    first = await client.get(
        "/api/mcp/pending-assignments",
        params={"limit": 1},
        headers=_headers(),
    )
    assert first.status_code == 200
    body = first.json()
    assert [item["submission_id"] for item in body["items"]] == [waiting_a.id]
    assert body["next_cursor"] is not None

    second = await client.get(
        "/api/mcp/pending-assignments",
        params={"cursor": body["next_cursor"], "limit": 1},
        headers=_headers(),
    )
    assert second.status_code == 200
    assert [item["submission_id"] for item in second.json()["items"]] == [
        waiting_b.id
    ]
    assert second.json()["next_cursor"] is not None

    third = await client.get(
        "/api/mcp/pending-assignments",
        params={"cursor": second.json()["next_cursor"], "limit": 1},
        headers=_headers(),
    )
    assert [item["submission_id"] for item in third.json()["items"]] == [
        waiting_c.id
    ]
    assert third.json()["next_cursor"] is None


@pytest.mark.asyncio
async def test_pending_assignments_rejects_invalid_cursor(client, db_session):
    response = await client.get(
        "/api/mcp/pending-assignments",
        params={"cursor": "not-a-valid-cursor"},
        headers=_headers(),
    )
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_package_returns_needs_rubric_with_single_use_handle(
    client, db_session
):
    """无配置 rubric 且无题目提取快照时,评分包返回 needs_rubric 与提取句柄。

    同一题目只允许一个有效提取句柄;再次打开会撤销旧句柄。
    """
    question = Question(
        name="无 rubric 题",
        original_filename="q.pdf",
        file_path="/tmp/q.pdf",
        ocr_text="内容理解 60分\n论证分析 40分",
        status=QuestionStatus.ready,
    )
    submission = Submission(
        original_filename="answer.pdf",
        file_path="/tmp/a.pdf",
        question=question,
        ocr_text="学生内容",
        status=SubmissionStatus.awaiting_mcp,
    )
    db_session.add(submission)
    db_session.commit()

    first = await client.get(
        f"/api/mcp/submissions/{submission.id}/package", headers=_headers()
    )
    assert first.status_code == 200
    payload = first.json()
    assert payload["needs_rubric"] is True
    assert payload["question_id"] == question.id
    assert payload["question_ocr_text"] == question.ocr_text
    assert payload["rubric_handle"]
    assert payload["content"] is None

    # 再次打开:旧提取句柄被撤销,返回新句柄
    second = await client.get(
        f"/api/mcp/submissions/{submission.id}/package", headers=_headers()
    )
    assert second.json()["needs_rubric"] is True
    assert second.json()["rubric_handle"] != payload["rubric_handle"]

    # 旧句柄已失效
    stale = await client.put(
        f"/api/mcp/questions/{question.id}/rubric",
        headers=_headers(),
        json={
            "handle": payload["rubric_handle"],
            "status": "absent_or_ambiguous",
            "items": [],
            "total_max_score": None,
        },
    )
    assert stale.status_code == 409


@pytest.mark.asyncio
async def test_save_question_rubric_complete_persists_authoritative_snapshot(
    client, db_session
):
    """客户端提取的完整 rubric 经服务端校验后写入题目级权威快照。"""
    question = Question(
        name="期末作文",
        original_filename="q.pdf",
        file_path="/tmp/q.pdf",
        ocr_text="内容理解 60分\n论证分析 40分\n总分100分",
        status=QuestionStatus.ready,
    )
    submission = Submission(
        original_filename="answer.pdf",
        file_path="/tmp/a.pdf",
        question=question,
        ocr_text="学生内容",
        status=SubmissionStatus.awaiting_mcp,
    )
    db_session.add(submission)
    db_session.commit()

    pkg = await client.get(
        f"/api/mcp/submissions/{submission.id}/package", headers=_headers()
    )
    handle = pkg.json()["rubric_handle"]

    response = await client.put(
        f"/api/mcp/questions/{question.id}/rubric",
        headers=_headers(),
        json={
            "handle": handle,
            "status": "complete",
            "items": [
                {
                    "criterion": "内容理解",
                    "max_score": 60,
                    "details": "准确理解题目要求",
                    "source_quote": "内容理解 60分",
                },
                {
                    "criterion": "论证分析",
                    "max_score": 40,
                    "details": "论证清晰有证据",
                    "source_quote": "论证分析 40分",
                },
            ],
            "total_max_score": 100,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "complete"
    assert body["question_id"] == question.id
    assert body["rubric_snapshot_id"]

    db_session.refresh(question)
    assert question.extracted_rubric is not None
    assert question.extracted_rubric_items is not None
    assert len(question.extracted_rubric_items) == 2

    # 保存后再次打开:不再 needs_rubric,评分包使用题目提取快照
    again = await client.get(
        f"/api/mcp/submissions/{submission.id}/package", headers=_headers()
    )
    assert again.json()["needs_rubric"] is False
    content = again.json()["content"]
    assert body["rubric_snapshot_id"] in content


@pytest.mark.asyncio
async def test_save_question_rubric_rejects_invalid_quote(client, db_session):
    """source_quote 不是 OCR 原文子串或未含满分时,服务端确定性校验拒绝写入。"""
    question = Question(
        name="作文题",
        original_filename="q.pdf",
        file_path="/tmp/q.pdf",
        ocr_text="内容理解 60分",
        status=QuestionStatus.ready,
    )
    submission = Submission(
        original_filename="answer.pdf",
        file_path="/tmp/a.pdf",
        question=question,
        ocr_text="学生内容",
        status=SubmissionStatus.awaiting_mcp,
    )
    db_session.add(submission)
    db_session.commit()

    pkg = await client.get(
        f"/api/mcp/submissions/{submission.id}/package", headers=_headers()
    )
    handle = pkg.json()["rubric_handle"]

    response = await client.put(
        f"/api/mcp/questions/{question.id}/rubric",
        headers=_headers(),
        json={
            "handle": handle,
            "status": "complete",
            "items": [
                {
                    "criterion": "内容理解",
                    "max_score": 60,
                    "details": "准确理解题目要求",
                    "source_quote": "杜撰的引用文本",
                }
            ],
            "total_max_score": 60,
        },
    )
    assert response.status_code == 422
    db_session.refresh(question)
    assert question.extracted_rubric is None
    assert question.extracted_rubric_items is None


@pytest.mark.asyncio
async def test_save_question_rubric_absent_avoids_repeat_extraction(
    client, db_session
):
    """题目无明确 rubric 时持久化 absent_or_ambiguous,后续不再询问客户端。"""
    question = Question(
        name="无标准题",
        original_filename="q.pdf",
        file_path="/tmp/q.pdf",
        ocr_text="请完成课程设计",
        status=QuestionStatus.ready,
    )
    submission = Submission(
        original_filename="answer.pdf",
        file_path="/tmp/a.pdf",
        question=question,
        ocr_text="学生内容",
        status=SubmissionStatus.awaiting_mcp,
    )
    db_session.add(submission)
    db_session.commit()

    pkg = await client.get(
        f"/api/mcp/submissions/{submission.id}/package", headers=_headers()
    )
    handle = pkg.json()["rubric_handle"]

    response = await client.put(
        f"/api/mcp/questions/{question.id}/rubric",
        headers=_headers(),
        json={"handle": handle, "status": "absent_or_ambiguous"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "absent_or_ambiguous"
    assert response.json()["rubric_snapshot_id"] is None

    db_session.refresh(question)
    assert question.extracted_rubric is None
    assert question.extracted_rubric_version is not None

    # 再次打开直接走内置默认 rubric,不再 needs_rubric
    again = await client.get(
        f"/api/mcp/submissions/{submission.id}/package", headers=_headers()
    )
    assert again.json()["needs_rubric"] is False
    assert "built_in_default" in again.json()["content"]


@pytest.mark.asyncio
async def test_save_question_rubric_rejects_after_ocr_change(client, db_session):
    """题目 OCR 变化后旧提取句柄失效,拒绝保存。"""
    question = Question(
        name="替换题",
        original_filename="q.pdf",
        file_path="/tmp/q.pdf",
        ocr_text="内容理解 60分",
        status=QuestionStatus.ready,
    )
    submission = Submission(
        original_filename="answer.pdf",
        file_path="/tmp/a.pdf",
        question=question,
        ocr_text="学生内容",
        status=SubmissionStatus.awaiting_mcp,
    )
    db_session.add(submission)
    db_session.commit()

    pkg = await client.get(
        f"/api/mcp/submissions/{submission.id}/package", headers=_headers()
    )
    handle = pkg.json()["rubric_handle"]

    # 模拟题目 OCR 在提取期间变化(共享 session,ORM 变更即被后续请求读取)
    db_session.get(Question, question.id).ocr_text = "新的题目内容 80分"
    db_session.commit()

    response = await client.put(
        f"/api/mcp/questions/{question.id}/rubric",
        headers=_headers(),
        json={"handle": handle, "status": "absent_or_ambiguous"},
    )
    assert response.status_code == 409
