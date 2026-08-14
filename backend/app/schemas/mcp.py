"""Codex MCP 专用请求/响应模型。"""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.submission import SubmissionGradingMode, SubmissionStatus
from app.schemas.scoring import ScoreDetail


class McpSelfCheck(BaseModel):
    rubric_items_reviewed: list[str] = Field(default_factory=list, max_length=100)
    issues_found: list[str] = Field(default_factory=list, max_length=20)
    changes_made: list[str] = Field(default_factory=list, max_length=20)
    second_pass_completed: bool = False


class McpAssessmentResponse(BaseModel):
    submission_id: int
    status: SubmissionStatus
    grading_revision: int
    grading_mode: SubmissionGradingMode
    quality_checks: dict
    idempotent: bool = False


class McpHealthResponse(BaseModel):
    status: str
    service: str
    database: str
    worker_required: bool = True
    mcp_api_version: str


# Public MCP workflow (API v8) --------------------------------------------


class McpPreflightFile(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    question_number: int | None = Field(default=None, ge=1)
    entrypoint: bool = True


class McpPreflightRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question_name: str = Field(min_length=1, max_length=200)
    code_files: list[McpPreflightFile] = Field(default_factory=list, max_length=20)


class McpQuestionCandidate(BaseModel):
    id: int
    name: str
    original_filename: str


class McpPreflightResponse(BaseModel):
    status: Literal["ready_to_submit", "needs_question_choice"]
    question_id: int | None = None
    question_name: str | None = None
    code_manifest: list[dict] = Field(default_factory=list)
    candidates: list[McpQuestionCandidate] = Field(default_factory=list)


class McpPackageResponse(BaseModel):
    submission_id: int
    status: SubmissionStatus
    review_url: str | None = None
    error_message: str | None = None
    assessment: dict | None = None
    content: str | None = None
    context_complete: bool = False
    continuation_token: str | None = None
    grading_handle: str | None = None


class McpAssessmentRequest(BaseModel):
    """Codex 提交的评分建议(对外唯一请求模型)。

    ``expected_revision``/``context_hash`` 由服务端在保存时从作业当前状态
    补全,不在对外请求中出现;它们是防呆用的版本校验字段。
    """

    model_config = ConfigDict(extra="forbid")
    rubric_source: Literal["configured", "question_extracted", "built_in_default"]
    rubric_snapshot_id: str = Field(min_length=8, max_length=128)
    request_id: UUID
    score: float = Field(ge=0)
    max_score: float = Field(gt=0)
    confidence: float = Field(ge=0, le=1, default=0)
    feedback: str = Field(min_length=1, max_length=20_000)
    details: list[ScoreDetail] = Field(min_length=1, max_length=100)
    self_check: McpSelfCheck = Field(default_factory=McpSelfCheck)


class McpSaveAssessmentRequest(BaseModel):
    grading_handle: str = Field(min_length=20, max_length=2000)
    assessment: McpAssessmentRequest


class McpVisualConfirmationRequest(BaseModel):
    grading_handle: str = Field(min_length=20, max_length=2000)
    verdict: Literal["consistent", "mismatch"]
    note: str | None = Field(default=None, max_length=2_000)

    @model_validator(mode="after")
    def mismatch_requires_note(self):
        if self.verdict == "mismatch" and not (self.note or "").strip():
            raise ValueError("存在不一致时必须提供说明")
        if self.verdict == "consistent" and self.note:
            raise ValueError("一致确认不应包含不一致说明")
        return self


class McpVisualConfirmationResponse(BaseModel):
    submission_id: int
    grading_handle: str
    verdict: Literal["consistent", "mismatch"]
    note: str | None = None
    confirmed_at: str


# 兼容别名:McpAssessmentRequest 与旧的精简版请求模型已合并,对外与 Codex
# 交互的就是这一个模型。保留旧名避免调用方(含测试)逐个改动。
McpSimpleAssessmentRequest = McpAssessmentRequest
