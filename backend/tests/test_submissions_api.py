"""Submission 接口测试:上传校验、列表、详情。"""

import logging
from pathlib import Path

import pytest
from sqlalchemy import select, text

from app.core.time import utc_now_naive
from app.models.conversation import Conversation
from app.models.submission import Submission, SubmissionStatus


def test_database_timestamp_is_naive_utc():
    """数据库使用无时区时间列，应用生成的 UTC 时间必须不带 tzinfo。"""
    assert utc_now_naive().tzinfo is None


async def test_upload_non_pdf_returns_400(client):
    """POST /api/submissions 任一文件非 PDF 返回 400。"""
    response = await client.post(
        "/api/submissions",
        files=[
            ("file", ("test.txt", b"hello", "text/plain")),
        ],
        data={"question_id": "1"},
    )
    assert response.status_code == 400


async def test_get_nonexistent_submission_returns_404(client):
    """GET /api/submissions/999 不存在的 ID 返回 404。"""
    response = await client.get("/api/submissions/999")
    assert response.status_code == 404


async def test_get_submission_list_empty(client):
    """GET /api/submissions 空列表返回 200。"""
    response = await client.get("/api/submissions")
    assert response.status_code == 200
    assert response.json() == {"items": [], "total": 0, "skip": 0, "limit": 10}


async def test_get_submission_list_includes_ai_suggestion(client, db_session):
    """列表精简查询应预加载 AI 建议，避免响应序列化触发异步懒加载。"""
    suggestion = {
        "score": 88,
        "max_score": 100,
        "confidence": 0.9,
        "feedback": "整体良好",
        "details": [],
    }
    sub = Submission(
        original_filename="suggestion.pdf",
        file_path="/tmp/suggestion.pdf",
        status=SubmissionStatus.ready_for_review,
        ai_suggestion=suggestion,
    )
    db_session.add(sub)
    await db_session.commit()

    response = await client.get(
        "/api/submissions?skip=0&limit=10&include_count=false"
    )

    assert response.status_code == 200
    assert response.json()["items"][0]["ai_suggestion"] == suggestion


@pytest.mark.parametrize(
    "deletable_status",
    [
        SubmissionStatus.ready_for_review,
        SubmissionStatus.reviewed,
        SubmissionStatus.failed,
    ],
)
async def test_batch_delete_accepts_terminal_statuses(
    client, db_session, deletable_status
):
    """三个终态均允许删除。"""
    sub = Submission(
        original_filename="terminal.pdf",
        file_path="/tmp/nonexistent-terminal.pdf",
        status=deletable_status,
    )
    db_session.add(sub)
    await db_session.commit()
    sub_id = sub.id

    response = await client.request(
        "DELETE", "/api/submissions", json={"ids": [sub_id]}
    )

    assert response.status_code == 200
    assert response.json() == {"deleted_count": 1}
    assert await db_session.get(Submission, sub_id) is None


@pytest.mark.parametrize(
    "processing_status",
    [
        SubmissionStatus.pending,
        SubmissionStatus.ocr_processing,
        SubmissionStatus.ocr_done,
        SubmissionStatus.agent_grading,
        SubmissionStatus.agent_reviewing,
        SubmissionStatus.agent_revising,
    ],
)
async def test_batch_delete_rejects_processing_status(
    client, db_session, processing_status
):
    """任一处理中状态都必须拒绝删除。"""
    sub = Submission(
        original_filename="processing.pdf",
        file_path="/tmp/nonexistent-processing.pdf",
        status=processing_status,
    )
    db_session.add(sub)
    await db_session.commit()

    response = await client.request(
        "DELETE", "/api/submissions", json={"ids": [sub.id]}
    )

    assert response.status_code == 409
    assert response.json()["detail"] == {
        "message": "正在处理的记录不可删除，请等待批改完成后重试",
        "blocked_ids": [sub.id],
    }
    assert await db_session.get(Submission, sub.id) is not None


async def test_batch_delete_removes_database_conversations_and_pdfs(
    client, db_session, tmp_path
):
    """删除成功后清理主记录、关联对话和学生 PDF，共享题目保留。"""
    await db_session.execute(text("PRAGMA foreign_keys=ON"))
    submission_pdf = tmp_path / "submission.pdf"
    question_pdf = tmp_path / "question.pdf"
    submission_pdf.write_bytes(b"%PDF-1.4")
    question_pdf.write_bytes(b"%PDF-1.4")
    sub = Submission(
        original_filename="submission.pdf",
        file_path=str(submission_pdf),
        question_original_filename="question.pdf",
        question_file_path=str(question_pdf),
        status=SubmissionStatus.reviewed,
    )
    db_session.add(sub)
    await db_session.flush()
    conversation = Conversation(
        submission_id=sub.id,
        role="user",
        content="请复核",
    )
    db_session.add(conversation)
    await db_session.commit()
    sub_id = sub.id

    response = await client.request(
        "DELETE", "/api/submissions", json={"ids": [sub_id]}
    )

    assert response.status_code == 200
    assert await db_session.get(Submission, sub_id) is None
    conversations = (
        await db_session.execute(
            select(Conversation).where(Conversation.submission_id == sub_id)
        )
    ).scalars().all()
    assert conversations == []
    assert not submission_pdf.exists()
    assert question_pdf.exists()


async def test_batch_delete_is_atomic_when_request_contains_processing_record(
    client, db_session, tmp_path
):
    """终态与处理中记录混合删除时整批拒绝，记录和文件均保留。"""
    terminal_pdf = tmp_path / "terminal.pdf"
    terminal_pdf.write_bytes(b"%PDF-1.4")
    terminal = Submission(
        original_filename="terminal.pdf",
        file_path=str(terminal_pdf),
        status=SubmissionStatus.reviewed,
    )
    processing = Submission(
        original_filename="processing.pdf",
        file_path=str(tmp_path / "processing.pdf"),
        status=SubmissionStatus.agent_grading,
    )
    db_session.add_all([terminal, processing])
    await db_session.commit()

    response = await client.request(
        "DELETE",
        "/api/submissions",
        json={"ids": [terminal.id, processing.id]},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["blocked_ids"] == [processing.id]
    assert await db_session.get(Submission, terminal.id) is not None
    assert await db_session.get(Submission, processing.id) is not None
    assert terminal_pdf.exists()


async def test_batch_delete_ignores_missing_and_duplicate_ids(client, db_session):
    """不存在和重复 ID 安全处理，deleted_count 只统计真实唯一记录。"""
    sub = Submission(
        original_filename="one.pdf",
        file_path="/tmp/already-missing.pdf",
        status=SubmissionStatus.failed,
    )
    db_session.add(sub)
    await db_session.commit()

    response = await client.request(
        "DELETE",
        "/api/submissions",
        json={"ids": [sub.id, sub.id, 999999]},
    )

    assert response.status_code == 200
    assert response.json() == {"deleted_count": 1}


async def test_batch_delete_keeps_pdfs_when_database_commit_fails(
    client, db_session, tmp_path, monkeypatch
):
    """数据库事务失败时不得提前删除 PDF。"""
    submission_pdf = tmp_path / "commit-failure-submission.pdf"
    question_pdf = tmp_path / "commit-failure-question.pdf"
    submission_pdf.write_bytes(b"%PDF-1.4")
    question_pdf.write_bytes(b"%PDF-1.4")
    sub = Submission(
        original_filename="submission.pdf",
        file_path=str(submission_pdf),
        question_file_path=str(question_pdf),
        status=SubmissionStatus.reviewed,
    )
    db_session.add(sub)
    await db_session.commit()

    async def fail_commit():
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(db_session, "commit", fail_commit)
    with pytest.raises(RuntimeError, match="database unavailable"):
        await client.request(
            "DELETE", "/api/submissions", json={"ids": [sub.id]}
        )

    assert submission_pdf.exists()
    assert question_pdf.exists()


async def test_batch_delete_logs_pdf_cleanup_failure_after_database_commit(
    client, db_session, tmp_path, monkeypatch, caplog
):
    """文件清理失败不回滚数据库删除，但必须留下 warning 日志。"""
    submission_pdf = tmp_path / "cleanup-failure.pdf"
    submission_pdf.write_bytes(b"%PDF-1.4")
    sub = Submission(
        original_filename="cleanup-failure.pdf",
        file_path=str(submission_pdf),
        status=SubmissionStatus.failed,
    )
    db_session.add(sub)
    await db_session.commit()
    sub_id = sub.id

    def fail_unlink(self: Path, missing_ok: bool = False):
        raise PermissionError("permission denied")

    monkeypatch.setattr(Path, "unlink", fail_unlink)
    with caplog.at_level(logging.WARNING, logger="app.api.submissions"):
        response = await client.request(
            "DELETE", "/api/submissions", json={"ids": [sub_id]}
        )

    assert response.status_code == 200
    assert await db_session.get(Submission, sub_id) is None
    assert "删除 submission PDF 失败" in caplog.text
    assert submission_pdf.exists()


async def test_get_submission_detail_with_list_details(client, db_session):
    """回归测试:details 为数组类型时能正确序列化(Bug 1)。"""
    sub = Submission(
        original_filename="test.pdf",
        file_path="/tmp/test.pdf",
        status=SubmissionStatus.reviewed,
        ocr_text="作业内容",
        score=85.0,
        feedback="整体不错",
        details=[
            {"criterion": "内容理解", "score": 25, "comment": "理解准确"},
            {"criterion": "论证分析", "score": 28, "comment": "逻辑清晰"},
        ],
    )
    db_session.add(sub)
    await db_session.commit()
    await db_session.refresh(sub)

    response = await client.get(f"/api/submissions/{sub.id}")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data["details"], list)
    assert len(data["details"]) == 2
    assert data["details"][0]["criterion"] == "内容理解"
    assert "file_path" not in data  # Bug 2: file_path 不应泄露


async def test_teacher_can_finalize_submission(client, db_session):
    """教师通过 /finalize 接口提交最终评分,状态变为 reviewed。"""
    sub = Submission(
        original_filename="review.pdf",
        file_path="/tmp/review.pdf",
        status=SubmissionStatus.ready_for_review,
        score=70,
        max_score=100,
        feedback="AI 反馈",
        details=[],
        ai_result={"score": 70, "max_score": 100},
    )
    db_session.add(sub)
    await db_session.commit()
    await db_session.refresh(sub)

    response = await client.post(
        f"/api/submissions/{sub.id}/finalize",
        json={
            "reviewer_name": "Dr Chen",
            "score": 85,
            "max_score": 100,
            "feedback": "教师确认后的反馈",
            "details": [
                {
                    "criterion": "内容",
                    "score": 85,
                    "max_score": 100,
                    "comment": "内容完整",
                    "evidence": ["原文证据"],
                }
            ],
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "reviewed"
    assert data["score"] == 85
    assert data["reviewed_by"] == "Dr Chen"
    assert data["ai_result"]["score"] == 70


async def test_finalize_rejects_already_reviewed(client, db_session):
    """对已 reviewed 的作业调用 finalize 返回 409。"""
    sub = Submission(
        original_filename="done.pdf",
        file_path="/tmp/done.pdf",
        status=SubmissionStatus.reviewed,
    )
    db_session.add(sub)
    await db_session.commit()
    await db_session.refresh(sub)

    response = await client.post(
        f"/api/submissions/{sub.id}/finalize",
        json={
            "reviewer_name": "Dr Chen",
            "score": 85,
            "max_score": 100,
            "feedback": "反馈",
            "details": [
                {
                    "criterion": "内容",
                    "score": 85,
                    "max_score": 100,
                    "comment": "内容完整",
                    "evidence": [],
                }
            ],
        },
    )
    assert response.status_code == 409
