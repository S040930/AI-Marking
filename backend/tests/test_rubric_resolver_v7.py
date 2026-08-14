import hashlib
from types import SimpleNamespace

import pytest

from app.services.rubric import resolve_rubric, validate_assessment_details


def _question():
    text = "评分：内容 60 分。分析 40 分。"
    return SimpleNamespace(
        config_profile_id=7,
        ocr_text=text,
        extracted_rubric="历史文本格式不作为权威",
        extracted_rubric_items=[
            {
                "rubric_item_id": "legacy-id",
                "criterion": "内容",
                "max_score": 60,
                "details": "理解题目",
                "source_quote": "内容 60 分",
            },
            {
                "rubric_item_id": "legacy-id-2",
                "criterion": "分析",
                "max_score": 40,
                "details": "论证清晰",
                "source_quote": "分析 40 分",
            },
        ],
        extracted_rubric_ocr_hash="sha256:" + hashlib.sha256(text.encode()).hexdigest(),
        extracted_rubric_version="question-rubric-v1",
    )


def test_question_snapshot_precedes_config_and_legacy_ids_are_rebuilt():
    resolved = resolve_rubric(
        _question(),
        {
            "rubric_definition": {
                "items": [{"criterion": "配置", "max_score": 100, "details": "配置"}],
                "total_max_score": 100,
            }
        },
    )
    assert resolved.source == "question_extracted"
    assert all(item["rubric_item_id"].startswith("rubric_") for item in resolved.items)


def test_invalid_question_snapshot_falls_back_to_structured_config():
    question = _question()
    question.extracted_rubric_ocr_hash = "sha256:" + "0" * 64
    resolved = resolve_rubric(
        question,
        {
            "rubric_definition": {
                "items": [{"criterion": "配置", "max_score": 100, "details": "配置"}],
                "total_max_score": 100,
            }
        },
    )
    assert resolved.source == "configured"


def test_details_must_cover_exact_resolved_items():
    resolved = resolve_rubric(None, {})
    details = [
        {
            "rubric_item_id": item["rubric_item_id"],
            "criterion": item["criterion"],
            "max_score": item["max_score"],
        }
        for item in resolved.items
    ]
    validate_assessment_details(details, resolved)
    with pytest.raises(ValueError):
        validate_assessment_details(details[:-1], resolved)
