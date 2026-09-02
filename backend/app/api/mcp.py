"""本机外部编程助手 MCP 适配器使用的受保护 REST 接口。

业务逻辑（句柄管理、评分策略、上下文打包、评分持久化）位于
``app/application/mcp_workflow.py``，本模块只做 HTTP 参数适配与序列化。
"""

from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from app.application.mcp_workflow import (
    confirm_visual_review,
    get_submission_or_404,
    normalized_name,
    open_grading_package,
    save_assessment,
    save_assessment_review,
    save_question_rubric,
)
from app.core.config import AI_MARKING_SERVICE, MCP_API_VERSION
from app.db.session import get_db, get_session_factory
from app.models.question import Question, QuestionStatus
from app.models.submission import Submission, SubmissionGradingMode, SubmissionStatus
from app.schemas.mcp import (
    McpAssessmentResponse,
    McpHealthResponse,
    McpPackageResponse,
    McpPendingAssignment,
    McpPendingAssignmentsResponse,
    McpPreflightRequest,
    McpPreflightResponse,
    McpQuestionCandidate,
    McpSaveAssessmentRequest,
    McpSaveReviewRequest,
    McpSaveReviewResponse,
    McpSaveRubricRequest,
    McpSaveRubricResponse,
    McpVisualConfirmationRequest,
    McpVisualConfirmationResponse,
    McpWaitReadyResponse,
)
from app.services.code_manifest import auto_question_number, resolve_code_manifest
from app.services.document_storage import (
    validate_code_filenames,
)
from app.services.events import wait_for_submission_status

router = APIRouter(prefix="/mcp", tags=["mcp"])
logger = logging.getLogger(__name__)


@router.get("/health", response_model=McpHealthResponse)
def mcp_health(
    db: Session = Depends(get_db),
) -> McpHealthResponse:
    try:
        db.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail="数据库不可用") from exc
    return McpHealthResponse(
        status="ok",
        service=AI_MARKING_SERVICE,
        database="ok",
        mcp_api_version=MCP_API_VERSION,
    )


@router.get(
    "/pending-assignments",
    response_model=McpPendingAssignmentsResponse,
)
def mcp_pending_assignments(
    cursor: str | None = Query(default=None, max_length=2000),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> McpPendingAssignmentsResponse:
    """列出等待 MCP 客户端评分的作业(按上传时间与 ID 升序)。

    仅列出 ``awaiting_mcp`` 的作业。使用 keyset 游标分页:
    ``(uploaded_at, id) >`` 上一个游标,保证排序稳定且不随翻页偏移。
    ``limit`` 范围 1–100。
    """
    stmt = (
        select(Submission, Question.name.label("question_name"))
        .join(Question, Question.id == Submission.question_id)
        .where(
            Submission.grading_mode == SubmissionGradingMode.external_agent,
            Submission.status == SubmissionStatus.awaiting_mcp,
        )
    )
    if cursor:
        # ISO 时间戳本身含冒号,必须从右侧拆分出最后的 ID。
        parts = cursor.rsplit(":", 1)
        if len(parts) != 2:
            raise HTTPException(status_code=409, detail="待办游标无效，请从头分页")
        try:
            cursor_uploaded_at = datetime.fromisoformat(parts[0])
            cursor_id = int(parts[1])
        except ValueError as exc:
            raise HTTPException(
                status_code=409, detail="待办游标无效，请从头分页"
            ) from exc
        stmt = stmt.where(
            (Submission.uploaded_at, Submission.id) > (cursor_uploaded_at, cursor_id)
        )
    rows = (
        db.execute(
            stmt.order_by(Submission.uploaded_at.asc(), Submission.id.asc()).limit(
                limit + 1
            )
        )
        .all()
    )
    has_more = len(rows) > limit
    items = rows[:limit]
    next_cursor = None
    if has_more:
        last = items[-1]
        next_cursor = f"{last.Submission.uploaded_at.isoformat()}:{last.Submission.id}"
    return McpPendingAssignmentsResponse(
        items=[
            McpPendingAssignment(
                submission_id=row.Submission.id,
                original_filename=row.Submission.original_filename,
                question_name=row.question_name,
                uploaded_at=row.Submission.uploaded_at,
            )
            for row in items
        ],
        next_cursor=next_cursor,
    )


@router.post("/submission-preflight", response_model=McpPreflightResponse)
def mcp_submission_preflight(
    payload: McpPreflightRequest,
    db: Session = Depends(get_db),
) -> McpPreflightResponse:
    """Resolve a teacher-provided question name and validate only file metadata."""
    query = normalized_name(payload.question_name)
    ready_questions = db.execute(
        select(Question).where(Question.status == QuestionStatus.ready)
    ).scalars().all()
    exact = [
        row
        for row in ready_questions
        if query in {normalized_name(row.name), normalized_name(row.original_filename)}
    ]
    if len(exact) != 1:
        candidates = [
            row
            for row in ready_questions
            if query in normalized_name(row.name)
            or query in normalized_name(row.original_filename)
        ][:20]
        return McpPreflightResponse(
            status="needs_question_choice",
            candidates=[
                McpQuestionCandidate(
                    id=row.id, name=row.name, original_filename=row.original_filename
                )
                for row in candidates
            ],
        )

    question = exact[0]
    question_text = question.ocr_text or ""
    filenames = validate_code_filenames([item.filename for item in payload.code_files]) if payload.code_files else []
    # 为每个文件补全 explicit 条目:qN 文件自动推导题号,非 qN 文件必须携带
    # 显式题号(由本地 MCP 客户端提供);resolve 要求 explicit 覆盖全部文件。
    explicit = {}
    for item, filename in zip(payload.code_files, filenames, strict=True):
        auto = auto_question_number(filename)
        if auto is not None:
            explicit[filename] = {"question_number": auto, "entrypoint": item.entrypoint}
        elif item.question_number is not None:
            explicit[filename] = {"question_number": item.question_number, "entrypoint": item.entrypoint}
    try:
        resolved = resolve_code_manifest(
            filenames=filenames,
            question_text=question_text,
            explicit=explicit or None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    mapping = [
        {
            "filename": filename,
            "question_number": int(item["question_number"]),
            **({"entrypoint": False} if not item["entrypoint"] else {}),
        }
        for filename, item in resolved.items()
    ]
    return McpPreflightResponse(
        status="ready_to_submit",
        question_id=question.id,
        question_name=question.name,
        code_manifest=mapping,
    )


@router.get("/submissions/{submission_id}/package", response_model=McpPackageResponse)
def mcp_submission_package(
    submission_id: int,
    continuation_token: str | None = Query(default=None, max_length=2000),
    db: Session = Depends(get_db),
) -> McpPackageResponse:
    """Expose the grading context in bounded pages with opaque continuation."""
    sub = get_submission_or_404(db, submission_id)
    return open_grading_package(db, sub, continuation_token)


# MCP open 工具可等待的最长单次时长;客户端在总预算内自行重试。
MCP_WAIT_READY_MAX_TIMEOUT_SECONDS = 60.0

# 这些状态到达后客户端才能(或不必再)打开评分包。
_MCP_READY_STATUSES = frozenset(
    {
        SubmissionStatus.awaiting_mcp.value,
        SubmissionStatus.ready_for_review.value,
        SubmissionStatus.reviewed.value,
        SubmissionStatus.failed.value,
    }
)


@router.get(
    "/submissions/{submission_id}/wait-ready",
    response_model=McpWaitReadyResponse,
)
async def mcp_wait_ready(
    submission_id: int,
    timeout: float = Query(default=30.0, gt=0, le=MCP_WAIT_READY_MAX_TIMEOUT_SECONDS),
    db: Session = Depends(get_db),
    session_factory: sessionmaker = Depends(get_session_factory),
) -> McpWaitReadyResponse:
    """长轮询等待作业进入可打开状态（OCR 完成/失败或已有建议）。

    替代客户端固定间隔轮询 /package:PG 后端复用 LISTEN/NOTIFY 通道,
    状态变更即时返回;非 PG 后端内部退化为低频轮询。超时返回当前状态,
    客户端据 ``ready`` 决定是打开评分包还是继续等待。
    """
    get_submission_or_404(db, submission_id)
    status = await wait_for_submission_status(
        submission_id,
        _MCP_READY_STATUSES,
        timeout,
        session_factory=session_factory,
    )
    return McpWaitReadyResponse(
        submission_id=submission_id,
        status=status,
        ready=status in _MCP_READY_STATUSES,
    )


@router.post(
    "/submissions/{submission_id}/visual-confirmation",
    response_model=McpVisualConfirmationResponse,
)
def confirm_mcp_visual_review(
    submission_id: int,
    payload: McpVisualConfirmationRequest,
    db: Session = Depends(get_db),
) -> McpVisualConfirmationResponse:
    handle = confirm_visual_review(
        db, submission_id, payload.grading_handle, payload.verdict, payload.note
    )
    return McpVisualConfirmationResponse(
        submission_id=submission_id,
        grading_handle=payload.grading_handle,
        verdict=handle.visual_confirmation,
        note=handle.visual_confirmation_note,
        confirmed_at=handle.visual_confirmed_at.isoformat(),
    )


@router.put(
    "/submissions/{submission_id}/assessment-review",
    response_model=McpSaveReviewResponse,
)
def save_mcp_assessment_review(
    submission_id: int,
    payload: McpSaveReviewRequest,
    db: Session = Depends(get_db),
) -> McpSaveReviewResponse:
    """保存独立复核任务对当前评分建议的复核结论。"""
    sub = save_assessment_review(
        db,
        submission_id,
        payload.grading_handle,
        verdict=payload.verdict,
        summary=payload.summary,
        confidence=payload.confidence,
        items=[item.model_dump() for item in payload.items],
        client=payload.client,
    )
    return McpSaveReviewResponse(
        submission_id=sub.id,
        status=sub.status,
        reviewed_revision=sub.grading_revision,
        verdict=payload.verdict,
    )


@router.put(
    "/questions/{question_id}/rubric",
    response_model=McpSaveRubricResponse,
)
def save_mcp_question_rubric(
    question_id: str,
    payload: McpSaveRubricRequest,
    db: Session = Depends(get_db),
) -> McpSaveRubricResponse:
    """保存客户端从题目 OCR 提取的 rubric(服务端确定性校验)。"""
    result = save_question_rubric(
        db,
        question_id,
        payload.handle,
        status_value=payload.status,
        items=[
            {
                "criterion": item.criterion,
                "max_score": item.max_score,
                "details": item.details,
                "source_quote": item.source_quote,
            }
            for item in payload.items
        ],
        total_max_score=payload.total_max_score,
    )
    return McpSaveRubricResponse(**result)


# The v2 URL is retained for existing local clients; the MCP contract itself is v8.
@router.put(
    "/submissions/{submission_id}/assessment-v2", response_model=McpAssessmentResponse
)
def save_mcp_assessment(
    submission_id: int,
    payload: McpSaveAssessmentRequest,
    db: Session = Depends(get_db),
) -> McpAssessmentResponse:
    """Save a model-facing compact assessment using a server-issued grading handle."""
    return save_assessment(db, submission_id, payload)
