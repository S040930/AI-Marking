"""批改 agent 请求/响应契约模型(工具入参与内部响应)。

这些模型承载自研批改 agent 的进程内工具契约:严格模式(extra=forbid)
用于工具入参,宽模式用于服务端内部响应与回执序列化。
"""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.submission import SubmissionGradingMode, SubmissionStatus
from app.schemas.submission import AssessmentSuggestionOut, GradingQualityChecksOut


class GradingAssessmentResponse(BaseModel):
    submission_id: int
    status: SubmissionStatus
    grading_revision: int
    grading_mode: SubmissionGradingMode
    quality_checks: GradingQualityChecksOut
    idempotent: bool = False


class GradingPackageResponse(BaseModel):
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
    # 题目 OCR、question_id 与 rubric 提取句柄,agent 提取并保存后才能
    # 重新打开作业评分。
    needs_rubric: bool = False
    question_id: str | None = None
    question_ocr_text: str | None = None
    rubric_handle: str | None = None


class GradingScoreDetail(BaseModel):
    """v12 起 agent 提交的单项评分。

    agent 只决定它真正负责的内容:引用哪个 rubric item、给多少分、
    评语与证据。``criterion``/``max_score`` 由服务端按权威 rubric 回填,
    不接受 agent 提交。
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


class GradingAssessmentRequest(BaseModel):
    """批改 agent 提交的评分建议。

    agent 只提交自己决定的内容:逐项 ``rubric_item_id``/得分/评语/
    证据,加总分置信度与反馈。单项 ``criterion``/``max_score``、总得分、
    总满分、rubric 来源与快照 ID 全部由服务端从 ``grading_handle`` 对应的
    权威 rubric 推导,提交这些字段会被拒绝(extra=forbid)。
    版本和上下文由服务端从评分句柄读取并在持锁后核对,
    不在工具入参中出现。
    """

    model_config = ConfigDict(extra="forbid")

    request_id: UUID
    confidence: float = Field(ge=0, le=1, default=0)
    feedback: str = Field(min_length=1, max_length=20_000)
    details: list[GradingScoreDetail] = Field(min_length=1, max_length=100)


class GradingSaveAssessmentRequest(BaseModel):
    grading_handle: str = Field(min_length=20, max_length=2000)
    assessment: GradingAssessmentRequest
    # 由运行器注入的可选客户端标识,
    # 不出现在评分包 schema 中,LLM 不可见;仅用于教师端展示评分来源。
    client: str | None = Field(
        default=None, max_length=64, pattern=r"^[a-z0-9][a-z0-9._-]*$"
    )


class GradingVisualConfirmationRequest(BaseModel):
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


class GradingReviewItem(BaseModel):
    """复核者对单个评分项的结论。"""

    model_config = ConfigDict(extra="forbid")
    rubric_item_id: str = Field(min_length=5, max_length=100)
    verdict: Literal["agree", "disagree"]
    comment: str = Field(min_length=1, max_length=4_000)
    # 争议项可给出复核者建议分(0 到该项满分),由服务端按 rubric 校验上限
    suggested_score: float | None = Field(default=None, ge=0)


class GradingSaveReviewRequest(BaseModel):
    """独立复核任务提交的对当前评分建议的复核结论。

    ``grading_handle`` 来自对 ``ready_for_review`` 作业调用
    ``open_grading_package`` 后的评分包;服务端校验建议版本
    (grading_revision)未变化后写入 ``submissions.assessment_review``。
    ``items`` 必须逐项引用当前 rubric 的全部 item,与评分建议相同。
    """

    model_config = ConfigDict(extra="forbid")
    grading_handle: str = Field(min_length=20, max_length=2000)
    verdict: Literal["agree", "partial", "disagree"]
    summary: str = Field(min_length=1, max_length=20_000)
    confidence: float = Field(ge=0, le=1, default=0)
    items: list[GradingReviewItem] = Field(min_length=1, max_length=100)
    client: str | None = Field(
        default=None, max_length=64, pattern=r"^[a-z0-9][a-z0-9._-]*$"
    )


class GradingSaveReviewResponse(BaseModel):
    submission_id: int
    status: SubmissionStatus
    reviewed_revision: int
    verdict: Literal["agree", "partial", "disagree"]
