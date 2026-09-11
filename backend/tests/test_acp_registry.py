"""阶段2验收:Registry 缓存、白名单审核、供应链校验与安全解压。

测试条目遵循官方 Registry 格式(agentclientprotocol/registry,FORMAT.md)。
"""

from __future__ import annotations

import hashlib
import io
import tarfile
import zipfile
from pathlib import Path

import pytest

from app.acp.errors import AcpProtocolError
from app.acp.registry import (
    DEFAULT_AGENT_WHITELIST,
    RegistryClient,
    RegistryError,
    add_whitelist_agent,
    agent_cache_dir,
    audit_entry,
    audit_snapshot,
    effective_whitelist,
    is_exact_version,
    remove_whitelist_agent,
    reset_whitelist,
    safe_extract,
    validate_whitelist_rule,
    verify_sha256,
)


def _registry_payload(agents: list | dict) -> dict:
    return {"version": "1.0.0", "agents": agents}


def _codex_entry(**overrides) -> dict:
    """官方格式的 codex-acp 条目(npx 发行)。"""
    entry = {
        "id": "codex-acp",
        "name": "Codex",
        "version": "0.1.0",
        "repository": "https://github.com/agentclientprotocol/codex-acp",
        "distribution": {
            "npx": {"package": "@agentclientprotocol/codex-acp@0.1.0"}
        },
    }
    entry.update(overrides)
    return entry


def _opencode_entry(**overrides) -> dict:
    """官方格式的 opencode 条目(binary 发行)。"""
    entry = {
        "id": "opencode",
        "name": "OpenCode",
        "version": "1.0.0",
        "repository": "https://github.com/anomalyco/opencode",
        "distribution": {
            "binary": {
                "darwin-aarch64": {
                    "archive": "https://example.test/opencode-darwin-arm64.zip",
                    "sha256": "a" * 64,
                    "cmd": "./opencode",
                    "args": ["acp"],
                }
            }
        },
    }
    entry.update(overrides)
    return entry


def _opencode_whitelist() -> dict[str, dict[str, object]]:
    """仅用于底层发行审核测试的显式自定义名单。"""
    return {
        "opencode": {
            "repo": "anomalyco/opencode",
            "package": "opencode",
            "distributions": {"binary"},
        }
    }


# ---------------------------------------------------------------------------
# 精确版本与白名单审核
# ---------------------------------------------------------------------------


def test_exact_version_accepts_semver():
    assert is_exact_version("1.2.3")
    assert is_exact_version("0.1.0-beta.1")
    assert not is_exact_version("latest")
    assert not is_exact_version("^1.2.3")
    assert not is_exact_version("git+https://...")
    assert not is_exact_version("/local/path")


def test_audit_rejects_non_whitelisted_agent():
    with pytest.raises(RegistryError, match="白名单"):
        audit_entry("evil-agent", _codex_entry())


def test_audit_rejects_package_replacement():
    swapped = _codex_entry(
        distribution={"npx": {"package": "evil-package@0.1.0"}}
    )
    with pytest.raises(RegistryError, match="包身份"):
        audit_entry("codex-acp", swapped)


def test_audit_rejects_repo_replacement():
    swapped = _codex_entry(repository="https://github.com/attacker/evil")
    with pytest.raises(RegistryError, match="仓库身份"):
        audit_entry("codex-acp", swapped)


def test_audit_rejects_wrong_distribution():
    # 底层审核器仍支持显式名单;公共默认名单保持 Codex-only。
    bad = _opencode_entry(distribution={"uvx": {"package": "opencode"}})
    with pytest.raises(RegistryError, match="发行类型"):
        audit_entry("opencode", bad, whitelist=_opencode_whitelist())


def test_audit_rejects_inexact_version():
    with pytest.raises(RegistryError, match="精确版本"):
        audit_entry("codex-acp", _codex_entry(version="latest"))


def test_audit_rejects_binary_without_sha256():
    bad = _opencode_entry(
        distribution={
            "binary": {
                "darwin-aarch64": {
                    "archive": "https://example.test/x.zip",
                    "cmd": "./opencode",
                }
            }
        }
    )
    with pytest.raises(RegistryError, match="SHA-256"):
        audit_entry("opencode", bad, whitelist=_opencode_whitelist())


def test_audit_rejects_binary_missing_current_platform():
    bad = _opencode_entry(
        distribution={
            "binary": {
                "linux-x86_64": {
                    "archive": "https://example.test/x.tar.gz",
                    "sha256": "b" * 64,
                    "cmd": "./opencode",
                }
            }
        }
    )
    with pytest.raises(RegistryError, match="平台"):
        audit_entry("opencode", bad, whitelist=_opencode_whitelist())


def test_audit_accepts_valid_entries():
    audit_entry("codex-acp", _codex_entry())
    audit_entry("opencode", _opencode_entry(), whitelist=_opencode_whitelist())


# ---------------------------------------------------------------------------
# build_agent_entry:启动快照物化
# ---------------------------------------------------------------------------


def test_build_entry_npx_pins_exact_package():
    from app.acp.registry import build_agent_entry

    entry = build_agent_entry("codex-acp", _codex_entry())
    assert entry.command == "npx"
    assert entry.args == ["-y", "@agentclientprotocol/codex-acp@0.1.0"]
    assert entry.version == "0.1.0"
    assert entry.distribution == "npx"


def test_build_entry_binary_uses_current_platform():
    from app.acp.registry import build_agent_entry

    entry = build_agent_entry(
        "opencode", _opencode_entry(), whitelist=_opencode_whitelist()
    )
    assert entry.command == "opencode"
    assert entry.args == ["acp"]
    assert entry.sha256 == "a" * 64
    assert entry.source_url.endswith("opencode-darwin-arm64.zip")


def test_build_entry_rejects_version_mismatch():
    from app.acp.registry import build_agent_entry

    with pytest.raises(RegistryError, match="不一致"):
        build_agent_entry("codex-acp", _codex_entry(), requested_version="9.9.9")


def test_build_entry_npx_extra_args():
    from app.acp.registry import build_agent_entry

    raw = _codex_entry(
        distribution={
            "npx": {
                "package": "@agentclientprotocol/codex-acp@0.1.0",
                "args": ["--acp"],
            }
        }
    )
    entry = build_agent_entry("codex-acp", raw)
    assert entry.args == ["-y", "@agentclientprotocol/codex-acp@0.1.0", "--acp"]


# ---------------------------------------------------------------------------
# RegistryClient:缓存与离线兜底
# ---------------------------------------------------------------------------


async def test_fetch_writes_cache_and_reuses(tmp_path: Path, monkeypatch):
    client = RegistryClient(tmp_path)
    calls = {"n": 0}

    async def fake_get(self, url):
        calls["n"] += 1

        class Resp:
            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict:
                return _registry_payload([_codex_entry()])

        return Resp()

    monkeypatch.setattr(
        "app.acp.registry.httpx.AsyncClient.get", fake_get
    )
    first = await client.fetch()
    assert client.lookup(first, "codex-acp") is not None
    second = await client.fetch()
    assert second.get("fetched_at") == first.get("fetched_at")
    assert calls["n"] == 1  # 缓存命中,不再请求


async def test_fetch_falls_back_to_cache_when_offline(tmp_path: Path, monkeypatch):
    client = RegistryClient(tmp_path)
    # 预写一个合法缓存
    client._write_cache(
        {"fetched_at": 123.0, "agents": [_codex_entry()]}
    )

    async def fail_get(self, url):
        raise ConnectionError("network down")

    monkeypatch.setattr(
        "app.acp.registry.httpx.AsyncClient.get", fail_get
    )
    payload = await client.fetch()
    assert client.lookup(payload, "codex-acp")["name"] == "Codex"


async def test_fetch_raises_without_cache(tmp_path: Path, monkeypatch):
    client = RegistryClient(tmp_path)

    async def fail_get(self, url):
        raise ConnectionError("network down")

    monkeypatch.setattr(
        "app.acp.registry.httpx.AsyncClient.get", fail_get
    )
    with pytest.raises(RegistryError, match="无缓存"):
        await client.fetch()


async def test_resolve_rejects_inexact_version(tmp_path: Path):
    client = RegistryClient(tmp_path)
    with pytest.raises(RegistryError, match="精确版本"):
        await client.resolve("codex-acp", "latest")


async def test_resolve_unknown_agent(tmp_path: Path, monkeypatch):
    client = RegistryClient(tmp_path)

    async def fake_get(self, url):
        class Resp:
            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict:
                return _registry_payload([])

        return Resp()

    import httpx

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    with pytest.raises(RegistryError, match="没有 agent"):
        await client.resolve("codex-acp", "0.1.0")


async def test_resolve_builds_npm_command(tmp_path: Path, monkeypatch):
    client = RegistryClient(tmp_path)

    async def fake_get(self, url):
        class Resp:
            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict:
                return _registry_payload([_codex_entry()])

        return Resp()

    import httpx

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    entry = await client.resolve("codex-acp", "0.1.0")
    assert entry.command == "npx"
    assert entry.args == ["-y", "@agentclientprotocol/codex-acp@0.1.0"]
    assert entry.agent_id == "codex-acp"


async def test_resolve_rejects_replaced_entry(tmp_path: Path, monkeypatch):
    """Registry 条目被替换为其他包时拒绝解析。"""
    client = RegistryClient(tmp_path)

    async def fake_get(self, url):
        class Resp:
            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict:
                return _registry_payload(
                    [_codex_entry(
                        distribution={"npx": {"package": "evil-package@0.1.0"}}
                    )]
                )

        return Resp()

    import httpx

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    with pytest.raises(RegistryError, match="包身份"):
        await client.resolve("codex-acp", "0.1.0")


async def test_resolve_accepts_real_registry_payload(tmp_path: Path):
    """用真实 Registry 快照(若可下载)验证解析;离线则跳过。"""
    import json

    import httpx

    snapshot = tmp_path / "real.json"
    try:
        resp = httpx.get(
            "https://cdn.agentclientprotocol.com/registry/v1/latest/registry.json",
            timeout=10.0,
        )
        resp.raise_for_status()
        snapshot.write_text(resp.text, encoding="utf-8")
    except httpx.HTTPError:
        pytest.skip("无法访问官方 Registry(离线环境)")

    client = RegistryClient(tmp_path)
    client._write_cache(
        {**json.loads(snapshot.read_text(encoding="utf-8")), "fetched_at": 1.0}
    )
    payload = await client.fetch()
    raw = client.lookup(payload, "codex-acp")
    assert raw is not None
    entry = build_entry_from_payload(client, payload, "codex-acp")
    assert entry.command == "npx"
    assert entry.args[0] == "-y"
    assert entry.args[1].startswith("@agentclientprotocol/codex-acp@")


def build_entry_from_payload(client: RegistryClient, payload: dict, agent_id: str):
    from app.acp.registry import build_agent_entry

    return build_agent_entry(agent_id, client.lookup(payload, agent_id))


# ---------------------------------------------------------------------------
# SHA-256 与安全解压
# ---------------------------------------------------------------------------


def test_verify_sha256(tmp_path: Path):
    data = b"hello agent"
    f = tmp_path / "bin.tar.gz"
    f.write_bytes(data)
    verify_sha256(f, hashlib.sha256(data).hexdigest())
    with pytest.raises(RegistryError, match="SHA-256"):
        verify_sha256(f, "0" * 64)


def test_safe_extract_tar(tmp_path: Path):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        payload = b"#!/bin/sh\necho ok\n"
        info = tarfile.TarInfo("pkg/tool")
        info.size = len(payload)
        tf.addfile(info, io.BytesIO(payload))
    archive = tmp_path / "a.tar.gz"
    archive.write_bytes(buf.getvalue())

    dest = tmp_path / "out"
    extracted = safe_extract(archive, dest, allowed_executables={"tool"})
    assert extracted == [dest / "pkg" / "tool"]
    assert (dest / "pkg" / "tool").exists()


def test_safe_extract_rejects_path_traversal(tmp_path: Path):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        payload = b"x"
        info = tarfile.TarInfo("../escape")
        info.size = len(payload)
        tf.addfile(info, io.BytesIO(payload))
    archive = tmp_path / "evil.tar.gz"
    archive.write_bytes(buf.getvalue())
    with pytest.raises(RegistryError, match="穿越"):
        safe_extract(archive, tmp_path / "out", allowed_executables=set())


def test_safe_extract_rejects_symlink(tmp_path: Path):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        info = tarfile.TarInfo("pkg/link")
        info.type = tarfile.SYMTYPE
        info.linkname = "/etc/passwd"
        tf.addfile(info)
    archive = tmp_path / "evil.tar.gz"
    archive.write_bytes(buf.getvalue())
    with pytest.raises(RegistryError, match="链接"):
        safe_extract(archive, tmp_path / "out", allowed_executables=set())


def test_safe_extract_rejects_unlisted_executable(tmp_path: Path):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        payload = b"#!/bin/sh\n"
        info = tarfile.TarInfo("pkg/other")
        info.size = len(payload)
        tf.addfile(info, io.BytesIO(payload))
    archive = tmp_path / "a.tar.gz"
    archive.write_bytes(buf.getvalue())
    extracted = safe_extract(archive, tmp_path / "out", allowed_executables={"tool"})
    assert extracted == []


def test_safe_extract_zip_rejects_traversal(tmp_path: Path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("../evil", "x")
    archive = tmp_path / "evil.zip"
    archive.write_bytes(buf.getvalue())
    with pytest.raises(RegistryError):
        safe_extract(archive, tmp_path / "out", allowed_executables=set())


def test_agent_cache_dir_layout(tmp_path: Path):
    d = agent_cache_dir(tmp_path, "codex-acp", "0.1.0")
    assert d == tmp_path / "agents" / "codex-acp" / "0.1.0"
    with pytest.raises(AcpProtocolError):
        agent_cache_dir(tmp_path, "codex-acp", "latest")


def test_whitelist_covers_first_phase_agents():
    assert set(DEFAULT_AGENT_WHITELIST) == {"codex-acp"}


# ---------------------------------------------------------------------------
# 白名单管理:教师增删与校验
# ---------------------------------------------------------------------------


def test_validate_whitelist_rule_normalizes():
    rule = validate_whitelist_rule(
        "my-agent",
        "https://github.com/me/my-agent.git/",
        "@me/my-agent@1.2.3",
        ["npx"],
    )
    assert rule == {
        "repo": "me/my-agent",
        "package": "@me/my-agent",
        "distributions": ["npx"],
    }


def test_validate_whitelist_rule_rejects_bad_input():
    import pytest as _pytest

    cases = [
        ("Bad ID", "me/x", "pkg", ["npx"]),
        ("ok", "justname", "pkg", ["npx"]),
        ("ok", "me/x", "", ["npx"]),
        ("ok", "me/x", "pkg", []),
        ("ok", "me/x", "pkg", ["curl"]),
    ]
    for agent_id, repo, package, dists in cases:
        with _pytest.raises(ValueError):
            validate_whitelist_rule(agent_id, repo, package, dists)


def test_add_remove_reset_whitelist_roundtrip(tmp_path: Path):
    assert effective_whitelist(tmp_path) == DEFAULT_AGENT_WHITELIST

    add_whitelist_agent(tmp_path, "my-agent", "me/my-agent", "@me/my-agent", ["npx"])
    assert effective_whitelist(tmp_path) == DEFAULT_AGENT_WHITELIST

    # 旧覆盖文件可以被写入，但不得扩大首期公共名单。
    add_whitelist_agent(tmp_path, "my-agent", "me/other", "other", ["uvx"])
    assert effective_whitelist(tmp_path) == DEFAULT_AGENT_WHITELIST

    # 清理旧覆盖仍然是幂等的。
    assert remove_whitelist_agent(tmp_path, "my-agent") is True
    assert effective_whitelist(tmp_path) == DEFAULT_AGENT_WHITELIST

    # 首期 Codex 条目不可被旧覆盖移除。
    assert remove_whitelist_agent(tmp_path, "codex-acp") is True
    assert "codex-acp" in effective_whitelist(tmp_path)
    # 未知条目
    assert remove_whitelist_agent(tmp_path, "nope") is False

    reset_whitelist(tmp_path)
    assert effective_whitelist(tmp_path) == DEFAULT_AGENT_WHITELIST
    assert not (tmp_path / "whitelist-overrides.json").exists()


def test_audit_entry_with_custom_whitelist(tmp_path: Path):
    wl = {
        "my-agent": {
            "repo": "me/my-agent",
            "package": "@me/my-agent",
            "distributions": {"npx"},
        }
    }
    raw = {
        "id": "my-agent",
        "version": "0.2.0",
        "repository": "https://github.com/me/my-agent",
        "distribution": {"npx": {"package": "@me/my-agent@0.2.0"}},
    }
    audit_entry("my-agent", raw, whitelist=wl)
    # 换包身份 → 拒绝
    with pytest.raises(RegistryError, match="包身份"):
        audit_entry(
            "my-agent",
            {**raw, "distribution": {"npx": {"package": "evil@0.2.0"}}},
            whitelist=wl,
        )
    # 自定义条目不影响默认名单的独立性:默认白名单审核不受 overrides 影响
    audit_entry("codex-acp", _codex_entry())


def test_audit_snapshot_checks_identity():
    snapshot = {
        "agent_id": "codex-acp",
        "distribution": "npx",
        "package": "@agentclientprotocol/codex-acp",
        "version": "0.58.0",
    }
    audit_snapshot("codex-acp", snapshot)
    with pytest.raises(RegistryError, match="包身份"):
        audit_snapshot("codex-acp", {**snapshot, "package": "evil"})
    with pytest.raises(RegistryError, match="发行类型"):
        audit_snapshot("codex-acp", {**snapshot, "distribution": "uvx"})
    with pytest.raises(RegistryError, match="白名单"):
        audit_snapshot("ghost", snapshot, whitelist={})
