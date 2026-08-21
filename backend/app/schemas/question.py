"""题目库 API schemas。"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.question import QuestionReplacementStatus, QuestionStatus


class QuestionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    config_profile_id: int
    name: str
    original_filename: str
    status: QuestionStatus
    error_message: str | None = None
    replacement_status: QuestionReplacementStatus | None = None
    replacement_error_message: str | None = None
    created_at: datetime
    updated_at: datetime
    last_used_at: datetime | None = None
    submission_count: int = 0


class QuestionDetail(QuestionOut):
    ocr_text: str | None = None


class GradingPromptOut(BaseModel):
    """按题目生成的可审计批改提示词：与运行时评分包中的 grading_policy 同源。"""

    question_id: int
    name: str
    grading_mode: str
    review_enabled: bool
    source: str
    snapshot_id: str | None = None
    total_max_score: float
    needs_rubric: bool
    ocr_text: str | None = None
    grading_policy: dict
    text: str


class PaginatedQuestions(BaseModel):
    items: list[QuestionOut]
    total: int
    skip: int
    limit: int


class QuestionRenameRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)


class QuestionConfigProfileRequest(BaseModel):
    config_profile_id: int = Field(..., ge=1, description="配置项目 ID")


class QuestionConfirmRequest(BaseModel):
    confirmation_name: str = Field(min_length=1, max_length=255)


class QuestionMutationResponse(BaseModel):
    deleted_submission_count: int


class QuestionReplacementResponse(BaseModel):
    question_id: int
    replacement_status: QuestionReplacementStatus
    affected_submission_count: int
