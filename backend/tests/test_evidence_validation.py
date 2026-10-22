"""Structured grading evidence validation regressions (MCP API v12)."""

from types import SimpleNamespace
from uuid import UUID

import pytest

from app.application.grading_validation import validate_evidence_refs
from app.core.errors import ValidationError
from app.schemas.grading import GradingAssessmentRequest


def _request(details: list[dict]) -> GradingAssessmentRequest:
    return GradingAssessmentRequest(
        request_id=UUID("11111111-1111-4111-8111-111111111111"),
        confidence=0.8,
        feedback="ok",
        details=details,
    )


def _detail(**overrides: object) -> dict:
    detail = {
        "rubric_item_id": "item-1",
        "score": 1,
        "comment": "ok",
        "evidence_refs": [],
        "evidence_status": "supported",
    }
    detail.update(overrides)
    return detail


def _assessment_with_report_quote(quote: str) -> GradingAssessmentRequest:
    return _request(
        [
            _detail(
                evidence_refs=[{"type": "report_quote", "quote": quote}],
            )
        ]
    )


def test_empty_report_quote_is_rejected():
    submission = SimpleNamespace(
        ocr_text="The report contains verifiable evidence.",
        code_files=[
            SimpleNamespace(original_filename="Q1.py", source_text="print('ok')")
        ],
    )

    with pytest.raises(ValidationError) as caught:
        validate_evidence_refs(submission, _assessment_with_report_quote(""))

    assert "报告证据无法定位" in str(caught.value.detail)


def test_locatable_report_quote_is_accepted():
    submission = SimpleNamespace(
        ocr_text="The report contains verifiable evidence.",
        code_files=[
            SimpleNamespace(original_filename="Q1.py", source_text="print('ok')")
        ],
    )

    validate_evidence_refs(
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


def _report_only_submission() -> SimpleNamespace:
    return SimpleNamespace(
        ocr_text="The report shows 95% accuracy.",
        code_files=[],
    )


def _assessment_with_source_line(ref: dict) -> GradingAssessmentRequest:
    return _request([_detail(evidence_refs=[ref])])


def test_source_line_accepts_numeric_string_line():
    submission = _code_submission()

    validate_evidence_refs(
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

    validate_evidence_refs(
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

    validate_evidence_refs(
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

    validate_evidence_refs(
        submission,
        _assessment_with_report_quote("The report: shows 95 accuracy."),
    )


def test_wrong_line_reports_actual_line_number():
    submission = _code_submission()

    with pytest.raises(ValidationError) as caught:
        validate_evidence_refs(
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
        validate_evidence_refs(
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


def test_positive_score_without_evidence_is_rejected():
    submission = _code_submission()

    with pytest.raises(ValidationError) as caught:
        validate_evidence_refs(
            submission,
            _request([_detail(evidence_refs=[])]),
        )

    assert "得分大于 0 必须提供结构化证据" in str(caught.value.detail)


def test_positive_score_requires_evidence_on_report_only_submission():
    """v12:证据强制扩展到纯报告作业,score>0 必须有可定位 report_quote。"""
    submission = _report_only_submission()

    with pytest.raises(ValidationError) as caught:
        validate_evidence_refs(
            submission,
            _request([_detail(evidence_refs=[])]),
        )

    assert "得分大于 0 必须提供结构化证据" in str(caught.value.detail)


def test_report_only_submission_accepts_locatable_report_quote():
    submission = _report_only_submission()

    validate_evidence_refs(
        submission,
        _assessment_with_report_quote("The report shows 95% accuracy."),
    )


def test_test_case_evidence_requires_current_run_result_and_exact_server_quote():
    submission = _code_submission()
    quote = "Q1 passed: square — 标准输出与预期一致"
    request = _request(
        [
            _detail(
                evidence_refs=[
                    {
                        "type": "test_case",
                        "test_case_id": "q1-t1",
                        "suite_version": 3,
                        "quote": quote,
                    }
                ]
            )
        ]
    )
    evidence = {"q1-t1": {"suite_version": 3, "quote": quote}}

    validate_evidence_refs(submission, request, test_evidence=evidence)

    with pytest.raises(ValidationError, match="不属于当前批改 run"):
        validate_evidence_refs(submission, request, test_evidence={})


def test_test_case_evidence_rejects_forged_quote_or_suite_version():
    submission = _code_submission()
    request = _request(
        [
            _detail(
                evidence_refs=[
                    {
                        "type": "test_case",
                        "test_case_id": "q1-t1",
                        "suite_version": 2,
                        "quote": "Q1 passed: square — fabricated",
                    }
                ]
            )
        ]
    )
    evidence = {
        "q1-t1": {
            "suite_version": 3,
            "quote": "Q1 passed: square — 标准输出与预期一致",
        }
    }

    with pytest.raises(ValidationError) as caught:
        validate_evidence_refs(submission, request, test_evidence=evidence)

    assert "版本与本次运行结果不匹配" in str(caught.value.detail)


def test_source_line_rejected_on_report_only_submission():
    submission = _report_only_submission()

    with pytest.raises(ValidationError) as caught:
        validate_evidence_refs(
            submission,
            _assessment_with_source_line(
                {
                    "type": "source_line",
                    "filename": "task1.py",
                    "line": 1,
                    "quote": "print('ok')",
                }
            ),
        )

    assert "无代码作业不允许 source_line 证据" in str(caught.value.detail)


def test_not_found_requires_zero_score():
    submission = _report_only_submission()

    with pytest.raises(ValidationError) as caught:
        validate_evidence_refs(
            submission,
            _request(
                [
                    _detail(
                        score=2,
                        evidence_status="not_found",
                    )
                ]
            ),
        )

    assert "not_found 状态下得分必须为 0" in str(caught.value.detail)


def test_not_found_rejects_evidence_refs():
    submission = _report_only_submission()

    with pytest.raises(ValidationError) as caught:
        validate_evidence_refs(
            submission,
            _request(
                [
                    _detail(
                        score=0,
                        evidence_status="not_found",
                        evidence_refs=[
                            {"type": "report_quote", "quote": "95% accuracy"}
                        ],
                    )
                ]
            ),
        )

    assert "not_found 状态不得引用证据" in str(caught.value.detail)


def test_not_found_zero_score_without_refs_is_accepted():
    submission = _report_only_submission()

    validate_evidence_refs(
        submission,
        _request(
            [
                _detail(
                    score=0,
                    evidence_status="not_found",
                    comment="No discussion of limitations was found.",
                )
            ]
        ),
    )


def test_zero_score_with_locatable_evidence_is_accepted():
    """supported + 0 分合法:内容存在但不得分,引用证据说明依据。"""
    submission = _code_submission()

    validate_evidence_refs(
        submission,
        _request(
            [
                _detail(
                    score=0,
                    evidence_refs=[
                        {
                            "type": "source_line",
                            "filename": "task1.py",
                            "line": 5,
                            "quote": "print(result)",
                        }
                    ],
                )
            ]
        ),
    )
