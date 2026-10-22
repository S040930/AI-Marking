"""模型通道 API:订阅可用性、OAuth 端点与 run 创建门控。"""

from __future__ import annotations

import time

from app.agent.providers import chatgpt_oauth
from app.models.question import Question, QuestionStatus
from app.models.submission import Submission, SubmissionStatus
from app.services.provider_config import (
    OPENAI_API_KEY_KEY,
    PROVIDER_CHATGPT_SUB,
    PROVIDER_DEFAULT_KEY,
    PROVIDER_OPENAI_API,
    get_provider_config,
    save_chatgpt_tokens,
    set_provider_config,
)

_TOKENS = {
    "access_token": "access-secret-token",
    "refresh_token": "refresh-secret-token",
    "account_id": "acct-0000-1111-2222-333344445555",
    "plan": "plus",
    "expires_at": int(time.time()) + 3600,
}


def _seed_submission(db_session) -> int:
    db_session.add(
        Question(
            id="q1",
            name="题目1",
            original_filename="q1.pdf",
            file_path="uploads/q1.pdf",
            status=QuestionStatus.ready,
        )
    )
    submission = Submission(
        original_filename="s1.pdf",
        file_path="uploads/s1.pdf",
        question_id="q1",
        status=SubmissionStatus.awaiting_grading,
    )
    db_session.add(submission)
    db_session.commit()
    return submission.id


async def test_provider_summary_reports_subscription_state(db_session, client):
    save_chatgpt_tokens(db_session, _TOKENS)
    response = await client.get("/api/agent/providers")
    assert response.status_code == 200
    body = response.json()
    sub = body["providers"][PROVIDER_CHATGPT_SUB]
    assert sub["configured"] is True
    assert sub["available"] is True
    assert sub["plan"] == "plus"
    assert sub["account_last4"] == "5555"
    assert sub["expires_at"].endswith("+00:00")
    # 任何 token 原文都不得出现在响应里
    assert "access-secret-token" not in response.text
    assert "refresh-secret-token" not in response.text


async def test_create_run_blocked_when_subscription_not_logged_in(
    db_session, client
):
    submission_id = _seed_submission(db_session)
    response = await client.post(
        "/api/agent/runs",
        json={"submission_id": submission_id, "provider": PROVIDER_CHATGPT_SUB},
    )
    assert response.status_code == 409
    assert "尚未登录" in response.json()["detail"]


async def test_create_run_blocked_when_subscription_expired_without_refresh(
    db_session, client
):
    submission_id = _seed_submission(db_session)
    save_chatgpt_tokens(
        db_session,
        {
            "access_token": "expired",
            "refresh_token": "",
            "expires_at": int(time.time()) - 120,
        },
    )
    response = await client.post(
        "/api/agent/runs",
        json={"submission_id": submission_id, "provider": PROVIDER_CHATGPT_SUB},
    )
    assert response.status_code == 409
    assert "过期" in response.json()["detail"]


async def test_oauth_status_idle_before_login(client):
    chatgpt_oauth.reset()
    response = await client.get("/api/agent/providers/chatgpt_sub/oauth/status")
    assert response.status_code == 200
    assert response.json()["status"] == "idle"


async def test_oauth_start_returns_authorize_url(client, monkeypatch):
    captured: dict = {}

    def fake_start_login(*, on_tokens=None):  # noqa: ANN001
        captured["sink"] = on_tokens
        return chatgpt_oauth.LoginState(
            status="pending",
            authorize_url="https://auth.example/authorize?x=1",
            expires_at=time.time() + 300,
        )

    monkeypatch.setattr(chatgpt_oauth, "start_login", fake_start_login)
    response = await client.post("/api/agent/providers/chatgpt_sub/oauth/start")
    assert response.status_code == 200
    body = response.json()
    assert body["authorize_url"].startswith("https://auth.example/")
    assert body["expires_at"]
    # 回调线程拿到的 sink 必须能落库(而不是空回调)
    assert callable(captured["sink"])


async def test_oauth_status_persists_tokens_once(db_session, client, monkeypatch):
    state = chatgpt_oauth.LoginState(
        status="completed", tokens=dict(_TOKENS), started_at=time.time()
    )
    cleared: list[int] = []
    monkeypatch.setattr(chatgpt_oauth, "get_status", lambda: state)
    monkeypatch.setattr(chatgpt_oauth, "pending_tokens", lambda: dict(_TOKENS))
    monkeypatch.setattr(chatgpt_oauth, "clear_tokens", lambda: cleared.append(1))
    response = await client.get("/api/agent/providers/chatgpt_sub/oauth/status")
    assert response.status_code == 200
    assert response.json()["status"] == "completed"
    assert get_provider_config(db_session)["chatgpt_tokens"].startswith("{")
    assert cleared == [1]


async def test_oauth_status_keeps_tokens_when_compensation_fails(
    db_session, client, monkeypatch
):
    """补偿落库失败时不能清掉内存副本,否则只能重新登录。"""
    state = chatgpt_oauth.LoginState(
        status="completed", tokens=dict(_TOKENS), started_at=time.time()
    )
    cleared: list[int] = []
    monkeypatch.setattr(chatgpt_oauth, "get_status", lambda: state)
    monkeypatch.setattr(chatgpt_oauth, "pending_tokens", lambda: dict(_TOKENS))
    monkeypatch.setattr(chatgpt_oauth, "clear_tokens", lambda: cleared.append(1))

    def boom(db, tokens):  # noqa: ANN001
        raise RuntimeError("db down")

    monkeypatch.setattr("app.api.agent_providers.save_chatgpt_tokens", boom)
    response = await client.get("/api/agent/providers/chatgpt_sub/oauth/status")

    assert response.status_code == 200
    assert cleared == []
    assert "chatgpt_tokens" not in get_provider_config(db_session)


async def test_logout_clears_tokens_and_resets_default(db_session, client):
    chatgpt_oauth.reset()
    save_chatgpt_tokens(db_session, _TOKENS)
    await client.put(
        "/api/agent/providers/default", json={"provider": PROVIDER_CHATGPT_SUB}
    )
    response = await client.post("/api/agent/providers/chatgpt_sub/oauth/logout")
    assert response.status_code == 200
    body = response.json()
    assert body["provider_default"] == PROVIDER_OPENAI_API
    assert body["providers"][PROVIDER_CHATGPT_SUB]["available"] is False
    config = get_provider_config(db_session)
    assert "chatgpt_tokens" not in config
    assert config[PROVIDER_DEFAULT_KEY] == PROVIDER_OPENAI_API


async def test_provider_test_endpoint_needs_auth_without_tokens(client):
    response = await client.post("/api/agent/providers/chatgpt_sub/test")
    assert response.status_code == 200
    assert response.json()["status"] == "needs_auth"


async def test_create_run_rejects_unverified_subscription_effort(db_session, client):
    """订阅通道实测只认 low..max;minimal 必须在建 run 时就被拦住。"""
    submission_id = _seed_submission(db_session)
    save_chatgpt_tokens(db_session, _TOKENS)

    response = await client.post(
        "/api/agent/runs",
        json={
            "submission_id": submission_id,
            "provider": PROVIDER_CHATGPT_SUB,
            "reasoning_effort": "minimal",
        },
    )

    assert response.status_code == 422
    assert "minimal" in response.json()["detail"]


async def test_create_run_allows_minimal_effort_on_official_channel(db_session, client):
    submission_id = _seed_submission(db_session)
    set_provider_config(db_session, {OPENAI_API_KEY_KEY: "sk-test"})

    response = await client.post(
        "/api/agent/runs",
        json={
            "submission_id": submission_id,
            "provider": PROVIDER_OPENAI_API,
            "reasoning_effort": "minimal",
        },
    )

    assert response.status_code == 200


async def test_update_run_configuration_rejects_unverified_effort(db_session, client):
    submission_id = _seed_submission(db_session)
    save_chatgpt_tokens(db_session, _TOKENS)
    created = await client.post(
        "/api/agent/runs",
        json={"submission_id": submission_id, "provider": PROVIDER_CHATGPT_SUB},
    )
    assert created.status_code == 200
    run_id = created.json()["run"]["id"]

    response = await client.patch(
        f"/api/agent/runs/{run_id}/configuration",
        json={"reasoning_effort": "ultra"},
    )

    assert response.status_code == 422
    assert "ultra" in response.json()["detail"]
