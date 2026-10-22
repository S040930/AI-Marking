"""ChatGPT 订阅 OAuth 模块的单元测试:令牌归一化与登录状态机。"""

from __future__ import annotations

import base64
import errno
import json
import threading
import time
import types
import urllib.parse
from types import SimpleNamespace

import pytest

from app.agent.providers import chatgpt_oauth as oauth


def _jwt(claims: dict) -> str:
    def b64(payload: dict) -> str:
        raw = json.dumps(payload).encode()
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    return f"{b64({'alg': 'none'})}.{b64(claims)}.sig"


def _id_token(account_id: str = "acct-abc-123", plan: str = "plus") -> str:
    return _jwt(
        {
            "https://api.openai.com/auth": {
                "chatgpt_account_id": account_id,
                "chatgpt_plan_type": plan,
            }
        }
    )


class _FakeServer:
    def __init__(self) -> None:
        self.expected_state = "state-1"
        self.code_verifier = "verifier-1"
        self.shutdown_calls = 0
        self.close_calls = 0

    def shutdown(self) -> None:
        self.shutdown_calls += 1

    def server_close(self) -> None:
        self.close_calls += 1


class _FakeHttpServer:
    """可配置的 ThreadingHTTPServer 替身(只用于 bind 重试测试)。"""

    def __init__(self, failures: int) -> None:
        self.failures = failures
        self.attempts = 0

    def __call__(self, address, handler):  # noqa: ANN001 - 模拟构造器
        self.attempts += 1
        if self.attempts <= self.failures:
            raise OSError(errno.EADDRINUSE, "Address already in use")
        return SimpleNamespace(address=address, handler=handler)


@pytest.fixture(autouse=True)
def _reset_login_state():
    oauth.reset()
    yield
    oauth.reset()


def _active(sink=None, *, generation: int | None = None) -> oauth._ActiveLogin:
    return oauth._ActiveLogin(
        generation=oauth._state.generation if generation is None else generation,
        server=_FakeServer(),
        done=threading.Event(),
        sink=sink,
    )


# ---------------------------------------------------------------------------
# normalize_tokens
# ---------------------------------------------------------------------------


def test_normalize_tokens_prefers_access_jwt_exp():
    exp = int(time.time()) + 7200
    tokens = oauth.normalize_tokens(
        {
            "access_token": _jwt({"exp": exp}),
            "refresh_token": "r1",
            "expires_in": 60,
        }
    )
    # 以 JWT exp 为准,并保留 60s 提前量;不能退回 expires_in=60
    assert abs(tokens["expires_at"] - (exp - 60)) <= 2


def test_normalize_tokens_falls_back_to_expires_in():
    tokens = oauth.normalize_tokens(
        {"access_token": "not-a-jwt", "refresh_token": "r1", "expires_in": 600}
    )
    assert tokens["expires_at"] >= int(time.time()) + 500


def test_normalize_tokens_merges_previous_account_fields():
    previous = {
        "id_token": _id_token(),
        "account_id": "acct-abc-123",
        "plan": "plus",
        "refresh_token": "r1",
    }
    tokens = oauth.normalize_tokens(
        {"access_token": "A2", "refresh_token": "R2", "expires_in": 600},
        previous=previous,
    )
    assert tokens["account_id"] == "acct-abc-123"
    assert tokens["plan"] == "plus"
    assert tokens["id_token"] == previous["id_token"]
    assert tokens["refresh_token"] == "R2"


def test_normalize_tokens_new_id_token_overrides_previous():
    previous = {"id_token": _id_token("acct-old", "plus")}
    tokens = oauth.normalize_tokens(
        {"access_token": "A2", "id_token": _id_token("acct-new", "pro")},
        previous=previous,
    )
    assert tokens["account_id"] == "acct-new"
    assert tokens["plan"] == "pro"


def test_normalize_tokens_requires_access_token():
    with pytest.raises(oauth.OAuthError):
        oauth.normalize_tokens({"refresh_token": "r1"})


# ---------------------------------------------------------------------------
# 回调:落库 / 代际 / 补偿
# ---------------------------------------------------------------------------


def test_callback_persists_tokens_via_sink(monkeypatch):
    saved: list[dict] = []
    monkeypatch.setattr(
        oauth,
        "_exchange_code",
        lambda code, verifier: {
            "access_token": "A1",
            "refresh_token": "R1",
            "expires_at": int(time.time()) + 3600,
        },
    )
    active = _active(saved.append)

    status, _ = oauth._handle_callback(
        active, urllib.parse.urlparse("/auth/callback?state=state-1&code=xyz")
    )

    assert status == 200
    assert saved and saved[0]["access_token"] == "A1"
    assert oauth.get_status().status == "completed"
    # 已落库:内存不留副本
    assert oauth.pending_tokens() is None


def test_callback_sink_failure_keeps_tokens_for_compensation(monkeypatch):
    monkeypatch.setattr(oauth, "PERSIST_BACKOFF_SECONDS", 0)
    monkeypatch.setattr(
        oauth,
        "_exchange_code",
        lambda code, verifier: {
            "access_token": "A1",
            "refresh_token": "R1",
            "expires_at": int(time.time()) + 3600,
        },
    )

    def broken(tokens: dict) -> None:
        raise RuntimeError("db down")

    active = _active(broken)
    status, _ = oauth._handle_callback(
        active, urllib.parse.urlparse("/auth/callback?state=state-1&code=xyz")
    )

    assert status == 200
    assert oauth.get_status().status == "completed"
    assert "保存失败" in (oauth.get_status().detail or "")
    pending = oauth.pending_tokens()
    assert pending is not None and pending["access_token"] == "A1"


def test_stale_generation_callback_is_ignored(monkeypatch):
    monkeypatch.setattr(
        oauth,
        "_exchange_code",
        lambda code, verifier: {"access_token": "A1", "refresh_token": "R1"},
    )
    saved: list[dict] = []
    active = _active(saved.append, generation=oauth._state.generation - 1)

    status, _ = oauth._handle_callback(
        active, urllib.parse.urlparse("/auth/callback?state=state-1&code=xyz")
    )

    assert status == 200
    assert saved == []
    assert oauth.get_status().status == "idle"


def test_callback_rejects_state_mismatch(monkeypatch):
    called = {"exchange": 0}

    def exchange(code, verifier):  # noqa: ANN001
        called["exchange"] += 1
        return {}

    monkeypatch.setattr(oauth, "_exchange_code", exchange)
    status, _ = oauth._handle_callback(
        _active(lambda tokens: None),
        urllib.parse.urlparse("/auth/callback?state=wrong&code=xyz"),
    )

    assert status == 400
    assert called["exchange"] == 0


def test_callback_denied_sets_failed():
    status, _ = oauth._handle_callback(
        _active(lambda tokens: None),
        urllib.parse.urlparse("/auth/callback?state=state-1&error=access_denied"),
    )
    assert status == 400
    assert oauth.get_status().status == "failed"
    assert "access_denied" in (oauth.get_status().detail or "")


def test_clear_tokens_after_successful_compensation():
    tokens = {"access_token": "A1"}
    with oauth._lock:
        oauth._state.status = "completed"
        oauth._state.tokens = dict(tokens)

    assert oauth.pending_tokens() == tokens
    oauth.clear_tokens()
    assert oauth.pending_tokens() is None


# ---------------------------------------------------------------------------
# 端口绑定与关闭
# ---------------------------------------------------------------------------


def test_bind_retries_eaddrinuse_then_succeeds(monkeypatch):
    fake = _FakeHttpServer(failures=2)
    monkeypatch.setattr(oauth, "ThreadingHTTPServer", fake)
    monkeypatch.setattr(
        oauth, "time", types.SimpleNamespace(sleep=lambda _s: None, time=time.time)
    )

    server = oauth._bind_callback_server()

    assert fake.attempts == 3
    assert server is not None


def test_bind_exhausted_message_mentions_previous_login_and_codex(monkeypatch):
    fake = _FakeHttpServer(failures=oauth.BIND_RETRY_ATTEMPTS)
    monkeypatch.setattr(oauth, "ThreadingHTTPServer", fake)
    monkeypatch.setattr(
        oauth, "time", types.SimpleNamespace(sleep=lambda _s: None, time=time.time)
    )

    with pytest.raises(oauth.OAuthError) as excinfo:
        oauth._bind_callback_server()

    message = str(excinfo.value)
    assert "端口仍被占用" in message
    assert "上一次登录" in message
    assert "codex" in message


def test_reset_closes_server_synchronously():
    server = _FakeServer()
    oauth._active = oauth._ActiveLogin(
        generation=oauth._state.generation,
        server=server,  # type: ignore[arg-type]
        done=threading.Event(),
        sink=None,
    )

    oauth.reset()

    assert server.shutdown_calls == 1
    assert server.close_calls == 1
    assert oauth.get_status().status == "idle"
