"""题目库 API schemas。"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.question import QuestionReplacementStatus, QuestionStatus


class QuestionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
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


class PaginatedQuestions(BaseModel):
    items: list[QuestionOut]
    total: int
    skip: int
    limit: int


class QuestionRenameRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)


class QuestionConfirmRequest(BaseModel):
    confirmation_name: str = Field(min_length=1, max_length=255)


class QuestionMutationResponse(BaseModel):
    deleted_submission_count: int


class QuestionReplacementResponse(BaseModel):
    question_id: int
    replacement_status: QuestionReplacementStatus
    affected_submission_count: int
