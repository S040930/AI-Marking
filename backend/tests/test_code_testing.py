"""Question-scoped test suite versioning and run snapshot coverage."""

from app.models.grading_run import GradingRun
from app.models.question import Question, QuestionStatus
from app.models.submission import Submission, SubmissionStatus
from app.services.code_testing import (
    CodeTestCase,
    approve_suite,
    create_draft_suite,
    get_current_suite,
    invalidate_question_suites,
    load_run_results,
    load_run_suite_snapshot,
    persist_run_results,
    persist_run_snapshot,
    source_fingerprint,
)
from app.services.code_testing import (
    test_evidence_map as get_test_evidence_map,
)


def _question(db_session):
    question = Question(
        id="code-test-q1",
        name="代码测试题",
        original_filename="question.pdf",
        file_path="/tmp/question.pdf",
        file_sha256="a" * 64,
        ocr_text="输出输入数字的平方。",
        status=QuestionStatus.ready,
    )
    db_session.add(question)
    db_session.flush()
    submission = Submission(
        original_filename="answer.pdf",
        file_path="/tmp/answer.pdf",
        question=question,
        status=SubmissionStatus.awaiting_grading,
    )
    db_session.add(submission)
    db_session.flush()
    run = GradingRun(submission_id=submission.id, provider="openai_api")
    db_session.add(run)
    db_session.flush()
    return question, run


def _case(title: str = "square") -> CodeTestCase:
    return CodeTestCase(
        question_number=1,
        title=title,
        mode="program_io",
        language="python",
        input="4\n",
        expected_stdout="16\n",
    )


def test_approved_suite_is_reused_and_new_version_supersedes_old(db_session):
    question, run = _question(db_session)
    fingerprint = source_fingerprint(question, "rubric-v1")

    first = create_draft_suite(
        db_session,
        question_id=question.id,
        fingerprint=fingerprint,
        cases=[_case()],
        generated_by_run_id=run.id,
    )
    db_session.commit()
    assert get_current_suite(db_session, question.id, fingerprint).id == first.id

    first = approve_suite(
        db_session, first.id, question_id=question.id, fingerprint=fingerprint
    )
    second = create_draft_suite(
        db_session,
        question_id=question.id,
        fingerprint=fingerprint,
        cases=[_case("boundary")],
        generated_by_run_id=run.id,
    )
    db_session.commit()
    assert get_current_suite(db_session, question.id, fingerprint).id == second.id

    second = approve_suite(
        db_session, second.id, question_id=question.id, fingerprint=fingerprint
    )
    db_session.commit()
    assert first.status == "superseded"
    assert second.status == "approved"
    assert second.version == first.version + 1


def test_changed_question_or_rubric_invalidates_active_suite_but_keeps_version(
    db_session,
):
    question, run = _question(db_session)
    fingerprint = source_fingerprint(question, "rubric-v1")
    suite = create_draft_suite(
        db_session,
        question_id=question.id,
        fingerprint=fingerprint,
        cases=[_case()],
        generated_by_run_id=run.id,
    )
    db_session.commit()

    question.ocr_text = "输出输入数字的立方。"
    db_session.flush()
    assert get_current_suite(
        db_session, question.id, source_fingerprint(question, "rubric-v1")
    ) is None
    db_session.commit()

    db_session.refresh(suite)
    assert suite.status == "invalidated"
    assert suite.version == 1
    assert suite.tests[0]["expected_stdout"] == "16\n"


def test_question_replacement_invalidates_but_does_not_delete_suite_rows(db_session):
    question, run = _question(db_session)
    fingerprint = source_fingerprint(question, "rubric-v1")
    suite = create_draft_suite(
        db_session,
        question_id=question.id,
        fingerprint=fingerprint,
        cases=[_case()],
        generated_by_run_id=run.id,
    )
    db_session.commit()

    invalidate_question_suites(db_session, question.id)
    db_session.commit()
    db_session.refresh(suite)
    assert suite.status == "invalidated"
    assert suite.tests[0]["id"] == "q1-t1"


def test_run_suite_and_result_snapshots_are_immutable_evidence(db_session):
    question, run = _question(db_session)
    fingerprint = source_fingerprint(question, "rubric-v1")
    suite = create_draft_suite(
        db_session,
        question_id=question.id,
        fingerprint=fingerprint,
        cases=[_case()],
        generated_by_run_id=run.id,
    )
    approve_suite(db_session, suite.id, question_id=question.id, fingerprint=fingerprint)
    snapshot = persist_run_snapshot(db_session, run.id, suite)
    result = {
        "id": "q1-t1",
        "question_number": 1,
        "title": "square",
        "mode": "program_io",
        "language": "python",
        "status": "passed",
        "output": "标准输出与预期一致",
    }
    not_run = {
        "id": "q1-t2",
        "question_number": 1,
        "title": "Notebook",
        "mode": "program_io",
        "language": "any",
        "status": "not_run",
        "output": "Notebook 不自动执行",
    }
    payload = persist_run_results(db_session, run.id, snapshot, [result, not_run])
    db_session.commit()

    assert load_run_suite_snapshot(db_session, run.id)["version"] == suite.version
    assert load_run_results(db_session, run.id)["results"] == [result, not_run]
    assert get_test_evidence_map(db_session, run.id)["q1-t1"]["suite_version"] == suite.version
    assert "q1-t2" not in get_test_evidence_map(db_session, run.id)
    assert payload["summary"] == {"total": 2, "passed": 1, "failed": 0, "not_run": 1}


def test_test_schema_rejects_notebook_assertion_cases():
    import pytest

    with pytest.raises(ValueError):
        CodeTestCase(
            question_number=1,
            title="assert",
            mode="notebook_assertion",
            language="any",
        )
