from pydantic import ValidationError

from app.schemas.mcp import McpAssessmentRequest
from app.services.question_rubric import (
    RubricExtraction,
    RubricExtractionItem,
    validate_extraction,
)


def _complete():
    return RubricExtraction(
        status="complete",
        total_max_score=100,
        items=[
            RubricExtractionItem(
                criterion="正确性",
                max_score=100,
                details="结果正确",
                source_quote="正确性：100 分，结果正确",
            )
        ],
    )


def test_question_rubric_requires_ocr_quote_and_builds_stable_item_id():
    snapshot = validate_extraction(_complete(), "评分标准：正确性：100 分，结果正确")
    assert snapshot is not None
    assert snapshot[0]["items"][0]["rubric_item_id"].startswith("rubric_")


def test_question_rubric_rejects_quote_not_in_ocr():
    assert validate_extraction(_complete(), "评分标准：其他标准 100 分") is None


def test_question_rubric_rejects_inconsistent_total():
    result = _complete()
    result.total_max_score = 90
    assert validate_extraction(result, "正确性：100 分，结果正确") is None


def test_mcp_rejects_caller_supplied_question_rubric_text():
    try:
        McpAssessmentRequest(
            rubric_source="question_extracted",
            question_rubric="伪造 rubric",
            score=1,
            max_score=1,
            feedback="x",
            details=[{"criterion": "x", "score": 1, "max_score": 1, "comment": "x", "evidence": ["x"]}],
        )
    except ValidationError:
        return
    raise AssertionError("caller-supplied question_rubric must be rejected")
