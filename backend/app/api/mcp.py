"""本机外部编程助手 MCP 适配器使用的受保护 REST 接口。

业务逻辑（句柄管理、评分策略、上下文打包、评分持久化）位于
``app/services/mcp_workflow.py``，本模块只做 HTTP 编排。
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from app.core.config import AI_MARKING_SERVICE, MCP_API_VERSION, settings
from app.core.time import utc_now_naive
from app.db.session import get_db
from app.models.mcp_assessment_receipt import McpAssessmentReceipt
from app.models.mcp_workflow_handle import McpWorkflowHandle
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
)
from app.services.code_manifest import auto_question_number, resolve_code_manifest
from app.services.document_storage import (
    validate_code_filenames,
)
from app.services.events import notify_submission_status
from app.services.mcp_workflow import (
    HANDLE_TTL_SECONDS,
    PACKAGE_PAGE_CHARS,
    assessment_payload_hash,
    build_assessment_summary,
    build_context_parts,
    build_package_text,
    create_workflow_handle,
    get_locked_submission_in_order,
    get_submission_or_404,
    load_workflow_handle,
    normalized_name,
    resolve_submission_rubric,
    token_hash,
)
from app.services.mcp_workflow import (
    save_mcp_assessment as _save_mcp_assessment,
)
from app.services.question_rubric import (
    EXTRACTOR_VERSION,
    RubricExtraction,
    persist_question_rubric,
    validate_extraction,
)

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
    if sub.status == SubmissionStatus.failed:
        return McpPackageResponse(
            submission_id=sub.id,
            status=sub.status,
            error_message=sub.error_message,
        )
    if sub.status == SubmissionStatus.reviewed:
        return McpPackageResponse(
            submission_id=sub.id,
            status=sub.status,
            assessment=build_assessment_summary(sub),
        )
    if sub.status not in (
        SubmissionStatus.awaiting_mcp,
        SubmissionStatus.ready_for_review,
    ):
        return McpPackageResponse(submission_id=sub.id, status=sub.status)

    # needs_rubric 分支:题目没有可信 rubric 快照、配置项目也没有 rubric 时,
    # 服务端解析回退到内置默认(100 分)。此时要求客户端先从题目 OCR 提取
    # rubric 并保存(save_ai_marking_question_rubric),再重新打开作业评分。
    # 返回题目 OCR 与短期 rubric 提取句柄;同一题目同时只允许一个有效
    # 提取句柄,防止并发提取互相覆盖。
    resolved = resolve_submission_rubric(db, sub)
    if (
        resolved.source == "built_in_default"
        and sub.question is not None
        and (sub.question.ocr_text or "")
    ):
        ocr_text = sub.question.ocr_text or ""
        ocr_hash = "sha256:" + hashlib.sha256(ocr_text.encode()).hexdigest()
        # 当前 OCR 已做过一次提取(complete 会走 question_extracted 分支,
        # absent_or_ambiguous 表示题目没有明确标准)时,不再重复询问客户端,
        # 直接落到下面的内置默认评分包。
        already_extracted = (
            sub.question.extracted_rubric_ocr_hash == ocr_hash
            and sub.question.extracted_rubric_version == EXTRACTOR_VERSION
        )
        if not already_extracted:
            # 撤销该题目已有的 rubric 提取句柄(含过期清理),保证单一有效句柄
            db.execute(
                delete(McpWorkflowHandle).where(
                    McpWorkflowHandle.kind == "rubric_extraction",
                    McpWorkflowHandle.question_id == sub.question.id,
                )
            )
            now = utc_now_naive()
            db.execute(delete(McpWorkflowHandle).where(McpWorkflowHandle.expires_at < now))
            handle = secrets.token_urlsafe(32)
            db.add(
                McpWorkflowHandle(
                    token_hash=token_hash(handle),
                    kind="rubric_extraction",
                    submission_id=sub.id,
                    question_id=sub.question.id,
                    ocr_hash=ocr_hash,
                    context_hash=ocr_hash,
                    grading_revision=sub.grading_revision,
                    expires_at=now + timedelta(seconds=HANDLE_TTL_SECONDS),
                )
            )
            db.commit()
            return McpPackageResponse(
                submission_id=sub.id,
                status=sub.status,
                needs_rubric=True,
                question_id=sub.question.id,
                question_ocr_text=ocr_text,
                rubric_handle=handle,
                review_url=f"/review/{sub.id}",
            )

    package, context_hash = build_package_text(db, sub)
    if len(package) > settings.MCP_MAX_GRADING_CONTEXT_CHARS:
        raise HTTPException(
            status_code=422,
            detail="评分上下文超过 MCP_MAX_GRADING_CONTEXT_CHARS，请拆分作业或缩减提交内容后重试",
        )
    offset = 0
    if continuation_token:
        cursor = load_workflow_handle(db, continuation_token, kind="package")
        if (
            cursor.submission_id != sub.id
            or cursor.context_hash != context_hash
            or cursor.grading_revision != sub.grading_revision
        ):
            raise HTTPException(status_code=409, detail="评分上下文已变化，请重新打开作业")
        offset = int(cursor.offset or 0)
    if offset < 0 or offset >= len(package):
        raise HTTPException(status_code=409, detail="评分包游标无效，请重新打开作业")
    end = min(len(package), offset + PACKAGE_PAGE_CHARS)
    complete = end == len(package)
    next_token = None
    if not complete:
        next_token = create_workflow_handle(db,
            {
                "kind": "package",
                "submission_id": sub.id,
                "context_hash": context_hash,
                "grading_revision": sub.grading_revision,
                "offset": end,
            }
        )
    grading_handle = None
    if complete:
        grading_handle = create_workflow_handle(db, {
            "kind": "grading",
            "submission_id": sub.id,
            "context_hash": context_hash,
            "grading_revision": sub.grading_revision,
            "context_complete": True,
        })
    db.commit()
    return McpPackageResponse(
        submission_id=sub.id,
        status=sub.status,
        content=package[offset:end],
        context_complete=complete,
        continuation_token=next_token,
        grading_handle=grading_handle,
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
    handle = load_workflow_handle(db, payload.grading_handle, kind="grading")
    if handle.submission_id != submission_id or not handle.context_complete:
        raise HTTPException(status_code=409, detail="评分句柄与作业不匹配")
    sub = get_locked_submission_in_order(db, submission_id)
    handle = load_workflow_handle(db, payload.grading_handle, kind="grading", lock=True)
    _, _, context_hash, _ = build_context_parts(db, sub)
    if handle.context_hash != context_hash or handle.grading_revision != sub.grading_revision:
        raise HTTPException(status_code=409, detail="评分上下文已变化，请重新打开作业")
    if not sub.code_files:
        raise HTTPException(status_code=409, detail="当前作业不需要代码运行结果确认")
    handle.visual_confirmation = payload.verdict
    handle.visual_confirmation_note = payload.note.strip() if payload.note else None
    handle.visual_confirmed_at = utc_now_naive()
    db.commit()
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
    """保存独立复核任务对当前评分建议的复核结论。

    - 评分句柄必须来自完整读取 ``ready_for_review`` 作业的评分包
    - 句柄绑定的 context_hash 与 grading_revision 必须仍然匹配,
      建议已更新时返回 409,复核者需重新打开作业
    - ``items`` 必须逐项引用当前 rubric 的全部 item(不重复、不缺失);
      ``suggested_score`` 不得超过该项满分
    - 复核结论写入 ``submissions.assessment_review`` 供教师在网页参考,
      不改变建议本身与状态;教师仍在网页确认最终成绩
    """
    handle = load_workflow_handle(db, payload.grading_handle, kind="grading")
    if handle.submission_id != submission_id or not handle.context_complete:
        raise HTTPException(status_code=409, detail="评分句柄与作业不匹配，请重新打开作业")
    sub = get_locked_submission_in_order(db, submission_id)
    handle = load_workflow_handle(db, payload.grading_handle, kind="grading", lock=True)
    if sub.status == SubmissionStatus.reviewed:
        raise HTTPException(status_code=409, detail="该作业已确认最终成绩，无需复核")
    if sub.status != SubmissionStatus.ready_for_review or not sub.assessment_suggestion:
        raise HTTPException(status_code=409, detail="该作业还没有可复核的评分建议")
    _, _, context_hash, resolved = build_context_parts(db, sub)
    if (
        handle.context_hash != context_hash
        or handle.grading_revision != sub.grading_revision
    ):
        raise HTTPException(
            status_code=409,
            detail="评分建议已更新，请重新打开作业后复核当前建议",
        )

    expected = {item.rubric_item_id: item for item in resolved.definition.items}
    seen: set[str] = set()
    for item in payload.items:
        if item.rubric_item_id not in expected or item.rubric_item_id in seen:
            raise HTTPException(
                status_code=422, detail="复核项必须逐项引用当前 rubric 且不能重复"
            )
        if item.suggested_score is not None and (
            item.suggested_score > expected[item.rubric_item_id].max_score + 0.01
        ):
            raise HTTPException(status_code=422, detail="复核建议分不能超过该项满分")
        seen.add(item.rubric_item_id)
    if seen != set(expected):
        raise HTTPException(status_code=422, detail="复核项未完整覆盖当前 rubric")

    sub.assessment_review = {
        "verdict": payload.verdict,
        "summary": payload.summary,
        "confidence": payload.confidence,
        "items": [
            {
                "rubric_item_id": item.rubric_item_id,
                "criterion": expected[item.rubric_item_id].criterion,
                "max_score": expected[item.rubric_item_id].max_score,
                "verdict": item.verdict,
                "comment": item.comment,
                "suggested_score": item.suggested_score,
            }
            for item in payload.items
        ],
        "reviewed_revision": sub.grading_revision,
        "client": payload.client,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    # 状态不变(仍为 ready_for_review);复用状态事件让前端刷新详情看到复核结果
    notify_submission_status(db, sub.id, sub.status.value)
    db.commit()
    logger.info(
        "MCP assessment review saved [submission=%s, revision=%s, verdict=%s]",
        sub.id,
        sub.grading_revision,
        payload.verdict,
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
    """保存客户端从题目 OCR 提取的 rubric(服务端确定性校验)。

    - 校验 rubric 提取句柄有效、绑定 submission/question、OCR 未变化
    - ``complete`` 时逐项 source_quote 必须是 OCR 子串且含满分、分项=总分、
      条目不重复,通过后写入题目级权威快照
    - ``absent_or_ambiguous`` 时持久化识别结果,后续评分走配置 rubric 或
      内置默认,避免重复询问客户端
    - 保存后旧评分句柄失效,客户端必须重新 ``open_ai_marking_assignment``
      以新 rubric 快照评分
    """
    handle = load_workflow_handle(db, payload.handle, kind="rubric_extraction")
    if handle.question_id != question_id:
        raise HTTPException(status_code=409, detail="rubric 提取句柄与题目不匹配")
    question = db.get(Question, question_id, with_for_update=True)
    if question is None or not question.ocr_text:
        raise HTTPException(status_code=409, detail="题目 OCR 内容不存在")
    sub = db.get(Submission, handle.submission_id, with_for_update=True)
    handle = load_workflow_handle(db, payload.handle, kind="rubric_extraction", lock=True)
    current_ocr_hash = "sha256:" + hashlib.sha256(
        question.ocr_text.encode()
    ).hexdigest()
    if handle.ocr_hash != current_ocr_hash:
        raise HTTPException(
            status_code=409, detail="题目 OCR 已变化，请重新打开作业提取 rubric"
        )
    # 绑定 submission 仍处于 awaiting_mcp(评分句柄未生成时才有提取句柄)
    if sub is None or sub.status != SubmissionStatus.awaiting_mcp:
        raise HTTPException(status_code=409, detail="作业状态已变化，请重新打开作业")

    extraction = RubricExtraction(
        status=payload.status,
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
    validated = validate_extraction(extraction, question.ocr_text)
    if payload.status == "complete" and not validated:
        raise HTTPException(
            status_code=422,
            detail="rubric 提取校验失败：引用必须是 OCR 原文子串且含满分、分项之和必须等于总分、条目不重复",
        )

    result = persist_question_rubric(
        db,
        question,
        status=payload.status,
        validated=validated,
        ocr_text=question.ocr_text,
    )
    # 提取句柄是一次性的:保存后立即失效,客户端必须重新打开作业
    db.delete(handle)
    db.commit()
    snapshot_id = None
    if result["status"] == "complete":
        from app.services.config import get_config_dict as _get_config_dict
        from app.services.rubric import resolve_rubric as _resolve_rubric

        config = _get_config_dict(db, profile_id=question.config_profile_id)
        snapshot_id = _resolve_rubric(question, config).snapshot_id
    return McpSaveRubricResponse(
        status=result["status"],
        question_id=question.id,
        question_name=question.name,
        rubric_snapshot_id=snapshot_id,
    )


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
    request_id = str(payload.assessment.request_id)
    request_payload_hash = assessment_payload_hash(payload.assessment)
    receipt = db.execute(
        select(McpAssessmentReceipt).where(
            McpAssessmentReceipt.submission_id == submission_id,
            McpAssessmentReceipt.request_id == request_id,
        )
    ).scalar_one_or_none()
    incoming_handle_hash = token_hash(payload.grading_handle)
    if receipt is not None:
        if receipt.payload_hash != request_payload_hash or receipt.handle_hash != incoming_handle_hash:
            raise HTTPException(status_code=409, detail="request_id 已用于另一份评分内容")
        saved = McpAssessmentResponse.model_validate(receipt.response)
        saved.idempotent = True
        return saved
    handle = load_workflow_handle(db, payload.grading_handle, kind="grading")
    if handle.submission_id != submission_id or not handle.context_complete:
        raise HTTPException(status_code=409, detail="评分句柄与作业不匹配，请重新打开作业")
    sub = get_submission_or_404(db, submission_id)
    _question, _answer, context_hash, resolved = build_context_parts(db, sub)
    if (
        handle.context_hash != context_hash
        or handle.grading_revision != sub.grading_revision
    ):
        raise HTTPException(status_code=409, detail="评分上下文已变化，请重新打开作业")
    return _save_mcp_assessment(
        submission_id,
        payload.assessment,
        db,
        handle=handle,
        expected_revision=sub.grading_revision,
        context_hash=context_hash,
        client=payload.client,
    )
