"""批改 agent API 请求/响应模型。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

AgentRunStatusLiteral = Literal[
    "queued",
    "starting",
    "running",
    "waiting_for_teacher",
    "cancelling",
    "completed",
    "failed",
    "cancelled",
]


class AgentRunCreateRequest(BaseModel):
    """发起批改;provider/model/effort 缺省时取系统默认配置。"""

    model_config = ConfigDict(extra="forbid")

    submission_id: int
    provider: str | None = Field(default=None, min_length=1, max_length=32)
    model: str | None = Field(default=None, min_length=1, max_length=128)
    reasoning_effort: str | None = Field(default=None, min_length=1, max_length=64)


class AgentConfigurationUpdateRequest(BaseModel):
    """运行中切换 model/effort;null 不改动。"""

    model_config = ConfigDict(extra="forbid")

    model: str | None = Field(default=None, min_length=1, max_length=128)
    reasoning_effort: str | None = Field(default=None, min_length=1, max_length=64)


class AgentCheckpointOut(BaseModel):
    type: str
    message: str
    asked_at: datetime | None = None
    expires_at: datetime | None = None


class AgentRunOut(BaseModel):
    id: int
    submission_id: int
    provider: str
    model: str | None = None
    reasoning_effort: str | None = None
    effective_at: Literal["current", "next_turn"] = "current"
    status: AgentRunStatusLiteral
    checkpoint: AgentCheckpointOut | None = None
    teacher_verdict: Literal["consistent", "mismatch"] | None = None
    teacher_note: str | None = None
    error_message: str | None = None
    attempts: int = 0
    max_attempts: int = 3
    usage: dict[str, Any] | None = None
    created_at: datetime
    finished_at: datetime | None = None


class AgentRunDetailResponse(BaseModel):
    run: AgentRunOut
    latest_seq: int = 0
    applied_config: dict[str, Any] | None = None
    pending_config: dict[str, Any] | None = None


class AgentRunEventOut(BaseModel):
    seq: int
    kind: str
    payload: dict[str, Any]
    created_at: datetime


class AgentRunReplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verdict: Literal["consistent", "mismatch"]
    note: str | None = Field(default=None, max_length=2000)


class ProviderStatusOut(BaseModel):
    configured: bool = False
    key_last4: str | None = None
    base_url: str = ""
    default_model: str = ""
    reasoning_effort: str = ""
    available: bool = True
    # 订阅通道专有:套餐、账号后四位、令牌过期时间(UTC ISO)
    plan: str = ""
    account_last4: str | None = None
    expires_at: str = ""


class ProviderListResponse(BaseModel):
    provider_default: str
    providers: dict[str, ProviderStatusOut]


class ProviderOAuthStartResponse(BaseModel):
    authorize_url: str
    expires_at: str | None = None


class ProviderOAuthStatusResponse(BaseModel):
    status: Literal["idle", "pending", "completed", "failed"]
    detail: str | None = None
    authorize_url: str | None = None
    expires_at: str | None = None


class ProviderConfigUpdateRequest(BaseModel):
    """更新通道配置;null 不改动、空串清除。

    ``api_key`` 只写不读(响应永不回显密钥本体)。
    """

    model_config = ConfigDict(extra="forbid")

    api_key: str | None = Field(default=None, max_length=512)
    base_url: str | None = Field(default=None, max_length=512)
    default_model: str | None = Field(default=None, max_length=128)
    reasoning_effort: str | None = Field(default=None, max_length=64)


class ProviderDefaultUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Literal["openai_api", "chatgpt_sub"]


class ProviderTestResponse(BaseModel):
    name: str
    status: Literal["ready", "needs_auth", "unsupported", "failed"]
    detail: str | None = None
    models: list[str] = Field(default_factory=list)
