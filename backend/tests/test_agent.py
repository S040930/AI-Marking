"""受约束批改 Agent 的路由测试。"""

import pytest

from app.services import agent
from app.services.rubric import normalize_definition

_TEST_ITEM_ID = normalize_definition({"items": [{"criterion": "内容", "max_score": 100, "details": "评分细则"}], "total_max_score": 100}).items[0].rubric_item_id


def _draft(score: float = 80) -> dict:
    return {
        "score": score,
        "max_score": 100,
        "feedback": "总体反馈",
        "details": [
            {
                "criterion": "内容",
                "rubric_item_id": _TEST_ITEM_ID,
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


async def _run_with_responses(
    monkeypatch,
    responses: list[dict],
    *,
    review_enabled: bool = True,
    cached_rubric: str = "",
    capture_prompts: list | None = None,
    config: dict | None = None,
):
    queue = responses.copy()

    async def fake_completion(config, system_prompt, user_prompt, schema, **kwargs):
        if capture_prompts is not None:
            capture_prompts.append(user_prompt)
        return schema.model_validate(queue.pop(0))

    monkeypatch.setattr(agent, "_json_completion", fake_completion)
    return await agent.run_marking_agent(
        "学生作业正文",
        config or {"rubric_definition": {"items": [{"criterion": "内容", "max_score": 100, "details": "评分细则"}], "total_max_score": 100}, "llm_api_key": "test"},
        review_enabled=review_enabled,
        cached_rubric=cached_rubric,
    )


@pytest.mark.asyncio
async def test_agent_review_disabled_skips_critic(monkeypatch):
    """复核关闭时跳过 critic/revise,直接完成。"""
    result = await _run_with_responses(
        monkeypatch,
        [_draft()],
        review_enabled=False,
    )

    assert result["outcome"] == "done"
    assert result.get("critic") is None
    assert [item["node"] for item in result["trace"]] == ["grade", "validate"]


@pytest.mark.asyncio
async def test_agent_uses_resolved_rubric_in_grade_prompt(monkeypatch):
    """Agent 使用服务端解析的结构化 rubric，不读取旧缓存文本。"""
    prompts: list[str] = []
    await _run_with_responses(
        monkeypatch,
        [_draft(), _critic("approve", 0.9)],
        cached_rubric="题目识别出的缓存细则",
        capture_prompts=prompts,
        config={"rubric_definition": {"items": [{"criterion": "内容", "max_score": 100, "details": "评分细则"}], "total_max_score": 100}, "llm_api_key": "test"},
    )

    assert prompts
    assert "rubric_" in prompts[0]
    assert "题目识别出的缓存细则" not in prompts[0]


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


@pytest.mark.asyncio
async def test_run_critic_pass_returns_critic_dict(monkeypatch):
    """独立复核入口返回 CriticResult 字段字典,且走审核 LLM。"""
    prompts: list[str] = []

    async def fake_completion(config, system_prompt, user_prompt, schema, **kwargs):
        assert kwargs.get("use_review") is True
        prompts.append(user_prompt)
        return schema.model_validate(_critic("approve", 0.9))

    monkeypatch.setattr(agent, "_json_completion", fake_completion)
    result = await agent.run_critic_pass(
        {"review_llm_api_key": "test"},
        ocr_text="学生作业正文",
        question_text="作业题目",
        rubric="内容 100 分",
        draft=_draft(),
    )

    assert result["decision"] == "approve"
    assert result["confidence"] == 0.9
    assert prompts and "学生作业正文" in prompts[0]


@pytest.mark.asyncio
async def test_run_critic_pass_falls_back_on_review_llm_error(monkeypatch):
    """复核 LLM 不可用时降级为 review_required,不抛出。"""

    async def fake_completion(config, system_prompt, user_prompt, schema, **kwargs):
        raise agent.AgentError("审核 LLM 未配置")

    monkeypatch.setattr(agent, "_json_completion", fake_completion)
    result = await agent.run_critic_pass(
        {"llm_api_key": "test"},
        ocr_text="学生作业正文",
        draft=_draft(),
    )

    assert result["decision"] == "review_required"
    assert result["confidence"] == 0
    assert "自动复核服务不可用" in result["issues"]


def test_llm_client_cache_is_bounded(monkeypatch):
    """M2:LLM 客户端缓存有界,超出 ``_LLM_CLIENT_CACHE_MAX`` 时逐出最旧条目。"""
    monkeypatch.setattr(agent, "_llm_clients", {})
    monkeypatch.setattr(agent, "_LLM_CLIENT_CACHE_MAX", 4)

    keys = [f"k{i}" for i in range(6)]
    for i, key in enumerate(keys):
        cfg = {"llm_api_key": key, "llm_base_url": f"https://b{i}.example.com"}
        first = agent._client(cfg)
        assert agent._client(cfg) is first, "相同配置应复用同一客户端"

    assert len(agent._llm_clients) == agent._LLM_CLIENT_CACHE_MAX
    assert (
        keys[0],
        "https://b0.example.com",
    ) not in agent._llm_clients, "最旧条目应被逐出"
    assert (keys[1], "https://b1.example.com") not in agent._llm_clients
    assert (keys[-1], "https://b5.example.com") in agent._llm_clients

    # 逐出的 key 再次请求时仍能重建新客户端(功能即时生效)
    refetched = agent._client(
        {"llm_api_key": keys[0], "llm_base_url": "https://b0.example.com"}
    )
    assert refetched is not None
    assert len(agent._llm_clients) <= agent._LLM_CLIENT_CACHE_MAX


def test_review_client_reuses_shared_bounded_cache(monkeypatch):
    """M2:审核客户端与批改客户端共享同一有界缓存,同一 key 复用同一实例。"""
    monkeypatch.setattr(agent, "_llm_clients", {})
    monkeypatch.setattr(agent, "_LLM_CLIENT_CACHE_MAX", 2)

    grading = agent._client(
        {"llm_api_key": "k1", "llm_base_url": "https://a.example.com"}
    )
    reviewing = agent._review_client(
        {"review_llm_api_key": "k1", "review_llm_base_url": "https://a.example.com"}
    )
    assert reviewing is grading
    assert len(agent._llm_clients) == 1
