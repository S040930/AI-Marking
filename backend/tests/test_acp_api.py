"""ACP API 验收:agent 目录、run 生命周期、SSE 事件回放与教师答复。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.acp import runs as run_repo
from app.acp.domain import AcpRunStatus
from app.acp.models import AcpRun
from app.acp.registry import AgentEntry
from app.models.question import Question, QuestionStatus
from app.models.submission import Submission, SubmissionStatus


@pytest.fixture
def seeded(db_session):
    """题目 + awaiting_mcp 作业。"""
    db_session.add(
        Question(
            id="q-acp",
            name="ACP 题目",
            original_filename="acp.pdf",
            file_path="uploads/acp.pdf",
            status=QuestionStatus.ready,
        )
    )
    db_session.commit()
    sub = Submission(
        original_filename="s.pdf",
        file_path="uploads/s.pdf",
        question_id="q-acp",
        status=SubmissionStatus.awaiting_mcp,
    )
    db_session.add(sub)
    db_session.commit()
    return sub.id


@pytest.fixture
def installed_agent(tmp_path, monkeypatch, db_session):
    """Persist a reviewed, diagnosed test installation."""
    import app.api.acp as acp_api
    from app.acp.models import AcpAgentInstallation

    entry = AgentEntry(
        agent_id="codex-acp",
        registry_version=None,
        distribution="npx",
        package="@agentclientprotocol/codex-acp",
        version="0.1.0",
        command="nonexistent-cmd-for-diag",
        args=[],
        env={},
    )
    cache = tmp_path / "acp-cache"
    cache.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(acp_api, "_CACHE_DIR", cache)
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
    return cache


async def test_agents_endpoint_lists_whitelist(client):
    resp = await client.get("/api/acp/agents")
    assert resp.status_code == 200
    data = resp.json()
    ids = {a["agent_id"] for a in data["agents"]}
    assert ids == {"codex-acp"}
    assert "sandbox_certified" not in data["agents"][0]


async def test_create_run_requires_installed_agent(client, seeded, tmp_path, monkeypatch):
    # 隔离真实缓存目录:本机可能已安装该 agent(如通过设置页真实安装)。
    import app.api.acp as acp_api

    monkeypatch.setattr(acp_api, "_CACHE_DIR", tmp_path / "acp-cache")
    resp = await client.post(
        "/api/acp/runs", json={"submission_id": seeded}
    )
    assert resp.status_code == 409
    assert "尚未安装" in resp.json()["detail"]


async def test_create_run_with_installed_agent(client, seeded, installed_agent):
    resp = await client.post(
        "/api/acp/runs", json={"submission_id": seeded}
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["run"]["status"] == "queued"
    assert data["run"]["agent_id"] == "codex-acp"


async def test_create_code_run_only_requires_connection_ready(
    client, seeded, installed_agent, db_session
):
    """代码作业不再依赖历史认证状态。"""
    from app.models.submission_code_file import SubmissionCodeFile

    db_session.add(
        SubmissionCodeFile(
            submission_id=seeded,
            question_number=1,
            original_filename="main.py",
            file_path="uploads/main.py",
            file_kind="python",
            source_sha256="0" * 64,
        )
    )
    db_session.commit()

    resp = await client.post("/api/acp/runs", json={"submission_id": seeded})

    assert resp.status_code == 200, resp.text
    assert resp.json()["run"]["status"] == "queued"


def _entry():

    return AgentEntry(
        agent_id="fake",
        registry_version=None,
        distribution="binary",
        package="fake-agent",
        version="0.1.0",
        command="echo",
        args=[],
        env={},
    )


async def test_second_active_run_conflicts(client, seeded, installed_agent):
    first = await client.post(
        "/api/acp/runs", json={"submission_id": seeded}
    )
    assert first.status_code == 200
    second = await client.post(
        "/api/acp/runs", json={"submission_id": seeded}
    )
    assert second.status_code == 409


async def test_run_status_flow_and_events(client, seeded, db_session, installed_agent):
    created = await client.post(
        "/api/acp/runs", json={"submission_id": seeded}
    )
    run_id = created.json()["run"]["id"]

    # 状态查询
    detail = await client.get(f"/api/acp/runs/{run_id}")
    assert detail.status_code == 200
    assert detail.json()["run"]["status"] == "queued"

    # 事件回放
    events = await client.get(f"/api/acp/runs/{run_id}/events")
    assert events.status_code == 200
    seqs = [e["seq"] for e in events.json()]
    assert seqs == sorted(seqs)
    assert any(e["kind"] == "notice" for e in events.json())

    # 取消
    cancelled = await client.post(f"/api/acp/runs/{run_id}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    detail = await client.get(f"/api/acp/runs/{run_id}")
    assert detail.json()["run"]["status"] == "cancelled"

    # 终态后再次取消:幂等
    again = await client.post(f"/api/acp/runs/{run_id}/cancel")
    assert again.status_code == 200


async def test_update_run_codex_configuration_hot_swap(
    client, seeded, db_session, installed_agent
):
    """运行中热切换配置:未启动立即生效,进行中排队下一回合,终态拒绝。"""
    created = await client.post("/api/acp/runs", json={"submission_id": seeded})
    run_id = created.json()["run"]["id"]

    # queued:run 尚未启动,配置直接生效(不排队)
    queued_update = await client.patch(
        f"/api/acp/runs/{run_id}/codex-configuration",
        json={"speed_mode": "standard"},
    )
    assert queued_update.status_code == 200, queued_update.text
    assert queued_update.json()["effective_at"] == "current"
    assert queued_update.json()["pending_config"] is None

    # running:排队到下一回合,不谎报已生效
    with db_session as db:
        run = db.get(AcpRun, run_id)
        run_repo.transition_run(db, run, AcpRunStatus.starting)
        run = db.get(AcpRun, run_id)
        run_repo.transition_run(db, run, AcpRunStatus.running)

    running_update = await client.patch(
        f"/api/acp/runs/{run_id}/codex-configuration",
        json={"speed_mode": "standard"},
    )
    assert running_update.status_code == 200, running_update.text
    body = running_update.json()
    assert body["effective_at"] == "next_turn"
    assert body["pending_config"] is not None
    assert body["applied_config"] is None

    events = await client.get(f"/api/acp/runs/{run_id}/events")
    assert "configuration_queued" in [e["kind"] for e in events.json()]

    # 终态后不可再改配置
    with db_session as db:
        run = db.get(AcpRun, run_id)
        run_repo.transition_run(db, run, AcpRunStatus.failed)

    rejected = await client.patch(
        f"/api/acp/runs/{run_id}/codex-configuration",
        json={"speed_mode": "standard"},
    )
    assert rejected.status_code == 409


async def test_reply_checkpoint_flow(client, seeded, db_session, installed_agent):
    created = await client.post(
        "/api/acp/runs", json={"submission_id": seeded}
    )
    run_id = created.json()["run"]["id"]

    # 未进入检查点时答复 → 409
    early = await client.post(
        f"/api/acp/runs/{run_id}/reply",
        json={"verdict": "consistent"},
    )
    assert early.status_code == 409

    # 进入检查点
    with db_session as db:
        run = db.get(__import__("app.acp.models", fromlist=["AcpRun"]).AcpRun, run_id)
        run_repo.transition_run(db, run, AcpRunStatus.starting)
        run = db.get(__import__("app.acp.models", fromlist=["AcpRun"]).AcpRun, run_id)
        run_repo.transition_run(db, run, AcpRunStatus.running)
        run = db.get(__import__("app.acp.models", fromlist=["AcpRun"]).AcpRun, run_id)
        submission = db.get(Submission, seeded)
        run_repo.save_checkpoint(
            db,
            run,
            checkpoint={"type": "code_consistency", "message": "一致?"},
            dwell_seconds=1800,
            confirmation_revision=submission.grading_revision,
            confirmation_context_hash=__import__(
                "app.application.mcp_workflow", fromlist=["build_context_parts"]
            ).build_context_parts(db, submission)[3],
        )

    reply = await client.post(
        f"/api/acp/runs/{run_id}/reply",
        json={"verdict": "mismatch", "note": "输出与报告不一致"},
    )
    assert reply.status_code == 200
    assert reply.json()["verdict"] == "mismatch"

    detail = await client.get(f"/api/acp/runs/{run_id}")
    body = detail.json()["run"]
    assert body["teacher_verdict"] == "mismatch"
    assert body["teacher_note"] == "输出与报告不一致"
    assert body["checkpoint"] is None


async def test_events_404_for_missing_run(client):
    resp = await client.get("/api/acp/runs/9999/events")
    assert resp.status_code == 404


async def test_stream_replays_then_closes(client, seeded, db_session, installed_agent):
    """SSE 回放历史事件;终态且读完后关闭流。"""
    created = await client.post(
        "/api/acp/runs", json={"submission_id": seeded}
    )
    run_id = created.json()["run"]["id"]
    with db_session as db:
        run = db.get(__import__("app.acp.models", fromlist=["AcpRun"]).AcpRun, run_id)
        run_repo.transition_run(db, run, AcpRunStatus.cancelled)

    resp = await client.get(f"/api/acp/runs/{run_id}/stream")
    assert resp.status_code == 200
    body = resp.text
    assert "run_event" in body
    assert "notice" in body


# ---------------------------------------------------------------------------
# 安装 / 默认 agent / 连接状态
# ---------------------------------------------------------------------------


class _FakeRegistryClient:
    """替代 RegistryClient:返回固定条目,避免真实网络。"""

    def __init__(self, raw: dict):
        self._raw = raw

    async def fetch(self, *, force_refresh: bool = False):
        return {"version": "test-registry", "agents": [self._raw]}

    def lookup(self, payload, agent_id):
        for item in payload.get("agents") or []:
            if isinstance(item, dict) and item.get("id") == agent_id:
                return item
        return None

    async def resolve(self, agent_id, version, *, force_refresh: bool = False):
        import app.api.acp as acp_api
        from app.acp.registry import build_agent_entry, effective_whitelist

        return build_agent_entry(
            agent_id,
            self._raw,
            requested_version=version,
            whitelist=effective_whitelist(acp_api._CACHE_DIR),
        )


def tmp_registry_dir():
    import tempfile

    return Path(tempfile.mkdtemp())


def _npm_raw(agent_id="codex-acp"):
    return {
        "id": agent_id,
        "name": "Codex ACP",
        "version": "1.2.3",
        "repository": "https://github.com/agentclientprotocol/codex-acp",
        "distribution": {"npx": {"package": "@agentclientprotocol/codex-acp@1.2.3"}},
    }


async def test_install_npm_writes_snapshot(client, tmp_path, monkeypatch, db_session):
    import app.api.acp as acp_api

    cache = tmp_path / "acp-cache"
    monkeypatch.setattr(acp_api, "_CACHE_DIR", cache)
    monkeypatch.setattr(
        acp_api, "_registry_client", lambda: _FakeRegistryClient(_npm_raw())
    )
    def fake_install_npx(agent_id, entry):
        command = cache / "agents" / agent_id / entry.version / "agent"
        command.parent.mkdir(parents=True, exist_ok=True)
        command.write_text("#!/bin/sh\n", encoding="utf-8")
        entry.command = str(command)
        entry.args = []

    monkeypatch.setattr(acp_api, "_install_npx", fake_install_npx)
    resp = await client.post("/api/acp/agents/codex-acp/install")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["version"] == "1.2.3"
    assert data["reinstalled"] is True
    from app.acp.models import AcpAgentInstallation

    installation = db_session.get(AcpAgentInstallation, "codex-acp")
    assert installation.launch_snapshot["command"].endswith("/agent")

    # 再装一次:同版本 → already installed,不重写
    resp2 = await client.post("/api/acp/agents/codex-acp/install")
    assert resp2.status_code == 200
    assert resp2.json()["reinstalled"] is False


async def test_install_binary_downloads_and_verifies(
    client, tmp_path, monkeypatch, db_session
):
    """binary 发行:下载 → SHA-256 → 安全解压 → command 指向解压文件。"""
    import hashlib
    import io
    import tarfile

    import app.api.acp as acp_api

    cache = tmp_path / "acp-cache"
    monkeypatch.setattr(acp_api, "_CACHE_DIR", cache)

    # 构造一个合法 tar.gz,内含白名单可执行文件 opencode
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        payload = b"#!/bin/sh\necho ok\n"
        info = tarfile.TarInfo("dist/opencode")
        info.size = len(payload)
        tf.addfile(info, io.BytesIO(payload))
    archive_bytes = buf.getvalue()
    good_sha = hashlib.sha256(archive_bytes).hexdigest()

    def _binary_raw(sha: str) -> dict:
        return {
            "id": "opencode",
            "name": "OpenCode",
            "version": "0.9.1",
            "repository": "https://github.com/anomalyco/opencode",
            "distribution": {
                "binary": {
                    "darwin-aarch64": {
                        "archive": "https://example.test/opencode-darwin-arm64.tar.gz",
                        "sha256": sha,
                        "cmd": "./opencode",
                        "args": ["acp"],
                    },
                    "linux-x86_64": {
                        "archive": "https://example.test/opencode-linux-x64.tar.gz",
                        "sha256": sha,
                        "cmd": "./opencode",
                        "args": ["acp"],
                    },
                }
            },
        }

    def fake_download(url, dest, *, timeout=120.0):
        dest.write_bytes(archive_bytes)

    from app.acp import registry as registry_mod

    monkeypatch.setattr(registry_mod, "download_to", fake_download)
    monkeypatch.setattr(
        acp_api, "_registry_client", lambda: _FakeRegistryClient(_binary_raw(good_sha))
    )

    # 首期固定 Codex-only，其他发行类型即使 Registry 元数据完整也拒绝。
    resp = await client.post("/api/acp/agents/opencode/install")
    assert resp.status_code == 404


async def test_install_rejects_non_whitelist(client, tmp_path, monkeypatch):
    import app.api.acp as acp_api

    monkeypatch.setattr(acp_api, "_CACHE_DIR", tmp_path / "acp-cache")
    resp = await client.post("/api/acp/agents/unknown-agent/install")
    assert resp.status_code == 404


def _installed_entry(agent_id: str, version: str):

    return AgentEntry(
        agent_id=agent_id,
        registry_version=None,
        distribution="npx",
        package="@agentclientprotocol/codex-acp",
        version=version,
        command="npx",
        args=[],
        env={},
    )


def _installed_marker(cache: Path, agent_id: str, version: str) -> Path:
    """遗留的安装标记文件;卸载清理仍会删除它。"""
    marker = cache / "agents" / agent_id / "installed.json"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps(_installed_entry(agent_id, version).to_dict()), encoding="utf-8")
    return marker


def _installed_row(db, agent_id: str, version: str, **kw):
    """插入 DB 安装行(安装事实源);等价于一次成功安装后的落库状态。"""
    from app.acp.models import AcpAgentInstallation

    entry = _installed_entry(agent_id, version)
    row = AcpAgentInstallation(
        agent_id=agent_id,
        version=version,
        distribution=entry.distribution,
        launch_snapshot=entry.to_dict(),
        **kw,
    )
    db.add(row)
    db.commit()
    return row


async def test_uninstall_removes_marker_and_version_cache(
    client, tmp_path, monkeypatch
):
    import app.api.acp as acp_api
    from app.acp.registry import agent_cache_dir

    cache = tmp_path / "acp-cache"
    monkeypatch.setattr(acp_api, "_CACHE_DIR", cache)
    monkeypatch.setattr(
        acp_api, "_registry_client", lambda: _FakeRegistryClient(_npm_raw())
    )
    # 假装 npm 安装成功(真实 npm install 由集成环境验证)
    monkeypatch.setattr(acp_api, "_install_npx", lambda agent_id, entry: None)
    resp = await client.post("/api/acp/agents/codex-acp/install")
    assert resp.status_code == 200, resp.text
    version = resp.json()["version"]
    version_dir = agent_cache_dir(cache, "codex-acp", version)
    version_dir.mkdir(parents=True, exist_ok=True)
    (version_dir / "payload.bin").write_bytes(b"cached")
    # 遗留标记文件也应被卸载清理
    _installed_marker(cache, "codex-acp", version)

    resp = await client.delete("/api/acp/agents/codex-acp/install")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["uninstalled"] is True
    assert body["version"] == version
    assert body["removed_cache"] is True
    assert not (cache / "agents" / "codex-acp" / "installed.json").exists()
    assert not version_dir.exists()

    # 卸载后 agent 仍留在目录里,状态回到未安装
    listing = await client.get("/api/acp/agents")
    codex = [a for a in listing.json()["agents"] if a["agent_id"] == "codex-acp"][0]
    assert codex["installed_version"] is None

    # 重复卸载 → 404
    assert (await client.delete("/api/acp/agents/codex-acp/install")).status_code == 404


async def test_uninstall_rejects_not_installed_and_unknown(client, tmp_path, monkeypatch):
    import app.api.acp as acp_api

    monkeypatch.setattr(acp_api, "_CACHE_DIR", tmp_path / "acp-cache")
    # 未安装 → 404
    assert (await client.delete("/api/acp/agents/codex-acp/install")).status_code == 404
    # 不在白名单 → 404
    assert (await client.delete("/api/acp/agents/nope/install")).status_code == 404


async def test_uninstall_blocked_by_active_run(client, seeded, tmp_path, monkeypatch, db_session):
    import app.api.acp as acp_api
    from app.acp import runs as run_repo

    cache = tmp_path / "acp-cache"
    monkeypatch.setattr(acp_api, "_CACHE_DIR", cache)
    _installed_row(db_session, "codex-acp", "1.2.3")
    marker = _installed_marker(cache, "codex-acp", "1.2.3")

    run_repo.create_run(
        db_session,
        submission_id=seeded,
        agent_id="codex-acp",
        agent_snapshot={},
        workspace_path=str(tmp_path),
    )
    db_session.commit()

    resp = await client.delete("/api/acp/agents/codex-acp/install")
    assert resp.status_code == 409
    # 被拒绝时不删除安装标记与安装行
    assert marker.exists()
    listing = await client.get("/api/acp/agents")
    codex = [a for a in listing.json()["agents"] if a["agent_id"] == "codex-acp"][0]
    assert codex["installed_version"] == "1.2.3"


async def test_uninstall_clears_default_marker(client, tmp_path, monkeypatch, db_session):
    import app.api.acp as acp_api

    cache = tmp_path / "acp-cache"
    monkeypatch.setattr(acp_api, "_CACHE_DIR", cache)
    _installed_row(db_session, "codex-acp", "1.2.3", is_default=True)
    _installed_marker(cache, "codex-acp", "1.2.3")

    resp = await client.delete("/api/acp/agents/codex-acp/install")
    assert resp.status_code == 200
    # 默认助手必须已安装 → 标记随安装行删除,回落到名单第一个
    resp = await client.get("/api/acp/default-agent")
    assert resp.json()["agent_id"] == "codex-acp"


async def test_set_default_agent_flow(client, tmp_path, monkeypatch, db_session):
    import app.api.acp as acp_api

    cache = tmp_path / "acp-cache"
    monkeypatch.setattr(acp_api, "_CACHE_DIR", cache)

    # 未安装 → 409
    resp = await client.post("/api/acp/agents/codex-acp/set-default")
    assert resp.status_code == 409

    # 安装后成功,并可通过 GET 读取
    _installed_row(db_session, "codex-acp", "1.2.3")
    ok = await client.post("/api/acp/agents/codex-acp/set-default")
    assert ok.status_code == 200
    current = await client.get("/api/acp/default-agent")
    assert current.json()["agent_id"] == "codex-acp"


async def test_connection_status_persisted(client, tmp_path, monkeypatch, db_session):
    """连接测试结论持久化;GET /agents 返回最近状态而非固定 unknown。"""
    import app.api.acp as acp_api
    from app.acp.errors import AcpError

    cache = tmp_path / "acp-cache"
    monkeypatch.setattr(acp_api, "_CACHE_DIR", cache)
    _installed_row(db_session, "codex-acp", "1.2.3")
    # 启动即失败 → 连接测试结论为 failed 且持久化(不抛 5xx)

    class _FailingSession:
        def __init__(self, *a, **kw):
            pass

        async def start(self):
            raise AcpError("agent 进程启动失败")

        async def close(self):
            return None

    monkeypatch.setattr("app.acp.session.AcpSession", _FailingSession)

    resp = await client.post("/api/acp/agents/codex-acp/test")
    assert resp.status_code == 200
    assert resp.json()["status"] == "failed"

    listing = await client.get("/api/acp/agents")
    assert listing.status_code == 200
    by_id = {a["agent_id"]: a for a in listing.json()["agents"]}
    assert by_id["codex-acp"]["connection_status"] == "failed"


async def test_custom_whitelist_interfaces_are_removed(client):
    assert (await client.get("/api/acp/whitelist")).status_code == 404
    assert (await client.post("/api/acp/whitelist/reset")).status_code == 404
    assert (await client.delete("/api/acp/whitelist/gemini")).status_code == 404
