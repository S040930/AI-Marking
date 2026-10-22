"""模型通道配置与 OAuth HTTP 接口。"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, sessionmaker

from app.agent.provider import AgentProviderError
from app.agent.providers import PROVIDER_NAMES, chatgpt_oauth, create_provider
from app.agent.providers.openai_api import DEFAULT_BASE_URL
from app.db.session import get_db, get_session_factory
from app.schemas.agent import (
    ProviderConfigUpdateRequest,
    ProviderDefaultUpdateRequest,
    ProviderListResponse,
    ProviderOAuthStartResponse,
    ProviderOAuthStatusResponse,
    ProviderTestResponse,
)
from app.services.chatgpt_refresh_lease import (
    acquire_refresh_lease,
    release_refresh_lease,
)
from app.services.provider_config import (
    CHATGPT_DEFAULT_MODEL_KEY,
    OPENAI_API_KEY_KEY,
    OPENAI_BASE_URL_KEY,
    OPENAI_DEFAULT_MODEL_KEY,
    OPENAI_REASONING_EFFORT_KEY,
    PROVIDER_CHATGPT_SUB,
    PROVIDER_DEFAULT_KEY,
    PROVIDER_OPENAI_API,
    clear_chatgpt_tokens,
    get_provider_config,
    load_chatgpt_tokens,
    provider_default,
    provider_summary,
    save_chatgpt_tokens,
    set_provider_config,
)

router = APIRouter(prefix="/agent", tags=["agent"])
logger = logging.getLogger(__name__)


@router.get("/providers", response_model=ProviderListResponse)
def list_providers(db: Session = Depends(get_db)) -> ProviderListResponse:
    return ProviderListResponse(**provider_summary(db))


@router.put("/providers/default", response_model=ProviderListResponse)
def set_default_provider(
    body: ProviderDefaultUpdateRequest, db: Session = Depends(get_db)
) -> ProviderListResponse:
    set_provider_config(db, {PROVIDER_DEFAULT_KEY: body.provider})
    return ProviderListResponse(**provider_summary(db))


@router.put("/providers/{name}/config", response_model=ProviderListResponse)
def update_provider_config(
    name: str, body: ProviderConfigUpdateRequest, db: Session = Depends(get_db)
) -> ProviderListResponse:
    """更新通道配置;null 不改动、空串清除;密钥只写不读。"""
    if name not in PROVIDER_NAMES:
        raise HTTPException(status_code=404, detail=f"未知通道 {name}")
    updates: dict[str, object] = {}
    if name == PROVIDER_OPENAI_API:
        if body.api_key is not None:
            updates[OPENAI_API_KEY_KEY] = body.api_key
        if body.base_url is not None:
            updates[OPENAI_BASE_URL_KEY] = body.base_url
        if body.default_model is not None:
            updates[OPENAI_DEFAULT_MODEL_KEY] = body.default_model
        if body.reasoning_effort is not None:
            updates[OPENAI_REASONING_EFFORT_KEY] = body.reasoning_effort
    else:  # chatgpt_sub:仅默认模型;api_key/base_url/effort 不适用
        unsupported = [
            field
            for field, value in (
                ("api_key", body.api_key),
                ("base_url", body.base_url),
                ("reasoning_effort", body.reasoning_effort),
            )
            if value is not None
        ]
        if unsupported:
            raise HTTPException(
                status_code=422,
                detail=f"订阅通道不支持字段: {', '.join(unsupported)}",
            )
        if body.default_model is not None:
            updates[CHATGPT_DEFAULT_MODEL_KEY] = body.default_model
    set_provider_config(db, updates)
    return ProviderListResponse(**provider_summary(db))


@router.get("/providers/{name}/models", response_model=ProviderTestResponse)
async def list_provider_models(
    name: str, db: Session = Depends(get_db)
) -> ProviderTestResponse:
    """返回当前通道可用的模型,供批改配置选择。"""
    if name not in PROVIDER_NAMES:
        raise HTTPException(status_code=404, detail=f"未知通道 {name}")
    if name == PROVIDER_CHATGPT_SUB:
        tokens = load_chatgpt_tokens(db)
        if not tokens or not tokens.get("access_token"):
            return ProviderTestResponse(
                name=name, status="needs_auth", detail="尚未登录 ChatGPT 订阅"
            )
        default_model = (
            provider_summary(db)["providers"][name].get("default_model") or None
        )
        lease_owner = uuid.uuid4().hex

        def _acquire_lease(ttl_seconds: float) -> bool:
            return acquire_refresh_lease(
                db, owner=lease_owner, ttl_seconds=ttl_seconds
            )

        def _release_lease() -> None:
            release_refresh_lease(db, owner=lease_owner)

        provider = create_provider(
            PROVIDER_CHATGPT_SUB,
            tokens=tokens,
            default_model=default_model,
            token_sink=lambda updated: save_chatgpt_tokens(db, updated),
            token_loader=lambda: load_chatgpt_tokens(db),
            refresh_lease_acquire=_acquire_lease,
            refresh_lease_release=_release_lease,
        )
        try:
            models = await provider.list_models()
        except AgentProviderError as exc:
            status = "needs_auth" if exc.kind == "auth_required" else "failed"
            return ProviderTestResponse(
                name=name, status=status, detail=exc.message[:300]
            )
        finally:
            await provider.aclose()
        return ProviderTestResponse(
            name=name, status="ready", models=[model.id for model in models][:50]
        )
    config = get_provider_config(db)
    api_key = config.get(OPENAI_API_KEY_KEY) or ""
    if not api_key:
        return ProviderTestResponse(
            name=name, status="needs_auth", detail="尚未配置 API key"
        )
    base_url = (config.get(OPENAI_BASE_URL_KEY) or DEFAULT_BASE_URL).rstrip("/")
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{base_url}/models",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=15.0,
            )
    except httpx.HTTPError as exc:
        return ProviderTestResponse(
            name=name, status="failed", detail=f"{type(exc).__name__}: {exc}"[:300]
        )
    if response.status_code in (401, 403):
        return ProviderTestResponse(
            name=name, status="needs_auth", detail="API key 无效或未授权"
        )
    if response.status_code >= 400:
        return ProviderTestResponse(
            name=name,
            status="failed",
            detail=f"HTTP {response.status_code}: {response.text[:200]}",
        )
    models: list[str] = []
    try:
        payload = response.json()
        models = [
            str(item.get("id"))
            for item in payload.get("data", [])
            if isinstance(item, dict) and item.get("id")
        ][:50]
    except (ValueError, AttributeError):
        models = []
    return ProviderTestResponse(name=name, status="ready", models=models)


@router.post("/providers/{name}/test", response_model=ProviderTestResponse)
async def test_provider(
    name: str, db: Session = Depends(get_db)
) -> ProviderTestResponse:
    """测试通道连接并返回可用模型列表。"""
    return await list_provider_models(name, db)


def _iso_timestamp(epoch: float | None) -> str | None:
    if not epoch:
        return None
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat(timespec="seconds")


@router.post(
    "/providers/chatgpt_sub/oauth/start",
    response_model=ProviderOAuthStartResponse,
)
def start_chatgpt_login(
    factory: sessionmaker = Depends(get_session_factory),
) -> ProviderOAuthStartResponse:
    """发起 PKCE 登录;返回需要浏览器打开的授权地址。

    回调线程换到令牌后会**立即**用注入的 sink 落库,不依赖前端轮询;
    因此这里必须传 session 工厂,不能传请求级 Session(线程不安全)。
    """

    def _save_tokens(tokens: dict) -> None:
        with factory() as session:
            save_chatgpt_tokens(session, tokens)

    try:
        state = chatgpt_oauth.start_login(on_tokens=_save_tokens)
    except chatgpt_oauth.OAuthError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return ProviderOAuthStartResponse(
        authorize_url=state.authorize_url or "",
        expires_at=_iso_timestamp(state.expires_at),
    )


@router.get(
    "/providers/chatgpt_sub/oauth/status",
    response_model=ProviderOAuthStatusResponse,
)
def chatgpt_login_status(db: Session = Depends(get_db)) -> ProviderOAuthStatusResponse:
    """轮询登录结果;回调落库失败时在这里补偿一次(成功才清内存副本)。"""
    state = chatgpt_oauth.get_status()
    tokens = chatgpt_oauth.pending_tokens()
    if tokens is not None:
        try:
            save_chatgpt_tokens(db, tokens)
        except Exception:  # noqa: BLE001 - 补偿失败不影响状态展示
            db.rollback()
            logger.warning("ChatGPT 订阅令牌补偿落库失败", exc_info=True)
        else:
            chatgpt_oauth.clear_tokens()
            logger.info("ChatGPT 订阅登录完成，令牌已保存")
    return ProviderOAuthStatusResponse(
        status=state.status,
        detail=state.detail,
        authorize_url=state.authorize_url,
        expires_at=_iso_timestamp(state.expires_at),
    )


@router.post(
    "/providers/chatgpt_sub/oauth/logout",
    response_model=ProviderListResponse,
)
def logout_chatgpt(db: Session = Depends(get_db)) -> ProviderListResponse:
    clear_chatgpt_tokens(db)
    chatgpt_oauth.reset()
    if provider_default(db) == PROVIDER_CHATGPT_SUB:
        set_provider_config(db, {PROVIDER_DEFAULT_KEY: PROVIDER_OPENAI_API})
    return ProviderListResponse(**provider_summary(db))
