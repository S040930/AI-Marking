"""ChatGPT 订阅的 OAuth PKCE 登录流程。

与 codex CLI 相同的公开 client_id + ``127.0.0.1:1455`` 回调;本模块只管
"拿到/刷新令牌"与登录状态机,令牌落库由调用方通过 ``on_tokens`` 回调注入,
因此不触数据库、不 import 业务层。

设计:
- ``start_login(on_tokens=...)`` 起一个后台回调服务器并返回授权地址;
- 浏览器完成登录后,回调线程用授权码换令牌,**立即调用 ``on_tokens`` 落库**
  (失败重试 3 次),落库成功才把内存副本清掉;
- ``get_status()`` 供前端轮询;``pending_tokens()`` / ``clear_tokens()``
  供 API 层在落库失败时做补偿;
- 每次登录带 generation,迟到的回调/看守线程不会影响下一代登录;
- 5 分钟未完成则标记失败并关闭监听端口;
- ``refresh_tokens()`` 供 provider 在 access_token 过期时轮换。
"""

from __future__ import annotations

import base64
import errno
import hashlib
import itertools
import json
import logging
import secrets
import threading
import time
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Literal

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

CODEX_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
AUTHORIZE_URL = "https://auth.openai.com/oauth/authorize"
TOKEN_URL = "https://auth.openai.com/oauth/token"
REDIRECT_URI = "http://localhost:1455/auth/callback"
CALLBACK_PORT = 1455
OAUTH_SCOPE = "openid profile email offline_access"
LOGIN_TTL_SECONDS = 300.0
BIND_RETRY_ATTEMPTS = 10
BIND_RETRY_SECONDS = 0.1
PERSIST_ATTEMPTS = 3
PERSIST_BACKOFF_SECONDS = 0.2

LoginStatus = Literal["idle", "pending", "completed", "failed"]
TokenSink = Callable[[dict[str, Any]], None]


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
    generation: int = 0
    persist_error: str | None = None


@dataclass
class _ActiveLogin:
    """一代登录的私有资源:回调 server、完成信号与落库回调。"""

    generation: int
    server: ThreadingHTTPServer
    done: threading.Event
    sink: TokenSink | None = None


_lock = threading.Lock()
_state = LoginState()
_active: _ActiveLogin | None = None
_generation = itertools.count(1)


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


def _expires_at(access: str, payload: dict[str, Any], now: float) -> int:
    """过期时间:优先 access_token 的 JWT ``exp``,其次 ``expires_in``,最后 1 小时。

    订阅端令牌实测有效期约 10 天;只信 ``expires_in``(缺失时按 3600)会让
    provider 每小时轮换一次 refresh_token,放大跨进程竞争窗口。
    """
    exp = _jwt_claims(access).get("exp")
    if isinstance(exp, (int, float)) and exp > now:
        ttl = float(exp) - now
    else:
        raw = payload.get("expires_in")
        try:
            ttl = float(raw) if raw is not None else 3600.0
        except (TypeError, ValueError):
            ttl = 3600.0
    return int(now + max(ttl - 60.0, 60.0))


def normalize_tokens(
    payload: dict[str, Any],
    *,
    fallback_refresh: str = "",
    previous: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """把 token 端点响应整理成落库结构(不含任何日志输出)。

    ``access_token`` / ``refresh_token`` / ``expires_at`` / ``obtained_at``
    永远取新值;``id_token`` / ``account_id`` / ``plan`` 在新响应缺失时回退
    ``previous``——刷新响应不一定带 ``id_token``,整键覆盖会把账号信息清空。
    """
    prev = previous or {}
    access = str(payload.get("access_token") or "")
    if not access:
        raise OAuthError("token 端点未返回 access_token")

    id_token = str(payload.get("id_token") or prev.get("id_token") or "")
    claims = _jwt_claims(id_token).get("https://api.openai.com/auth") or {}
    account = claims if isinstance(claims, dict) else {}
    now = time.time()
    return {
        "access_token": access,
        "refresh_token": str(
            payload.get("refresh_token")
            or fallback_refresh
            or prev.get("refresh_token")
            or ""
        ),
        "id_token": id_token,
        "account_id": str(account.get("chatgpt_account_id") or prev.get("account_id") or ""),
        "plan": str(account.get("chatgpt_plan_type") or prev.get("plan") or ""),
        "expires_at": _expires_at(access, payload, now),
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
        if parsed.path != "/auth/callback":
            self.send_response(404)
            self.end_headers()
            return
        active = getattr(self.server, "active_login", None)
        if not isinstance(active, _ActiveLogin):
            # 端口被上一代登录残留占用时的兜底:不触碰任何状态
            self._respond(409, "登录会话已失效，请回到应用重新发起登录。")
            return
        status, message = _handle_callback(active, parsed)
        self._respond(status, message)

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


def _handle_callback(
    active: _ActiveLogin, parsed: urllib.parse.ParseResult
) -> tuple[int, str]:
    """处理一次回调(不含 HTTP 细节);返回 (状态码, 展示文案)。"""
    query = urllib.parse.parse_qs(parsed.query)
    state = (query.get("state") or [""])[0]
    code = (query.get("code") or [""])[0]
    error = (query.get("error") or [""])[0]
    if state != getattr(active.server, "expected_state", ""):
        return 400, "登录状态校验失败，请回到应用重新发起登录。"
    if not code:
        _complete(active, failed=True, detail=f"授权被拒绝或取消: {error or '未知原因'}")
        return 400, f"登录失败：{error or '未返回授权码'}"
    try:
        tokens = _exchange_code(code, getattr(active.server, "code_verifier", ""))
    except OAuthError as exc:
        _complete(active, failed=True, detail=str(exc))
        return 502, f"换取令牌失败：{exc}"
    _complete(active, tokens=tokens)
    return 200, "登录完成，可以关闭此页回到应用。"


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


# ---------------------------------------------------------------------------
# 登录状态机
# ---------------------------------------------------------------------------


def _persist_with_retries(tokens: dict[str, Any], sink: TokenSink) -> str | None:
    """回调线程内同步落库;返回错误摘要(成功返回 None)。"""
    last: Exception | None = None
    for attempt in range(1, PERSIST_ATTEMPTS + 1):
        try:
            sink(dict(tokens))
            return None
        except Exception as exc:  # noqa: BLE001 - 落库失败要保留令牌做补偿
            last = exc
            logger.warning(
                "ChatGPT 订阅令牌落库失败(第 %s/%s 次)",
                attempt,
                PERSIST_ATTEMPTS,
                exc_info=True,
            )
            if attempt < PERSIST_ATTEMPTS:
                time.sleep(PERSIST_BACKOFF_SECONDS * attempt)
    name = type(last).__name__ if last is not None else "UnknownError"
    return f"登录成功，但令牌保存失败（{name}）；请检查数据库后重新登录。"


def _complete(
    active: _ActiveLogin,
    *,
    tokens: dict[str, Any] | None = None,
    failed: bool = False,
    detail: str | None = None,
) -> None:
    """结束一代登录:先落库,再发布状态,最后唤醒看守线程。"""
    with _lock:
        if active.generation != _state.generation:
            return  # 上一代登录的迟到回调,直接丢弃

    if failed or not tokens:
        with _lock:
            if active.generation == _state.generation:
                _state.status = "failed"
                _state.detail = detail
                _state.tokens = None
                _state.persist_error = None
        active.done.set()
        return

    persist_error: str | None = None
    if active.sink is not None:
        persist_error = _persist_with_retries(tokens, active.sink)

    with _lock:
        if active.generation != _state.generation:
            return
        _state.status = "completed"
        _state.detail = detail
        if persist_error:
            _state.persist_error = persist_error
            _state.tokens = dict(tokens)
        elif active.sink is not None:
            _state.persist_error = None
            _state.tokens = None
        else:
            # 没有注入 sink(旧调用方/测试):保留副本,由 status 端点补偿落库
            _state.persist_error = None
            _state.tokens = dict(tokens)
    active.done.set()


def _stop_server(server: ThreadingHTTPServer) -> None:
    """同步关闭监听端口(调用前必须已启动 serve_forever)。"""
    try:
        server.shutdown()
        server.server_close()
    except Exception:  # noqa: BLE001 - 关闭失败不影响登录结果
        logger.warning("关闭 OAuth 回调服务器失败", exc_info=True)


def _bind_callback_server() -> ThreadingHTTPServer:
    """绑定 127.0.0.1:1455;旧 server 刚关闭时端口可能仍在 TIME_WAIT/收尾。"""
    last: OSError | None = None
    for attempt in range(BIND_RETRY_ATTEMPTS):
        try:
            return ThreadingHTTPServer(("127.0.0.1", CALLBACK_PORT), _CallbackHandler)
        except OSError as exc:
            last = exc
            if exc.errno != errno.EADDRINUSE:
                raise OAuthError(
                    f"无法监听 127.0.0.1:{CALLBACK_PORT}（{exc}）"
                ) from exc
            if attempt < BIND_RETRY_ATTEMPTS - 1:
                time.sleep(BIND_RETRY_SECONDS)
    raise OAuthError(
        f"无法监听 127.0.0.1:{CALLBACK_PORT}（端口仍被占用）。"
        "若刚结束上一次登录，请稍等约 1 秒重试；"
        "否则请关闭占用该端口的程序（例如正在登录的 codex）后重试。"
    ) from last


def _watch(active: _ActiveLogin, deadline: float) -> None:
    """等待回调完成或超时,然后关闭监听端口。只处理自己那一代。"""
    active.done.wait(timeout=max(0.0, deadline - time.time()))
    with _lock:
        if active.generation == _state.generation and _state.status == "pending":
            _state.status = "failed"
            _state.detail = "登录超时（5 分钟），请重新发起"
    _stop_server(active.server)


def _snapshot_locked() -> LoginState:
    return LoginState(
        status=_state.status,
        authorize_url=_state.authorize_url,
        detail=_state.persist_error or _state.detail,
        started_at=_state.started_at,
        expires_at=_state.expires_at,
        tokens=_state.tokens,
        generation=_state.generation,
    )


# ---------------------------------------------------------------------------
# 对外接口
# ---------------------------------------------------------------------------


def start_login(*, on_tokens: TokenSink | None = None) -> LoginState:
    """发起登录;返回授权地址。已有进行中的登录时复用。"""
    global _active
    with _lock:
        if (
            _state.status == "pending"
            and _state.expires_at
            and time.time() < _state.expires_at
        ):
            return _snapshot_locked()
        old = _active
        _active = None

    # 先同步关掉上一代 server,确保端口真的释放后再绑定
    if old is not None:
        old.done.set()
        _stop_server(old.server)

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

    server = _bind_callback_server()
    server.expected_state = state_token  # type: ignore[attr-defined]
    server.code_verifier = verifier  # type: ignore[attr-defined]

    generation = next(_generation)
    active = _ActiveLogin(
        generation=generation,
        server=server,
        done=threading.Event(),
        sink=on_tokens,
    )
    server.active_login = active  # type: ignore[attr-defined]

    # 先启动监听线程,再发布状态:保证任何 shutdown 调用都发生在 serve_forever 之后
    threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True
    ).start()

    started = time.time()
    deadline = started + LOGIN_TTL_SECONDS
    with _lock:
        _state.status = "pending"
        _state.authorize_url = authorize_url
        _state.detail = None
        _state.persist_error = None
        _state.started_at = started
        _state.expires_at = deadline
        _state.tokens = None
        _state.generation = generation
        _active = active

    threading.Thread(target=_watch, args=(active, deadline), daemon=True).start()
    return _snapshot_locked()


def get_status() -> LoginState:
    with _lock:
        return _snapshot_locked()


def pending_tokens() -> dict[str, Any] | None:
    """返回尚未落库的令牌副本;不改变状态(供 status 端点补偿)。"""
    with _lock:
        if _state.status != "completed" or not _state.tokens:
            return None
        return dict(_state.tokens)


def clear_tokens() -> None:
    """落库成功后清掉内存副本。"""
    with _lock:
        _state.tokens = None
        _state.persist_error = None


def reset() -> None:
    """清除登录状态并关闭监听(退出登录时调用)。"""
    global _active
    with _lock:
        active = _active
        _active = None
        _state.status = "idle"
        _state.authorize_url = None
        _state.detail = None
        _state.persist_error = None
        _state.tokens = None
        _state.started_at = None
        _state.expires_at = None
        _state.generation = next(_generation)
    if active is not None:
        active.done.set()
        _stop_server(active.server)


async def refresh_tokens(
    client: httpx.AsyncClient,
    refresh_token: str,
    *,
    previous: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """用 refresh_token 轮换令牌;失败抛 ``OAuthError``。

    ``previous`` 用于在刷新响应缺字段时保留 id_token/account_id/plan。
    """
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
    return normalize_tokens(
        payload, fallback_refresh=refresh_token, previous=previous
    )
