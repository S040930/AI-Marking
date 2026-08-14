"""题目库接口的复用、删除与并发保护测试。"""

from pathlib import Path

from sqlalchemy import select, text
from sqlalchemy.orm import sessionmaker

from app.models.background_job import (
    BackgroundJob,
    BackgroundJobStatus,
    BackgroundJobType,
)
from app.models.conversation import Conversation
from app.models.question import (
    Question,
    QuestionReplacementStatus,
    QuestionStatus,
)
from app.models.submission import Submission, SubmissionStatus
from app.services import question_replace
from app.services.ocr import OCRError


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
    db_session.commit()
    return question, path


async def test_create_question_binds_default_config_profile(client, db_session):
    """未指定配置项目的题目自动绑定默认项目。"""
    from app.models.config_profile import ConfigProfile

    created = await client.post(
        "/api/questions",
        files={"file": ("new-q.pdf", b"%PDF-1.4", "application/pdf")},
        data={"name": "默认绑定题"},
    )
    assert created.status_code == 201
    default_id = db_session.query(ConfigProfile).filter_by(is_default=True).one().id
    assert created.json()["config_profile_id"] == default_id
    assert (
        db_session.get(Question, created.json()["id"]).config_profile_id == default_id
    )


async def test_create_question_with_explicit_config_profile(client, db_session):
    """显式指定 config_profile_id 时题目按项目绑定。"""
    from app.models.config_profile import ConfigProfile

    profile = ConfigProfile(name="AB 项目", is_default=False)
    db_session.add(profile)
    db_session.commit()

    created = await client.post(
        "/api/questions",
        files={"file": ("new-q.pdf", b"%PDF-1.4", "application/pdf")},
        data={"name": "AB绑定题", "config_profile_id": str(profile.id)},
    )
    assert created.status_code == 201
    assert created.json()["config_profile_id"] == profile.id


async def test_switch_question_config_profile(client, db_session, tmp_path):
    """PATCH /questions/{id}/config-profile 切换题目的配置项目。"""
    from app.models.config_profile import ConfigProfile

    profile_a = ConfigProfile(name="项目A", is_default=False)
    db_session.add(profile_a)
    db_session.commit()

    question, _ = await _question(db_session, tmp_path)

    switched = await client.patch(
        f"/api/questions/{question.id}/config-profile",
        json={"config_profile_id": profile_a.id},
    )
    assert switched.status_code == 200
    assert switched.json()["config_profile_id"] == profile_a.id
    assert db_session.get(Question, question.id).config_profile_id == profile_a.id
    # 不存在的项目拒绝
    missing = await client.patch(
        f"/api/questions/{question.id}/config-profile",
        json={"config_profile_id": 9999},
    )
    assert missing.status_code == 422


async def test_switch_question_config_profile_blocks_during_ocr(client):
    """OCR 处理中的题目不可切换配置项目。"""
    creating = await client.post(
        "/api/questions",
        files={"file": ("q.pdf", b"%PDF-1.4", "application/pdf")},
        data={"name": "排队题"},
    )
    assert creating.status_code == 201
    qid = creating.json()["id"]
    resp = await client.patch(
        f"/api/questions/{qid}/config-profile",
        json={"config_profile_id": creating.json()["config_profile_id"]},
    )
    assert resp.status_code == 409


async def test_create_and_retry_question_use_one_durable_job(client, db_session):
    created = await client.post(
        "/api/questions",
        files={"file": ("new-question.pdf", b"%PDF-1.4", "application/pdf")},
        data={"name": "新题目"},
    )
    assert created.status_code == 201
    question_id = created.json()["id"]
    question = db_session.get(Question, question_id)
    job = (
        db_session.execute(
            select(BackgroundJob).where(BackgroundJob.question_id == question_id)
        )
    ).scalar_one()
    assert job.status == BackgroundJobStatus.queued

    question.status = QuestionStatus.failed
    job.status = BackgroundJobStatus.dead
    job.attempts = 3
    db_session.commit()
    retried = await client.post(
        f"/api/questions/{question_id}/retry-ocr",
        files={"file": ("replacement.pdf", b"%PDF-1.4-new", "application/pdf")},
    )
    assert retried.status_code == 200

    jobs = (
        (
            db_session.execute(
                select(BackgroundJob).where(BackgroundJob.question_id == question_id)
            )
        )
        .scalars()
        .all()
    )
    assert len(jobs) == 1
    assert jobs[0].status == BackgroundJobStatus.queued
    assert jobs[0].attempts == 0
    Path(question.file_path).unlink(missing_ok=True)


async def test_list_search_rename_and_preview_question(client, db_session, tmp_path):
    question, _ = await _question(db_session, tmp_path)
    question.original_filename = "essay.pdf"
    db_session.commit()

    response = await client.get("/api/questions", params={"search": "期末"})
    assert response.status_code == 200
    assert response.json()["items"][0]["submission_count"] == 0

    renamed = await client.patch(
        f"/api/questions/{question.id}", json={"name": "新版作文"}
    )
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "新版作文"

    preview = await client.get(f"/api/questions/{question.id}/pdf")
    preview_head = await client.head(f"/api/questions/{question.id}/pdf")
    assert preview.status_code == 200
    assert preview.headers["content-type"] == "application/pdf"
    assert 'filename="essay.pdf"' in preview.headers["content-disposition"]
    assert preview_head.status_code == 200
    assert preview_head.headers["content-type"] == "application/pdf"


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
    db_session.commit()

    response = await client.request(
        "DELETE", "/api/submissions", json={"ids": [sub.id]}
    )
    assert response.status_code == 200
    assert question_path.exists()
    assert db_session.get(Question, question.id) is not None


async def test_delete_question_cascades_terminal_records_and_files(
    client, db_session, tmp_path
):
    db_session.execute(text("PRAGMA foreign_keys=ON"))
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
    db_session.flush()
    db_session.add(Conversation(submission_id=sub.id, role="user", content="复核"))
    db_session.commit()
    sub_id = sub.id

    response = await client.request(
        "DELETE",
        f"/api/questions/{question.id}",
        json={"confirmation_name": question.name},
    )
    assert response.status_code == 200
    assert response.json() == {"deleted_submission_count": 1}
    assert db_session.get(Question, question.id) is None
    assert db_session.get(Submission, sub_id) is None
    conversations = (
        (
            db_session.execute(
                select(Conversation).where(Conversation.submission_id == sub_id)
            )
        )
        .scalars()
        .all()
    )
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
    db_session.commit()

    response = await client.request(
        "DELETE",
        f"/api/questions/{question.id}",
        json={"confirmation_name": question.name},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["blocked_submission_ids"] == [sub.id]
    assert db_session.get(Question, question.id) is not None
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


async def test_replace_question_queues_then_atomically_switches(
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
    db_session.commit()
    sub_id = sub.id

    async def fake_ocr(file_path, api_url, token):
        return "新版题目 OCR"

    monkeypatch.setattr("app.api.questions._upload_dir", lambda: tmp_path)
    response = await client.post(
        f"/api/questions/{question.id}/replace",
        data={
            "confirmation_name": question.name,
            "acknowledge_deletion": "true",
        },
        files={"file": ("new.pdf", b"%PDF-1.4 new", "application/pdf")},
    )

    assert response.status_code == 202
    assert response.json() == {
        "question_id": question.id,
        "replacement_status": "pending",
        "affected_submission_count": 1,
    }
    db_session.refresh(question)
    assert question.ocr_text == "题目要求"
    assert question.replacement_status == QuestionReplacementStatus.pending
    assert old_question_path.exists()
    assert student_path.exists()
    job = (
        db_session.execute(
            select(BackgroundJob).where(BackgroundJob.question_id == question.id)
        )
    ).scalar_one()
    assert job.job_type == BackgroundJobType.question_replace

    factory = sessionmaker(bind=db_session.bind, expire_on_commit=False)
    monkeypatch.setattr(question_replace, "SessionLocal", factory)
    monkeypatch.setattr(question_replace, "ocr_pdf", fake_ocr)
    await question_replace.run_question_replace(question.id)

    db_session.refresh(question)
    assert question.ocr_text == "新版题目 OCR"
    assert question.original_filename == "new.pdf"
    assert question.replacement_status is None
    db_session.expire_all()
    assert db_session.get(Submission, sub_id) is None
    assert not old_question_path.exists()
    assert not student_path.exists()
    assert question.file_path.endswith("_question.pdf")


async def test_replace_question_failure_keeps_old_version(
    client, db_session, tmp_path, monkeypatch
):
    from app import worker
    from app.core.config import settings
    from app.services.queue import claim_next_job

    monkeypatch.setattr(settings, "TASK_MAX_ATTEMPTS", 1)

    question, old_path = await _question(db_session, tmp_path)
    monkeypatch.setattr("app.api.questions._upload_dir", lambda: tmp_path)
    response = await client.post(
        f"/api/questions/{question.id}/replace",
        data={"confirmation_name": question.name},
        files={"file": ("broken.pdf", b"%PDF-broken", "application/pdf")},
    )
    assert response.status_code == 202
    db_session.refresh(question)
    staged_path = Path(question.replacement_file_path)

    async def failed_ocr(file_path, api_url, token):
        raise OCRError("无法识别新版")

    factory = sessionmaker(bind=db_session.bind, expire_on_commit=False)
    monkeypatch.setattr(question_replace, "SessionLocal", factory)
    monkeypatch.setattr(question_replace, "ocr_pdf", failed_ocr)
    monkeypatch.setattr(worker, "SessionLocal", factory)

    claimed = claim_next_job(db_session, worker_id="test-worker", lease_seconds=60)
    await worker._run_claimed(claimed)

    db_session.refresh(question)
    assert question.status == QuestionStatus.ready
    assert question.ocr_text == "题目要求"
    assert question.file_path == str(old_path)
    assert question.replacement_status == QuestionReplacementStatus.failed
    assert "无法识别新版" in question.replacement_error_message
    assert old_path.exists()
    assert not staged_path.exists()


async def test_replace_requires_acknowledge_deletion_when_submissions_exist(
    client, db_session, tmp_path, monkeypatch
):
    """B3: 题目下有历史作业时,未显式 acknowledge_deletion 应被 422 拒绝。"""
    question, _ = await _question(db_session, tmp_path)
    db_session.add(
        Submission(
            original_filename="old-student.pdf",
            file_path=str(tmp_path / "old.pdf"),
            question=question,
            status=SubmissionStatus.reviewed,
        )
    )
    db_session.commit()

    monkeypatch.setattr("app.api.questions._upload_dir", lambda: tmp_path)
    response = await client.post(
        f"/api/questions/{question.id}/replace",
        data={"confirmation_name": question.name},
        files={"file": ("new.pdf", b"%PDF-1.4 new", "application/pdf")},
    )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["acknowledge_required"] is True
    assert detail["affected_submission_count"] == 1
    # 确认未被入队
    db_session.refresh(question)
    assert question.replacement_status is None


async def test_replace_without_submissions_does_not_require_acknowledge(
    client, db_session, tmp_path, monkeypatch
):
    """B3: 题目下无历史作业时,无需 acknowledge_deletion 即可替换。"""
    question, _ = await _question(db_session, tmp_path)

    monkeypatch.setattr("app.api.questions._upload_dir", lambda: tmp_path)
    response = await client.post(
        f"/api/questions/{question.id}/replace",
        data={"confirmation_name": question.name},
        files={"file": ("new.pdf", b"%PDF-1.4 new", "application/pdf")},
    )

    assert response.status_code == 202
    assert response.json()["affected_submission_count"] == 0


async def test_replacement_freezes_question_mutations(client, db_session, tmp_path):
    question, _ = await _question(db_session, tmp_path)
    question.replacement_status = QuestionReplacementStatus.pending
    question.replacement_file_path = str(tmp_path / "staged.pdf")
    question.replacement_original_filename = "staged.pdf"
    db_session.add(
        BackgroundJob(
            job_type=BackgroundJobType.question_replace,
            question_id=question.id,
            status=BackgroundJobStatus.queued,
        )
    )
    db_session.commit()

    renamed = await client.patch(
        f"/api/questions/{question.id}", json={"name": "不能改名"}
    )
    deleted = await client.request(
        "DELETE",
        f"/api/questions/{question.id}",
        json={"confirmation_name": question.name},
    )
    replaced = await client.post(
        f"/api/questions/{question.id}/replace",
        data={"confirmation_name": question.name},
        files={"file": ("again.pdf", b"%PDF", "application/pdf")},
    )

    assert renamed.status_code == 409
    assert deleted.status_code == 409
    assert replaced.status_code == 409
