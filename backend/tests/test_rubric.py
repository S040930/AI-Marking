import pytest

from app.services.rubric import (
    DEFAULT_RUBRIC,
    RUBRIC_PRIORITY,
    RUBRIC_VERSION,
    effective_rubric,
    normalize_definition,
    validate_declared_rubric,
)

DEFINITION = {
    "items": [{"criterion": "内容", "max_score": 100, "details": "评分细则"}],
    "total_max_score": 100,
}


def test_effective_rubric_uses_structured_configured_definition():
    resolution = effective_rubric({"rubric_definition": DEFINITION})
    assert resolution.source == "configured"
    assert resolution.definition.total_max_score == 100
    assert resolution.definition.items[0].criterion == "内容"


def test_effective_rubric_falls_back_to_default_and_ignores_legacy_text():
    resolution = effective_rubric({"rubric": "旧自由文本"})
    assert resolution.source == "built_in_default"
    assert resolution.text == DEFAULT_RUBRIC


def test_declared_rubric_must_match_snapshot():
    resolution = effective_rubric({"rubric_definition": DEFINITION})
    validate_declared_rubric(resolution, "configured", resolution.text)
    with pytest.raises(ValueError):
        validate_declared_rubric(resolution, "configured", "修改后的 rubric")


def test_invalid_definition_is_rejected():
    with pytest.raises(ValueError):
        normalize_definition({"items": [{"criterion": "重复", "max_score": 1, "details": "a"}, {"criterion": "重复", "max_score": 1, "details": "b"}], "total_max_score": 2})


def test_rubric_priority_and_version():
    assert RUBRIC_PRIORITY == ("question_extracted", "configured", "built_in_default")
    assert RUBRIC_VERSION == "rubric-definition-v2"
