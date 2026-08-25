"""Structured grading evidence validation regressions."""

from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi import HTTPException

from app.schemas.mcp import McpAssessmentRequest
from app.services.mcp_workflow import validate_code_evidence


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

    with pytest.raises(HTTPException) as caught:
        validate_code_evidence(submission, _assessment_with_report_quote(""))

    assert caught.value.status_code == 422
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
