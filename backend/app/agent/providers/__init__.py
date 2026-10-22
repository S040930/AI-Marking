"""Provider 工厂:按名称构造通道实例。

本包只允许标准库/httpx/``app.core``/``app.agent``:凭证与配置由调用方
(services/provider_config 的消费者)以普通值注入,本层不做数据库访问。
"""

from __future__ import annotations

from typing import Any

import httpx

from app.agent.provider import AgentProvider, AgentProviderError
from app.agent.providers.chatgpt_sub import ChatGptSubProvider
from app.agent.providers.openai_api import OpenAiApiProvider

PROVIDER_OPENAI_API = "openai_api"
PROVIDER_CHATGPT_SUB = "chatgpt_sub"
PROVIDER_NAMES: tuple[str, ...] = (PROVIDER_OPENAI_API, PROVIDER_CHATGPT_SUB)


def create_provider(
    name: str,
    *,
    api_key: str | None = None,
    base_url: str | None = None,
    default_model: str | None = None,
    reasoning_effort: str | None = None,
    tokens: dict[str, Any] | None = None,
    token_sink: Any | None = None,
    token_loader: Any | None = None,
    refresh_lease_acquire: Any | None = None,
    refresh_lease_release: Any | None = None,
    http_client: httpx.AsyncClient | None = None,
    session_id: str | None = None,
) -> AgentProvider:
    """构造 provider;未知或未登录的通道抛 AgentProviderError。"""
    if name == PROVIDER_OPENAI_API:
        if not api_key:
            raise AgentProviderError(
                kind="auth_required", message="官方通道尚未配置 API key"
            )
        return OpenAiApiProvider(
            api_key=api_key,
            base_url=base_url,
            default_model=default_model,
            reasoning_effort=reasoning_effort,
            http_client=http_client,
        )
    if name == PROVIDER_CHATGPT_SUB:
        if not tokens or not tokens.get("access_token"):
            raise AgentProviderError(
                kind="auth_required", message="尚未登录 ChatGPT 订阅"
            )
        return ChatGptSubProvider(
            tokens=tokens,
            default_model=default_model,
            reasoning_effort=reasoning_effort,
            http_client=http_client,
            on_tokens_refreshed=token_sink,
            load_tokens=token_loader,
            refresh_lease_acquire=refresh_lease_acquire,
            refresh_lease_release=refresh_lease_release,
            session_id=session_id,
        )
    raise AgentProviderError(
        kind="invalid_request",
        message=f"未知通道 {name!r};可用: {', '.join(PROVIDER_NAMES)}",
    )
