"""事件侧工具:文本增量合并与敏感信息脱敏。

由旧事件模块裁剪而来:保留 250ms/4KB 合并窗口与脱敏规则,
删除旧协议归一化与对话专用事件。
"""

from __future__ import annotations

import re
import time

# 文本增量合并窗口:250ms 或 4KB 先到者触发落库。
FLUSH_INTERVAL_SECONDS = 0.25
FLUSH_INTERVAL_CHARS = 4 * 1024

_TOOL_OUTPUT_MAX_CHARS = 4_000


class TextDeltaBuffer:
    """把 message_delta 合并成更大的事件,降低事件行数。"""

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

    def flush(self) -> str | None:
        if not self._parts:
            self._opened_at = None
            return None
        merged = redact_text("".join(self._parts))
        self._parts = []
        self._chars = 0
        self._opened_at = None
        return merged or None

    @property
    def pending_chars(self) -> int:
        return self._chars


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


def tool_output_summary(output: object) -> str:
    """工具输出摘要:仅保留前 4000 字符,供事件落库。"""
    text = output if isinstance(output, str) else repr(output)
    text = redact_text(text)
    if len(text) <= _TOOL_OUTPUT_MAX_CHARS:
        return text
    return text[:_TOOL_OUTPUT_MAX_CHARS] + "\n[截断]"
