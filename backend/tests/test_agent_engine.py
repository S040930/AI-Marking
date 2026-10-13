"""AgentEngine 单测:脚本化 provider 驱动的 tool-calling 循环行为。"""

from __future__ import annotations

import asyncio
import inspect

from app.agent.engine import AgentEngine
from app.agent.models import EngineStatus, ToolResult, ToolSpec, TurnRequest
from app.agent.provider import (
    AgentProviderError,
    ModelOption,
    ProviderAuthStatus,
    ProviderReasoningDelta,
    ProviderTextDelta,
    ProviderToolCallStarted,
    ProviderTurnCompleted,
)
from app.agent.tools import CheckpointRequested, ToolRegistry


class ScriptedProvider:
    """按脚本逐回合产出事件;脚本项为事件列表或要抛出的异常。"""

    name = "scripted"
    default_model = "scripted-model"

    def __init__(self, script):
        self.script = list(script)
        self.requests: list[TurnRequest] = []

    def create_turn(self, request):
        self.requests.append(request)
        turn = self.script.pop(0)

        async def _gen():
            if isinstance(turn, BaseException):
                raise turn
            for event in turn:
                if inspect.isawaitable(event):
                    # 挂起型脚本项:让本回合永不自然结束(用于取消测试)
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


def _function_call(call_id: str, name: str, arguments: str) -> dict:
    return {
        "type": "function_call",
        "call_id": call_id,
        "name": name,
        "arguments": arguments,
    }


def _registry(**handlers) -> ToolRegistry:
    registry = ToolRegistry()
    for name, handler in handlers.items():
        registry.register(
            ToolSpec(name=name, description=name, parameters={"type": "object"}),
            handler,
        )
    return registry


def _engine(provider, tools, **kwargs) -> AgentEngine:
    kwargs.setdefault("model", "scripted-model")
    kwargs.setdefault("cancel_poll_seconds", 0.05)
    return AgentEngine(
        provider, tools, instructions="test instructions", **kwargs
    )


async def _collect(engine, items):
    events = []

    async def emit(event):
        events.append(event)

    outcome = await engine.run(items, emit)
    return outcome, events


async def test_text_only_turn_is_incomplete():
    provider = ScriptedProvider(
        [
            [
                ProviderTextDelta("hello "),
                ProviderTextDelta("world"),
                ProviderTurnCompleted("completed", [], {"input_tokens": 3}),
            ]
        ]
    )
    engine = _engine(provider, _registry())
    outcome, events = await _collect(engine, [{"role": "user", "content": "hi"}])
    assert outcome.status == EngineStatus.incomplete
    kinds = [e.kind for e in events]
    assert "message_delta" in kinds
    assert "turn_completed" in kinds
    text = "".join(
        e.payload["text"] for e in events if e.kind == "message_delta"
    )
    assert text == "hello world"
    assert provider.requests[0].instructions == "test instructions"


async def test_tool_round_trip_then_save_completes():
    registry = _registry(
        echo=lambda args: ToolResult(ok=True, output={"echo": args}),
        save_assessment=lambda args: ToolResult(ok=True, output={"status": "saved"}),
    )
    provider = ScriptedProvider(
        [
            [
                ProviderToolCallStarted("call_1", "echo"),
                ProviderTurnCompleted(
                    "completed",
                    [_function_call("call_1", "echo", '{"x": 1}')],
                    {"input_tokens": 10, "output_tokens": 5},
                ),
            ],
            [
                ProviderTurnCompleted(
                    "completed",
                    [_function_call("call_2", "save_assessment", '{"assessment": {}}')],
                    {"input_tokens": 20},
                ),
            ],
        ]
    )
    engine = _engine(provider, registry)
    outcome, events = await _collect(engine, [{"role": "user", "content": "grade"}])
    assert outcome.status == EngineStatus.completed
    assert outcome.rounds == 2
    kinds = [e.kind for e in events]
    assert "tool_started" in kinds
    finished = [e for e in events if e.kind == "tool_finished"]
    assert len(finished) == 2
    assert all(e.payload["ok"] for e in finished)
    # usage 跨回合累计
    usage_events = [e for e in events if e.kind == "usage"]
    assert usage_events[0].payload["input_tokens"] == 10
    assert usage_events[-1].payload["input_tokens"] == 20
    # 第二回合的请求携带第一回合工具输出与 function_call
    second_items = provider.requests[1].items
    assert any(item.get("type") == "function_call_output" for item in second_items)
    # 最终 history 包含全部调用
    types = [item.get("type") for item in outcome.items]
    assert types.count("function_call_output") == 2


async def test_checkpoint_outcome_stops_engine():
    def ask(args):
        raise CheckpointRequested("请确认运行结果与报告是否一致")

    registry = _registry(request_teacher_confirmation=ask)
    provider = ScriptedProvider(
        [
            [
                ProviderTurnCompleted(
                    "completed",
                    [
                        _function_call(
                            "call_1",
                            "request_teacher_confirmation",
                            '{"message": "请确认"}',
                        )
                    ],
                    None,
                ),
            ]
        ]
    )
    engine = _engine(provider, registry)
    outcome, events = await _collect(engine, [{"role": "user", "content": "go"}])
    assert outcome.status == EngineStatus.checkpoint
    assert "一致" in (outcome.checkpoint_message or "")
    assert any(e.kind == "checkpoint_requested" for e in events)
    # 检查点后不应再发起回合
    assert len(provider.requests) == 1


async def test_tool_error_returns_to_model():
    registry = _registry(
        flaky=lambda args: ToolResult(ok=False, error="引文无法定位，请修正 line")
    )
    provider = ScriptedProvider(
        [
            [
                ProviderTurnCompleted(
                    "completed",
                    [_function_call("call_1", "flaky", "{}")],
                    None,
                ),
            ],
            [ProviderTurnCompleted("completed", [], None)],
        ]
    )
    engine = _engine(provider, registry)
    outcome, events = await _collect(engine, [{"role": "user", "content": "go"}])
    assert outcome.status == EngineStatus.incomplete
    finished = [e for e in events if e.kind == "tool_finished"][0]
    assert finished.payload["ok"] is False
    # 失败信息回注给模型
    second_items = provider.requests[1].items
    outputs = [
        item for item in second_items if item.get("type") == "function_call_output"
    ]
    assert outputs and "无法定位" in outputs[0]["output"]


async def test_invalid_json_arguments_returned_to_model():
    registry = _registry(echo=lambda args: ToolResult(ok=True, output=args))
    provider = ScriptedProvider(
        [
            [
                ProviderTurnCompleted(
                    "completed",
                    [_function_call("call_1", "echo", "not-json")],
                    None,
                ),
            ],
            [ProviderTurnCompleted("completed", [], None)],
        ]
    )
    engine = _engine(provider, registry)
    _, events = await _collect(engine, [{"role": "user", "content": "go"}])
    finished = [e for e in events if e.kind == "tool_finished"][0]
    assert finished.payload["ok"] is False
    assert "JSON" in finished.payload["summary"]


async def test_transport_error_retries_then_recovers():
    provider = ScriptedProvider(
        [
            AgentProviderError(
                kind="retryable", message="boom", retryable=True
            ),
            [ProviderTurnCompleted("completed", [], None)],
        ]
    )
    engine = _engine(provider, _registry(), sleep=lambda _s: asyncio.sleep(0))
    outcome, events = await _collect(engine, [{"role": "user", "content": "go"}])
    assert outcome.status == EngineStatus.incomplete
    assert len(provider.requests) == 2
    assert any(
        e.kind == "notice" and "重试" in e.payload.get("text", "") for e in events
    )


async def test_non_retryable_error_fails():
    provider = ScriptedProvider(
        [AgentProviderError(kind="auth_required", message="bad key")]
    )
    engine = _engine(provider, _registry())
    outcome, events = await _collect(engine, [{"role": "user", "content": "go"}])
    assert outcome.status == EngineStatus.failed
    assert "bad key" in (outcome.error or "")
    assert any(e.kind == "error" for e in events)


async def test_retry_exhausted_fails():
    provider = ScriptedProvider(
        [
            AgentProviderError(kind="retryable", message="e1", retryable=True),
            AgentProviderError(kind="retryable", message="e2", retryable=True),
            AgentProviderError(kind="retryable", message="e3", retryable=True),
        ]
    )
    engine = _engine(provider, _registry(), sleep=lambda _s: asyncio.sleep(0))
    outcome, _ = await _collect(engine, [{"role": "user", "content": "go"}])
    assert outcome.status == EngineStatus.failed
    assert len(provider.requests) == 3  # 1 次原始 + 2 次重试


async def test_cancel_polling_stops_turn():
    state = {"calls": 0}

    def should_cancel():
        state["calls"] += 1
        return state["calls"] > 1

    provider = ScriptedProvider(
        [
            [
                ProviderTextDelta("working"),
                asyncio.Event().wait(),  # 永不完成的等待,交由取消轮询终止
            ]
        ]
    )
    engine = _engine(
        provider, _registry(), should_cancel=should_cancel, cancel_poll_seconds=0.05
    )
    outcome, _ = await _collect(engine, [{"role": "user", "content": "go"}])
    assert outcome.status == EngineStatus.cancelled


async def test_max_rounds_exhaustion_fails():
    registry = _registry(echo=lambda args: ToolResult(ok=True, output={}))
    turn = [
        ProviderTurnCompleted(
            "completed", [_function_call("call", "echo", "{}")], None
        )
    ]
    provider = ScriptedProvider([turn, turn])
    engine = _engine(provider, registry, max_rounds=2)
    outcome, events = await _collect(engine, [{"role": "user", "content": "go"}])
    assert outcome.status == EngineStatus.failed
    assert "最大回合数" in (outcome.error or "")
    assert any(e.kind == "error" for e in events)


async def test_reasoning_delta_is_emitted():
    provider = ScriptedProvider(
        [
            [
                ProviderReasoningDelta("thinking..."),
                ProviderTurnCompleted("completed", [], None),
            ]
        ]
    )
    engine = _engine(provider, _registry())
    _, events = await _collect(engine, [{"role": "user", "content": "go"}])
    assert any(
        e.kind == "reasoning" and e.payload["text"] == "thinking..." for e in events
    )
