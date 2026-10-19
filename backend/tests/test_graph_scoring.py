"""P4 评分分支的纯函数：分块、提交解析、错误扁平化。"""

from __future__ import annotations

import json

from app.agent.graph.scoring import (
    _chunks,
    _failed_ids,
    _flatten_validation,
    _parse_submissions,
)
from app.core.errors import ValidationError


def test_chunks_preserve_order_and_size() -> None:
    items = [{"rubric_item_id": f"r{i}"} for i in range(7)]
    groups = _chunks(items, 3)
    assert [len(group) for group in groups] == [3, 3, 1]
    assert groups[0][0]["rubric_item_id"] == "r0"
    assert groups[-1][-1]["rubric_item_id"] == "r6"


def _call(name: str, payload: dict) -> dict:
    return {
        "type": "function_call",
        "name": name,
        "arguments": json.dumps(payload, ensure_ascii=False),
    }


def test_parse_submissions_filters_unknown_and_malformed() -> None:
    items = [
        _call(
            "submit_item_score",
            {
                "rubric_item_id": "r1",
                "score": 5,
                "comment": "ok",
                "evidence_refs": [],
                "evidence_status": "supported",
            },
        ),
        _call(
            "submit_item_score",
            {
                "rubric_item_id": "unknown",
                "score": 1,
                "comment": "x",
                "evidence_refs": [],
                "evidence_status": "supported",
            },
        ),
        {"type": "function_call", "name": "submit_item_score", "arguments": "not json"},
        {"type": "function_call", "name": "other", "arguments": "{}"},
    ]
    parsed = _parse_submissions(items, {"r1"})
    assert list(parsed) == ["r1"]
    assert parsed["r1"]["score"] == 5.0


def test_flatten_validation_and_failed_ids() -> None:
    exc = ValidationError(
        {"message": "证据错误", "evidence": ["r1: 报告证据无法定位", "r2: 行号越界"]}
    )
    messages = _flatten_validation(exc)
    assert messages == ["r1: 报告证据无法定位", "r2: 行号越界"]
    assert _failed_ids(messages, {"r1", "r2"}) == ["r1", "r2"]
    assert _failed_ids(["评分项未完整覆盖"], {"r1"}) == []
