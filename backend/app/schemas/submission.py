"""Submission Pydantic schemas (pydantic v2 风格)。

枚举复用 ``app.models.submission.SubmissionStatus``,避免重复定义。
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.submission import SubmissionStatus


class SubmissionOut(BaseModel):
    """列表精简版:列表与摘要展示用。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    original_filename: str
    status: SubmissionStatus
    score: float | None = None
    uploaded_at: datetime
    completed_at: datetime | None = None


class SubmissionDetail(SubmissionOut):
    """完整版:详情页展示用。"""

    ocr_text: str | None = None
    feedback: str | None = None
    details: list[dict] | None = None
    error_message: str | None = None


class SubmissionCreateResponse(BaseModel):
    """上传成功后返回最小信息。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    status: SubmissionStatus


class PaginatedSubmissions(BaseModel):
    """分页列表响应:items + 分页元信息。"""

    items: list[SubmissionOut]
    total: int
    skip: int
    limit: int
