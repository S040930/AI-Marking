"""HTTP contract tests for finalized-result Excel export."""

from io import BytesIO
from zipfile import ZipFile

from app.models.question import Question, QuestionStatus
from app.models.submission import Submission, SubmissionStatus


def _question(db_session, name: str = "成绩分析题目") -> Question:
    question = Question(
        name=name,
        original_filename=f"{name}.pdf",
        file_path="/tmp/question.pdf",
        ocr_text="Question text",
        status=QuestionStatus.ready,
    )
    db_session.add(question)
    db_session.flush()
    return question


async def test_export_requires_existing_question_and_valid_threshold(client):
    invalid = await client.get(
        "/api/submissions/export.xlsx",
        params={"question_id": "missing", "pass_threshold": 0},
    )
    missing = await client.get(
        "/api/submissions/export.xlsx",
        params={"question_id": "missing", "pass_threshold": 60},
    )

    assert invalid.status_code == 422
    assert missing.status_code == 404


async def test_export_rejects_question_without_reviewed_results(client, db_session):
    question = _question(db_session)
    db_session.commit()

    response = await client.get(
        "/api/submissions/export.xlsx",
        params={"question_id": question.id, "pass_threshold": 60},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "该题目暂无已审阅成绩，无法导出"


async def test_export_contains_only_reviewed_final_results(client, db_session):
    question = _question(db_session)
    reviewed = Submission(
        original_filename="reviewed-student.pdf",
        file_path="/tmp/reviewed.pdf",
        question=question,
        status=SubmissionStatus.reviewed,
        score=42,
        max_score=50,
        feedback="Final teacher feedback",
        reviewed_by="Dr Chen",
        details=[
            {
                "rubric_item_id": "analysis",
                "criterion": "Analysis",
                "score": 42,
                "max_score": 50,
                "comment": "Well supported",
                "evidence": ["Quoted evidence"],
            }
        ],
    )
    pending_review = Submission(
        original_filename="not-reviewed.pdf",
        file_path="/tmp/pending.pdf",
        question=question,
        status=SubmissionStatus.ready_for_review,
        score=49,
        max_score=50,
        feedback="AI suggestion only",
        details=[],
    )
    db_session.add_all([reviewed, pending_review])
    db_session.commit()

    response = await client.get(
        "/api/submissions/export.xlsx",
        params={
            "question_id": question.id,
            "pass_threshold": 70,
            "locale": "en-US",
        },
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    assert "grade_analysis_" in response.headers["content-disposition"]
    with ZipFile(BytesIO(response.content)) as workbook:
        workbook_xml = workbook.read("xl/workbook.xml").decode()
        summary_xml = workbook.read("xl/worksheets/sheet2.xml").decode()
        assert "Results Analysis" in workbook_xml
        assert "Grade Summary" in workbook_xml
        assert "Criterion Details" in workbook_xml
        assert "reviewed-student.pdf" in summary_xml
        assert "Final teacher feedback" in summary_xml
        assert "not-reviewed.pdf" not in summary_xml
