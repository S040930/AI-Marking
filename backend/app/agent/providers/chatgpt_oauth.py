"""ChatGPT 订阅的 OAuth PKCE 登录流程。

与 codex CLI 相同的公开 client_id + ``127.0.0.1:1455`` 回调;本模块只管
"拿到/刷新令牌",令牌落库由调用方(``services/provider_config``)负责,
因此不触数据库、不 import 业务层。

设计:
- ``start_login()`` 起一个后台回调服务器并返回授权地址;
- 浏览器完成登录后,回调线程用授权码换令牌并存入模块内单例;
- ``get_status()`` 供前端轮询,``consume_tokens()`` 由 API 层一次性取走落库;
- 5 分钟未完成则标记失败并关闭监听端口;
- ``refresh_tokens()`` 供 provider 在 access_token 过期时轮换。
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import threading
import time
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Literal

import httpx

from app.core.config import settings

CODEX_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
AUTHORIZE_URL = "https://auth.openai.com/oauth/authorize"
TOKEN_URL = "https://auth.openai.com/oauth/token"
REDIRECT_URI = "http://localhost:1455/auth/callback"
CALLBACK_PORT = 1455
OAUTH_SCOPE = "openid profile email offline_access"
LOGIN_TTL_SECONDS = 300.0

LoginStatus = Literal["idle", "pending", "completed", "failed"]


class OAuthError(Exception):
    """登录/刷新失败;消息可直接展示给教师。"""


@dataclass
class LoginState:
    status: LoginStatus = "idle"
    authorize_url: str | None = None
    detail: str | None = None
    started_at: float | None = None
    expires_at: float | None = None
    tokens: dict[str, Any] | None = field(default=None, repr=False)


_lock = threading.Lock()
_state = LoginState()
_server: ThreadingHTTPServer | None = None


# ---------------------------------------------------------------------------
# 令牌解析
# ---------------------------------------------------------------------------


def _jwt_claims(token: str) -> dict[str, Any]:
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload))
        return claims if isinstance(claims, dict) else {}
    except Exception:  # noqa: BLE001 - 结构异常按空声明处理
        return {}


def normalize_tokens(payload: dict[str, Any], *, fallback_refresh: str = "") -> dict[str, Any]:
    """把 token 端点响应整理成落库结构(不含任何日志输出)。"""
    access = str(payload.get("access_token") or "")
    if not access:
        raise OAuthError("token 端点未返回 access_token")
    id_token = str(payload.get("id_token") or "")
    account = (_jwt_claims(id_token).get("https://api.openai.com/auth") or {})
    expires_in = payload.get("expires_in")
    try:
        ttl = float(expires_in) if expires_in is not None else 3600.0
    except (TypeError, ValueError):
        ttl = 3600.0
    return {
        "access_token": access,
        "refresh_token": str(payload.get("refresh_token") or fallback_refresh),
        "id_token": id_token,
        "account_id": str(account.get("chatgpt_account_id") or ""),
        "plan": str(account.get("chatgpt_plan_type") or ""),
        "expires_at": int(time.time() + max(ttl - 60.0, 60.0)),
        "obtained_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def _client_kwargs() -> dict[str, Any]:
    proxy = (settings.MODEL_PROXY_URL or "").strip()
    return {
        "proxy": proxy or None,
        "trust_env": not proxy,
        "timeout": httpx.Timeout(connect=15.0, read=30.0, write=15.0, pool=15.0),
    }


# ---------------------------------------------------------------------------
# PKCE
# ---------------------------------------------------------------------------


def _pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


# ---------------------------------------------------------------------------
# 回调服务器
# ---------------------------------------------------------------------------


class _CallbackHandler(BaseHTTPRequestHandler):
    server_version = "ai-marking-oauth"

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 接口
        parsed = urllib.parse.urlparse(self.path)
        server: ThreadingHTTPServer = self.server  # type: ignore[assignment]
        if parsed.path != "/auth/callback":
            self.send_response(404)
            self.end_headers()
            return
        query = urllib.parse.parse_qs(parsed.query)
        state = (query.get("state") or [""])[0]
        code = (query.get("code") or [""])[0]
        error = (query.get("error") or [""])[0]
        if state != getattr(server, "expected_state", ""):
            self._respond(400, "登录状态校验失败，请回到应用重新发起登录。")
            return
        if not code:
            self._finish(failed=True, detail=f"授权被拒绝或取消: {error or '未知原因'}")
            self._respond(400, f"登录失败：{error or '未返回授权码'}")
            return
        try:
            tokens = _exchange_code(code, getattr(server, "code_verifier", ""))
        except OAuthError as exc:
            self._finish(failed=True, detail=str(exc))
            self._respond(502, f"换取令牌失败：{exc}")
            return
        self._finish(tokens=tokens)
        self._respond(200, "登录完成，可以关闭此页回到应用。")

    def _finish(
        self,
        *,
        tokens: dict[str, Any] | None = None,
        failed: bool = False,
        detail: str | None = None,
    ) -> None:
        with _lock:
            _state.status = "failed" if failed else "completed"
            _state.detail = detail
            _state.tokens = tokens
        done.set()

    def _respond(self, status: int, message: str) -> None:
        body = (
            "<html><head><meta charset='utf-8'><title>AI 批改</title></head>"
            f"<body style='font-family:system-ui;padding:40px'><h3>{message}</h3></body></html>"
        )
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(body.encode())

    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
        return


done = threading.Event()


def _exchange_code(code: str, verifier: str) -> dict[str, Any]:
    with httpx.Client(**_client_kwargs()) as client:
        response = client.post(
            TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": REDIRECT_URI,
                "client_id": CODEX_CLIENT_ID,
                "code_verifier": verifier,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    if response.status_code >= 400:
        raise OAuthError(f"token 端点返回 HTTP {response.status_code}")
    try:
        payload = response.json()
    except ValueError as exc:
        raise OAuthError("token 端点返回非 JSON") from exc
    return normalize_tokens(payload)


def _shutdown_later(server: ThreadingHTTPServer) -> None:
    try:
        server.shutdown()
        server.server_close()
    except Exception:  # noqa: BLE001 - 关闭失败不影响登录结果
        pass


def _watch(server: ThreadingHTTPServer, deadline: float) -> None:
    """等待回调完成或超时,然后关闭监听端口。"""
    done.wait(timeout=max(0.0, deadline - time.time()))
    with _lock:
        if _state.status == "pending":
            _state.status = "failed"
            _state.detail = "登录超时（5 分钟），请重新发起"
    threading.Thread(target=_shutdown_later, args=(server,), daemon=True).start()


# ---------------------------------------------------------------------------
# 对外接口
# ---------------------------------------------------------------------------


def start_login() -> LoginState:
    """发起登录;返回授权地址。已有进行中的登录时复用。"""
    global _server
    with _lock:
        if _state.status == "pending" and _state.expires_at and time.time() < _state.expires_at:
            return LoginState(
                status=_state.status,
                authorize_url=_state.authorize_url,
                detail=_state.detail,
                started_at=_state.started_at,
                expires_at=_state.expires_at,
            )
        if _server is not None:
            threading.Thread(target=_shutdown_later, args=(_server,), daemon=True).start()
            _server = None

    verifier, challenge = _pkce_pair()
    state_token = secrets.token_urlsafe(24)
    query = urllib.parse.urlencode(
        {
            "response_type": "code",
            "client_id": CODEX_CLIENT_ID,
            "redirect_uri": REDIRECT_URI,
            "scope": OAUTH_SCOPE,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": state_token,
            "id_token_add_organizations": "true",
            "codex_cli_simplified_flow": "true",
        }
    )
    authorize_url = f"{AUTHORIZE_URL}?{query}"

    try:
        server = ThreadingHTTPServer(("127.0.0.1", CALLBACK_PORT), _CallbackHandler)
    except OSError as exc:
        raise OAuthError(
            f"无法监听 127.0.0.1:{CALLBACK_PORT}（{exc}）；"
            "请关闭占用该端口的程序（例如正在登录的 codex）后重试"
        ) from exc
    server.expected_state = state_token  # type: ignore[attr-defined]
    server.code_verifier = verifier  # type: ignore[attr-defined]

    done.clear()
    started = time.time()
    deadline = started + LOGIN_TTL_SECONDS
    with _lock:
        _state.status = "pending"
        _state.authorize_url = authorize_url
        _state.detail = None
        _state.started_at = started
        _state.expires_at = deadline
        _state.tokens = None
        _server = server

    threading.Thread(target=server.serve_forever, daemon=True).start()
    threading.Thread(target=_watch, args=(server, deadline), daemon=True).start()
    return LoginState(
        status="pending",
        authorize_url=authorize_url,
        started_at=started,
        expires_at=deadline,
    )


def get_status() -> LoginState:
    with _lock:
        return LoginState(
            status=_state.status,
            authorize_url=_state.authorize_url,
            detail=_state.detail,
            started_at=_state.started_at,
            expires_at=_state.expires_at,
            tokens=_state.tokens,
        )


def consume_tokens() -> dict[str, Any] | None:
    """已完成登录时一次性取走令牌(供 API 层落库)。"""
    with _lock:
        if _state.status != "completed" or not _state.tokens:
            return None
        tokens = _state.tokens
        _state.tokens = None
        return tokens


def reset() -> None:
    """清除登录状态并关闭监听(退出登录时调用)。"""
    global _server
    with _lock:
        server = _server
        _server = None
        _state.status = "idle"
        _state.authorize_url = None
        _state.detail = None
        _state.tokens = None
        _state.started_at = None
        _state.expires_at = None
    done.set()
    if server is not None:
        threading.Thread(target=_shutdown_later, args=(server,), daemon=True).start()


async def refresh_tokens(
    client: httpx.AsyncClient, refresh_token: str
) -> dict[str, Any]:
    """用 refresh_token 轮换令牌;失败抛 ``OAuthError``。"""
    if not refresh_token:
        raise OAuthError("缺少 refresh_token，请重新登录")
    try:
        response = await client.post(
            TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": CODEX_CLIENT_ID,
                "scope": OAUTH_SCOPE,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    except httpx.HTTPError as exc:
        raise OAuthError(f"刷新令牌时网络错误: {type(exc).__name__}") from exc
    if response.status_code >= 400:
        raise OAuthError("登录已过期，请重新登录 ChatGPT 订阅")
    try:
        payload = response.json()
    except ValueError as exc:
        raise OAuthError("刷新令牌响应非 JSON") from exc
    return normalize_tokens(payload, fallback_refresh=refresh_token)
