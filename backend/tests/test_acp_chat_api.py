"""ACP 对话面板 API 验收:会话生命周期、权限交互、模型注入与 SSE。"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

import pytest
from sqlalchemy.orm import sessionmaker

from app.acp.chat import reset_chat_registry
from app.acp.errors import AcpError
from app.acp.models import AcpAgentInstallation
from app.acp.registry import AgentEntry
from app.models.question import Question, QuestionStatus
from app.models.submission import Submission, SubmissionStatus

BACKEND_ROOT = Path(__file__).resolve().parents[1]


def _fake_entry(extra_args: tuple[str, ...] = ()) -> AgentEntry:
    """可运行的假 agent 快照;身份字段与 codex-acp 白名单规则一致。"""
    return AgentEntry(
        agent_id="codex-acp",
        registry_version=None,
        distribution="npx",
        package="@agentclientprotocol/codex-acp",
        version="0.1.0",
        command=sys.executable,
        args=["-m", "tests.acp_fake_agent", *extra_args],
        env={"PYTHONPATH": str(BACKEND_ROOT)},
    )


@pytest.fixture(autouse=True)
async def chat_env(tmp_path, monkeypatch, db_session):
    """隔离缓存/上传目录;注册表绑定本测试的 SQLite 工厂并在结束后回收进程。"""
    import app.api.acp_chat as acp_chat_api
    import app.core.config as config

    monkeypatch.setattr(acp_chat_api, "_CACHE_DIR", tmp_path / "acp-cache")
    uploads = tmp_path / "uploads"
    uploads.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(config.settings, "UPLOAD_DIR", str(uploads))

    factory = sessionmaker(bind=db_session.bind, expire_on_commit=False)
    registry = reset_chat_registry()
    registry.configure(factory)
    yield registry
    await registry.shutdown()


@pytest.fixture
def review_submission(db_session, chat_env):
    """ready_for_review 作业;文件落在受控 uploads 目录内。"""
    import app.core.config as config

    db_session.add(
        Question(
            id="q-chat",
            name="对话题目",
            original_filename="chat.pdf",
            file_path=str(Path(config.settings.UPLOAD_DIR) / "chat.pdf"),
            status=QuestionStatus.ready,
        )
    )
    report = Path(config.settings.UPLOAD_DIR) / "report.pdf"
    report.write_bytes(b"%PDF-1.4 fake report")
    sub = Submission(
        original_filename="report.pdf",
        file_path=str(report),
        question_id="q-chat",
        status=SubmissionStatus.ready_for_review,
    )
    db_session.add(sub)
    db_session.commit()
    return sub.id


@pytest.fixture
def installed_fake(db_session, review_submission):
    entry = _fake_entry()
    db_session.add(
        AcpAgentInstallation(
            agent_id=entry.agent_id,
            version=entry.version,
            distribution=entry.distribution,
            launch_snapshot=entry.to_dict(),
            connection_status="ready",
        )
    )
    db_session.commit()
    return entry


@pytest.fixture
def awaiting_submission(db_session, chat_env):
    """awaiting_mcp 作业:批改尚未发起,允许在助手里选模型并发起批改。"""
    import app.core.config as config

    db_session.add(
        Question(
            id="q-awaiting",
            name="待批改题目",
            original_filename="awaiting.pdf",
            file_path=str(Path(config.settings.UPLOAD_DIR) / "awaiting.pdf"),
            status=QuestionStatus.ready,
        )
    )
    report = Path(config.settings.UPLOAD_DIR) / "awaiting.pdf"
    report.write_bytes(b"%PDF-1.4 fake awaiting")
    sub = Submission(
        original_filename="awaiting.pdf",
        file_path=str(report),
        question_id="q-awaiting",
        status=SubmissionStatus.awaiting_mcp,
    )
    db_session.add(sub)
    db_session.commit()
    return sub.id


@pytest.fixture
def installed_fake_awaiting(db_session, awaiting_submission):
    entry = _fake_entry()
    db_session.add(
        AcpAgentInstallation(
            agent_id=entry.agent_id,
            version=entry.version,
            distribution=entry.distribution,
            launch_snapshot=entry.to_dict(),
            connection_status="ready",
        )
    )
    db_session.commit()
    return entry


async def _wait_for(
    client, chat_id: int, predicate, timeout: float = 15.0, what: str = "condition"
):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        r = await client.get(f"/api/acp/chat/sessions/{chat_id}")
        last = r.json()
        if predicate(last):
            return last
        await asyncio.sleep(0.05)
    raise AssertionError(f"等待 {what} 超时: {last}")


async def _events(client, chat_id: int):
    r = await client.get(f"/api/acp/chat/sessions/{chat_id}/events")
    assert r.status_code == 200
    return r.json()


async def _create_session(client, submission_id: int, model_id: str | None = None):
    payload = {"submission_id": submission_id, "codex_config": {}}
    if model_id:
        payload["codex_config"] = {"model_id": model_id}
    r = await client.post("/api/acp/chat/sessions", json=payload)
    assert r.status_code == 200, r.text
    return r.json()


# ---------------------------------------------------------------------------
# 建会话校验
# ---------------------------------------------------------------------------


async def test_create_session_requires_installed_agent(client, review_submission):
    r = await client.post(
        "/api/acp/chat/sessions",
        json={"submission_id": review_submission},
    )
    assert r.status_code == 409
    assert "尚未安装" in r.json()["detail"]


async def test_create_session_allows_awaiting_mcp_without_run(
    client, awaiting_submission, installed_fake_awaiting
):
    """awaiting_mcp 且无活跃 run:允许建会话,教师可在助手里发起批改。

    与 run 的互斥下沉到「存在活跃 run」闸门,不再靠作业状态一刀切。
    """
    r = await client.post(
        "/api/acp/chat/sessions",
        json={"submission_id": awaiting_submission},
    )
    assert r.status_code == 200, r.text


async def test_create_session_rejects_active_run(
    client, awaiting_submission, installed_fake_awaiting, monkeypatch
):
    """已有活跃批改 run 时拒绝建会话,避免与 run 争用 MCP 写工具。"""
    import app.api.acp_chat as acp_chat_api

    monkeypatch.setattr(
        acp_chat_api,
        "get_active_run_for_submission",
        lambda db, submission_id: object(),
    )
    r = await client.post(
        "/api/acp/chat/sessions",
        json={"submission_id": awaiting_submission},
    )
    assert r.status_code == 409
    assert "批改运行" in r.json()["detail"]


async def test_send_message_rejects_active_run(
    client, review_submission, installed_fake, monkeypatch
):
    """run 可能在建会话之后才发起,发消息同样要过活跃 run 闸门。"""
    import app.api.acp_chat as acp_chat_api

    chat = await _create_session(client, review_submission)
    monkeypatch.setattr(
        acp_chat_api,
        "get_active_run_for_submission",
        lambda db, submission_id: object(),
    )
    r = await client.post(
        f"/api/acp/chat/sessions/{chat['id']}/messages",
        json={"text": "继续"},
    )
    assert r.status_code == 409
    assert "批改运行" in r.json()["detail"]


async def test_create_session_rejects_unknown_model(client, review_submission, installed_fake):
    r = await client.post(
        "/api/acp/chat/sessions",
        json={
            "submission_id": review_submission,
            "codex_config": {"model_id": "no-such-model"},
        },
    )
    assert r.status_code == 422


async def test_codex_configuration_is_live_and_fast_requires_confirmation(
    client, review_submission, installed_fake
):
    from app.acp.model_catalog import reset_model_cache

    reset_model_cache()
    catalog = await client.get(
        "/api/acp/codex/configuration", params={"model_id": "fake-model-b"}
    )
    assert catalog.status_code == 200
    body = catalog.json()
    assert [item["id"] for item in body["reasoning_efforts"]] == [
        "medium",
        "high",
        "xhigh",
    ]
    assert {item["id"] for item in body["speed_modes"]} == {"standard", "fast"}

    unconfirmed = await client.post(
        "/api/acp/chat/sessions",
        json={
            "submission_id": review_submission,
            "codex_config": {
                "model_id": "fake-model-b",
                "reasoning_effort": "high",
                "speed_mode": "fast",
            },
        },
    )
    assert unconfirmed.status_code == 422

    confirmed = await client.post(
        "/api/acp/chat/sessions",
        json={
            "submission_id": review_submission,
            "codex_config": {
                "model_id": "fake-model-b",
                "reasoning_effort": "high",
                "speed_mode": "fast",
                "fast_confirmed": True,
            },
        },
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["desired_config"]["speed_mode"] == "fast"


# ---------------------------------------------------------------------------
# 会话主流程:发送 → 权限 → 批准/拒绝 → 回 idle
# ---------------------------------------------------------------------------


async def test_chat_flow_with_permission_allow(client, review_submission, installed_fake):
    chat = await _create_session(client, review_submission)
    chat_id = chat["id"]
    assert chat["status"] == "idle"

    sent = await client.post(
        f"/api/acp/chat/sessions/{chat_id}/messages", json={"text": "分析这份报告"}
    )
    assert sent.status_code == 200, sent.text

    row = await _wait_for(
        client, chat_id, lambda d: d["status"] == "waiting_permission", what="权限请求"
    )
    assert row["status"] == "waiting_permission"

    events = await _events(client, chat_id)
    kinds = [e["kind"] for e in events]
    assert "user_message" in kinds
    assert "message_delta" in kinds
    assert "tool_started" in kinds
    assert "permission_request" in kinds

    permission = next(e for e in events if e["kind"] == "permission_request")
    pid = permission["payload"]["permission_id"]

    allowed = await client.post(
        f"/api/acp/chat/sessions/{chat_id}/permission",
        json={"permission_id": pid, "allow": True},
    )
    assert allowed.status_code == 200

    row = await _wait_for(client, chat_id, lambda d: d["status"] == "idle", what="回合结束")
    events = await _events(client, chat_id)
    kinds = [e["kind"] for e in events]
    assert "permission_resolved" in kinds
    assert "turn_completed" in kinds
    resolved = next(e for e in events if e["kind"] == "permission_resolved")
    assert resolved["payload"]["outcome"] == "allowed"


async def test_chat_flow_permission_deny(client, review_submission, installed_fake):
    chat = await _create_session(client, review_submission)
    chat_id = chat["id"]
    await client.post(
        f"/api/acp/chat/sessions/{chat_id}/messages", json={"text": "继续"}
    )
    await _wait_for(
        client, chat_id, lambda d: d["status"] == "waiting_permission", what="权限请求"
    )
    events = await _events(client, chat_id)
    pid = next(
        e["payload"]["permission_id"] for e in events if e["kind"] == "permission_request"
    )

    denied = await client.post(
        f"/api/acp/chat/sessions/{chat_id}/permission",
        json={"permission_id": pid, "allow": False},
    )
    assert denied.status_code == 200
    await _wait_for(client, chat_id, lambda d: d["status"] == "idle", what="回合结束")
    events = await _events(client, chat_id)
    resolved = next(e for e in events if e["kind"] == "permission_resolved")
    assert resolved["payload"]["outcome"] == "denied"


async def test_permission_timeout_auto_denies(
    client, review_submission, installed_fake, monkeypatch
):
    import app.acp.chat as chat_mod

    monkeypatch.setattr(chat_mod.get_chat_registry(), "permission_dwell_seconds", 0.2)
    chat = await _create_session(client, review_submission)
    chat_id = chat["id"]
    await client.post(
        f"/api/acp/chat/sessions/{chat_id}/messages", json={"text": "测试超时"}
    )
    await _wait_for(client, chat_id, lambda d: d["status"] == "idle", what="超时拒绝后结束")
    events = await _events(client, chat_id)
    texts = [str(e["payload"].get("text", "")) for e in events]
    assert any("超时" in t for t in texts)
    resolved = next(e for e in events if e["kind"] == "permission_resolved")
    assert resolved["payload"]["outcome"] == "denied"


async def test_permission_answer_twice_conflicts(client, review_submission, installed_fake):
    chat = await _create_session(client, review_submission)
    chat_id = chat["id"]
    await client.post(
        f"/api/acp/chat/sessions/{chat_id}/messages", json={"text": "触发权限"}
    )
    await _wait_for(
        client, chat_id, lambda d: d["status"] == "waiting_permission", what="权限请求"
    )
    events = await _events(client, chat_id)
    pid = next(
        e["payload"]["permission_id"] for e in events if e["kind"] == "permission_request"
    )
    first = await client.post(
        f"/api/acp/chat/sessions/{chat_id}/permission",
        json={"permission_id": pid, "allow": True},
    )
    assert first.status_code == 200
    await _wait_for(client, chat_id, lambda d: d["status"] == "idle", what="回合结束")
    second = await client.post(
        f"/api/acp/chat/sessions/{chat_id}/permission",
        json={"permission_id": pid, "allow": True},
    )
    assert second.status_code == 409


# ---------------------------------------------------------------------------
# 并发/取消/关闭
# ---------------------------------------------------------------------------


async def test_send_while_busy_conflicts(client, review_submission, installed_fake, monkeypatch):
    # 用会请求权限的默认模式,让 turn 停在等待教师处
    chat = await _create_session(client, review_submission)
    chat_id = chat["id"]
    await client.post(
        f"/api/acp/chat/sessions/{chat_id}/messages", json={"text": "第一条"}
    )
    await _wait_for(
        client, chat_id, lambda d: d["status"] == "waiting_permission", what="权限请求"
    )
    r = await client.post(
        f"/api/acp/chat/sessions/{chat_id}/messages", json={"text": "第二条"}
    )
    assert r.status_code == 409


async def test_cancel_and_close(client, review_submission, db_session, monkeypatch):
    entry = _fake_entry(("--hang-prompt",))
    db_session.add(
        AcpAgentInstallation(
            agent_id=entry.agent_id,
            version=entry.version,
            distribution=entry.distribution,
            launch_snapshot=entry.to_dict(),
            connection_status="ready",
        )
    )
    db_session.commit()

    chat = await _create_session(client, review_submission)
    chat_id = chat["id"]
    await client.post(
        f"/api/acp/chat/sessions/{chat_id}/messages", json={"text": "长任务"}
    )
    row = await _wait_for(
        client, chat_id, lambda d: d["status"] == "running", what="turn 进行中"
    )
    assert row["status"] == "running"

    # running 时发送 → 409
    busy = await client.post(
        f"/api/acp/chat/sessions/{chat_id}/messages", json={"text": "抢发"}
    )
    assert busy.status_code == 409

    cancelled = await client.post(f"/api/acp/chat/sessions/{chat_id}/cancel")
    assert cancelled.status_code == 200
    await _wait_for(client, chat_id, lambda d: d["status"] == "idle", what="取消后回 idle")

    closed = await client.delete(f"/api/acp/chat/sessions/{chat_id}")
    assert closed.status_code == 200
    detail = await client.get(f"/api/acp/chat/sessions/{chat_id}")
    assert detail.json()["status"] == "closed"

    # closed 后发送 → 409
    again = await client.post(
        f"/api/acp/chat/sessions/{chat_id}/messages", json={"text": "复活"}
    )
    assert again.status_code == 409


async def test_closed_session_stream_replays_then_done(
    client, review_submission, installed_fake
):
    chat = await _create_session(client, review_submission)
    chat_id = chat["id"]
    await client.post(
        f"/api/acp/chat/sessions/{chat_id}/messages", json={"text": "你好"}
    )
    await _wait_for(
        client, chat_id, lambda d: d["status"] == "waiting_permission", what="权限请求"
    )
    events = await _events(client, chat_id)
    pid = next(
        e["payload"]["permission_id"] for e in events if e["kind"] == "permission_request"
    )
    await client.post(
        f"/api/acp/chat/sessions/{chat_id}/permission",
        json={"permission_id": pid, "allow": True},
    )
    await _wait_for(client, chat_id, lambda d: d["status"] == "idle", what="回合结束")
    await client.delete(f"/api/acp/chat/sessions/{chat_id}")

    # SSE:回放历史后应收到 done 注释并结束
    collected: list[str] = []
    async with client.stream(
        "GET", f"/api/acp/chat/sessions/{chat_id}/stream"
    ) as resp:
        assert resp.status_code == 200
        async for line in resp.aiter_lines():
            collected.append(line)
            if line == ": done":
                break
    assert any(line.startswith("event: chat_event") for line in collected)
    assert ": done" in collected


# ---------------------------------------------------------------------------
# 模型注入与历史列表
# ---------------------------------------------------------------------------


async def test_model_env_injected(client, review_submission, db_session):
    """agent 广播 config options 时:模型列表来自探测,选择经 set_config_option 生效。"""
    from app.acp.model_catalog import reset_model_cache

    entry = _fake_entry(("--no-permission",))
    db_session.add(
        AcpAgentInstallation(
            agent_id=entry.agent_id,
            version=entry.version,
            distribution=entry.distribution,
            launch_snapshot=entry.to_dict(),
            connection_status="ready",
        )
    )
    db_session.commit()
    reset_model_cache()

    models = await client.get("/api/acp/codex/configuration")
    assert models.status_code == 200
    body = models.json()
    assert any(m["id"] == "fake-model-a" for m in body["models"])
    assert any(m["id"] == "fake-model-b" for m in body["models"])
    # agent 默认选中项带 current 标记
    assert next(m for m in body["models"] if m["id"] == "fake-model-a")["current"] is True

    chat = await _create_session(client, review_submission, model_id="fake-model-b")
    chat_id = chat["id"]
    assert chat["codex_config"]["model_id"] == "fake-model-b"
    await client.post(
        f"/api/acp/chat/sessions/{chat_id}/messages", json={"text": "模型测试"}
    )
    await _wait_for(client, chat_id, lambda d: d["status"] == "idle", what="回合结束")
    events = await _events(client, chat_id)
    texts = [str(e["payload"].get("text", "")) for e in events if e["kind"] == "message_delta"]
    assert any("MODEL_ACTIVE:fake-model-b" in t for t in texts), texts


async def test_idle_chat_configuration_applies_to_existing_session(
    client, review_submission, installed_fake
):
    chat = await _create_session(client, review_submission)
    chat_id = chat["id"]
    await client.post(
        f"/api/acp/chat/sessions/{chat_id}/messages", json={"text": "启动会话"}
    )
    await _wait_for(
        client, chat_id, lambda d: d["status"] == "waiting_permission", what="权限请求"
    )
    permission = next(
        event
        for event in (await _events(client, chat_id))
        if event["kind"] == "permission_request"
    )
    await client.post(
        f"/api/acp/chat/sessions/{chat_id}/permission",
        json={"permission_id": permission["payload"]["permission_id"], "allow": True},
    )
    await _wait_for(client, chat_id, lambda d: d["status"] == "idle", what="回合结束")

    updated = await client.patch(
        f"/api/acp/chat/sessions/{chat_id}/codex-configuration",
        json={
            "model_id": "fake-model-b",
            "reasoning_effort": "high",
            "speed_mode": "fast",
            "fast_confirmed": True,
        },
    )
    assert updated.status_code == 200, updated.text
    body = updated.json()
    assert body["pending_config"] is None
    assert body["applied_config"]["model_id"] == "fake-model-b"
    assert body["applied_config"]["reasoning_effort"] == "high"
    assert body["applied_config"]["speed_mode"] == "fast"
    assert any(
        event["kind"] == "configuration_applied"
        for event in (await _events(client, chat_id))
    )


async def test_permission_mode_hot_update(client, review_submission, installed_fake):
    """已有会话 PATCH permission_mode:行内档位即时更新并落事件。"""
    chat = await _create_session(client, review_submission)
    chat_id = chat["id"]
    assert chat["permission_mode"] == "ask"
    await client.post(
        f"/api/acp/chat/sessions/{chat_id}/messages", json={"text": "启动会话"}
    )
    await _wait_for(
        client, chat_id, lambda d: d["status"] == "waiting_permission", what="权限请求"
    )
    permission = next(
        event
        for event in (await _events(client, chat_id))
        if event["kind"] == "permission_request"
    )
    await client.post(
        f"/api/acp/chat/sessions/{chat_id}/permission",
        json={"permission_id": permission["payload"]["permission_id"], "allow": True},
    )
    await _wait_for(client, chat_id, lambda d: d["status"] == "idle", what="回合结束")

    updated = await client.patch(
        f"/api/acp/chat/sessions/{chat_id}/codex-configuration",
        json={"permission_mode": "auto_review"},
    )
    assert updated.status_code == 200, updated.text
    body = updated.json()
    assert body["permission_mode"] == "auto_review"
    events = await _events(client, chat_id)
    mode_events = [
        e for e in events if e["kind"] == "permission_mode_updated"
    ]
    assert mode_events and mode_events[-1]["payload"]["permission_mode"] == "auto_review"

    # 新回合不再弹权限请求:假 agent 在 auto_review 下由后端自动放行。
    await client.post(
        f"/api/acp/chat/sessions/{chat_id}/messages", json={"text": "再次对话"}
    )
    final = await _wait_for(client, chat_id, lambda d: d["status"] == "idle", what="回合结束")
    assert final["status"] == "idle"


async def test_model_list_falls_back_without_config_options(
    client, review_submission, db_session
):
    """agent 不广播 config options 时回退内置兼容目录;未知模型仍 422。"""
    from app.acp.model_catalog import reset_model_cache

    entry = _fake_entry(("--no-permission", "--no-config-options"))
    db_session.add(
        AcpAgentInstallation(
            agent_id=entry.agent_id,
            version=entry.version,
            distribution=entry.distribution,
            launch_snapshot=entry.to_dict(),
            connection_status="ready",
        )
    )
    db_session.commit()
    reset_model_cache()

    models = await client.get("/api/acp/codex/configuration")
    assert models.status_code == 200
    assert models.json()["models"] == []


async def test_session_history_ordering(client, review_submission, installed_fake):
    first = await _create_session(client, review_submission)
    second = await _create_session(client, review_submission)
    listing = await client.get(
        "/api/acp/chat/sessions", params={"submission_id": review_submission}
    )
    assert listing.status_code == 200
    ids = [s["id"] for s in listing.json()["sessions"]]
    assert second["id"] in ids and first["id"] in ids
    # 按更新时间倒序,后建的在前
    assert ids.index(second["id"]) < ids.index(first["id"])


async def test_first_message_config_failure_degrades(
    client, review_submission, db_session, chat_env, monkeypatch
):
    """首条消息配置应用失败:降级继续,不把消息打成 409,落 configuration_failed。"""
    entry = _fake_entry(("--no-permission",))
    db_session.add(
        AcpAgentInstallation(
            agent_id=entry.agent_id,
            version=entry.version,
            distribution=entry.distribution,
            launch_snapshot=entry.to_dict(),
            connection_status="ready",
        )
    )
    db_session.commit()

    async def _boom(session, session_id, selection):
        raise AcpError("Agent 拒绝配置")

    monkeypatch.setattr("app.acp.model_catalog.apply_codex_selection", _boom)

    chat = await _create_session(client, review_submission)
    chat_id = chat["id"]
    sent = await client.post(
        f"/api/acp/chat/sessions/{chat_id}/messages", json={"text": "分析这份报告"}
    )
    assert sent.status_code == 200, sent.text
    final = await _wait_for(
        client, chat_id, lambda d: d["status"] == "idle", what="回合结束"
    )
    assert final["status"] == "idle"
    events = await _events(client, chat_id)
    kinds = [e["kind"] for e in events]
    assert "configuration_failed" in kinds
    assert "turn_completed" in kinds


# ---------------------------------------------------------------------------
# 永久删除
# ---------------------------------------------------------------------------


async def test_permanent_delete_stops_running_session_and_cleans_up(
    client, review_submission, db_session
):
    """运行中的会话被停止;行、事件、工作区目录全部清除。"""
    from app.acp.models import AcpChatEvent
    from app.acp.workspace import chat_workspace_root

    entry = _fake_entry(("--hang-prompt",))
    db_session.add(
        AcpAgentInstallation(
            agent_id=entry.agent_id,
            version=entry.version,
            distribution=entry.distribution,
            launch_snapshot=entry.to_dict(),
            connection_status="ready",
        )
    )
    db_session.commit()

    chat = await _create_session(client, review_submission)
    chat_id = chat["id"]
    await client.post(
        f"/api/acp/chat/sessions/{chat_id}/messages", json={"text": "长任务"}
    )
    await _wait_for(client, chat_id, lambda d: d["status"] == "running", what="turn 进行中")

    workspace = chat_workspace_root() / f"chat-{chat_id}"
    assert workspace.is_dir()

    deleted = await client.delete(f"/api/acp/chat/sessions/{chat_id}/permanent")
    assert deleted.status_code == 200
    assert deleted.json()["status"] == "deleted"

    # 行已删除,事件级联清除,工作区目录已清理
    remaining = (
        db_session.query(AcpChatEvent)
        .filter(AcpChatEvent.session_id == chat_id)
        .count()
    )
    assert remaining == 0
    assert not workspace.exists()

    # GET 404
    gone = await client.get(f"/api/acp/chat/sessions/{chat_id}")
    assert gone.status_code == 404

    # 再次永久删除 → 404
    again = await client.delete(f"/api/acp/chat/sessions/{chat_id}/permanent")
    assert again.status_code == 404


async def test_permanent_delete_closed_session_keeps_close_semantics(
    client, review_submission, installed_fake
):
    """原 close 接口仍保留历史;permanent 删除已 closed 会话也安全。"""
    chat = await _create_session(client, review_submission)
    chat_id = chat["id"]

    closed = await client.delete(f"/api/acp/chat/sessions/{chat_id}")
    assert closed.status_code == 200
    detail = await client.get(f"/api/acp/chat/sessions/{chat_id}")
    assert detail.json()["status"] == "closed"

    deleted = await client.delete(f"/api/acp/chat/sessions/{chat_id}/permanent")
    assert deleted.status_code == 200
    assert deleted.json()["status"] == "deleted"
    gone = await client.get(f"/api/acp/chat/sessions/{chat_id}")
    assert gone.status_code == 404


async def test_permanent_delete_requires_existing_session(client, review_submission):
    r = await client.delete("/api/acp/chat/sessions/9999/permanent")
    assert r.status_code == 404


async def test_deleted_session_stream_ends_with_done(
    client, review_submission, installed_fake, db_session
):
    """永久删除后,仍在进行的 SSE 流应读到 done 注释并结束。

    直接消费流生成器:httpx ASGITransport 会整体缓冲响应体,
    无法在 client.stream 内并发执行删除请求。
    """
    from app.api.acp_chat import _chat_event_stream

    chat = await _create_session(client, review_submission)
    chat_id = chat["id"]
    await client.post(
        f"/api/acp/chat/sessions/{chat_id}/messages", json={"text": "你好"}
    )
    await _wait_for(
        client, chat_id, lambda d: d["status"] == "waiting_permission", what="权限请求"
    )

    factory = sessionmaker(bind=db_session.bind, expire_on_commit=False)
    collected: list[str] = []
    deleted = False
    async for chunk in _chat_event_stream(chat_id, factory, after_seq=0):
        collected.append(chunk.strip())
        if not deleted:
            # 回放首个事件后删除会话;流应在下一次轮询时以 done 终止。
            resp = await client.delete(
                f"/api/acp/chat/sessions/{chat_id}/permanent"
            )
            assert resp.status_code == 200
            deleted = True
            continue
        if chunk.strip() == ": done":
            break
    assert deleted
    assert ": done" in collected
