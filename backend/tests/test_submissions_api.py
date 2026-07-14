"""Submission 接口测试:上传校验、列表、详情。"""

import logging
from pathlib import Path

import pytest
from sqlalchemy import select, text

from app.core.time import utc_now_naive
from app.models.background_job import BackgroundJob, BackgroundJobStatus
from app.models.conversation import Conversation
from app.models.question import (
    Question,
    QuestionReplacementStatus,
    QuestionStatus,
)
from app.models.submission import Submission, SubmissionStatus


def test_database_timestamp_is_naive_utc():
    """数据库使用无时区时间列，应用生成的 UTC 时间必须不带 tzinfo。"""
    assert utc_now_naive().tzinfo is None


def _ready_question(db_session, name: str = "测试题目") -> Question:
    """显式创建可用于批改的题目，避免测试绕过真实 question_id 约束。"""
    question = Question(
        name=name,
        original_filename="question.pdf",
        file_path="/tmp/question.pdf",
        ocr_text="测试题目内容",
        status=QuestionStatus.ready,
    )
    db_session.add(question)
    return question


async def test_upload_unsupported_document_returns_422(client):
    """POST /api/submissions 拒绝 PDF/DOCX 之外的文件。"""
    response = await client.post(
        "/api/submissions",
        files=[
            ("file", ("test.txt", b"hello", "text/plain")),
        ],
        data={"question_id": "1"},
    )
    assert response.status_code == 422


async def test_get_nonexistent_submission_returns_404(client):
    """GET /api/submissions/999 不存在的 ID 返回 404。"""
    response = await client.get("/api/submissions/999")
    assert response.status_code == 404


async def test_docx_source_preview_is_served_as_pdf(
    client, db_session, tmp_path
):
    question_path = tmp_path / "question.pdf"
    question_path.write_bytes(b"%PDF-question")
    question = Question(
        name="DOCX 题目",
        original_filename="question.docx",
        file_path=str(question_path),
        ocr_text="题目",
        status=QuestionStatus.ready,
    )
    submission_path = tmp_path / "answer.pdf"
    submission_path.write_bytes(b"%PDF-answer")
    submission = Submission(
        original_filename="answer.docx",
        file_path=str(submission_path),
        question=question,
        status=SubmissionStatus.failed,
    )
    db_session.add(submission)
    db_session.commit()

    answer = await client.get(f"/api/submissions/{submission.id}/pdf")
    question_preview = await client.get(
        f"/api/submissions/{submission.id}/pdf?type=question"
    )

    assert answer.headers["content-type"] == "application/pdf"
    assert 'filename="answer.pdf"' in answer.headers["content-disposition"]
    assert question_preview.headers["content-type"] == "application/pdf"
    assert 'filename="question.pdf"' in question_preview.headers[
        "content-disposition"
    ]


async def test_failed_submission_retries_in_same_record(
    client, db_session, tmp_path
):
    path = tmp_path / "failed.pdf"
    path.write_bytes(b"%PDF-old")
    sub = Submission(
        original_filename="failed.pdf",
        file_path=str(path),
        question=_ready_question(db_session),
        status=SubmissionStatus.failed,
        ocr_text="旧 OCR",
        score=10,
        max_score=20,
        feedback="旧反馈",
        ai_result={"score": 10},
        ai_suggestion={"score": 10},
        error_message="OCR 失败",
    )
    db_session.add(sub)
    db_session.flush()
    db_session.add(
        Conversation(submission_id=sub.id, role="user", content="旧对话")
    )
    db_session.commit()

    response = await client.post(f"/api/submissions/{sub.id}/retry")
    assert response.status_code == 200
    assert response.json() == {"id": sub.id, "status": "pending"}
    db_session.refresh(sub)
    assert sub.ocr_text is None
    assert sub.score is None
    assert sub.ai_result is None
    assert sub.error_message is None
    assert path.exists()
    conversations = (
        db_session.execute(
            select(Conversation).where(Conversation.submission_id == sub.id)
        )
    ).scalars().all()
    assert conversations == []
    job = (
        db_session.execute(
            select(BackgroundJob).where(
                BackgroundJob.submission_id == sub.id
            )
        )
    ).scalar_one()
    assert job.status == BackgroundJobStatus.queued


async def test_failed_submission_can_replace_pdf_on_retry(
    client, db_session, tmp_path, monkeypatch
):
    old_path = tmp_path / "old.pdf"
    old_path.write_bytes(b"%PDF-old")
    new_path = tmp_path / "new-saved.pdf"
    new_path.write_bytes(b"%PDF-new")
    sub = Submission(
        original_filename="old.pdf",
        file_path=str(old_path),
        question=_ready_question(db_session),
        status=SubmissionStatus.failed,
    )
    db_session.add(sub)
    db_session.commit()

    async def fake_save(file, upload_dir, suffix=""):
        await file.close()
        return "new.pdf", new_path

    monkeypatch.setattr("app.api.submissions.save_document_as_pdf", fake_save)
    response = await client.post(
        f"/api/submissions/{sub.id}/retry",
        files={"file": ("new.pdf", b"%PDF-new", "application/pdf")},
    )
    assert response.status_code == 200
    db_session.refresh(sub)
    assert sub.original_filename == "new.pdf"
    assert sub.file_path == str(new_path)
    assert not old_path.exists()


async def test_failed_submission_retry_requires_available_pdf(
    client, db_session, tmp_path
):
    sub = Submission(
        original_filename="missing.pdf",
        file_path=str(tmp_path / "missing.pdf"),
        question=_ready_question(db_session),
        status=SubmissionStatus.failed,
    )
    db_session.add(sub)
    db_session.commit()
    response = await client.post(f"/api/submissions/{sub.id}/retry")
    assert response.status_code == 409
    assert "重新选择 PDF" in response.json()["detail"]


async def test_failed_submission_retry_rejects_frozen_question(
    client, db_session, tmp_path
):
    question = Question(
        name="冻结题目",
        original_filename="q.pdf",
        file_path=str(tmp_path / "q.pdf"),
        ocr_text="题目",
        status=QuestionStatus.ready,
        replacement_status=QuestionReplacementStatus.pending,
        replacement_file_path=str(tmp_path / "new-q.pdf"),
        replacement_original_filename="new-q.pdf",
    )
    path = tmp_path / "student.pdf"
    path.write_bytes(b"%PDF")
    sub = Submission(
        original_filename="student.pdf",
        file_path=str(path),
        question=question,
        status=SubmissionStatus.failed,
    )
    db_session.add(sub)
    db_session.commit()
    response = await client.post(f"/api/submissions/{sub.id}/retry")
    assert response.status_code == 409
    assert "新版正在处理中" in response.json()["detail"]


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
        question=_ready_question(db_session),
        status=SubmissionStatus.ready_for_review,
        ai_suggestion=suggestion,
    )
    db_session.add(sub)
    db_session.commit()

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
        question=_ready_question(db_session),
        status=deletable_status,
    )
    db_session.add(sub)
    db_session.commit()
    sub_id = sub.id

    response = await client.request(
        "DELETE", "/api/submissions", json={"ids": [sub_id]}
    )

    assert response.status_code == 200
    assert response.json() == {"deleted_count": 1}
    assert db_session.get(Submission, sub_id) is None


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
        question=_ready_question(db_session),
        status=processing_status,
    )
    db_session.add(sub)
    db_session.commit()

    response = await client.request(
        "DELETE", "/api/submissions", json={"ids": [sub.id]}
    )

    assert response.status_code == 409
    assert response.json()["detail"] == {
        "message": "正在处理的记录不可删除，请等待批改完成后重试",
        "blocked_ids": [sub.id],
    }
    assert db_session.get(Submission, sub.id) is not None


async def test_batch_delete_removes_database_conversations_and_pdfs(
    client, db_session, tmp_path
):
    """删除成功后清理主记录、关联对话和学生 PDF，共享题目保留。"""
    db_session.execute(text("PRAGMA foreign_keys=ON"))
    submission_pdf = tmp_path / "submission.pdf"
    question_pdf = tmp_path / "question.pdf"
    submission_pdf.write_bytes(b"%PDF-1.4")
    question_pdf.write_bytes(b"%PDF-1.4")
    question = Question(
        name="共享题目",
        original_filename="question.pdf",
        file_path=str(question_pdf),
        ocr_text="题目内容",
        status=QuestionStatus.ready,
    )
    db_session.add(question)
    db_session.flush()
    sub = Submission(
        original_filename="submission.pdf",
        file_path=str(submission_pdf),
        question=question,
        status=SubmissionStatus.reviewed,
    )
    db_session.add(sub)
    db_session.flush()
    conversation = Conversation(
        submission_id=sub.id,
        role="user",
        content="请复核",
    )
    db_session.add(conversation)
    db_session.commit()
    sub_id = sub.id

    response = await client.request(
        "DELETE", "/api/submissions", json={"ids": [sub_id]}
    )

    assert response.status_code == 200
    assert db_session.get(Submission, sub_id) is None
    conversations = (
        db_session.execute(
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
        question=_ready_question(db_session, "终态题目"),
        status=SubmissionStatus.reviewed,
    )
    processing = Submission(
        original_filename="processing.pdf",
        file_path=str(tmp_path / "processing.pdf"),
        question=_ready_question(db_session, "处理中题目"),
        status=SubmissionStatus.agent_grading,
    )
    db_session.add_all([terminal, processing])
    db_session.commit()

    response = await client.request(
        "DELETE",
        "/api/submissions",
        json={"ids": [terminal.id, processing.id]},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["blocked_ids"] == [processing.id]
    assert db_session.get(Submission, terminal.id) is not None
    assert db_session.get(Submission, processing.id) is not None
    assert terminal_pdf.exists()


async def test_batch_delete_ignores_missing_and_duplicate_ids(client, db_session):
    """不存在和重复 ID 安全处理，deleted_count 只统计真实唯一记录。"""
    sub = Submission(
        original_filename="one.pdf",
        file_path="/tmp/already-missing.pdf",
        question=_ready_question(db_session),
        status=SubmissionStatus.failed,
    )
    db_session.add(sub)
    db_session.commit()

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
    question = Question(
        name="共享题目",
        original_filename="question.pdf",
        file_path=str(question_pdf),
        ocr_text="题目内容",
        status=QuestionStatus.ready,
    )
    db_session.add(question)
    db_session.flush()
    sub = Submission(
        original_filename="submission.pdf",
        file_path=str(submission_pdf),
        question=question,
        status=SubmissionStatus.reviewed,
    )
    db_session.add(sub)
    db_session.commit()

    def fail_commit():
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
        question=_ready_question(db_session),
        status=SubmissionStatus.failed,
    )
    db_session.add(sub)
    db_session.commit()
    sub_id = sub.id

    def fail_unlink(self: Path, missing_ok: bool = False):
        raise PermissionError("permission denied")

    monkeypatch.setattr(Path, "unlink", fail_unlink)
    with caplog.at_level(logging.WARNING, logger="app.api.submissions"):
        response = await client.request(
            "DELETE", "/api/submissions", json={"ids": [sub_id]}
        )

    assert response.status_code == 200
    assert db_session.get(Submission, sub_id) is None
    assert "删除 submission PDF 失败" in caplog.text
    assert submission_pdf.exists()


async def test_get_submission_detail_with_list_details(client, db_session):
    """回归测试:details 为数组类型时能正确序列化(Bug 1)。"""
    sub = Submission(
        original_filename="test.pdf",
        file_path="/tmp/test.pdf",
        question=_ready_question(db_session),
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
    db_session.commit()
    db_session.refresh(sub)

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
        question=_ready_question(db_session),
        status=SubmissionStatus.ready_for_review,
        score=70,
        max_score=100,
        feedback="AI 反馈",
        details=[],
        ai_result={"score": 70, "max_score": 100},
    )
    db_session.add(sub)
    db_session.commit()
    db_session.refresh(sub)

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


async def test_chat_finalize_intent_waits_for_teacher_confirmation(
    client, db_session, monkeypatch
):
    sub = Submission(
        original_filename="chat-review.pdf",
        file_path="/tmp/chat-review.pdf",
        question=_ready_question(db_session),
        status=SubmissionStatus.ready_for_review,
        ocr_text="学生答案",
        ai_suggestion={
            "score": 70,
            "max_score": 100,
            "feedback": "初始反馈",
            "details": [],
            "confidence": 0.8,
        },
    )
    db_session.add(sub)
    db_session.commit()

    async def fake_chat(**kwargs):
        return {
            "reply": "请确认最终评分。",
            "intent": "finalize",
            "reviewer_name": "Dr Chen",
            "suggestion": {
                "score": 85,
                "max_score": 100,
                "confidence": 0.9,
                "feedback": "最终反馈",
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
        }

    monkeypatch.setattr(
        "app.api.submissions.chat_with_teacher", fake_chat
    )
    response = await client.post(
        f"/api/submissions/{sub.id}/chat",
        json={"message": "确认这个评分", "reviewer_name": "Dr Chen"},
    )
    assert response.status_code == 200
    assert response.json()["action"] == "finalize"
    assert response.json()["finalize_payload"]["score"] == 85
    db_session.refresh(sub)
    assert sub.status == SubmissionStatus.ready_for_review
    assert sub.score is None

    confirmed = await client.post(
        f"/api/submissions/{sub.id}/finalize",
        json=response.json()["finalize_payload"],
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "reviewed"


async def test_chat_finalize_rejects_invalid_suggestion_without_persisting(
    client, db_session, monkeypatch
):
    """finalize 意图必须有完整待确认 payload；无效快照不得写入半截对话。"""
    sub = Submission(
        original_filename="chat-invalid.pdf",
        file_path="/tmp/chat-invalid.pdf",
        question=_ready_question(db_session),
        status=SubmissionStatus.ready_for_review,
        ocr_text="学生答案",
        ai_suggestion={
            "score": 70,
            "max_score": 100,
            "feedback": "初始反馈",
            "details": [],
            "confidence": 0.8,
        },
    )
    db_session.add(sub)
    db_session.commit()

    async def fake_chat(**kwargs):
        return {
            "reply": "请确认最终评分。",
            "intent": "finalize",
            "reviewer_name": "Dr Chen",
            "suggestion": {"score": 85},
        }

    monkeypatch.setattr(
        "app.api.submissions.chat_with_teacher", fake_chat
    )
    response = await client.post(
        f"/api/submissions/{sub.id}/chat",
        json={"message": "确认这个评分", "reviewer_name": "Dr Chen"},
    )

    assert response.status_code == 422
    assert "最终评分格式非法" in response.json()["detail"]
    messages = (
        db_session.execute(
            select(Conversation).where(Conversation.submission_id == sub.id)
        )
    ).scalars().all()
    assert messages == []


async def test_chat_finalize_intent_downgraded_when_already_reviewed(
    client, db_session, monkeypatch
):
    """B5: 已 reviewed 状态下 AI 返回 finalize 意图时降级为 reply,
    且持久化的 assistant 消息不应再携带建议快照。"""
    sub = Submission(
        original_filename="chat-reviewed.pdf",
        file_path="/tmp/chat-reviewed.pdf",
        question=_ready_question(db_session),
        status=SubmissionStatus.reviewed,
        ocr_text="学生答案",
        score=80,
        max_score=100,
        reviewed_by="Dr Chen",
    )
    db_session.add(sub)
    db_session.commit()

    suggestion_raw = {
        "score": 85,
        "max_score": 100,
        "confidence": 0.9,
        "feedback": "最终反馈",
        "details": [
            {"criterion": "内容", "score": 85, "max_score": 100, "comment": "..."}
        ],
    }

    async def fake_chat(**kwargs):
        return {
            "reply": "已审阅完毕,无需再次确认。",
            "intent": "finalize",
            "reviewer_name": "Dr Chen",
            "suggestion": suggestion_raw,
        }

    monkeypatch.setattr("app.api.submissions.chat_with_teacher", fake_chat)
    response = await client.post(
        f"/api/submissions/{sub.id}/chat",
        json={"message": "确认这个评分", "reviewer_name": "Dr Chen"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["action"] == "reply"
    assert body["finalize_payload"] is None

    msgs = (
        db_session.execute(
            select(Conversation).where(Conversation.submission_id == sub.id)
        )
    ).scalars().all()
    assistant_msgs = [m for m in msgs if m.role == "assistant"]
    assert len(assistant_msgs) == 1
    assert assistant_msgs[0].suggestion is None


async def test_finalize_rejects_already_reviewed(client, db_session):
    """对已 reviewed 的作业调用 finalize 返回 409。"""
    sub = Submission(
        original_filename="done.pdf",
        file_path="/tmp/done.pdf",
        question=_ready_question(db_session),
        status=SubmissionStatus.reviewed,
    )
    db_session.add(sub)
    db_session.commit()
    db_session.refresh(sub)

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


async def test_submission_requires_question_id(tmp_path):
    """模型约束:缺少 question_id 的 submission 落库必须失败。

    使用独立内存库直接验证 question_id NOT NULL 约束真实生效。
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.db.base import Base

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    with session_factory() as s:
        s.add(
            Submission(
                original_filename="s.pdf",
                file_path="/tmp/s.pdf",
                status=SubmissionStatus.reviewed,
            )
        )
        with pytest.raises(Exception):
            s.flush()


async def test_upload_rejects_question_not_ready(
    client, db_session, tmp_path
):
    """题目未完成 OCR(非 ready)时,上传作业应被 409 拒绝。"""
    question = Question(
        name="未就绪题目",
        original_filename="q.pdf",
        file_path=str(tmp_path / "q.pdf"),
        status=QuestionStatus.pending,
    )
    db_session.add(question)
    db_session.commit()

    response = await client.post(
        "/api/submissions",
        files=[("file", ("h.pdf", b"%PDF-1.4\n", "application/pdf"))],
        data={"question_id": str(question.id)},
    )
    assert response.status_code == 409
