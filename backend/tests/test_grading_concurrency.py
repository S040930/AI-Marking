"""Regression coverage for handle versions, rubric caching and finalization."""

import json
from uuid import uuid4

import pytest
from sqlalchemy import update

from app.application import mcp_workflow as workflow
from app.application.locking import lock_submission_after_question
from app.models.mcp_workflow_handle import McpWorkflowHandle
from app.models.question import Question, QuestionStatus
from app.models.submission import Submission, SubmissionStatus
from app.services.config import upsert_config

HEADERS = {"X-AI-Marking-MCP-Token": "test-token"}


@pytest.fixture
def assignment(db_session):
    workflow._package_cache.clear()
    question = Question(id="version-regression", name="Version test", original_filename="version.pdf",
                        file_path="/tmp/version.pdf", ocr_text="Task", status=QuestionStatus.ready)
    sub = Submission(question=question, original_filename="answer.pdf", file_path="/tmp/answer.pdf",
                     ocr_text="Report evidence", status=SubmissionStatus.awaiting_mcp)
    db_session.add(sub)
    db_session.commit()
    configure(db_session, 100)
    yield sub
    workflow._package_cache.clear()


def configure(db, maximum):
    upsert_config(db, {"rubric_definition": {
        "items": [{"criterion": "Task", "max_score": maximum, "details": "Completion"}],
        "total_max_score": maximum,
    }})


async def open_package(client, sub):
    response = await client.get(f"/api/mcp/submissions/{sub.id}/package", headers=HEADERS)
    assert response.status_code == 200, response.text
    return response.json()


def assessment(package, score=10):
    policy = json.loads(package["content"].split("\n", 1)[1].split("\n\n", 1)[0])["grading_policy"]["resolved_rubric"]
    return {"grading_handle": package["grading_handle"], "assessment": {
        "request_id": str(uuid4()), "feedback": "Feedback", "confidence": 0.8,
        "details": [{"rubric_item_id": policy["items"][0]["rubric_item_id"], "score": score,
                     "comment": "Completion", "evidence_refs": [{"type": "report_quote", "quote": "Report evidence"}]}],
    }}


async def save(client, sub, payload):
    return await client.put(f"/api/mcp/submissions/{sub.id}/assessment-v2", headers=HEADERS, json=payload)


async def test_old_handle_cannot_overwrite_but_same_request_replays(client, assignment, db_session):
    first, second = await open_package(client, assignment), await open_package(client, assignment)
    request = assessment(first)
    saved = await save(client, assignment, request)
    assert saved.status_code == 200
    replay = await save(client, assignment, request)
    assert replay.status_code == 200
    assert replay.json()["idempotent"] is True
    assert replay.json()["grading_revision"] == 1
    stale = await save(client, assignment, assessment(second, 40))
    assert stale.status_code == 409
    assert stale.json()["detail"]["current_revision"] == 1
    db_session.refresh(assignment)
    assert assignment.score == 10
    assert assignment.grading_revision == 1
    changed = {**request, "assessment": {**request["assessment"], "feedback": "Different"}}
    assert (await save(client, assignment, changed)).status_code == 409


async def test_code_confirmation_does_not_allow_stale_handle(client, assignment, db_session):
    from app.models.submission_code_file import SubmissionCodeFile

    assignment.code_files.append(SubmissionCodeFile(question_number=1, original_filename="q1.py", file_path="/tmp/q1.py",
                                                    file_kind="py", source_text="print(1)", source_sha256="a" * 64))
    db_session.commit()
    first, second = await open_package(client, assignment), await open_package(client, assignment)
    for package in (first, second):
        handle = workflow.load_workflow_handle(db_session, package["grading_handle"], kind="grading")
        handle.visual_confirmation = "consistent"
    db_session.commit()
    # Zero with not_found is valid for code and needs no invented source quote.
    request = assessment(first, 0)
    for item in request["assessment"]["details"]:
        item.update(evidence_refs=[], evidence_status="not_found")
    assert (await save(client, assignment, request)).status_code == 200
    request["grading_handle"] = second["grading_handle"]
    request["assessment"]["request_id"] = str(uuid4())
    assert (await save(client, assignment, request)).status_code == 409


async def test_rubric_update_invalidates_cache_and_old_handle(client, assignment, db_session):
    old = await open_package(client, assignment)
    configure(db_session, 50)
    new = await open_package(client, assignment)
    header = json.loads(new["content"].split("\n", 1)[1].split("\n\n", 1)[0])
    assert header["grading_policy"]["resolved_rubric"]["total_max_score"] == 50
    assert (await save(client, assignment, assessment(old))).status_code == 409
    assert (await save(client, assignment, assessment(new))).status_code == 200
    db_session.refresh(assignment)
    assert assignment.max_score == 50


async def test_rubric_update_invalidates_pagination(client, assignment, db_session):
    assignment.ocr_text = "Report evidence " * 4000
    db_session.commit()
    first = await open_package(client, assignment)
    assert first["continuation_token"]
    configure(db_session, 50)
    response = await client.get(f"/api/mcp/submissions/{assignment.id}/package", headers=HEADERS,
                                params={"continuation_token": first["continuation_token"]})
    assert response.status_code == 409
    fresh = await open_package(client, assignment)
    last = await client.get(f"/api/mcp/submissions/{assignment.id}/package", headers=HEADERS,
                           params={"continuation_token": fresh["continuation_token"]})
    assert last.status_code == 200
    assert last.json()["grading_handle"]


@pytest.mark.parametrize("locker", [lock_submission_after_question, workflow.get_locked_submission_in_order])
def test_lock_refreshes_preloaded_submission_and_question(db_session, assignment, locker):
    db_session.execute(update(Submission).where(Submission.id == assignment.id).values(grading_revision=2),
                       execution_options={"synchronize_session": False})
    db_session.execute(update(Question).where(Question.id == assignment.question_id).values(ocr_text="New task"),
                       execution_options={"synchronize_session": False})
    db_session.commit()
    assert assignment.grading_revision == 0
    current = locker(db_session, assignment.id)
    assert current.grading_revision == 2
    assert current.question.ocr_text == "New task"


def test_handle_lock_refreshes_preloaded_confirmation(db_session, assignment):
    token = workflow.create_workflow_handle(db_session, {"kind": "grading", "submission_id": assignment.id,
                                             "context_hash": "h", "grading_revision": 0, "context_complete": True})
    db_session.commit()
    handle = workflow.load_workflow_handle(db_session, token, kind="grading")
    db_session.execute(update(McpWorkflowHandle).where(McpWorkflowHandle.id == handle.id).values(grading_revision=2),
                       execution_options={"synchronize_session": False})
    db_session.commit()
    assert workflow.load_workflow_handle(db_session, token, kind="grading", lock=True).grading_revision == 2


@pytest.mark.parametrize("revision,status", [(None, 422), (0, 409), (1, 200)])
async def test_finalize_requires_current_revision(client, assignment, db_session, revision, status):
    assert (await save(client, assignment, assessment(await open_package(client, assignment)))).status_code == 200
    payload = {"reviewer_name": "Teacher", "score": 20, "max_score": 100, "feedback": "Reviewed",
               "details": [{"criterion": "Task", "score": 20, "max_score": 100, "comment": "Reviewed"}]}
    if revision is not None:
        payload["expected_revision"] = revision
    response = await client.post(f"/api/submissions/{assignment.id}/finalize", json=payload)
    assert response.status_code == status, response.text
    db_session.refresh(assignment)
    if status != 200:
        assert assignment.status == SubmissionStatus.ready_for_review
        assert assignment.score == 10
        assert assignment.reviewed_at is None
