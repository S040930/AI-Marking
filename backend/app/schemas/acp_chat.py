"""ACP 交互式对话会话的请求/响应模型。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.acp import (
    CodexConfigRequest,
    CodexConfigSnapshot,
)

AcpChatStatusLiteral = Literal[
    "idle",
    "running",
    "waiting_permission",
    "closed",
    "error",
]


class AcpChatCreateRequest(BaseModel):
    submission_id: int
    permission_mode: Literal["ask", "auto_review"] = "ask"
    codex_config: CodexConfigRequest = Field(default_factory=CodexConfigRequest)

    model_config = ConfigDict(extra="forbid")


class AcpChatConfigurationUpdateRequest(CodexConfigRequest):
    """已有会话的配置热更新;permission_mode 缺省表示保持不变。"""

    permission_mode: Literal["ask", "auto_review"] | None = None


class AcpChatSessionOut(BaseModel):
    id: int
    submission_id: int
    agent_id: str
    permission_mode: Literal["ask", "auto_review"] = "ask"
    codex_config: CodexConfigSnapshot
    applied_codex_config: CodexConfigSnapshot | None = None
    pending_codex_config: CodexConfigSnapshot | None = None
    effective_at: Literal["current", "next_turn"] = "current"
    desired_config: CodexConfigSnapshot | None = None
    applied_config: CodexConfigSnapshot | None = None
    pending_config: CodexConfigSnapshot | None = None
    status: AcpChatStatusLiteral
    last_error: str | None = None
    latest_seq: int = 0
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class AcpChatSessionListResponse(BaseModel):
    sessions: list[AcpChatSessionOut]


class AcpChatMessageRequest(BaseModel):
    text: str = Field(min_length=1, max_length=8000)


class AcpChatEventOut(BaseModel):
    seq: int
    kind: str
    payload: dict[str, Any]
    created_at: datetime


class AcpChatPermissionRequest(BaseModel):
    permission_id: str = Field(min_length=8, max_length=64)
    allow: bool
    option_id: str | None = Field(default=None, max_length=64)
