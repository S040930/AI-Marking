"""内嵌 agent 的中立数据模型:对话项、回合请求、引擎事件与结局。

对话项直接采用 Responses API 的 item 形态(``dict``),因为两条模型通道
(官方 API 与订阅适配器)都讲这套方言:无状态重放时把历史项原样放回
``input`` 即可,reasoning item(含 encrypted_content)也照原样回传。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

# 对话项:{"role": "user"/"assistant", "content": ...} 或
# {"type": "function_call"/"function_call_output"/"reasoning", ...}。
ChatItem = dict[str, Any]


def user_message(text: str) -> ChatItem:
    return {"role": "user", "content": text}


def function_call_output(call_id: str, output: Any) -> ChatItem:
    """工具执行结果回注;output 统一序列化为字符串。"""
    import json

    if isinstance(output, str):
        text = output
    else:
        text = json.dumps(output, ensure_ascii=False, default=str)
    return {"type": "function_call_output", "call_id": call_id, "output": text}


@dataclass(frozen=True)
class ToolSpec:
    """暴露给模型的单个工具定义。

    ``writes`` 只用于编排层做白名单：只有 supervisor 节点能拿到写工具；
    模型本身看不到这个字段。
    """

    name: str
    description: str
    parameters: dict[str, Any]
    strict: bool = False
    writes: bool = False


@dataclass
class ToolResult:
    """工具执行结果;``ok=False`` 时 ``error`` 作为可修正线索回给模型。"""

    ok: bool
    output: Any = None
    error: str | None = None


@dataclass(frozen=True)
class TurnRequest:
    """一次模型回合的无状态请求。

    - ``instructions`` 是系统级指令(prompts/grading.md 全文);
    - ``items`` 是完整历史(含上一回合的 function_call/输出与 reasoning);
    - 官方通道持久化 ``store=false``,每次重放完整上下文。
    """

    instructions: str
    items: list[ChatItem]
    tools: list[ToolSpec]
    model: str | None = None
    reasoning_effort: str | None = None
    max_output_tokens: int | None = None
    store: bool = False
    # 官方通道的稳定缓存键；订阅端不接受该字段，序列化时忽略。
    prompt_cache_key: str | None = None
    # 通道私有的续会话状态(如订阅端的 x-codex-turn-state),原样透传
    provider_state: dict[str, Any] | None = None


@dataclass(frozen=True)
class EngineEvent:
    """引擎向外产出的事件;runner 负责落库与状态机推进。"""

    kind: Literal[
        "message_delta",
        "reasoning",
        "tool_started",
        "tool_finished",
        "checkpoint_requested",
        "usage",
        "turn_completed",
        "notice",
        "error",
    ]
    payload: dict[str, Any] = field(default_factory=dict)


# 结局分类(completed/checkpoint/cancelled/incomplete/failed)现由
# ``graph.model_gateway.LoopResult`` 承载；本模块只剩中立数据模型。
