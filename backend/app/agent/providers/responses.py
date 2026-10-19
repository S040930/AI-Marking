"""Responses API 的 SSE 事件 -> ProviderEvent 映射(两条通道共用)。

官方 API 与 ChatGPT 订阅端讲同一套 Responses 方言,差别只在端点、请求头
与续会话状态:订阅端不返回 reasoning item,改用响应头 ``x-codex-turn-state``
承载跨回合状态,因此映射器接受一个 ``provider_state`` 并原样挂在完成事件上。
"""

from __future__ import annotations

from typing import Any, AsyncIterator

import httpx

from app.agent.provider import (
    AgentProviderError,
    ProviderEvent,
    ProviderReasoningDelta,
    ProviderTextDelta,
    ProviderToolCallStarted,
    ProviderTurnCompleted,
)
from app.agent.providers.sse import iter_sse_events


async def map_responses_stream(
    response: httpx.Response,
    *,
    provider_state: dict[str, Any] | None = None,
) -> AsyncIterator[ProviderEvent]:
    """把 Responses API 的 SSE 事件流映射为 ProviderEvent。

    function_call 的 arguments 以 delta 累积;``response.completed`` 时以
    ``response.output`` 为准给出完整 item 列表(含 reasoning 与 function_call)。
    """
    output_items: dict[int, dict[str, Any]] = {}
    argument_buffers: dict[int, str] = {}
    completed: dict[str, Any] | None = None

    async for name, data in iter_sse_events(response):
        if name in ("response.output_text.delta",):
            delta = data.get("delta")
            if isinstance(delta, str) and delta:
                yield ProviderTextDelta(delta)
        elif name in (
            "response.reasoning_summary_text.delta",
            "response.reasoning_text.delta",
        ):
            delta = data.get("delta")
            if isinstance(delta, str) and delta:
                yield ProviderReasoningDelta(delta)
        elif name == "response.output_item.added":
            item = data.get("item")
            if isinstance(item, dict):
                output_index = int(data.get("output_index") or 0)
                output_items[output_index] = dict(item)
                if item.get("type") == "function_call" and item.get("call_id"):
                    yield ProviderToolCallStarted(
                        call_id=str(item["call_id"]),
                        name=str(item.get("name") or ""),
                    )
        elif name == "response.function_call_arguments.delta":
            output_index = int(data.get("output_index") or 0)
            delta = data.get("delta")
            if isinstance(delta, str):
                argument_buffers[output_index] = (
                    argument_buffers.get(output_index, "") + delta
                )
        elif name == "response.function_call_arguments.done":
            output_index = int(data.get("output_index") or 0)
            arguments = data.get("arguments")
            if isinstance(arguments, str):
                argument_buffers[output_index] = arguments
        elif name == "response.output_item.done":
            output_index = int(data.get("output_index") or 0)
            item = data.get("item")
            if isinstance(item, dict):
                merged = dict(item)
                if merged.get("type") == "function_call":
                    buffered = argument_buffers.get(output_index)
                    if buffered is not None:
                        merged["arguments"] = buffered
                output_items[output_index] = merged
        elif name == "response.completed":
            completed = data.get("response") if isinstance(data.get("response"), dict) else {}
        elif name == "response.incomplete":
            completed = data.get("response") if isinstance(data.get("response"), dict) else {}
            if completed is not None:
                completed.setdefault("status", "incomplete")
        elif name == "response.failed":
            payload = data.get("response") if isinstance(data.get("response"), dict) else {}
            error = payload.get("error") or {}
            message = (error.get("message") if isinstance(error, dict) else str(error)) or (
                "模型回合失败"
            )
            raise AgentProviderError(kind="unknown", message=str(message))
        elif name == "error":
            error = data.get("error") or {}
            message = (
                error.get("message") if isinstance(error, dict) else str(error)
            ) or data.get("message") or "模型流错误"
            code = error.get("code") if isinstance(error, dict) else None
            if code in ("rate_limit_exceeded", "insufficient_quota"):
                raise AgentProviderError(
                    kind="rate_limited", message=str(message), retryable=True
                )
            raise AgentProviderError(kind="unknown", message=str(message))

    if completed is None:
        raise AgentProviderError(
            kind="retryable",
            message="模型流在没有 response.completed 的情况下结束",
            retryable=True,
        )
    # completed.response.output 是权威完整列表;流式累积的 items 作为兜底合并
    final_output = completed.get("output")
    if isinstance(final_output, list) and final_output:
        items = [item for item in final_output if isinstance(item, dict)]
    else:
        items = [
            item for _, item in sorted(output_items.items(), key=lambda kv: kv[0])
        ]
    usage = completed.get("usage") if isinstance(completed.get("usage"), dict) else None
    stop_reason = str(completed.get("status") or "completed")
    yield ProviderTurnCompleted(
        stop_reason=stop_reason,
        response_items=items,
        usage=usage,
        provider_state=provider_state,
    )
