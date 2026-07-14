"""受约束批改 Agent 的路由测试。"""

import pytest

from app.services import agent


def _draft(score: float = 80) -> dict:
    return {
        "score": score,
        "max_score": 100,
        "feedback": "总体反馈",
        "details": [
            {
                "criterion": "内容",
                "score": score,
                "max_score": 100,
                "comment": "评分说明",
                "evidence": ["原文证据"],
            }
        ],
    }


def _critic(decision: str, confidence: float, issues: list[str] | None = None) -> dict:
    return {
        "decision": decision,
        "confidence": confidence,
        "issues": issues or [],
        "summary": "独立复核完成",
        "revision_instructions": "修正评分问题",
    }


async def _run_with_responses(monkeypatch, responses: list[dict]):
    queue = responses.copy()

    async def fake_completion(config, system_prompt, user_prompt, schema, **kwargs):
        return schema.model_validate(queue.pop(0))

    monkeypatch.setattr(agent, "_json_completion", fake_completion)
    return await agent.run_marking_agent(
        "学生作业正文",
        {"rubric": "内容 100 分", "llm_api_key": "test"},
    )


@pytest.mark.asyncio
async def test_agent_approves_valid_draft(monkeypatch):
    result = await _run_with_responses(
        monkeypatch,
        [_draft(), _critic("approve", 0.9)],
    )

    assert result["outcome"] == "done"
    assert result["revision_count"] == 0
    assert [item["node"] for item in result["trace"]] == [
        "grade",
        "validate",
        "critic",
    ]


@pytest.mark.asyncio
async def test_agent_revises_only_once(monkeypatch):
    result = await _run_with_responses(
        monkeypatch,
        [
            _draft(70),
            _critic("revise", 0.6, ["证据不足"]),
            _draft(75),
            _critic("approve", 0.85),
        ],
    )

    assert result["outcome"] == "done"
    assert result["revision_count"] == 1
    assert [item["node"] for item in result["trace"]] == [
        "grade",
        "validate",
        "critic",
        "revise",
        "validate",
        "critic",
    ]


@pytest.mark.asyncio
async def test_agent_routes_second_rejection_to_human(monkeypatch):
    result = await _run_with_responses(
        monkeypatch,
        [
            _draft(70),
            _critic("revise", 0.6, ["证据不足"]),
            _draft(72),
            _critic("revise", 0.65, ["仍缺少证据"]),
        ],
    )

    assert result["outcome"] == "review_required"
    assert "仍缺少证据" in result["review_reason"]
