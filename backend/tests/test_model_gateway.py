"""模型网关测试：覆盖回合循环、工具回注、检查点配对与取消/重试语义。"""

from __future__ import annotations

import inspect

from app.agent.graph.model_gateway import ModelGateway, prompt_cache_key
from app.agent.models import ToolResult, ToolSpec, TurnRequest
from app.agent.provider import (
    AgentProviderError,
    ModelOption,
    ProviderAuthStatus,
    ProviderReasoningDelta,
    ProviderTextDelta,
    ProviderTurnCompleted,
)
from app.agent.tools import CheckpointRequested, ToolRegistry


class ScriptedProvider:
    name = "scripted"
    default_model = "scripted-model"

    def __init__(self, script):
        self.script = list(script)
        self.requests: list[TurnRequest] = []

    def create_turn(self, request):
        self.requests.append(request)
        turn = self.script.pop(0)

        async def _gen():
            for event in turn:
                if isinstance(event, Exception):
                    raise event
                if inspect.isawaitable(event):
                    await event
                else:
                    yield event

        return _gen()

    async def list_models(self):
        return [ModelOption(id="scripted-model")]

    def auth_status(self):
        return ProviderAuthStatus(ready=True)

    async def aclose(self):
        pass


def _call(call_id: str, name: str, arguments: str = "{}") -> dict:
    return {
        "type": "function_call",
        "call_id": call_id,
        "name": name,
        "arguments": arguments,
    }


def _tool(name: str, handler, *, writes: bool = False) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name=name,
            description=name,
            parameters={"type": "object"},
            writes=writes,
        ),
        handler,
    )
    return registry


async def _run(
    provider,
    tools,
    *,
    agent="supervisor",
    cache_key=None,
    should_cancel=None,
    max_rounds=40,
):
    events = []

    async def emit(event):
        events.append(event)

    gateway = ModelGateway(
        provider,
        tools,
        instructions="test instructions",
        agent=agent,
        model="scripted-model",
        max_rounds=max_rounds,
        cancel_poll_seconds=0.05,
        cache_key=cache_key,
        should_cancel=should_cancel,
    )
    result = await gateway.run_tool_loop([], emit)
    return result, events


async def test_tool_roundtrip_completes_and_tags_agent() -> None:
    provider = ScriptedProvider(
        [
            [
                ProviderTextDelta("working"),
                ProviderTurnCompleted(
                    stop_reason="tool_calls",
                    response_items=[_call("c1", "save_assessment")],
                    usage={"input_tokens": 10, "output_tokens": 3},
                ),
            ]
        ]
    )
    tools = _tool(
        "save_assessment",
        lambda args: ToolResult(ok=True, output={"saved": True}),
        writes=True,
    )
    result, events = await _run(provider, tools, agent="item:r1")

    assert result.status == "completed"
    assert result.rounds == 1
    assert any(event.kind == "tool_started" for event in events)
    assert any(event.kind == "tool_finished" for event in events)
    assert all(event.payload.get("agent") == "item:r1" for event in events)
    assert provider.requests[0].prompt_cache_key is None


async def test_checkpoint_pairs_function_call_output() -> None:
    def ask(args):
        raise CheckpointRequested("请确认代码运行结果")

    provider = ScriptedProvider(
        [
            [
                ProviderTurnCompleted(
                    stop_reason="tool_calls",
                    response_items=[_call("c1", "request_teacher_confirmation")],
                )
            ]
        ]
    )
    result, events = await _run(
        provider, _tool("request_teacher_confirmation", ask)
    )

    assert result.status == "checkpoint"
    assert result.checkpoint_message == "请确认代码运行结果"
    outputs = [
        item for item in result.items if item.get("type") == "function_call_output"
    ]
    assert len(outputs) == 1
    assert "pending_teacher_confirmation" in outputs[0]["output"]
    assert any(event.kind == "checkpoint_requested" for event in events)


async def test_tool_error_is_fed_back_to_model() -> None:
    provider = ScriptedProvider(
        [
            [
                ProviderTurnCompleted(
                    stop_reason="tool_calls",
                    response_items=[_call("c1", "read_package_slice")],
                )
            ],
            [
                ProviderTurnCompleted(
                    stop_reason="stop",
                    response_items=[{"role": "assistant", "content": "done"}],
                )
            ],
        ]
    )
    result, _ = await _run(
        provider,
        _tool(
            "read_package_slice",
            lambda args: ToolResult(ok=False, error="unknown section"),
        ),
    )

    assert result.status == "incomplete"
    second_items = provider.requests[1].items
    feedback = [
        item for item in second_items if item.get("type") == "function_call_output"
    ]
    assert any("unknown section" in item["output"] for item in feedback)


async def test_prompt_cache_key_is_forwarded_only_when_set() -> None:
    provider = ScriptedProvider(
        [
            [
                ProviderTurnCompleted(
                    stop_reason="stop",
                    response_items=[{"role": "assistant", "content": "ok"}],
                    provider_state={"turn_state": "t-1"},
                )
            ]
        ]
    )
    key = prompt_cache_key(7, "test instructions")
    result, _ = await _run(provider, ToolRegistry(), cache_key=key)

    assert result.status == "incomplete"
    assert result.provider_state == {"turn_state": "t-1"}
    assert provider.requests[0].prompt_cache_key == key
    assert key.startswith("7:")


async def test_transport_error_is_retried_then_succeeds() -> None:
    provider = ScriptedProvider(
        [
            [AgentProviderError(kind="retryable", message="connection reset", retryable=True)],
            [
                ProviderTurnCompleted(
                    stop_reason="stop",
                    response_items=[{"role": "assistant", "content": "ok"}],
                )
            ],
        ]
    )
    result, events = await _run(provider, ToolRegistry())

    assert result.status == "incomplete"
    assert len(provider.requests) == 2
    assert any(event.kind == "notice" for event in events)


async def test_reasoning_delta_is_relayed() -> None:
    provider = ScriptedProvider(
        [
            [
                ProviderReasoningDelta("先看 rubric"),
                ProviderTurnCompleted(
                    stop_reason="stop",
                    response_items=[{"role": "assistant", "content": "ok"}],
                ),
            ]
        ]
    )
    result, events = await _run(provider, ToolRegistry())

    assert result.status == "incomplete"
    reasoning = [event for event in events if event.kind == "reasoning"]
    assert [event.payload.get("text") for event in reasoning] == ["先看 rubric"]


async def test_text_only_turn_ends_incomplete_and_streams_text() -> None:
    provider = ScriptedProvider(
        [
            [
                ProviderTextDelta("没有工具调用"),
                ProviderTurnCompleted(
                    stop_reason="stop",
                    response_items=[{"role": "assistant", "content": "没有工具调用"}],
                ),
            ]
        ]
    )
    result, events = await _run(provider, ToolRegistry())

    assert result.status == "incomplete"
    assert result.rounds == 1
    assert any(event.kind == "turn_completed" for event in events)
    assert any(
        event.kind == "message_delta" and "没有工具调用" in event.payload.get("text", "")
        for event in events
    )


async def test_cancel_flag_short_circuits_loop() -> None:
    provider = ScriptedProvider([])
    result, _ = await _run(
        provider, ToolRegistry(), should_cancel=lambda: True
    )

    assert result.status == "cancelled"
    assert provider.requests == []


async def test_max_rounds_stops_runaway_tool_loop() -> None:
    tool_call = [
        ProviderTurnCompleted(
            stop_reason="tool_calls",
            response_items=[_call("c1", "read_package_slice")],
        )
    ]
    provider = ScriptedProvider([tool_call, tool_call, tool_call])
    result, events = await _run(
        provider,
        _tool(
            "read_package_slice",
            lambda args: ToolResult(ok=True, output={"text": "..."}),
        ),
        max_rounds=3,
    )

    assert result.status == "failed"
    assert result.rounds == 3
    assert "最大回合数 3" in (result.error or "")
    assert any(event.kind == "error" for event in events)
