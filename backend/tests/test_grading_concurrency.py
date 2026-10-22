"""Regression coverage for handle versions, rubric caching and finalization.

直调 application 层(不依赖已删除的 MCP HTTP 端点):行为断言与原
/api/mcp 契约测试等价。
"""

from __future__ import annotations

import json
from uuid import uuid4

import pytest
from sqlalchemy import update

from app.application import grading_package
from app.application import grading_workflow as workflow
from app.application.locking import lock_submission_after_question
from app.core.errors import ConflictError
from app.models.grading_handle import GradingHandle
from app.models.question import Question, QuestionStatus
from app.models.submission import Submission, SubmissionStatus
from app.schemas.grading import GradingAssessmentRequest, GradingSaveAssessmentRequest
from app.services.config import upsert_config


@pytest.fixture
def assignment(db_session):
    grading_package._package_cache.clear()
    question = Question(
        id="version-regression",
        name="Version test",
        original_filename="version.pdf",
        file_path="/tmp/version.pdf",
        ocr_text="Task",
        status=QuestionStatus.ready,
    )
    sub = Submission(
        question=question,
        original_filename="answer.pdf",
        file_path="/tmp/answer.pdf",
        ocr_text="Report evidence",
        status=SubmissionStatus.awaiting_grading,
    )
    db_session.add(sub)
    db_session.commit()
    configure(db_session, 100)
    yield sub
    grading_package._package_cache.clear()


def configure(db, maximum):
    upsert_config(
        db,
        {
            "rubric_definition": {
                "items": [
                    {"criterion": "Task", "max_score": maximum, "details": "Completion"}
                ],
                "total_max_score": maximum,
            }
        },
    )


def open_package(db, sub, token=None):
    return grading_package.open_grading_package(db, sub, token)


def assessment(package, score=10, *, not_found=False) -> GradingSaveAssessmentRequest:
    header = json.loads(package.content.split("\n", 1)[1].split("\n\n", 1)[0])
    item = header["grading_policy"]["resolved_rubric"]["items"][0]
    detail = {
        "rubric_item_id": item["rubric_item_id"],
        "score": score,
        "comment": "Completion",
    }
    if not_found:
        detail.update(evidence_refs=[], evidence_status="not_found")
    else:
        detail["evidence_refs"] = [{"type": "report_quote", "quote": "Report evidence"}]
    return GradingSaveAssessmentRequest(
        grading_handle=package.grading_handle,
        assessment=GradingAssessmentRequest(
            request_id=uuid4(),
            confidence=0.8,
            feedback="Feedback",
            details=[detail],
        ),
    )


def save(db, sub, payload):
    return workflow.save_assessment(db, sub.id, payload)


def test_old_handle_cannot_overwrite_but_same_request_replays(db_session, assignment):
    first = open_package(db_session, assignment)
    second = open_package(db_session, assignment)
    request = assessment(first)
    saved = save(db_session, assignment, request)
    assert saved.status == SubmissionStatus.ready_for_review
    replay = save(db_session, assignment, request)
    assert replay.idempotent is True
    assert replay.grading_revision == 1
    with pytest.raises(ConflictError) as excinfo:
        save(db_session, assignment, assessment(second, 40))
    assert excinfo.value.detail["current_revision"] == 1
    db_session.refresh(assignment)
    assert assignment.score == 10
    assert assignment.grading_revision == 1
    changed = request.model_copy(
        update={
            "assessment": request.assessment.model_copy(
                update={"feedback": "Different"}
            )
        }
    )
    with pytest.raises(ConflictError):
        save(db_session, assignment, changed)


def test_code_confirmation_does_not_allow_stale_handle(db_session, assignment):
    from app.models.submission_code_file import SubmissionCodeFile

    assignment.code_files.append(
        SubmissionCodeFile(
            question_number=1,
            original_filename="q1.py",
            file_path="/tmp/q1.py",
            file_kind="py",
            source_text="print(1)",
            source_sha256="a" * 64,
        )
    )
    db_session.commit()
    first = open_package(db_session, assignment)
    second = open_package(db_session, assignment)
    for package in (first, second):
        handle = grading_package.load_workflow_handle(
            db_session, package.grading_handle, kind="grading"
        )
        handle.visual_confirmation = "consistent"
    db_session.commit()
    # Zero with not_found is valid for code and needs no invented source quote.
    request = assessment(first, 0, not_found=True)
    assert save(db_session, assignment, request).grading_revision == 1
    with pytest.raises(ConflictError):
        save(db_session, assignment, assessment(second, 0, not_found=True))


def test_rubric_update_invalidates_cache_and_old_handle(db_session, assignment):
    old = open_package(db_session, assignment)
    configure(db_session, 50)
    new = open_package(db_session, assignment)
    header = json.loads(new.content.split("\n", 1)[1].split("\n\n", 1)[0])
    assert header["grading_policy"]["resolved_rubric"]["total_max_score"] == 50
    with pytest.raises(ConflictError):
        save(db_session, assignment, assessment(old))
    save(db_session, assignment, assessment(new))
    db_session.refresh(assignment)
    assert assignment.max_score == 50


def test_rubric_update_invalidates_pagination(db_session, assignment):
    assignment.ocr_text = "Report evidence " * 4000
    db_session.commit()
    first = open_package(db_session, assignment)
    assert first.continuation_token
    configure(db_session, 50)
    with pytest.raises(ConflictError):
        open_package(db_session, assignment, first.continuation_token)
    fresh = open_package(db_session, assignment)
    last = open_package(db_session, assignment, fresh.continuation_token)
    assert last.grading_handle


@pytest.mark.parametrize(
    "locker", [lock_submission_after_question, workflow.get_locked_submission_in_order]
)
def test_lock_refreshes_preloaded_submission_and_question(
    db_session, assignment, locker
):
    db_session.execute(
        update(Submission)
        .where(Submission.id == assignment.id)
        .values(grading_revision=2),
        execution_options={"synchronize_session": False},
    )
    db_session.execute(
        update(Question)
        .where(Question.id == assignment.question_id)
        .values(ocr_text="New task"),
        execution_options={"synchronize_session": False},
    )
    db_session.commit()
    assert assignment.grading_revision == 0
    current = locker(db_session, assignment.id)
    assert current.grading_revision == 2
    assert current.question.ocr_text == "New task"


def test_handle_lock_refreshes_preloaded_confirmation(db_session, assignment):
    token = grading_package.create_workflow_handle(
        db_session,
        {
            "kind": "grading",
            "submission_id": assignment.id,
            "context_hash": "h",
            "grading_revision": 0,
            "context_complete": True,
        },
    )
    db_session.commit()
    handle = grading_package.load_workflow_handle(db_session, token, kind="grading")
    db_session.execute(
        update(GradingHandle)
        .where(GradingHandle.id == handle.id)
        .values(grading_revision=2),
        execution_options={"synchronize_session": False},
    )
    db_session.commit()
    assert (
        grading_package.load_workflow_handle(
            db_session, token, kind="grading", lock=True
        ).grading_revision
        == 2
    )


@pytest.mark.parametrize("revision,status", [(None, 422), (0, 409), (1, 200)])
async def test_finalize_requires_current_revision(
    client, db_session, assignment, revision, status
):
    save(db_session, assignment, assessment(open_package(db_session, assignment)))
    payload = {
        "reviewer_name": "Teacher",
        "score": 20,
        "max_score": 100,
        "feedback": "Reviewed",
        "details": [
            {"criterion": "Task", "score": 20, "max_score": 100, "comment": "Reviewed"}
        ],
    }
    if revision is not None:
        payload["expected_revision"] = revision
    response = await client.post(
        f"/api/submissions/{assignment.id}/finalize", json=payload
    )
    assert response.status_code == status, response.text
    db_session.refresh(assignment)
    if status != 200:
        assert assignment.status == SubmissionStatus.ready_for_review
        assert assignment.score == 10
        assert assignment.reviewed_at is None
