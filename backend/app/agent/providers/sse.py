"""SSE(text/event-stream)解析:把 httpx 响应流转成 (event, data) 序列。"""

from __future__ import annotations

import json
from typing import Any, AsyncIterator

import httpx


async def iter_sse_events(
    response: httpx.Response,
) -> AsyncIterator[tuple[str | None, dict[str, Any]]]:
    """逐事件解析 SSE;``data`` 为 JSON 对象,非 JSON 时包成 ``{"raw": ...}``。

    规范:以空行 dispatch;``event:`` 指定事件名,``data:`` 可多行;
    以冒号开头的行为注释(keepalive)。
    """
    event_name: str | None = None
    data_lines: list[str] = []

    def _dispatch() -> tuple[str | None, dict[str, Any]] | None:
        if not data_lines:
            return None
        raw = "\n".join(data_lines)
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = {"raw": raw}
        if not isinstance(payload, dict):
            payload = {"value": payload}
        return event_name, payload

    async for line in response.aiter_lines():
        if line == "":
            dispatched = _dispatch()
            if dispatched is not None:
                yield dispatched
            event_name, data_lines = None, []
            continue
        if line.startswith(":"):
            continue
        if line.startswith("event:"):
            event_name = line[len("event:") :].strip()
        elif line.startswith("data:"):
            data_lines.append(line[len("data:") :].lstrip())
    # 流在未以空行结尾时收尾
    dispatched = _dispatch()
    if dispatched is not None:
        yield dispatched
