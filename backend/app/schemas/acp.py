"""ACP 批改 API 请求/响应模型。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class CodexConfigRequest(BaseModel):
    """Only product-level Codex fields are accepted from the browser."""

    model_id: str | None = Field(default=None, min_length=1, max_length=128)
    reasoning_effort: str | None = Field(default=None, min_length=1, max_length=64)
    speed_mode: Literal["standard", "fast"] = "standard"
    fast_confirmed: bool = False

    model_config = ConfigDict(extra="forbid")


class CodexConfigSnapshot(BaseModel):
    model_id: str | None = None
    reasoning_effort: str | None = None
    speed_mode: Literal["standard", "fast"] = "standard"
    option_ids: dict[str, str] = Field(default_factory=dict)
    catalog_version: str | None = None

    model_config = ConfigDict(extra="forbid")


class CodexConfigurationCatalogOut(BaseModel):
    agent_id: Literal["codex-acp"]
    agent_version: str
    selected_model_id: str | None = None
    models: list[dict[str, Any]] = Field(default_factory=list)
    reasoning_efforts: list[dict[str, Any]] = Field(default_factory=list)
    speed_modes: list[dict[str, Any]] = Field(default_factory=list)
    capability_error: str | None = None


class AcpConfigurationStateOut(BaseModel):
    desired_config: CodexConfigSnapshot
    applied_config: CodexConfigSnapshot | None = None
    pending_config: CodexConfigSnapshot | None = None
    effective_at: Literal["current", "next_turn"]


class AcpConfigurationUpdateRequest(CodexConfigRequest):
    pass


class AcpAgentOut(BaseModel):
    agent_id: str
    display_name: str
    whitelist_package: str
    distributions: list[str]
    installed_version: str | None = None
    available_version: str | None = None
    connection_status: Literal[
        "ready", "needs_auth", "unsupported", "failed", "unknown"
    ]


class AcpAgentListResponse(BaseModel):
    agents: list[AcpAgentOut]
    registry_version: str | None = None
    fetched_at: datetime | None = None


class AcpTestConnectionResponse(BaseModel):
    agent_id: str
    status: Literal["ready", "needs_auth", "unsupported", "failed"]
    agent_info: str | None = None
    protocol_version: int | None = None
    mcp_visible: bool = False
    detail: str | None = None
    steps: dict[str, str] = Field(default_factory=dict)


class AcpInstallResponse(BaseModel):
    agent_id: str
    version: str
    distribution: str
    reinstalled: bool = True


class AcpUninstallResponse(BaseModel):
    agent_id: str
    version: str | None = None
    uninstalled: bool = True
    removed_cache: bool = False


class AcpWhitelistRuleOut(BaseModel):
    agent_id: str
    repo: str
    package: str
    distributions: list[str]
    source: Literal["default", "custom"]


class AcpWhitelistListResponse(BaseModel):
    rules: list[AcpWhitelistRuleOut]
    has_overrides: bool = False


class AcpWhitelistUpsertRequest(BaseModel):
    agent_id: str
    repo: str
    package: str
    distributions: list[str]


class AcpRunCreateRequest(BaseModel):
    submission_id: int
    permission_mode: Literal["ask", "auto_review"] = "ask"
    codex_config: CodexConfigRequest = Field(default_factory=CodexConfigRequest)

    model_config = ConfigDict(extra="forbid")


class AcpCheckpointOut(BaseModel):
    type: str
    message: str
    asked_at: datetime | None = None
    expires_at: datetime | None = None


class AcpRunOut(BaseModel):
    id: int
    submission_id: int
    agent_id: str
    permission_mode: Literal["ask", "auto_review"] = "ask"
    codex_config: CodexConfigSnapshot
    applied_codex_config: CodexConfigSnapshot | None = None
    pending_codex_config: CodexConfigSnapshot | None = None
    effective_at: Literal["current", "next_turn"] = "current"
    status: AcpRunStatusLiteral = Field(alias="status")
    acp_session_id: str | None = None
    checkpoint: AcpCheckpointOut | None = None
    teacher_verdict: Literal["consistent", "mismatch"] | None = None
    teacher_note: str | None = None
    error_message: str | None = None
    attempts: int = 0
    max_attempts: int = 3
    created_at: datetime
    finished_at: datetime | None = None

    model_config = {"populate_by_name": True}


AcpRunStatusLiteral = Literal[
    "queued",
    "starting",
    "running",
    "waiting_for_teacher",
    "cancelling",
    "completed",
    "failed",
    "cancelled",
]


class AcpRunEventOut(BaseModel):
    seq: int
    kind: str
    payload: dict[str, Any]
    created_at: datetime


class AcpRunReplyRequest(BaseModel):
    verdict: Literal["consistent", "mismatch"]
    note: str | None = Field(default=None, max_length=2000)


class AcpCodexConfigurationResponse(CodexConfigurationCatalogOut):
    pass


class AcpRunDetailResponse(BaseModel):
    run: AcpRunOut
    latest_seq: int = 0
    # Configuration aliases are exposed at the response root for PATCH
    # consumers; ``run`` keeps the established detail shape for existing UI.
    desired_config: CodexConfigSnapshot
    applied_config: CodexConfigSnapshot | None = None
    pending_config: CodexConfigSnapshot | None = None
    effective_at: Literal["current", "next_turn"] = "current"
