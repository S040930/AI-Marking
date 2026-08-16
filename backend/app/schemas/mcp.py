"""外部编程助手 MCP 请求/响应模型。"""

from datetime import datetime
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


# Public MCP workflow (API v10) --------------------------------------------


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
    # needs_rubric 分支:题目没有可信 rubric 快照且配置无 rubric 时返回
    # 题目 OCR、question_id 与 rubric 提取句柄,客户端提取并保存后才能
    # 重新打开作业评分。
    needs_rubric: bool = False
    question_id: int | None = None
    question_ocr_text: str | None = None
    rubric_handle: str | None = None


class McpPendingAssignment(BaseModel):
    """待评分作业列表项。"""

    submission_id: int
    original_filename: str
    question_name: str
    uploaded_at: datetime


class McpPendingAssignmentsResponse(BaseModel):
    items: list[McpPendingAssignment] = Field(default_factory=list)
    next_cursor: str | None = None


class McpRubricExtractionItem(BaseModel):
    """客户端从题目 OCR 中提取的单个评分项。"""

    model_config = ConfigDict(extra="forbid")
    criterion: str = Field(min_length=1, max_length=200)
    max_score: float = Field(gt=0)
    details: str = Field(min_length=1, max_length=2_000)
    source_quote: str = Field(min_length=1, max_length=4_000)


class McpSaveRubricRequest(BaseModel):
    """保存客户端提取的题目 rubric。

    ``handle`` 为 ``open_ai_marking_assignment`` 返回的 rubric 提取句柄;
    ``status`` 为 ``complete``（有明确 rubric）或 ``absent_or_ambiguous``
    （无明确 rubric）。``complete`` 时逐项 ``source_quote`` 必须是题目 OCR
    原文子串且包含该项满分,服务端确定性校验后写入题目级权威快照。
    """

    model_config = ConfigDict(extra="forbid")
    handle: str = Field(min_length=20, max_length=2000)
    status: Literal["complete", "absent_or_ambiguous"]
    items: list[McpRubricExtractionItem] = Field(default_factory=list, max_length=100)
    total_max_score: float | None = Field(default=None, gt=0)


class McpSaveRubricResponse(BaseModel):
    status: Literal["complete", "absent_or_ambiguous"]
    question_id: int
    question_name: str
    # complete 时为新 rubric 快照 ID;absent_or_ambiguous 时为 None
    rubric_snapshot_id: str | None = None


class McpAssessmentRequest(BaseModel):
    """外部编程助手提交的评分建议(对外唯一请求模型)。

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

    @model_validator(mode="after")
    def totals_are_consistent(self):
        """与 FinalizeRequest 一致的总分一致性校验。

        编程助手提交的 score 必须等于各评分项得分之和且不超过满分，否则
        服务端 quality_checks["arithmetic_valid"]=True 与写入的
        sub.score / assessment_suggestion 会自相矛盾，教师端展示也会出现总分
        与明细不符。
        """
        if self.score > self.max_score:
            raise ValueError("总分不能超过满分")
        if abs(sum(item.score for item in self.details) - self.score) > 0.01:
            raise ValueError("各评分项得分之和必须等于总分")
        if abs(sum(item.max_score for item in self.details) - self.max_score) > 0.01:
            raise ValueError("各评分项满分之和必须等于总满分")
        return self


class McpSaveAssessmentRequest(BaseModel):
    grading_handle: str = Field(min_length=20, max_length=2000)
    assessment: McpAssessmentRequest
    # 由 MCP server 进程注入的客户端标识(codex/claude-code/opencode 等),
    # 不出现在评分包 schema 中,LLM 不可见;仅用于教师端展示评分来源。
    client: str | None = Field(
        default=None, max_length=64, pattern=r"^[a-z0-9][a-z0-9._-]*$"
    )


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
