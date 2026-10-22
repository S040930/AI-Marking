"""ChatGPT 订阅 provider 测试:请求形态、SSE 映射、令牌刷新与错误分类。"""

from __future__ import annotations

import json
import time

import httpx
import pytest

from app.agent.models import TurnRequest
from app.agent.provider import (
    AgentProviderError,
    ProviderTextDelta,
    ProviderToolCallStarted,
    ProviderTurnCompleted,
)
from app.agent.providers import chatgpt_sub
from app.agent.providers.chatgpt_sub import BASE_URL, ChatGptSubProvider


def _sse(events: list[tuple[str, dict]]) -> bytes:
    chunks = []
    for name, payload in events:
        chunks.append(f"event: {name}\ndata: {json.dumps(payload)}\n\n")
    return "".join(chunks).encode()


def _completed(output: list[dict], usage: dict | None = None) -> tuple[str, dict]:
    return (
        "response.completed",
        {
            "type": "response.completed",
            "response": {
                "status": "completed",
                "output": output,
                "usage": usage or {"input_tokens": 10, "output_tokens": 2},
            },
        },
    )


def _provider(
    handler,
    *,
    tokens: dict | None = None,
    sink=None,
    loader=None,
    lease_acquire=None,
    lease_release=None,
    **kwargs,
) -> ChatGptSubProvider:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return ChatGptSubProvider(
        tokens=tokens
        or {
            "access_token": "access-1",
            "refresh_token": "refresh-1",
            "account_id": "acct-1234",
            "plan": "plus",
            "expires_at": int(time.time()) + 3600,
        },
        default_model="gpt-6.1-sol",
        http_client=client,
        on_tokens_refreshed=sink,
        load_tokens=loader,
        refresh_lease_acquire=lease_acquire,
        refresh_lease_release=lease_release,
        **kwargs,
    )


def _expired_tokens(**overrides) -> dict:
    tokens = {
        "access_token": "access-old",
        "refresh_token": "refresh-1",
        "account_id": "acct-1234",
        "plan": "plus",
        "expires_at": int(time.time()) - 10,
    }
    tokens.update(overrides)
    return tokens


def _request() -> TurnRequest:
    return TurnRequest(
        instructions="sys",
        items=[{"role": "user", "content": "hi"}],
        tools=[],
        model=None,
        reasoning_effort="low",
        max_output_tokens=128,
    )


async def test_text_turn_sends_required_shape_and_returns_turn_state():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            headers={"x-codex-turn-state": "ts-1"},
            content=_sse(
                [
                    ("response.output_text.delta", {"delta": "OK"}),
                    _completed(
                        [
                            {
                                "type": "message",
                                "role": "assistant",
                                "content": [{"type": "output_text", "text": "OK"}],
                            }
                        ]
                    ),
                ]
            ),
        )

    provider = _provider(handler)
    events = [event async for event in provider.create_turn(_request())]
    await provider.aclose()

    assert captured["url"] == f"{BASE_URL}/responses"
    assert captured["body"]["store"] is False
    assert captured["body"]["stream"] is True
    assert captured["body"]["model"] == "gpt-6.1-sol"
    assert "prompt_cache_key" not in captured["body"]
    assert captured["headers"]["originator"] == "ai_marking"
    assert captured["headers"]["chatgpt-account-id"] == "acct-1234"
    assert any(isinstance(event, ProviderTextDelta) for event in events)
    completed = next(e for e in events if isinstance(e, ProviderTurnCompleted))
    assert completed.provider_state == {"turn_state": "ts-1"}
    assert completed.usage == {"input_tokens": 10, "output_tokens": 2}


async def test_tool_call_items_are_returned_for_engine():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=_sse(
                [
                    (
                        "response.output_item.added",
                        {
                            "output_index": 0,
                            "item": {
                                "type": "function_call",
                                "call_id": "call_1",
                                "name": "probe_echo",
                            },
                        },
                    ),
                    (
                        "response.function_call_arguments.delta",
                        {"output_index": 0, "delta": '{"value":"PING"}'},
                    ),
                    _completed(
                        [
                            {
                                "type": "function_call",
                                "call_id": "call_1",
                                "name": "probe_echo",
                                "arguments": '{"value":"PING"}',
                            }
                        ]
                    ),
                ]
            ),
        )

    provider = _provider(handler)
    events = [event async for event in provider.create_turn(_request())]
    await provider.aclose()

    started = [e for e in events if isinstance(e, ProviderToolCallStarted)]
    assert started and started[0].call_id == "call_1"
    completed = next(e for e in events if isinstance(e, ProviderTurnCompleted))
    assert completed.response_items[0]["arguments"] == '{"value":"PING"}'


async def test_turn_state_from_provider_state_is_replayed_as_header():
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["turn_state"] = request.headers.get("x-codex-turn-state", "")
        return httpx.Response(
            200, content=_sse([_completed([{"type": "message", "content": []}])])
        )

    provider = _provider(handler)
    request = TurnRequest(
        instructions="sys",
        items=[],
        tools=[],
        model="gpt-6.1-sol",
        provider_state={"turn_state": "ts-old"},
    )
    async for _ in provider.create_turn(request):
        pass
    await provider.aclose()
    assert seen["turn_state"] == "ts-old"


async def test_expired_token_refreshes_and_persists():
    saved: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "auth.openai.com":
            assert b"grant_type=refresh_token" in request.content
            return httpx.Response(
                200,
                json={
                    "access_token": "access-2",
                    "refresh_token": "refresh-2",
                    "expires_in": 3600,
                },
            )
        assert request.headers["authorization"] == "Bearer access-2"
        return httpx.Response(200, content=_sse([_completed([])]))

    provider = _provider(
        handler,
        tokens={
            "access_token": "access-1",
            "refresh_token": "refresh-1",
            "expires_at": int(time.time()) - 10,
        },
        sink=saved.append,
    )
    async for _ in provider.create_turn(_request()):
        pass
    await provider.aclose()

    assert saved and saved[0]["access_token"] == "access-2"
    assert saved[0]["refresh_token"] == "refresh-2"


async def test_401_without_refresh_raises_auth_required():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401, json={"error": {"message": "Could not parse your token"}}
        )

    provider = _provider(
        handler,
        tokens={"access_token": "bad", "expires_at": int(time.time()) + 3600},
    )
    with pytest.raises(AgentProviderError) as excinfo:
        async for _ in provider.create_turn(_request()):
            pass
    await provider.aclose()
    assert excinfo.value.kind == "auth_required"


async def test_400_keeps_server_message_for_bad_model():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={
                "error": {
                    "message": "The 'nope' model is not supported when using Codex"
                }
            },
        )

    provider = _provider(handler)
    with pytest.raises(AgentProviderError) as excinfo:
        async for _ in provider.create_turn(_request()):
            pass
    await provider.aclose()
    assert excinfo.value.kind == "invalid_request"
    assert "not supported" in excinfo.value.message


async def test_429_maps_to_rate_limited_with_retry_after():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429, headers={"Retry-After": "7"}, json={"error": {"message": "slow down"}}
        )

    provider = _provider(handler)
    with pytest.raises(AgentProviderError) as excinfo:
        async for _ in provider.create_turn(_request()):
            pass
    await provider.aclose()
    assert excinfo.value.kind == "rate_limited"
    assert excinfo.value.retry_after == 7
    assert excinfo.value.retryable is True


async def test_list_models_filters_unverified_efforts():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["client_version"]
        return httpx.Response(
            200,
            json={
                "models": [
                    {
                        "slug": "gpt-6.1-sol",
                        "display_name": "GPT-6.1-Sol",
                        "supported_reasoning_levels": [
                            {"effort": "low"},
                            {"effort": "ultra"},
                            {"effort": "max"},
                        ],
                    }
                ]
            },
        )

    provider = _provider(handler)
    models = await provider.list_models()
    await provider.aclose()
    assert models[0].id == "gpt-6.1-sol"
    assert models[0].efforts == ("low", "max")


async def test_rejected_turn_state_is_dropped_and_retried():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        state = request.headers.get("x-codex-turn-state", "")
        calls.append(state)
        if state:
            return httpx.Response(
                400,
                json={"error": {"message": "invalid turn state header"}},
            )
        return httpx.Response(200, content=_sse([_completed([])]))

    provider = _provider(handler)
    request = TurnRequest(
        instructions="sys",
        items=[],
        tools=[],
        model="gpt-6.1-sol",
        provider_state={"turn_state": "stale"},
    )
    events = [event async for event in provider.create_turn(request)]
    await provider.aclose()

    assert calls == ["stale", ""]
    assert any(isinstance(event, ProviderTurnCompleted) for event in events)


async def test_default_model_falls_back_to_catalog_first_entry():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "models": [
                        {"slug": "gpt-6.1-sol", "display_name": "GPT-6.1-Sol"},
                        {"slug": "gpt-6-luna", "display_name": "GPT-6-Luna"},
                    ]
                },
            )
        seen["model"] = json.loads(request.content)["model"]
        return httpx.Response(200, content=_sse([_completed([])]))

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = ChatGptSubProvider(
        tokens={
            "access_token": "access-1",
            "expires_at": int(time.time()) + 3600,
        },
        default_model=None,
        http_client=client,
    )
    request = TurnRequest(instructions="sys", items=[], tools=[], model=None)
    async for _ in provider.create_turn(request):
        pass
    await provider.aclose()
    assert seen["model"] == "gpt-6.1-sol"


async def test_refresh_failure_falls_back_to_persisted_tokens():
    """refresh 被拒但库里已有别处轮换出的新令牌:直接用新令牌,不要求重登。"""
    calls = {"token": 0, "responses": 0}
    fresh = {
        "access_token": "loaded-access",
        "refresh_token": "loaded-refresh",
        "expires_at": int(time.time()) + 3600,
    }
    loads = {"count": 0}

    def loader():
        loads["count"] += 1
        # _ensure_token 与 _refresh 入锁各回读一次，均未命中；刷新失败后
        # 第三次回读才拿到别处轮换出的新令牌。
        return None if loads["count"] <= 2 else fresh

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "auth.openai.com":
            calls["token"] += 1
            return httpx.Response(400, json={"error": "invalid_grant"})
        calls["responses"] += 1
        assert request.headers["authorization"] == "Bearer loaded-access"
        return httpx.Response(200, content=_sse([_completed([])]))

    provider = _provider(
        handler,
        tokens={
            "access_token": "stale",
            "refresh_token": "old-refresh",
            "expires_at": int(time.time()) - 10,
        },
    )
    provider._load_tokens = loader
    events = [event async for event in provider.create_turn(_request())]
    await provider.aclose()

    assert calls["token"] == 1
    assert calls["responses"] == 1
    assert any(isinstance(event, ProviderTurnCompleted) for event in events)


async def test_401_reloads_persisted_tokens_before_refreshing():
    """401 后先看库:另一个进程已换过令牌时不再消耗 refresh_token。"""
    calls = {"token": 0, "responses": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "auth.openai.com":
            calls["token"] += 1
            return httpx.Response(500, json={"error": "should not be called"})
        calls["responses"] += 1
        if calls["responses"] == 1:
            return httpx.Response(401, json={"error": {"message": "expired"}})
        assert request.headers["authorization"] == "Bearer rotated"
        return httpx.Response(200, content=_sse([_completed([])]))

    provider = _provider(handler)
    provider._load_tokens = lambda: {
        "access_token": "rotated",
        "refresh_token": "rotated-refresh",
        "expires_at": int(time.time()) + 3600,
    }
    events = [event async for event in provider.create_turn(_request())]
    await provider.aclose()

    assert calls["token"] == 0
    assert calls["responses"] == 2
    assert any(isinstance(event, ProviderTurnCompleted) for event in events)


async def test_concurrent_turns_share_one_refresh_request():
    """并发回合只发一次 refresh(轮换令牌只能消费一次)。"""
    import asyncio

    token_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal token_calls
        if request.url.host == "auth.openai.com":
            token_calls += 1
            time.sleep(0.05)
            return httpx.Response(
                200,
                json={
                    "access_token": "new-access",
                    "refresh_token": "new-refresh",
                    "expires_in": 3600,
                },
            )
        return httpx.Response(200, content=_sse([_completed([])]))

    provider = _provider(
        handler,
        tokens={
            "access_token": "expired-access",
            "refresh_token": "old-refresh",
            "expires_at": int(time.time()) - 10,
        },
    )

    async def one_turn():
        return [event async for event in provider.create_turn(_request())]

    results = await asyncio.gather(one_turn(), one_turn())
    await provider.aclose()

    assert token_calls == 1
    assert all(any(isinstance(e, ProviderTurnCompleted) for e in r) for r in results)


# ---------------------------------------------------------------------------
# 令牌轮换:保留账号字段 / 落库失败 / 跨进程租约 / P2 硬化
# ---------------------------------------------------------------------------


async def test_refresh_preserves_account_and_plan_when_id_token_missing():
    """刷新响应不带 id_token 时,不能把 account_id/plan 覆盖成空串。"""
    saved: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "auth.openai.com":
            return httpx.Response(
                200,
                json={
                    "access_token": "access-new",
                    "refresh_token": "refresh-2",
                    "expires_in": 3600,
                },
            )
        return httpx.Response(200, content=_sse([_completed([])]))

    provider = _provider(handler, tokens=_expired_tokens(), sink=saved.append)
    [event async for event in provider.create_turn(_request())]
    await provider.aclose()

    assert saved and saved[0]["access_token"] == "access-new"
    assert saved[0]["account_id"] == "acct-1234"
    assert saved[0]["plan"] == "plus"


async def test_refresh_persist_failure_is_retried_then_succeeds(monkeypatch):
    monkeypatch.setattr(chatgpt_sub, "_PERSIST_BACKOFF_SECONDS", 0)
    calls = {"n": 0}

    def sink(tokens: dict) -> None:
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("db down")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "auth.openai.com":
            return httpx.Response(
                200,
                json={"access_token": "access-new", "refresh_token": "r2", "expires_in": 3600},
            )
        return httpx.Response(200, content=_sse([_completed([])]))

    provider = _provider(
        handler, tokens=_expired_tokens(), sink=sink, persist_attempts=3
    )
    events = [event async for event in provider.create_turn(_request())]
    await provider.aclose()

    assert calls["n"] == 3
    assert any(isinstance(e, ProviderTurnCompleted) for e in events)


async def test_refresh_persist_permanent_failure_raises_retryable(monkeypatch):
    monkeypatch.setattr(chatgpt_sub, "_PERSIST_BACKOFF_SECONDS", 0)

    def sink(tokens: dict) -> None:
        raise RuntimeError("db down")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "auth.openai.com":
            return httpx.Response(
                200,
                json={"access_token": "access-new", "refresh_token": "r2", "expires_in": 3600},
            )
        return httpx.Response(200, content=_sse([_completed([])]))

    provider = _provider(handler, tokens=_expired_tokens(), sink=sink)
    with pytest.raises(AgentProviderError) as excinfo:
        [event async for event in provider.create_turn(_request())]
    await provider.aclose()

    assert excinfo.value.kind == "retryable"
    assert "保存失败" in excinfo.value.message


async def test_next_turn_flushes_pending_tokens_without_second_refresh(monkeypatch):
    """落库失败后,下一回合先补救,不再消费一次 refresh_token。"""
    monkeypatch.setattr(chatgpt_sub, "_PERSIST_BACKOFF_SECONDS", 0)
    token_calls = {"n": 0}
    broken = {"on": True}

    def sink(tokens: dict) -> None:
        if broken["on"]:
            raise RuntimeError("db down")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "auth.openai.com":
            token_calls["n"] += 1
            return httpx.Response(
                200,
                json={"access_token": "access-new", "refresh_token": "r2", "expires_in": 3600},
            )
        return httpx.Response(200, content=_sse([_completed([])]))

    provider = _provider(handler, tokens=_expired_tokens(), sink=sink)
    with pytest.raises(AgentProviderError):
        [event async for event in provider.create_turn(_request())]

    broken["on"] = False
    [event async for event in provider.create_turn(_request())]
    await provider.aclose()

    assert token_calls["n"] == 1


async def test_lease_busy_waits_for_other_process_tokens():
    """拿不到跨进程租约时等待对方写库,而不是再消费一次 refresh_token。"""
    loads = {"n": 0}

    def loader() -> dict:
        loads["n"] += 1
        if loads["n"] <= 2:
            return _expired_tokens()
        return {
            "access_token": "access-new",
            "refresh_token": "refresh-2",
            "account_id": "acct-1234",
            "plan": "plus",
            "expires_at": int(time.time()) + 3600,
        }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host != "auth.openai.com"
        assert request.headers["authorization"] == "Bearer access-new"
        return httpx.Response(200, content=_sse([_completed([])]))

    provider = _provider(
        handler,
        tokens=_expired_tokens(),
        loader=loader,
        lease_acquire=lambda ttl: False,
        refresh_wait_seconds=1.0,
        refresh_poll_seconds=0.01,
    )
    events = [event async for event in provider.create_turn(_request())]
    await provider.aclose()

    assert any(isinstance(e, ProviderTurnCompleted) for e in events)


async def test_lease_timeout_is_retryable_not_auth_required():
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("不应发起任何请求")

    provider = _provider(
        handler,
        tokens=_expired_tokens(),
        loader=lambda: _expired_tokens(),
        lease_acquire=lambda ttl: False,
        refresh_wait_seconds=0.05,
        refresh_poll_seconds=0.01,
    )
    with pytest.raises(AgentProviderError) as excinfo:
        [event async for event in provider.create_turn(_request())]
    await provider.aclose()

    assert excinfo.value.kind == "retryable"
    assert "其他进程" in excinfo.value.message


async def test_lease_is_released_after_refresh():
    released: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "auth.openai.com":
            return httpx.Response(
                200,
                json={"access_token": "access-new", "refresh_token": "r2", "expires_in": 3600},
            )
        return httpx.Response(200, content=_sse([_completed([])]))

    provider = _provider(
        handler,
        tokens=_expired_tokens(),
        sink=lambda tokens: None,
        lease_acquire=lambda ttl: True,
        lease_release=lambda: released.append(1),
    )
    [event async for event in provider.create_turn(_request())]
    await provider.aclose()

    assert released == [1]


async def test_list_models_refreshes_once_on_401():
    calls = {"models": 0, "token": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "auth.openai.com":
            calls["token"] += 1
            return httpx.Response(
                200,
                json={"access_token": "access-new", "refresh_token": "r2", "expires_in": 3600},
            )
        calls["models"] += 1
        if calls["models"] == 1:
            return httpx.Response(401, json={"error": {"message": "expired"}})
        return httpx.Response(
            200,
            json={
                "models": [
                    {
                        "slug": "gpt-6.1-sol",
                        "display_name": "GPT-6.1-Sol",
                        "supported_reasoning_levels": [{"effort": "low"}],
                    }
                ]
            },
        )

    provider = _provider(handler, sink=lambda tokens: None)
    models = await provider.list_models()
    await provider.aclose()

    assert [model.id for model in models] == ["gpt-6.1-sol"]
    assert calls == {"models": 2, "token": 1}


async def test_redirect_response_is_not_success():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(307, headers={"Location": "https://example.com/x"})

    provider = _provider(handler)
    with pytest.raises(AgentProviderError) as excinfo:
        [event async for event in provider.create_turn(_request())]
    await provider.aclose()

    assert excinfo.value.kind == "invalid_request"
    assert "重定向" in excinfo.value.message


async def test_empty_catalog_does_not_fabricate_models():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"models": []})

    provider = _provider(handler)
    models = await provider.list_models()
    await provider.aclose()

    assert models == []


async def test_429_quota_exhausted_is_not_retryable():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            headers={
                "x-codex-primary-used-percent": "100",
                "x-codex-primary-reset-after-seconds": "36000",
            },
            json={"error": {"message": ""}},
        )

    provider = _provider(handler)
    with pytest.raises(AgentProviderError) as excinfo:
        [event async for event in provider.create_turn(_request())]
    await provider.aclose()

    assert excinfo.value.kind == "rate_limited"
    assert excinfo.value.retryable is False
    assert "配额已用尽" in excinfo.value.message


async def test_429_short_codex_reset_sets_retry_after():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            headers={
                "x-codex-primary-used-percent": "3",
                "x-codex-primary-reset-after-seconds": "7",
            },
            json={"error": {"message": ""}},
        )

    provider = _provider(handler)
    with pytest.raises(AgentProviderError) as excinfo:
        [event async for event in provider.create_turn(_request())]
    await provider.aclose()

    assert excinfo.value.retryable is True
    assert excinfo.value.retry_after == 7.0
