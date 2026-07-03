"""题目库接口的复用、删除与并发保护测试。"""

from sqlalchemy import select, text

from app.models.conversation import Conversation
from app.models.question import Question, QuestionStatus
from app.models.submission import Submission, SubmissionStatus


async def _question(db_session, tmp_path, *, name="期末作文"):
    path = tmp_path / f"{name}.pdf"
    path.write_bytes(b"%PDF-1.4")
    question = Question(
        name=name,
        original_filename=path.name,
        file_path=str(path),
        ocr_text="题目要求",
        status=QuestionStatus.ready,
    )
    db_session.add(question)
    await db_session.commit()
    return question, path


async def test_list_search_rename_and_preview_question(client, db_session, tmp_path):
    question, _ = await _question(db_session, tmp_path)

    response = await client.get("/api/questions", params={"search": "期末"})
    assert response.status_code == 200
    assert response.json()["items"][0]["submission_count"] == 0

    renamed = await client.patch(
        f"/api/questions/{question.id}", json={"name": "新版作文"}
    )
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "新版作文"

    preview = await client.get(f"/api/questions/{question.id}/pdf")
    assert preview.status_code == 200
    assert preview.headers["content-type"] == "application/pdf"


async def test_submission_delete_keeps_shared_question_pdf(
    client, db_session, tmp_path
):
    question, question_path = await _question(db_session, tmp_path)
    student_path = tmp_path / "student.pdf"
    student_path.write_bytes(b"%PDF-1.4")
    sub = Submission(
        original_filename="student.pdf",
        file_path=str(student_path),
        question=question,
        status=SubmissionStatus.reviewed,
    )
    db_session.add(sub)
    await db_session.commit()

    response = await client.request(
        "DELETE", "/api/submissions", json={"ids": [sub.id]}
    )
    assert response.status_code == 200
    assert question_path.exists()
    assert await db_session.get(Question, question.id) is not None


async def test_delete_question_cascades_terminal_records_and_files(
    client, db_session, tmp_path
):
    await db_session.execute(text("PRAGMA foreign_keys=ON"))
    question, question_path = await _question(db_session, tmp_path)
    student_path = tmp_path / "student.pdf"
    student_path.write_bytes(b"%PDF-1.4")
    sub = Submission(
        original_filename="student.pdf",
        file_path=str(student_path),
        question=question,
        status=SubmissionStatus.failed,
    )
    db_session.add(sub)
    await db_session.flush()
    db_session.add(
        Conversation(submission_id=sub.id, role="user", content="复核")
    )
    await db_session.commit()
    sub_id = sub.id

    response = await client.request(
        "DELETE",
        f"/api/questions/{question.id}",
        json={"confirmation_name": question.name},
    )
    assert response.status_code == 200
    assert response.json() == {"deleted_submission_count": 1}
    assert await db_session.get(Question, question.id) is None
    assert await db_session.get(Submission, sub_id) is None
    conversations = (
        await db_session.execute(
            select(Conversation).where(Conversation.submission_id == sub_id)
        )
    ).scalars().all()
    assert conversations == []
    assert not question_path.exists()
    assert not student_path.exists()


async def test_delete_question_rejects_processing_submission(
    client, db_session, tmp_path
):
    question, question_path = await _question(db_session, tmp_path)
    sub = Submission(
        original_filename="student.pdf",
        file_path=str(tmp_path / "student.pdf"),
        question=question,
        status=SubmissionStatus.agent_grading,
    )
    db_session.add(sub)
    await db_session.commit()

    response = await client.request(
        "DELETE",
        f"/api/questions/{question.id}",
        json={"confirmation_name": question.name},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["blocked_submission_ids"] == [sub.id]
    assert await db_session.get(Question, question.id) is not None
    assert question_path.exists()


async def test_delete_question_requires_exact_name(client, db_session, tmp_path):
    question, path = await _question(db_session, tmp_path)
    response = await client.request(
        "DELETE",
        f"/api/questions/{question.id}",
        json={"confirmation_name": "错误名称"},
    )
    assert response.status_code == 422
    assert path.exists()


async def test_replace_question_ocr_first_then_removes_old_records(
    client, db_session, tmp_path, monkeypatch
):
    question, old_question_path = await _question(db_session, tmp_path)
    student_path = tmp_path / "old-student.pdf"
    student_path.write_bytes(b"%PDF-1.4")
    sub = Submission(
        original_filename="old-student.pdf",
        file_path=str(student_path),
        question=question,
        status=SubmissionStatus.reviewed,
    )
    db_session.add(sub)
    await db_session.commit()
    sub_id = sub.id

    async def fake_ocr(file_path, api_url, token):
        return "新版题目 OCR"

    monkeypatch.setattr("app.api.questions._upload_dir", lambda: tmp_path)
    monkeypatch.setattr("app.api.questions.ocr_pdf", fake_ocr)
    response = await client.post(
        f"/api/questions/{question.id}/replace",
        data={"confirmation_name": question.name},
        files={"file": ("new.pdf", b"%PDF-1.4 new", "application/pdf")},
    )

    assert response.status_code == 200
    assert response.json() == {"deleted_submission_count": 1}
    await db_session.refresh(question)
    assert question.ocr_text == "新版题目 OCR"
    assert question.original_filename == "new.pdf"
    assert await db_session.get(Submission, sub_id) is None
    assert not old_question_path.exists()
    assert not student_path.exists()
    assert question.file_path.endswith("_question.pdf")
