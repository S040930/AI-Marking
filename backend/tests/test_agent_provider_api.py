"""模型通道 API:订阅可用性、OAuth 端点与 run 创建门控。"""

from __future__ import annotations

import time

from app.agent.providers import chatgpt_oauth
from app.models.question import Question, QuestionStatus
from app.models.submission import Submission, SubmissionStatus
from app.services.provider_config import (
    PROVIDER_CHATGPT_SUB,
    PROVIDER_DEFAULT_KEY,
    PROVIDER_OPENAI_API,
    get_provider_config,
    save_chatgpt_tokens,
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
    monkeypatch.setattr(
        chatgpt_oauth,
        "start_login",
        lambda: chatgpt_oauth.LoginState(
            status="pending",
            authorize_url="https://auth.example/authorize?x=1",
            expires_at=time.time() + 300,
        ),
    )
    response = await client.post("/api/agent/providers/chatgpt_sub/oauth/start")
    assert response.status_code == 200
    body = response.json()
    assert body["authorize_url"].startswith("https://auth.example/")
    assert body["expires_at"]


async def test_oauth_status_persists_tokens_once(db_session, client, monkeypatch):
    state = chatgpt_oauth.LoginState(
        status="completed", tokens=dict(_TOKENS), started_at=time.time()
    )
    monkeypatch.setattr(chatgpt_oauth, "get_status", lambda: state)
    monkeypatch.setattr(
        chatgpt_oauth, "consume_tokens", lambda: dict(_TOKENS)
    )
    response = await client.get("/api/agent/providers/chatgpt_sub/oauth/status")
    assert response.status_code == 200
    assert response.json()["status"] == "completed"
    assert get_provider_config(db_session)["chatgpt_tokens"].startswith("{")


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
