"""Structured grading evidence validation regressions."""

from types import SimpleNamespace
from uuid import UUID

import pytest

from app.application.mcp_workflow import validate_code_evidence
from app.core.errors import ValidationError
from app.schemas.mcp import McpAssessmentRequest


def _assessment_with_report_quote(quote: str) -> McpAssessmentRequest:
    return McpAssessmentRequest(
        rubric_source="configured",
        rubric_snapshot_id="snapshot-1",
        request_id=UUID("11111111-1111-4111-8111-111111111111"),
        score=1,
        max_score=1,
        confidence=0.8,
        feedback="ok",
        details=[
            {
                "rubric_item_id": "item-1",
                "criterion": "Task 1",
                "score": 1,
                "max_score": 1,
                "comment": "ok",
                "evidence_refs": [{"type": "report_quote", "quote": quote}],
            }
        ],
    )


def test_empty_report_quote_is_rejected():
    submission = SimpleNamespace(
        ocr_text="The report contains verifiable evidence.",
        code_files=[
            SimpleNamespace(original_filename="Q1.py", source_text="print('ok')")
        ],
    )

    with pytest.raises(ValidationError) as caught:
        validate_code_evidence(submission, _assessment_with_report_quote(""))

    assert "报告证据无法定位" in str(caught.value.detail)


def test_locatable_report_quote_is_accepted():
    submission = SimpleNamespace(
        ocr_text="The report contains verifiable evidence.",
        code_files=[
            SimpleNamespace(original_filename="Q1.py", source_text="print('ok')")
        ],
    )

    validate_code_evidence(
        submission,
        _assessment_with_report_quote("verifiable evidence"),
    )


def _code_submission() -> SimpleNamespace:
    return SimpleNamespace(
        ocr_text="The report shows 95% accuracy.",
        code_files=[
            SimpleNamespace(
                original_filename="task1.py",
                source_text=(
                    "import pandas as pd\n"
                    "data = pd.read_csv('data.csv')\n"
                    "data = data.dropna()\n"
                    "result = data.groupby('group').mean()\n"
                    "print(result)"
                ),
            )
        ],
    )


def _assessment_with_source_line(ref: dict) -> McpAssessmentRequest:
    return McpAssessmentRequest(
        rubric_source="configured",
        rubric_snapshot_id="snapshot-1",
        request_id=UUID("22222222-2222-4222-8222-222222222222"),
        score=1,
        max_score=1,
        confidence=0.8,
        feedback="ok",
        details=[
            {
                "rubric_item_id": "item-1",
                "criterion": "Task 1",
                "score": 1,
                "max_score": 1,
                "comment": "ok",
                "evidence_refs": [ref],
            }
        ],
    )


def test_source_line_accepts_numeric_string_line():
    submission = _code_submission()

    validate_code_evidence(
        submission,
        _assessment_with_source_line(
            {
                "type": "source_line",
                "filename": "task1.py",
                "line": "2",
                "quote": "data = pd.read_csv('data.csv')",
            }
        ),
    )


def test_source_line_accepts_line_range_span():
    submission = _code_submission()

    validate_code_evidence(
        submission,
        _assessment_with_source_line(
            {
                "type": "source_line",
                "filename": "task1.py",
                "line": 2,
                "end_line": 4,
                "quote": "data = pd.read_csv('data.csv')\n"
                "data = data.dropna()\n"
                "result = data.groupby('group').mean()",
            }
        ),
    )


def test_source_line_filename_matches_case_insensitively():
    submission = _code_submission()

    validate_code_evidence(
        submission,
        _assessment_with_source_line(
            {
                "type": "source_line",
                "filename": "TASK1.PY",
                "line": 2,
                "quote": "data = pd.read_csv('data.csv')",
            }
        ),
    )


def test_report_quote_tolerates_ocr_punctuation_and_case():
    submission = _code_submission()

    validate_code_evidence(
        submission,
        _assessment_with_report_quote("The report: shows 95 accuracy."),
    )


def test_wrong_line_reports_actual_line_number():
    submission = _code_submission()

    with pytest.raises(ValidationError) as caught:
        validate_code_evidence(
            submission,
            _assessment_with_source_line(
                {
                    "type": "source_line",
                    "filename": "task1.py",
                    "line": 5,
                    "quote": "data = data.dropna()",
                }
            ),
        )

    assert "实际位于 task1.py 第 3 行" in str(caught.value.detail)


def test_absent_quote_is_still_rejected():
    submission = _code_submission()

    with pytest.raises(ValidationError) as caught:
        validate_code_evidence(
            submission,
            _assessment_with_source_line(
                {
                    "type": "source_line",
                    "filename": "task1.py",
                    "line": 2,
                    "quote": "print('this line does not exist')",
                }
            ),
        )

    assert "无法定位" in str(caught.value.detail)
