"""外部编程助手 MCP 请求/响应模型。"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.submission import SubmissionGradingMode, SubmissionStatus
from app.schemas.submission import AssessmentSuggestionOut, McpQualityChecksOut


class McpAssessmentResponse(BaseModel):
    submission_id: int
    status: SubmissionStatus
    grading_revision: int
    grading_mode: SubmissionGradingMode
    quality_checks: McpQualityChecksOut
    idempotent: bool = False


class McpHealthResponse(BaseModel):
    status: str
    service: str
    database: str
    worker_required: bool = True
    mcp_api_version: str


# Public MCP workflow (API v12) --------------------------------------------


class McpPreflightFile(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    question_number: int | None = Field(default=None, ge=1)
    entrypoint: bool = True


class McpPreflightRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question_name: str = Field(min_length=1, max_length=200)
    code_files: list[McpPreflightFile] = Field(default_factory=list, max_length=20)


class McpQuestionCandidate(BaseModel):
    id: str
    name: str
    original_filename: str


class McpPreflightResponse(BaseModel):
    status: Literal["ready_to_submit", "needs_question_choice"]
    question_id: str | None = None
    question_name: str | None = None
    code_manifest: list[dict] = Field(default_factory=list)
    candidates: list[McpQuestionCandidate] = Field(default_factory=list)


class McpPackageResponse(BaseModel):
    submission_id: int
    status: SubmissionStatus
    review_url: str | None = None
    error_message: str | None = None
    assessment: AssessmentSuggestionOut | None = None
    content: str | None = None
    context_complete: bool = False
    continuation_token: str | None = None
    grading_handle: str | None = None
    # needs_rubric 分支:题目没有可信 rubric 快照且配置无 rubric 时返回
    # 题目 OCR、question_id 与 rubric 提取句柄,客户端提取并保存后才能
    # 重新打开作业评分。
    needs_rubric: bool = False
    question_id: str | None = None
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


class McpWaitReadyResponse(BaseModel):
    """wait-ready 长轮询响应:当前状态与是否已进入可打开状态。

    ``ready`` 为 true 时 status 必然属于 awaiting_mcp/ready_for_review/
    reviewed/failed,客户端可立即调用 open_ai_marking_assignment;
    false 表示超时仍未就绪,客户端可继续等待。
    """

    submission_id: int
    status: str
    ready: bool


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
    question_id: str
    question_name: str
    # complete 时为新 rubric 快照 ID;absent_or_ambiguous 时为 None
    rubric_snapshot_id: str | None = None


class McpScoreDetail(BaseModel):
    """v12 起客户端提交的单项评分。

    客户端只决定它真正负责的内容:引用哪个 rubric item、给多少分、
    评语与证据。``criterion``/``max_score`` 由服务端按权威 rubric 回填,
    不接受客户端提交。
    """

    model_config = ConfigDict(extra="forbid")

    rubric_item_id: str = Field(min_length=5, max_length=100)
    score: float = Field(ge=0)
    comment: str = Field(min_length=1)
    # 结构化证据引用(报告引用/源码行),格式见评分包 evidence_format。
    evidence_refs: list[dict] = Field(default_factory=list)
    # supported(默认):存在可引用的评分依据;
    # not_found:完整上下文(评分包仅在最末页签发 grading handle)中找不到
    # 对应内容,必须 score=0 且不引用证据,评语说明依据。
    evidence_status: Literal["supported", "not_found"] = "supported"


class McpAssessmentRequest(BaseModel):
    """外部编程助手提交的评分建议(MCP API v12)。

    v12 起客户端只提交自己决定的内容:逐项 ``rubric_item_id``/得分/评语/
    证据,加总分置信度与反馈。单项 ``criterion``/``max_score``、总得分、
    总满分、rubric 来源与快照 ID 全部由服务端从 ``grading_handle`` 对应的
    权威 rubric 推导,客户端提交这些字段会被拒绝(extra=forbid)。
    版本和上下文由服务端从评分句柄读取并在持锁后核对,
    不在对外请求中出现。
    """

    model_config = ConfigDict(extra="forbid")

    request_id: UUID
    confidence: float = Field(ge=0, le=1, default=0)
    feedback: str = Field(min_length=1, max_length=20_000)
    details: list[McpScoreDetail] = Field(min_length=1, max_length=100)


class McpSaveAssessmentRequest(BaseModel):
    grading_handle: str = Field(min_length=20, max_length=2000)
    assessment: McpAssessmentRequest
    # 由 MCP server 进程注入的可选客户端标识,
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


class McpReviewItem(BaseModel):
    """复核者对单个评分项的结论。"""

    model_config = ConfigDict(extra="forbid")
    rubric_item_id: str = Field(min_length=5, max_length=100)
    verdict: Literal["agree", "disagree"]
    comment: str = Field(min_length=1, max_length=4_000)
    # 争议项可给出复核者建议分(0 到该项满分),由服务端按 rubric 校验上限
    suggested_score: float | None = Field(default=None, ge=0)


class McpSaveReviewRequest(BaseModel):
    """独立复核任务提交的对当前评分建议的复核结论。

    ``grading_handle`` 来自对 ``ready_for_review`` 作业调用
    ``open_ai_marking_assignment`` 后的评分包;服务端校验建议版本
    (grading_revision)未变化后写入 ``submissions.assessment_review``。
    ``items`` 必须逐项引用当前 rubric 的全部 item,与评分建议相同。
    """

    model_config = ConfigDict(extra="forbid")
    grading_handle: str = Field(min_length=20, max_length=2000)
    verdict: Literal["agree", "partial", "disagree"]
    summary: str = Field(min_length=1, max_length=20_000)
    confidence: float = Field(ge=0, le=1, default=0)
    items: list[McpReviewItem] = Field(min_length=1, max_length=100)
    client: str | None = Field(
        default=None, max_length=64, pattern=r"^[a-z0-9][a-z0-9._-]*$"
    )


class McpSaveReviewResponse(BaseModel):
    submission_id: int
    status: SubmissionStatus
    reviewed_revision: int
    verdict: Literal["agree", "partial", "disagree"]
