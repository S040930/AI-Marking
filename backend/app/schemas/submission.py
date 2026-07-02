"""Submission Pydantic schemas (pydantic v2 风格)。

枚举复用 ``app.models.submission.SubmissionStatus``,避免重复定义。
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.submission import SubmissionStatus


class SubmissionOut(BaseModel):
    """列表精简版:列表与摘要展示用。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    original_filename: str
    question_original_filename: str | None = None
    status: SubmissionStatus
    score: float | None = None
    max_score: float | None = None
    confidence: float | None = None
    uploaded_at: datetime
    completed_at: datetime | None = None
    ai_suggestion: dict | None = None


class SubmissionDetail(SubmissionOut):
    """完整版:详情页展示用。"""

    ocr_text: str | None = None
    question_ocr_text: str | None = None
    feedback: str | None = None
    details: list[dict] | None = None
    ai_result: dict | None = None
    agent_trace: list[dict] | None = None
    review_reason: str | None = None
    reviewed_by: str | None = None
    review_note: str | None = None
    reviewed_at: datetime | None = None
    error_message: str | None = None


class ReviewDetail(BaseModel):
    """教师确认的单项评分。"""

    criterion: str = Field(min_length=1, max_length=200)
    score: float = Field(ge=0)
    max_score: float = Field(gt=0)
    comment: str = Field(min_length=1)
    evidence: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def score_within_maximum(self):
        if self.score > self.max_score:
            raise ValueError("单项得分不能超过该项满分")
        return self


class FinalizeRequest(BaseModel):
    """教师确认最终评分。"""

    reviewer_name: str = Field(min_length=1, max_length=100)
    score: float = Field(ge=0)
    max_score: float = Field(gt=0)
    feedback: str = Field(min_length=1)
    details: list[ReviewDetail] = Field(min_length=1)

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
    created_at: datetime


class ChatRequest(BaseModel):
    """教师发送的聊天消息。"""

    message: str = Field(min_length=1, max_length=2000)


class ChatResponse(BaseModel):
    """Chat 接口返回:AI 回复与消息 ID。"""

    reply: str
    message_id: int


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
    original_filename: str
    score: float | None = None
    max_score: float | None = None
    confidence: float | None = None
    uploaded_at: datetime
    completed_at: datetime | None = None
    error_message: str | None = None


class PaginatedSubmissions(BaseModel):
    """分页列表响应:items + 分页元信息。"""

    items: list[SubmissionOut]
    total: int
    skip: int
    limit: int
