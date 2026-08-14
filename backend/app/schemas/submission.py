"""Submission Pydantic schemas (pydantic v2 风格)。

枚举复用 ``app.models.submission.SubmissionStatus``,避免重复定义。
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.submission import SubmissionGradingMode, SubmissionStatus
from app.schemas.scoring import ScoreDetail


class SubmissionOut(BaseModel):
    """列表精简版:列表与摘要展示用。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    original_filename: str
    question_original_filename: str | None = None
    status: SubmissionStatus
    grading_mode: SubmissionGradingMode = SubmissionGradingMode.backend_agent
    grading_revision: int = 0
    graded_at: datetime | None = None
    score: float | None = None
    max_score: float | None = None
    confidence: float | None = None
    uploaded_at: datetime
    completed_at: datetime | None = None
    has_code: bool = False


class SubmissionCodeFileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    question_number: int
    entrypoint: bool = True
    original_filename: str
    file_kind: str
    source_sha256: str
    source_text: str | None = None
    execution_status: str
    execution_result: dict | None = None
    artifacts: list[dict] | None = None
    visual_reviews: list[dict] | None = None


class SubmissionCodeInputFileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    original_filename: str
    size_bytes: int
    sha256: str


class SubmissionDetail(SubmissionOut):
    """完整版:详情页展示用。"""

    ocr_text: str | None = None
    question_ocr_text: str | None = None
    feedback: str | None = None
    ai_suggestion: dict | None = None
    details: list[dict] | None = None
    ai_result: dict | None = None
    agent_trace: list[dict] | None = None
    review_reason: str | None = None
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None
    error_message: str | None = None
    code_runtime: dict | None = None
    code_visual_assets: list[dict] | None = None
    code_files: list[SubmissionCodeFileOut] = Field(default_factory=list)
    code_input_files: list[SubmissionCodeInputFileOut] = Field(default_factory=list)


class FinalizeRequest(BaseModel):
    """教师确认最终评分。"""

    reviewer_name: str = Field(min_length=1, max_length=100)
    score: float = Field(ge=0)
    max_score: float = Field(gt=0)
    feedback: str = Field(min_length=1)
    details: list[ScoreDetail] = Field(min_length=1)

    @model_validator(mode="after")
    def totals_are_consistent(self):
        detail_score = sum(item.score for item in self.details)
        detail_max = sum(item.max_score for item in self.details)
        if abs(detail_score - self.score) > 0.01:
            raise ValueError("各评分项得分之和必须等于总分")
        if abs(detail_max - self.max_score) > 0.01:
            raise ValueError("各评分项满分之和必须等于总满分")
        if self.score > self.max_score:
            raise ValueError("总分不能超过满分")
        return self


class ConversationOut(BaseModel):
    """教师-AI 对话消息。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    submission_id: int
    role: str
    content: str
    suggestion: dict | None = None
    created_at: datetime


class ChatRequest(BaseModel):
    """教师发送的聊天消息。"""

    message: str = Field(min_length=1, max_length=2000)
    reviewer_name: str | None = Field(
        default=None,
        max_length=100,
        description="教师姓名,仅在 AI 判断需要 finalize 时使用",
    )


class SuggestionSnapshot(BaseModel):
    """聊天过程中 AI 返回的评分快照。"""

    score: float = Field(ge=0)
    max_score: float = Field(gt=0)
    confidence: float = Field(ge=0, le=1, default=0)
    feedback: str = Field(default="")
    details: list[ScoreDetail] = Field(default_factory=list)


class ChatResponse(BaseModel):
    """Chat 接口返回:AI 回复、消息 ID、评分快照与 finalize 信息。"""

    reply: str
    message_id: int
    action: Literal["reply", "finalize"] = "reply"
    suggestion: SuggestionSnapshot | None = None
    finalize_payload: FinalizeRequest | None = None


class SubmissionCreateResponse(BaseModel):
    """上传成功后返回最小信息。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    status: SubmissionStatus


class SubmissionStatusOut(BaseModel):
    """轻量状态:前端轮询用,不包含 ``ocr_text``/``ai_result`` 等大字段。

    字段集合刻意比 ``SubmissionOut`` 更小,只保留前端在"处理中"阶段需要
    显示的状态/分数/错误信息 + 文件名/上传时间(用于 processing UI),
    大字段(``ocr_text``/``ai_result``/``details`` 等)在终态后再单独请求。
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    status: SubmissionStatus
    grading_mode: SubmissionGradingMode = SubmissionGradingMode.backend_agent
    grading_revision: int = 0
    original_filename: str
    score: float | None = None
    max_score: float | None = None
    confidence: float | None = None
    uploaded_at: datetime
    completed_at: datetime | None = None
    error_message: str | None = None


class BatchDeleteRequest(BaseModel):
    """批量删除请求。"""

    ids: list[int] = Field(min_length=1, max_length=100)


class BatchDeleteResponse(BaseModel):
    """批量删除响应。"""

    deleted_count: int


class PaginatedSubmissions(BaseModel):
    """分页列表响应:items + 分页元信息。"""

    items: list[SubmissionOut]
    total: int
    skip: int
    limit: int
