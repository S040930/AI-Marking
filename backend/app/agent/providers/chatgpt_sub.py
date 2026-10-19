"""ChatGPT 订阅通道 provider(阶段 6 spike 结论落地)。

与官方通道的差异(均有实测依据,见 docs/architecture/chatgpt-sub-spike.md):

- 端点 ``https://chatgpt.com/backend-api/codex/responses``;
- 模型目录 ``/backend-api/codex/models`` **必须**带 ``client_version``;
- 请求体 ``store`` 必须显式为 ``false``;除 Authorization 外请求头都可省
  (这里仍带 account-id 与 originator,便于多账号与排障);
- 端点**不返回 reasoning item**,跨回合状态由响应头 ``x-codex-turn-state``
  承载,经 ``provider_state`` 透传;
- 模型目录声明的 ``ultra`` 实测被拒,effort 只认 ``low/medium/high/xhigh/max``。

本模块只依赖标准库/httpx/``app.core``/``app.agent``,凭证由工厂注入。
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from typing import Any, AsyncIterator, Callable

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
from app.agent.providers import chatgpt_oauth
from app.agent.providers.chatgpt_token_manager import refresh_lock_or_new
from app.agent.providers.responses import map_responses_stream
from app.core.config import settings

logger = logging.getLogger(__name__)

BASE_URL = "https://chatgpt.com/backend-api/codex"
RESPONSES_PATH = "/responses"
MODELS_PATH = "/models"
ORIGINATOR = "ai_marking"
USER_AGENT = "ai-marking/0.1"

# 实测白名单:目录里的 supported_reasoning_levels 会掺假(如 ultra)
VERIFIED_EFFORTS: tuple[str, ...] = ("low", "medium", "high", "xhigh", "max")
_FALLBACK_MODELS: tuple[tuple[str, str], ...] = (
    ("gpt-6.1-sol", "GPT-6.1-Sol"),
    ("gpt-6-astra", "GPT-6-Astra"),
    ("gpt-6-sol", "GPT-6-Sol"),
    ("gpt-6-luna", "GPT-6-Luna"),
)

_READ_TIMEOUT_SECONDS = 600.0

TokenSink = Callable[[dict[str, Any]], None]
TokenLoader = Callable[[], dict[str, Any] | None]


def client_version() -> str:
    return os.environ.get("MODEL_CLIENT_VERSION") or settings.CODEX_CLIENT_VERSION


class ChatGptSubProvider:
    """订阅额度通道;每个实例绑定一组 token 与默认模型。"""

    name = "chatgpt_sub"

    def __init__(
        self,
        *,
        tokens: dict[str, Any],
        default_model: str | None = None,
        reasoning_effort: str | None = None,
        http_client: httpx.AsyncClient | None = None,
        on_tokens_refreshed: TokenSink | None = None,
        load_tokens: TokenLoader | None = None,
        session_id: str | None = None,
    ) -> None:
        self._access_token = str(tokens.get("access_token") or "")
        self._refresh_token = str(tokens.get("refresh_token") or "")
        self._account_id = str(tokens.get("account_id") or "")
        self._plan = str(tokens.get("plan") or "")
        try:
            self._expires_at = int(tokens.get("expires_at") or 0)
        except (TypeError, ValueError):
            self._expires_at = 0
        self.default_model = default_model
        self._reasoning_effort = reasoning_effort
        self._on_tokens_refreshed = on_tokens_refreshed
        self._load_tokens = load_tokens
        # 刷新单飞:refresh_token 轮换只能消费一次。进程级共享锁让同进程的
        # 多个 fan-out 分支串行刷新;无运行 loop 的同步构造退化为独立锁。
        self._refresh_lock = refresh_lock_or_new()
        self._session_id = session_id or str(uuid.uuid4())
        self._client = http_client
        self._owns_client = http_client is None
        self._turn_state: str | None = None

    # ------------------------------------------------------------------
    # 协议实现
    # ------------------------------------------------------------------

    def auth_status(self) -> ProviderAuthStatus:
        if not self._access_token:
            return ProviderAuthStatus(ready=False, detail="尚未登录 ChatGPT 订阅")
        if self._expires_at and self._expires_at - time.time() <= 60:
            if not self._refresh_token:
                return ProviderAuthStatus(
                    ready=False, detail="订阅登录已过期，请重新登录"
                )
            return ProviderAuthStatus(ready=True, detail="令牌将自动刷新")
        return ProviderAuthStatus(ready=True)

    async def list_models(self) -> list[ModelOption]:
        await self._ensure_token()
        client = self._get_client()
        try:
            response = await client.get(
                f"{BASE_URL}{MODELS_PATH}",
                params={"client_version": client_version()},
                headers=self._headers(),
            )
        except httpx.HTTPError as exc:
            raise transport_error(exc) from exc
        if response.status_code in (401, 403):
            raise AgentProviderError(
                kind="auth_required", message="订阅登录已失效，请重新登录"
            )
        if response.status_code >= 400:
            raise _http_error(response.status_code, response.text, response.headers)
        try:
            payload = response.json()
        except ValueError:
            return self._fallback_models()
        entries = payload.get("models") if isinstance(payload, dict) else None
        if not isinstance(entries, list):
            return self._fallback_models()
        models: list[ModelOption] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            slug = entry.get("slug") or entry.get("id")
            if not slug:
                continue
            levels = entry.get("supported_reasoning_levels") or []
            supported = [
                str(level.get("effort"))
                for level in levels
                if isinstance(level, dict) and level.get("effort")
            ]
            efforts = tuple(
                effort for effort in VERIFIED_EFFORTS if effort in supported
            )
            models.append(
                ModelOption(
                    id=str(slug),
                    label=str(entry.get("display_name") or slug),
                    efforts=efforts or VERIFIED_EFFORTS,
                )
            )
        return models or self._fallback_models()

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    async def create_turn(
        self, request: TurnRequest
    ) -> AsyncIterator[ProviderEvent]:
        await self._ensure_token()
        if not (request.model or self.default_model):
            # 订阅通道没有静态默认模型:未配置时取目录首项(最新主力模型)
            models = await self.list_models()
            if models:
                self.default_model = models[0].id
        state = dict(request.provider_state or {})
        if state.get("turn_state"):
            self._turn_state = str(state["turn_state"])
        refreshed = False
        for _ in range(3):
            stale_access = self._access_token
            try:
                async for event in self._stream_turn(request):
                    yield event
                return
            except _TurnStateRejectedError:
                # 旧 turn-state 被拒:去掉后重试,不终止整次批改
                continue
            except httpx.HTTPError as exc:
                raise transport_error(exc) from exc
            except AgentProviderError as exc:
                can_refresh = bool(self._refresh_token) or self._load_tokens is not None
                if not refreshed and exc.kind == "auth_required" and can_refresh:
                    refreshed = True
                    await self._refresh(stale_access=stale_access)
                    continue
                raise
        raise AgentProviderError(
            kind="retryable", message="订阅通道连续拒绝请求，请稍后重试", retryable=True
        )

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
            # 订阅端点强制要求显式 false(实测:省略或 true 均 400)
            "store": False,
            "stream": True,
            "prompt_cache_key": str(uuid.uuid4()),
        }
        effort = request.reasoning_effort or self._reasoning_effort
        if effort:
            body["reasoning"] = {"effort": effort, "summary": "auto"}
        if request.max_output_tokens:
            body["max_output_tokens"] = request.max_output_tokens
        return body

    def _headers(self) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self._access_token}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "User-Agent": USER_AGENT,
            "originator": ORIGINATOR,
            "session_id": self._session_id,
        }
        if self._account_id:
            headers["chatgpt-account-id"] = self._account_id
        return headers

    async def _stream_turn(
        self, request: TurnRequest
    ) -> AsyncIterator[ProviderEvent]:
        client = self._get_client()
        body = self.build_request_body(request)
        headers = self._headers()
        send_state = bool(self._turn_state)
        if send_state:
            headers["x-codex-turn-state"] = str(self._turn_state)
        async with client.stream(
            "POST",
            f"{BASE_URL}{RESPONSES_PATH}",
            json=body,
            headers=headers,
            timeout=httpx.Timeout(
                connect=15.0, read=_READ_TIMEOUT_SECONDS, write=30.0, pool=15.0
            ),
        ) as response:
            if response.status_code >= 400:
                raw = (await response.aread()).decode("utf-8", errors="replace")
                error = _http_error(response.status_code, raw, response.headers)
                if (
                    send_state
                    and response.status_code == 400
                    and "turn" in raw.lower()
                    and "state" in raw.lower()
                ):
                    # 旧状态不被接受时丢掉它重试一次,不让整次批改失败
                    self._turn_state = None
                    raise _TurnStateRejectedError() from error
                raise error
            turn_state = response.headers.get("x-codex-turn-state")
            if turn_state:
                self._turn_state = turn_state
            provider_state = {"turn_state": turn_state} if turn_state else None
            async for event in map_responses_stream(
                response, provider_state=provider_state
            ):
                yield event

    def _needs_refresh(self) -> bool:
        if not self._access_token:
            return True
        if not self._expires_at:
            return False
        return self._expires_at - time.time() <= 60

    def _reload_tokens(self) -> dict[str, Any] | None:
        """回读持久化令牌:别处(API/worker)可能刚完成轮换。"""
        if self._load_tokens is None:
            return None
        try:
            loaded = self._load_tokens()
        except Exception:  # noqa: BLE001 - 回读失败按无新令牌处理
            logger.warning("订阅令牌回读失败", exc_info=True)
            return None
        return loaded if isinstance(loaded, dict) else None

    async def _ensure_token(self) -> None:
        if not self._needs_refresh():
            return
        loaded = self._reload_tokens()
        if loaded:
            self._apply_tokens(loaded)
            if not self._needs_refresh():
                return
        if not self._refresh_token:
            raise AgentProviderError(
                kind="auth_required", message="订阅登录已过期，请重新登录"
            )
        await self._refresh()

    async def _refresh(self, *, stale_access: str | None = None) -> None:
        """刷新令牌;``stale_access`` 为刚被拒绝的 access token(401 重试用)。

        加锁做单飞:refresh_token 轮换只能消费一次,同实例并发刷新会互相作废。
        """
        async with self._refresh_lock:
            if stale_access is None:
                # 另一个实例可能刚完成轮换；进入锁后先回读，避免用旧
                # refresh_token 再消费一次（轮换令牌只能使用一次）。
                loaded = self._reload_tokens()
                if (
                    loaded
                    and loaded.get("access_token")
                    and loaded.get("access_token") != self._access_token
                ):
                    self._apply_tokens(loaded)
                    if not self._needs_refresh():
                        return
            if stale_access is not None:
                if self._access_token != stale_access:
                    return  # 已由并发调用换过
                loaded = self._reload_tokens()
                if loaded and loaded.get("access_token") not in (None, stale_access):
                    self._apply_tokens(loaded)
                    return
            elif not self._needs_refresh():
                return
            try:
                tokens = await chatgpt_oauth.refresh_tokens(
                    self._get_client(), self._refresh_token
                )
            except chatgpt_oauth.OAuthError as exc:
                # 刷新失败再看一眼持久化令牌:可能是另一个进程刚轮换成功
                loaded = self._reload_tokens()
                if loaded and loaded.get("access_token"):
                    self._apply_tokens(loaded)
                    if not self._needs_refresh() or stale_access is not None:
                        return
                raise AgentProviderError(
                    kind="auth_required", message=str(exc)
                ) from exc
            self._apply_tokens(tokens)
            if self._on_tokens_refreshed is not None:
                try:
                    self._on_tokens_refreshed(dict(tokens))
                except Exception:  # noqa: BLE001 - 落库失败不阻断本次调用
                    logger.warning("订阅令牌回写失败", exc_info=True)

    def _apply_tokens(self, tokens: dict[str, Any]) -> None:
        self._access_token = str(tokens.get("access_token") or self._access_token)
        self._refresh_token = str(
            tokens.get("refresh_token") or self._refresh_token
        )
        self._account_id = str(tokens.get("account_id") or self._account_id)
        self._plan = str(tokens.get("plan") or self._plan)
        try:
            self._expires_at = int(tokens.get("expires_at") or self._expires_at)
        except (TypeError, ValueError):
            self._expires_at = 0

    def _fallback_models(self) -> list[ModelOption]:
        models = [
            ModelOption(id=slug, label=label, efforts=VERIFIED_EFFORTS)
            for slug, label in _FALLBACK_MODELS
        ]
        if self.default_model and all(
            item.id != self.default_model for item in models
        ):
            models.insert(
                0, ModelOption(id=self.default_model, efforts=VERIFIED_EFFORTS)
            )
        return models

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            proxy = (settings.MODEL_PROXY_URL or "").strip() or None
            self._client = httpx.AsyncClient(
                proxy=proxy,
                trust_env=proxy is None,
                follow_redirects=False,
            )
            self._owns_client = True
        return self._client


class _TurnStateRejectedError(Exception):
    """内部信号:服务端拒绝旧 turn-state,需去掉后重试。"""


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


def _http_error(
    status_code: int, raw: str, headers: httpx.Headers
) -> AgentProviderError:
    """优先保留服务端错误文案(模型不支持/effort 非法等需要教师可见)。"""
    message = ""
    try:
        payload = json.loads(raw)
        error = payload.get("error") if isinstance(payload, dict) else None
        if isinstance(error, dict):
            message = str(error.get("message") or "")
        elif isinstance(payload, dict):
            message = str(payload.get("detail") or payload.get("message") or "")
    except ValueError:
        message = ""
    if status_code in (401, 403):
        return AgentProviderError(
            kind="auth_required",
            message=message or "订阅登录已失效，请重新登录",
            status_code=status_code,
        )
    if status_code == 429:
        return AgentProviderError(
            kind="rate_limited",
            message=message or "订阅通道限流",
            status_code=status_code,
            retryable=True,
            retry_after=parse_retry_after(headers.get("Retry-After")),
        )
    if status_code >= 500:
        return AgentProviderError(
            kind="retryable",
            message=message or f"模型服务暂时不可用(HTTP {status_code})",
            status_code=status_code,
            retryable=True,
            retry_after=parse_retry_after(headers.get("Retry-After")),
        )
    base = classify_http_error(status_code, raw)
    if base.kind == "context_length":
        return base
    return AgentProviderError(
        kind="invalid_request",
        message=(message or f"请求被订阅通道拒绝(HTTP {status_code})")[:500],
        status_code=status_code,
    )
