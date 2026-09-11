"""ACP session/update 与请求回调到统一事件的归一化。

worker 把 agent 的所有协议交互转成 ``AcpEvent`` 流:
- 落库到 ``acp_run_events``(seq 自增),供 SSE 回放与续读;
- 文本增量在内存合并(250ms/4KB 窗口)后落库,减少行数;
- 单事件 64KB、单 run 转录 5MB 上限在 worker 侧裁剪;
- 敏感环境变量、令牌与宿主绝对路径在此处脱敏。

权限请求与 form elicitation 归一化为 ``checkpoint`` 事件,由 API 转成
教师检查点;不落库隐藏推理。
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

# 事件体上限。超长内容按行截断并在末尾标注,保证 JSON 行可控。
MAX_EVENT_TEXT_CHARS = 64 * 1024
# 文本增量合并窗口:250ms 或 4KB 先到者触发落库。
FLUSH_INTERVAL_SECONDS = 0.25
FLUSH_INTERVAL_CHARS = 4 * 1024


class AcpEventKind(str, Enum):
    message_delta = "message_delta"  # agent 文本输出(已合并)
    thought_delta = "thought_delta"  # agent 思考摘要(不落库隐藏推理,仅标题)
    tool_started = "tool_started"
    tool_finished = "tool_finished"
    plan_update = "plan_update"
    checkpoint = "checkpoint"  # 权限请求或 form elicitation,等待教师
    turn_completed = "turn_completed"  # stop_reason 到达
    notice = "notice"  # 连接诊断、裁剪标注等系统提示
    # 对话面板(chat)专用
    user_message = "user_message"  # 教师输入的一轮消息
    permission_request = "permission_request"  # 等待教师批准/拒绝的操作
    permission_resolved = "permission_resolved"  # 教师(或超时)给出的裁决
    error = "error"  # 会话级错误(turn 失败等)


@dataclass
class AcpEvent:
    kind: AcpEventKind
    payload: dict[str, Any]
    created_at_ms: int = field(default_factory=lambda: int(time.time() * 1000))

    def to_db_payload(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "payload": _truncate(self.payload),
            "created_at_ms": self.created_at_ms,
        }


# ---------------------------------------------------------------------------
# 敏感信息脱敏
# ---------------------------------------------------------------------------

# 常见令牌形态:Bearer/JWT/长 hex 或 base64 串。
_TOKEN_LIKE = re.compile(
    r"(?i)\b(bearer\s+[a-z0-9._\-]{16,}|sk-[a-z0-9]{16,}|"
    r"[a-f0-9]{32,}|[A-Za-z0-9+/]{40,}={0,2})\b"
)
# 宿主绝对路径:项目目录与用户目录。
_HOST_PATH_LIKE = re.compile(r"(?:(?:/Users|/home)/[^/\s\"]+(?:/[\w.\-]+)+)")


def redact_text(value: str) -> str:
    """脱敏自由文本中的令牌与宿主绝对路径。"""
    value = _TOKEN_LIKE.sub("[REDACTED]", value)
    value = _HOST_PATH_LIKE.sub("[PATH]", value)
    return value


def redact_env(env: list[dict[str, str]] | list[Any] | None) -> list[dict[str, str]]:
    """脱敏环境变量列表;值一律脱敏为存在性标记,不落库明文。"""
    if not env:
        return []
    return [
        {"name": str(item.get("name", "?")), "value": "[REDACTED]"}
        for item in env
        if isinstance(item, dict)
    ]


def _truncate_text(value: str, limit: int = MAX_EVENT_TEXT_CHARS) -> str:
    if len(value) <= limit:
        return value
    return value[:limit] + f"\n[截断,原文 {len(value)} 字符]"


def _truncate(
    payload: dict[str, Any], limit: int = MAX_EVENT_TEXT_CHARS
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in payload.items():
        if isinstance(value, str):
            out[key] = _truncate_text(value, limit)
        elif isinstance(value, list) and len(value) > 200:
            out[key] = value[:200] + [{"truncated": True}]
        else:
            out[key] = value
    return out


# ---------------------------------------------------------------------------
# session/update 归一化
# ---------------------------------------------------------------------------


def normalize_session_update(update: Any) -> list[AcpEvent]:
    """把 SDK 的 session/update 联合类型转成 0..n 个 AcpEvent。

    返回列表是因为单个协议事件可能拆成多个领域事件(如 tool_call 内容)。
    """
    kind = getattr(update, "session_update", None)
    if kind == "agent_message_chunk":
        text = _chunk_text(update)
        if not text:
            return []
        return [AcpEvent(AcpEventKind.message_delta, {"text": redact_text(text)})]
    if kind == "agent_thought_chunk":
        # 不持久化隐藏推理:仅记录存在性提示,丢弃内容。
        return [AcpEvent(AcpEventKind.thought_delta, {"hidden": True})]
    if kind in ("tool_call", "tool_call_update"):
        return _normalize_tool_call(update)
    if kind in ("plan",):
        return _normalize_plan(update)
    return []


def _chunk_text(update: Any) -> str:
    content = getattr(update, "content", None)
    if content is None:
        return ""
    text = getattr(content, "text", None)
    return text if isinstance(text, str) else ""


def _normalize_tool_call(update: Any) -> list[AcpEvent]:
    tool_call_id = str(getattr(update, "tool_call_id", "") or "")
    title = redact_text(str(getattr(update, "title", "") or ""))
    status = getattr(update, "status", None)
    kind_value = getattr(update, "kind", None)
    base = {
        "tool_call_id": str(tool_call_id),
        "title": title,
        "kind": getattr(kind_value, "value", None) if kind_value is not None else None,
    }
    if status is None:
        return [AcpEvent(AcpEventKind.tool_started, base)]
    status_value = getattr(status, "value", None) or str(status)
    event = dict(base)
    event["status"] = status_value
    raw_output = getattr(update, "raw_output", None)
    if raw_output is not None:
        event["output"] = redact_text(str(raw_output)[:4000])
    is_final = status_value in ("completed", "failed")
    return [
        AcpEvent(
            AcpEventKind.tool_finished if is_final else AcpEventKind.tool_started,
            event,
        )
    ]


def _normalize_plan(update: Any) -> list[AcpEvent]:
    entries = getattr(update, "entries", None) or []
    items = []
    for entry in entries:
        items.append(
            {
                "content": redact_text(str(getattr(entry, "content", "") or "")),
                "status": str(getattr(entry, "status", "") or ""),
                "priority": str(getattr(entry, "priority", "") or ""),
            }
        )
    if not items:
        return []
    return [AcpEvent(AcpEventKind.plan_update, {"items": items})]


# ---------------------------------------------------------------------------
# 文本增量合并器
# ---------------------------------------------------------------------------


class TextDeltaBuffer:
    """把 message_delta 合并成更大的事件,降低事件行数。

    合并窗口:250ms 或 4KB 先到者触发。``flush(reason)`` 在 turn 结束、
    权限请求等节点强制落库。
    """

    def __init__(self) -> None:
        self._parts: list[str] = []
        self._chars = 0
        self._opened_at: float | None = None

    def add(self, text: str) -> bool:
        """追加文本;返回 True 表示窗口已满,应立即 flush。"""
        if not text:
            return False
        self._parts.append(text)
        self._chars += len(text)
        now = time.monotonic()
        if self._opened_at is None:
            self._opened_at = now
        return (
            self._chars >= FLUSH_INTERVAL_CHARS
            or now - self._opened_at >= FLUSH_INTERVAL_SECONDS
        )

    def should_flush(self) -> bool:
        if self._opened_at is None:
            return False
        return (
            self._chars >= FLUSH_INTERVAL_CHARS
            or time.monotonic() - self._opened_at >= FLUSH_INTERVAL_SECONDS
        )

    def flush(self) -> AcpEvent | None:
        if not self._parts:
            self._opened_at = None
            return None
        merged = redact_text("".join(self._parts))
        self._parts = []
        self._chars = 0
        self._opened_at = None
        return AcpEvent(AcpEventKind.message_delta, {"text": merged})

    @property
    def pending_chars(self) -> int:
        return self._chars
