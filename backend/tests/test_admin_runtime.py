"""Operational summary and recoverable dead-letter behavior."""

from app.models.background_job import (
    BackgroundJob,
    BackgroundJobStatus,
    BackgroundJobType,
)
from app.models.question import Question, QuestionStatus
from app.models.submission import Submission, SubmissionStatus


def _profile_id(db_session) -> int:
    from app.models.config_profile import ConfigProfile

    return db_session.query(ConfigProfile.id).filter_by(is_default=True).scalar()


async def test_runtime_summary_contains_only_bounded_counts(client, db_session):
    question = Question(
        id="runtime-question",
        config_profile_id=_profile_id(db_session),
        name="sensitive title",
        original_filename="student-name.pdf",
        file_path="/tmp/student-name.pdf",
        status=QuestionStatus.ready,
    )
    db_session.add(question)
    db_session.commit()

    response = await client.get("/api/admin/runtime")

    assert response.status_code == 200
    body = response.json()
    assert body["question_status_counts"]["ready"] == 1
    assert set(body["queue"]["status_counts"]) == {"queued", "running", "dead"}
    serialized = response.text
    assert "student-name" not in serialized
    assert "sensitive title" not in serialized


async def test_dead_question_retry_restores_target_and_job(
    client, db_session, tmp_path
):
    source = tmp_path / "question.pdf"
    source.write_bytes(b"%PDF-question")
    question = Question(
        id="dead-question",
        config_profile_id=_profile_id(db_session),
        name="dead",
        original_filename="dead.pdf",
        file_path=str(source),
        status=QuestionStatus.failed,
        error_message="worker crashed",
    )
    db_session.add(question)
    db_session.flush()
    job = BackgroundJob(
        job_type=BackgroundJobType.question_ocr,
        question_id=question.id,
        status=BackgroundJobStatus.dead,
        attempts=3,
    )
    db_session.add(job)
    db_session.commit()

    response = await client.post(f"/api/admin/dead-jobs/{job.id}/retry")

    assert response.status_code == 200
    db_session.expire_all()
    assert db_session.get(Question, question.id).status == QuestionStatus.pending
    restored = db_session.get(BackgroundJob, job.id)
    assert restored.status == BackgroundJobStatus.queued
    assert restored.attempts == 0


async def test_dead_submission_retry_rejects_missing_file(
    client, db_session, tmp_path
):
    question_file = tmp_path / "question.pdf"
    question_file.write_bytes(b"%PDF-question")
    question = Question(
        id="dead-submission-question",
        config_profile_id=_profile_id(db_session),
        name="question",
        original_filename="question.pdf",
        file_path=str(question_file),
        ocr_text="rubric",
        status=QuestionStatus.ready,
    )
    submission = Submission(
        question=question,
        original_filename="missing.pdf",
        file_path=str(tmp_path / "missing.pdf"),
        status=SubmissionStatus.failed,
    )
    db_session.add(submission)
    db_session.flush()
    job = BackgroundJob(
        job_type=BackgroundJobType.submission_ocr,
        submission_id=submission.id,
        status=BackgroundJobStatus.dead,
        attempts=3,
    )
    db_session.add(job)
    db_session.commit()

    response = await client.post(f"/api/admin/dead-jobs/{job.id}/retry")

    assert response.status_code == 409
    db_session.expire_all()
    assert db_session.get(BackgroundJob, job.id).status == BackgroundJobStatus.dead
    assert db_session.get(Submission, submission.id).status == SubmissionStatus.failed

