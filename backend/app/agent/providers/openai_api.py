"""官方 OpenAI Responses API provider(流式,store=false 无状态重放)。

职责边界(见 tests/test_architecture_boundaries.py):
只允许标准库/httpx/``app.core``/``app.agent``;凭证由工厂注入,
本模块不读数据库、不读 system_config。
"""

from __future__ import annotations

import logging
from typing import Any, AsyncIterator

import httpx

from app.agent.models import TurnRequest
from app.agent.provider import (
    AgentProviderError,
    ModelOption,
    ProviderAuthStatus,
    ProviderEvent,
    ProviderReasoningDelta,
    ProviderTextDelta,
    ProviderToolCallStarted,
    ProviderTurnCompleted,
    classify_http_error,
    parse_retry_after,
    transport_error,
)
from app.agent.providers.sse import iter_sse_events

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.openai.com/v1"
# 常见 Codex/GPT 系模型的默认推理档位;list_models 只做静态提示,
# 真实可用性以连接测试与首次调用为准。
_KNOWN_MODELS: tuple[ModelOption, ...] = (
    ModelOption(id="gpt-5.1-codex", label="GPT-5.1 Codex", efforts=("low", "medium", "high")),
    ModelOption(id="gpt-5.1", label="GPT-5.1", efforts=("low", "medium", "high")),
    ModelOption(id="gpt-5", label="GPT-5", efforts=("low", "medium", "high")),
    ModelOption(id="gpt-4.1", label="GPT-4.1", efforts=()),
)

_READ_TIMEOUT_SECONDS = 300.0


class OpenAiApiProvider:
    """官方 API 通道;每个实例绑定一组凭证与默认模型。"""

    name = "openai_api"

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str | None = None,
        default_model: str | None = None,
        reasoning_effort: str | None = None,
        extra_headers: dict[str, str] | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        if not api_key:
            raise AgentProviderError(
                kind="auth_required", message="未配置 API key"
            )
        self._api_key = api_key
        self._base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.default_model = default_model
        self._reasoning_effort = reasoning_effort
        self._extra_headers = dict(extra_headers or {})
        self._client = http_client
        self._owns_client = http_client is None

    # ------------------------------------------------------------------
    # 协议实现
    # ------------------------------------------------------------------

    def auth_status(self) -> ProviderAuthStatus:
        if not self._api_key:
            return ProviderAuthStatus(ready=False, detail="未配置 API key")
        return ProviderAuthStatus(ready=True)

    async def list_models(self) -> list[ModelOption]:
        models = list(_KNOWN_MODELS)
        if self.default_model and all(
            item.id != self.default_model for item in models
        ):
            models.insert(0, ModelOption(id=self.default_model))
        return models

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    async def create_turn(
        self, request: TurnRequest
    ) -> AsyncIterator[ProviderEvent]:
        body = self.build_request_body(request)
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            **self._extra_headers,
        }
        client = self._get_client()
        try:
            async with client.stream(
                "POST",
                f"{self._base_url}/responses",
                json=body,
                headers=headers,
                timeout=httpx.Timeout(
                    connect=15.0, read=_READ_TIMEOUT_SECONDS, write=30.0, pool=15.0
                ),
            ) as response:
                if response.status_code >= 400:
                    raw = (await response.aread()).decode(
                        "utf-8", errors="replace"
                    )
                    raise classify_http_error(
                        response.status_code,
                        raw,
                        retry_after=parse_retry_after(
                            response.headers.get("Retry-After")
                        ),
                    )
                async for event in _map_stream(response):
                    yield event
        except AgentProviderError:
            raise
        except httpx.HTTPError as exc:
            raise transport_error(exc) from exc

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def build_request_body(self, request: TurnRequest) -> dict[str, Any]:
        model = request.model or self.default_model
        if not model:
            raise AgentProviderError(
                kind="invalid_request", message="未选择模型"
            )
        body: dict[str, Any] = {
            "model": model,
            "instructions": request.instructions,
            "input": request.items,
            "tools": [_tool_payload(spec) for spec in request.tools],
            "tool_choice": "auto",
            "parallel_tool_calls": False,
            "store": request.store,
            "stream": True,
            # store=false 时必须显式请求加密思考内容,以便下一回合原样回传
            "include": ["reasoning.encrypted_content"],
        }
        effort = request.reasoning_effort or self._reasoning_effort
        if effort:
            body["reasoning"] = {"effort": effort, "summary": "auto"}
        if request.max_output_tokens:
            body["max_output_tokens"] = request.max_output_tokens
        return body

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(follow_redirects=False)
            self._owns_client = True
        return self._client


def _tool_payload(spec: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "type": "function",
        "name": spec.name,
        "description": spec.description,
        "parameters": spec.parameters,
    }
    if spec.strict:
        payload["strict"] = True
    return payload


async def _map_stream(response: httpx.Response) -> AsyncIterator[ProviderEvent]:
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
            message = (
                error.get("message")
                if isinstance(error, dict)
                else str(error)
            ) or "模型回合失败"
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
    )
