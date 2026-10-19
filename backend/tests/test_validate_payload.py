"""P1 预检规则测试：与保存路径共用 validate_assessment_payload。"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID

import pytest

from app.application.grading_workflow import validate_assessment_payload
from app.core.errors import ValidationError
from app.schemas.grading import GradingAssessmentRequest
from app.services.rubric import ResolvedRubric, RubricDefinition, RubricItem


def _resolved() -> ResolvedRubric:
    definition = RubricDefinition(
        items=[
            RubricItem(
                rubric_item_id="item-0001",
                criterion="结果准确性",
                max_score=10,
                details="报告结论与数据一致",
            ),
            RubricItem(
                rubric_item_id="item-0002",
                criterion="分析深度",
                max_score=10,
                details="有解释与讨论",
            ),
        ],
        total_max_score=20,
    )
    return ResolvedRubric(
        definition=definition,
        source="built_in_default",
        text="结果准确性 10 分；分析深度 10 分",
        snapshot_id="snap-1",
    )


def _sub() -> SimpleNamespace:
    return SimpleNamespace(
        code_files=[],
        ocr_text="The model achieved 92% accuracy on the test set.",
    )


def _payload(*, first_score: float, quote: str | None = None) -> GradingAssessmentRequest:
    refs = [{"type": "report_quote", "quote": quote}] if quote else []
    return GradingAssessmentRequest(
        request_id=UUID(int=0),
        confidence=0.9,
        feedback="整体完成度较好",
        details=[
            {
                "rubric_item_id": "item-0001",
                "score": first_score,
                "comment": "结果有报告原文支撑",
                "evidence_refs": refs,
                "evidence_status": "supported",
            },
            {
                "rubric_item_id": "item-0002",
                "score": 0,
                "comment": "未找到分析段落",
                "evidence_refs": [],
                "evidence_status": "not_found",
            },
        ],
    )


def test_valid_payload_passes() -> None:
    validate_assessment_payload(
        _sub(),
        _payload(first_score=5, quote="92% accuracy"),
        _resolved(),
    )


def test_missing_rubric_item_is_rejected() -> None:
    payload = _payload(first_score=5, quote="92% accuracy")
    payload.details = payload.details[:1]
    with pytest.raises(ValidationError, match="未完整覆盖"):
        validate_assessment_payload(_sub(), payload, _resolved())


def test_score_over_max_is_rejected() -> None:
    with pytest.raises(ValidationError, match="不能超过该项满分"):
        validate_assessment_payload(
            _sub(),
            _payload(first_score=11, quote="92% accuracy"),
            _resolved(),
        )


def test_unlocatable_report_quote_is_rejected() -> None:
    with pytest.raises(ValidationError) as excinfo:
        validate_assessment_payload(
            _sub(),
            _payload(first_score=5, quote="这句话不在报告里"),
            _resolved(),
        )
    assert "报告证据无法定位" in str(excinfo.value)
