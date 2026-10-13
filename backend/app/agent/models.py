"""内嵌 agent 的中立数据模型:对话项、回合请求、引擎事件与结局。

对话项直接采用 Responses API 的 item 形态(``dict``),因为两条模型通道
(官方 API 与订阅适配器)都讲这套方言:无状态重放时把历史项原样放回
``input`` 即可,reasoning item(含 encrypted_content)也照原样回传。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
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
    """暴露给模型的单个工具定义。"""

    name: str
    description: str
    parameters: dict[str, Any]
    strict: bool = False


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


class EngineStatus(str, Enum):
    """一次 engine.run 的结局分类。"""

    # save_assessment 成功落库,批改建议已就绪
    completed = "completed"
    # request_teacher_confirmation 触发的检查点,等待教师答复后续跑
    checkpoint = "checkpoint"
    # 收到取消信号
    cancelled = "cancelled"
    # 回合自然结束但未产出终态工具调用;由 runner 判定最终语义
    incomplete = "incomplete"
    # 达到回合上限或不可恢复错误
    failed = "failed"


@dataclass
class EngineOutcome:
    status: EngineStatus
    error: str | None = None
    checkpoint_message: str | None = None
    # 完整对话(重放与检查点续跑的状态),含本回合全部 items
    items: list[ChatItem] = field(default_factory=list)
    # 累计 usage(跨回合求和)
    usage: dict[str, Any] = field(default_factory=dict)
    # 已完成的回合数
    rounds: int = 0
