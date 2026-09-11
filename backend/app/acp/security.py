"""Codex native sandbox launch policy for ACP agents."""

from __future__ import annotations

import json
from pathlib import Path

from app.acp.registry import AgentEntry
from app.acp.session import AgentLaunchSpec


def secure_launch_spec(
    entry: AgentEntry, workspace: Path, *, permission_mode: str = "ask"
) -> AgentLaunchSpec:
    if permission_mode not in {"ask", "auto_review"}:
        raise ValueError("不支持的 ACP 权限档位")
    env = dict(entry.env)
    if entry.agent_id == "codex-acp":
        # 沙箱边界由 Codex 原生实现;应用只固定工作区可写并关闭网络,
        # 不再叠加认证探针或外层路径隔离。
        env["CODEX_CONFIG"] = json.dumps(
            {
                "sandbox_mode": "workspace-write",
                "sandbox_workspace_write": {"network_access": False},
            },
            separators=(",", ":"),
        )
    return AgentLaunchSpec(
        agent_id=entry.agent_id,
        command=entry.command,
        args=list(entry.args),
        env=env,
        cwd=str(workspace.resolve()),
        permission_mode=permission_mode,
        agent_version=entry.version,
    )
