from pydantic import ValidationError

from app.schemas.grading import GradingAssessmentRequest
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


def test_question_rubric_accepts_inferred_score_from_exact_grade_band_quote():
    result = RubricExtraction(
        status="complete",
        total_max_score=100,
        items=[
            RubricExtractionItem(
                criterion="Generic Marking Criteria",
                max_score=100,
                details="Grade A 81+ Excellent work; Grade B 70 - 80 Very good work.",
                source_quote="Grade A 81+ Excellent work; Grade B 70 - 80 Very good work.",
            )
        ],
    )
    validated = validate_extraction(
        result,
        "Generic Marking Criteria: Grade A 81+ Excellent work; Grade B 70 - 80 Very good work.",
    )
    assert validated is not None
    item = validated[0]["items"][0]
    assert item["criterion"] == "Generic Marking Criteria"
    assert item["details"] == item["source_quote"]
    assert validated[0]["text"] == item["source_quote"]


def test_question_rubric_accepts_holistic_criterion_outside_quote():
    """评分项名称各作业差异极大,不要求在引用里出现(交回 AI 语义判断)。"""
    result = RubricExtraction(
        status="complete",
        total_max_score=100,
        items=[
            RubricExtractionItem(
                criterion="整体等级评价",
                max_score=100,
                details="按等级段给整体评价。",
                source_quote="Grade A 81+ Excellent work; Grade B 70 - 80 Very good work.",
            )
        ],
    )
    validated = validate_extraction(
        result,
        "Generic Marking Criteria: Grade A 81+ Excellent work; Grade B 70 - 80 Very good work.",
    )

    assert validated is not None
    assert validated[0]["items"][0]["criterion"] == "整体等级评价"


def test_question_rubric_rejects_holistic_quote_not_in_ocr():
    """去掉名称限制后,引用仍必须是 OCR 原文子串(防编造的锚点)。"""
    result = RubricExtraction(
        status="complete",
        total_max_score=100,
        items=[
            RubricExtractionItem(
                criterion="整体等级评价",
                max_score=100,
                details="按等级段给整体评价。",
                source_quote="Grade A 81+ Excellent work; Grade B 70 - 80 Very good work.",
            )
        ],
    )

    assert validate_extraction(result, "评分标准：整体表现优秀。") is None


def test_question_rubric_rejects_inferred_score_without_grade_bands():
    result = RubricExtraction(
        status="complete",
        total_max_score=100,
        items=[
            RubricExtractionItem(
                criterion="整体评价",
                max_score=100,
                details="整体表现优秀。",
                source_quote="Generic Marking Criteria: Excellent work.",
            )
        ],
    )
    assert (
        validate_extraction(result, "Generic Marking Criteria: Excellent work.") is None
    )


def test_question_rubric_rejects_inconsistent_total():
    result = _complete()
    result.total_max_score = 90
    assert validate_extraction(result, "正确性：100 分，结果正确") is None


def test_mcp_rejects_caller_supplied_question_rubric_text():
    try:
        GradingAssessmentRequest(
            rubric_source="question_extracted",
            question_rubric="伪造 rubric",
            score=1,
            max_score=1,
            feedback="x",
            details=[
                {
                    "criterion": "x",
                    "score": 1,
                    "max_score": 1,
                    "comment": "x",
                    "evidence": ["x"],
                }
            ],
        )
    except ValidationError:
        return
    raise AssertionError("caller-supplied question_rubric must be rejected")
