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
    classify_http_error,
    parse_retry_after,
    transport_error,
)
from app.agent.providers.responses import map_responses_stream

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
                async for event in map_responses_stream(response):
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
        if request.prompt_cache_key:
            body["prompt_cache_key"] = request.prompt_cache_key
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
