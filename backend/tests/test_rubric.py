from app.services.rubric import (
    DEFAULT_RUBRIC,
    RUBRIC_PRIORITY,
    RUBRIC_VERSION,
    normalize_definition,
    resolve_rubric,
)

DEFINITION = {
    "items": [{"criterion": "内容", "max_score": 100, "details": "评分细则"}],
    "total_max_score": 100,
}


def test_resolver_uses_structured_configured_definition():
    resolution = resolve_rubric(None, {"rubric_definition": DEFINITION})
    assert resolution.source == "configured"
    assert resolution.definition.total_max_score == 100
    assert resolution.definition.items[0].criterion == "内容"


def test_resolver_falls_back_to_default_and_ignores_legacy_text():
    resolution = resolve_rubric(None, {"rubric": "旧自由文本"})
    assert resolution.source == "built_in_default"
    assert resolution.text == DEFAULT_RUBRIC


def test_invalid_definition_is_rejected():
    import pytest

    with pytest.raises(ValueError):
        normalize_definition({"items": [{"criterion": "重复", "max_score": 1, "details": "a"}, {"criterion": "重复", "max_score": 1, "details": "b"}], "total_max_score": 2})


def test_rubric_priority_and_version():
    assert RUBRIC_PRIORITY == ("question_extracted", "configured", "built_in_default")
    assert RUBRIC_VERSION == "rubric-definition-v2"
