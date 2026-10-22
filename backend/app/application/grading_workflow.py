"""批改评分写入用例：事务、锁、版本检查和幂等回执。"""

from __future__ import annotations

import hashlib
import json
import logging
import unicodedata
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.application.grading_package import (
    build_context_parts,
    load_workflow_handle,
    token_hash,
)
from app.application.grading_validation import (
    format_evidence_lines,
    validate_assessment_payload,
)
from app.application.lifecycle import transition_submission
from app.core.config import settings
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.core.time import utc_now_naive
from app.models.grading_assessment_receipt import GradingAssessmentReceipt
from app.models.grading_handle import GradingHandle
from app.models.question import Question
from app.models.submission import Submission, SubmissionGradingMode, SubmissionStatus
from app.schemas.grading import (
    GradingAssessmentRequest,
    GradingAssessmentResponse,
    GradingSaveAssessmentRequest,
)
from app.services.config import get_config_dict
from app.services.events import notify_submission_status
from app.services.rubric import ResolvedRubric, resolve_rubric

logger = logging.getLogger(__name__)


def assessment_payload_hash(payload: GradingAssessmentRequest) -> str:
    data = payload.model_dump(
        mode="json",
        exclude={"request_id", "expected_revision", "context_hash"},
        exclude_none=True,
    )
    return hashlib.sha256(
        json.dumps(
            data, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()


def get_locked_submission_or_404(db: Session, submission_id: int) -> Submission:
    sub = db.execute(
        select(Submission)
        .where(Submission.id == submission_id)
        .options(selectinload(Submission.question))
        .options(selectinload(Submission.code_files))
        .options(selectinload(Submission.code_input_files))
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if sub is None:
        raise NotFoundError("提交记录不存在")
    if sub.grading_mode != SubmissionGradingMode.agent:
        raise ConflictError("该提交不是 agent 评分模式")
    return sub


def get_locked_submission_in_order(db: Session, submission_id: int) -> Submission:
    """Lock Question before Submission, the global order for grading writes."""
    snapshot = db.get(Submission, submission_id)
    if snapshot is None:
        raise NotFoundError("提交记录不存在")
    db.execute(
        select(Question)
        .where(Question.id == snapshot.question_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one()
    return get_locked_submission_or_404(db, submission_id)


def normalized_name(value: str) -> str:
    return unicodedata.normalize("NFKC", value).strip().casefold()


def _latest_run_confirmation(
    db: Session, submission_id: int, sub: Submission, context_hash: str
):
    if not sub.code_files:
        return None

    from app.models.grading_run import GradingRun

    return db.execute(
        select(GradingRun)
        .where(
            GradingRun.submission_id == submission_id,
            GradingRun.confirmation_revision == sub.grading_revision,
            GradingRun.confirmation_context_hash == context_hash,
            GradingRun.teacher_verdict.in_(["consistent", "mismatch"]),
        )
        .order_by(GradingRun.teacher_answered_at.desc())
        .limit(1)
    ).scalar_one_or_none()


def _build_assessment_details(
    payload: GradingAssessmentRequest, resolved: ResolvedRubric
) -> tuple[float, list[dict]]:
    items_by_id = {item["rubric_item_id"]: item for item in resolved.items}
    detail_rows: list[dict] = []
    total_score = 0.0
    for detail in payload.details:
        item = items_by_id[detail.rubric_item_id]
        total_score += detail.score
        detail_rows.append(
            {
                "rubric_item_id": detail.rubric_item_id,
                "criterion": item["criterion"],
                "max_score": item["max_score"],
                "score": detail.score,
                "comment": detail.comment,
                "evidence_refs": detail.evidence_refs,
                "evidence_status": detail.evidence_status,
                "evidence": format_evidence_lines(detail.evidence_refs),
            }
        )
    return round(total_score, 6), detail_rows


def _visual_confirmation_metadata(
    handle: GradingHandle, run_confirmation
) -> dict | None:
    if handle.visual_confirmation:
        return {
            "verdict": handle.visual_confirmation,
            "note": handle.visual_confirmation_note,
            "confirmed_at": handle.visual_confirmed_at.isoformat()
            if handle.visual_confirmed_at
            else None,
        }
    if run_confirmation is None:
        return None
    return {
        "verdict": run_confirmation.teacher_verdict,
        "note": run_confirmation.teacher_note,
        "confirmed_at": run_confirmation.teacher_answered_at.isoformat()
        if run_confirmation.teacher_answered_at
        else None,
        "source": "teacher_checkpoint",
        "run_id": run_confirmation.id,
    }


def _build_assessment_metadata(
    *,
    sub: Submission,
    payload: GradingAssessmentRequest,
    handle: GradingHandle,
    run_confirmation,
    resolved: ResolvedRubric,
    context_hash: str,
    next_revision: int,
    request_id: str,
    payload_hash: str,
    client: str | None,
    run_id: int | None,
    code_test_run: dict | None,
) -> tuple[dict, dict]:
    requires_visual_confirmation = bool(sub.code_files)
    quality_checks = {
        "arithmetic_valid": True,
        "evidence_valid": True,
        "code_evidence_valid": not requires_visual_confirmation
        or all(
            detail.evidence_status == "not_found" or detail.evidence_refs
            for detail in payload.details
        ),
        "visual_confirmation_valid": not requires_visual_confirmation
        or bool(handle.visual_confirmation or run_confirmation),
    }
    metadata = {
        "source": "agent",
        "client": client,
        "run_id": run_id,
        "code_test_suite": (
            {
                "suite_id": code_test_run.get("suite_id"),
                "version": code_test_run.get("version"),
            }
            if code_test_run
            else None
        ),
        "code_test_summary": code_test_run.get("summary") if code_test_run else None,
        "rubric_source": resolved.source,
        "rubric_snapshot_id": resolved.snapshot_id,
        "rubric_snapshot": resolved.text,
        "context_hash": context_hash,
        "grading_revision": next_revision,
        "request_id": request_id,
        "payload_hash": payload_hash,
        "quality_checks": quality_checks,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "visual_confirmation": _visual_confirmation_metadata(
            handle, run_confirmation
        ),
    }
    return quality_checks, metadata


def save_agent_assessment(
    submission_id: int,
    payload: GradingAssessmentRequest,
    db: Session,
    grading_handle: str,
    *,
    client: str | None = None,
    run_id: int | None = None,
) -> GradingAssessmentResponse:
    logger.info(
        "grading assessment save requested [submission=%s, request_id=%s]",
        submission_id,
        payload.request_id,
    )
    sub = get_locked_submission_in_order(db, submission_id)
    if not grading_handle:
        raise ConflictError("评分句柄无效，请重新打开作业")
    # A concurrent identical request may have committed while this request
    # waited for the submission lock. Replay before checking its stale handle.
    replay = assessment_receipt_response(
        db, submission_id, payload, grading_handle
    )
    if replay is not None:
        return replay
    handle = load_workflow_handle(db, grading_handle, kind="grading", lock=True)
    if handle.submission_id != submission_id or not handle.context_complete:
        raise ConflictError("评分句柄与作业不匹配，请重新打开作业")
    question_text, submission_text, source_text, current_hash, resolved = (
        build_context_parts(db, sub)
    )
    if (
        len(question_text) + len(submission_text) + len(source_text)
        > settings.GRADING_MAX_CONTEXT_CHARS
    ):
        raise ValidationError(
            "评分上下文超过 GRADING_MAX_CONTEXT_CHARS，请拆分作业或缩减提交内容后重试"
        )
    if sub.status not in (
        SubmissionStatus.awaiting_grading,
        SubmissionStatus.ready_for_review,
    ):
        raise ConflictError("该作业当前不可保存评分建议")
    requires_visual_confirmation = bool(sub.code_files)
    run_confirmation = _latest_run_confirmation(
        db, submission_id, sub, current_hash
    )
    if requires_visual_confirmation and not (
        handle.visual_confirmation or run_confirmation
    ):
        raise ConflictError("含代码的评分必须先完成运行结果与报告一致性确认")
    request_id = str(payload.request_id)
    payload_hash = assessment_payload_hash(payload)

    if handle.grading_revision != sub.grading_revision:
        raise ConflictError(
            {
                "message": "评分建议版本已变化，请重新读取 assessment",
                "current_revision": sub.grading_revision,
            }
        )
    if handle.context_hash != current_hash:
        raise ConflictError("评分上下文已变化，请重新读取 manifest")

    # 与图预检共用同一套完整规则；持锁后再次执行可确保提交条件仍成立。
    from app.services.code_testing import load_run_results, test_evidence_map

    test_evidence = test_evidence_map(db, run_id) if run_id is not None else None
    code_test_run = load_run_results(db, run_id) if run_id is not None else None
    validate_assessment_payload(sub, payload, resolved, test_evidence=test_evidence)

    now = utc_now_naive()
    next_revision = sub.grading_revision + 1
    total_score, detail_rows = _build_assessment_details(payload, resolved)
    quality_checks, metadata = _build_assessment_metadata(
        sub=sub,
        payload=payload,
        handle=handle,
        run_confirmation=run_confirmation,
        resolved=resolved,
        context_hash=current_hash,
        next_revision=next_revision,
        request_id=request_id,
        payload_hash=payload_hash,
        client=client,
        run_id=run_id,
        code_test_run=code_test_run,
    )
    draft = {
        "score": total_score,
        "max_score": resolved.total_max_score,
        "feedback": payload.feedback,
        "details": detail_rows,
    }
    sub.score = total_score
    sub.max_score = resolved.total_max_score
    sub.confidence = payload.confidence
    sub.feedback = payload.feedback
    sub.details = detail_rows
    sub.assessment_suggestion = {
        **draft,
        "confidence": payload.confidence,
        "outcome": "review_required",
        "review_reason": "AI 批改评分建议需要教师确认",
        "agent_metadata": metadata,
    }
    sub.graded_at = now
    sub.grading_revision = next_revision
    transition_submission(sub, SubmissionStatus.ready_for_review)
    notify_submission_status(db, sub.id, SubmissionStatus.ready_for_review.value)
    response = GradingAssessmentResponse(
        submission_id=sub.id,
        status=sub.status,
        grading_revision=sub.grading_revision,
        grading_mode=sub.grading_mode,
        quality_checks=quality_checks,
    )
    db.add(
        GradingAssessmentReceipt(
            submission_id=sub.id,
            request_id=request_id,
            handle_hash=token_hash(grading_handle),
            payload_hash=payload_hash,
            response=response.model_dump(mode="json"),
        )
    )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.execute(
            select(GradingAssessmentReceipt).where(
                GradingAssessmentReceipt.submission_id == submission_id,
                GradingAssessmentReceipt.request_id == request_id,
            )
        ).scalar_one_or_none()
        if (
            existing is not None
            and existing.payload_hash == payload_hash
            and existing.handle_hash == token_hash(grading_handle)
        ):
            replay = GradingAssessmentResponse.model_validate(existing.response)
            replay.idempotent = True
            return replay
        raise ConflictError("request_id 已用于另一份评分内容")
    db.refresh(sub)
    logger.info(
        "grading assessment saved [submission=%s, revision=%s, status=%s]",
        sub.id,
        sub.grading_revision,
        sub.status.value,
    )
    return response


def confirm_visual_review(
    db: Session,
    submission_id: int,
    grading_handle: str,
    verdict: str,
    note: str | None,
) -> GradingHandle:
    """保存代码运行结果与报告一致性确认（教师确认链的一环）。"""
    handle = load_workflow_handle(db, grading_handle, kind="grading")
    if handle.submission_id != submission_id or not handle.context_complete:
        raise ConflictError("评分句柄与作业不匹配")
    sub = get_locked_submission_in_order(db, submission_id)
    handle = load_workflow_handle(db, grading_handle, kind="grading", lock=True)
    _, _, _, context_hash, _ = build_context_parts(db, sub)
    if (
        handle.context_hash != context_hash
        or handle.grading_revision != sub.grading_revision
    ):
        raise ConflictError("评分上下文已变化，请重新打开作业")
    if not sub.code_files:
        raise ConflictError("当前作业不需要代码运行结果确认")
    handle.visual_confirmation = verdict
    handle.visual_confirmation_note = note.strip() if note else None
    handle.visual_confirmed_at = utc_now_naive()
    db.commit()
    return handle


def save_assessment_review(
    db: Session,
    submission_id: int,
    grading_handle: str,
    *,
    verdict: str,
    summary: str,
    confidence: str,
    items: list[dict],
    client: str | None,
) -> Submission:
    """保存独立复核任务对当前评分建议的复核结论。

    - 评分句柄必须来自完整读取 ``ready_for_review`` 作业的评分包
    - 句柄绑定的 context_hash 与 grading_revision 必须仍然匹配,
      建议已更新时返回 409,复核者需重新打开作业
    - ``items`` 必须逐项引用当前 rubric 的全部 item(不重复、不缺失);
      ``suggested_score`` 不得超过该项满分
    - 复核结论写入 ``submissions.assessment_review`` 供教师在网页参考,
      不改变建议本身与状态;教师仍在网页确认最终成绩
    """
    handle = load_workflow_handle(db, grading_handle, kind="grading")
    if handle.submission_id != submission_id or not handle.context_complete:
        raise ConflictError("评分句柄与作业不匹配，请重新打开作业")
    sub = get_locked_submission_in_order(db, submission_id)
    handle = load_workflow_handle(db, grading_handle, kind="grading", lock=True)
    if sub.status == SubmissionStatus.reviewed:
        raise ConflictError("该作业已确认最终成绩，无需复核")
    if sub.status != SubmissionStatus.ready_for_review or not sub.assessment_suggestion:
        raise ConflictError("该作业还没有可复核的评分建议")
    _, _, _, context_hash, resolved = build_context_parts(db, sub)
    if (
        handle.context_hash != context_hash
        or handle.grading_revision != sub.grading_revision
    ):
        raise ConflictError("评分建议已更新，请重新打开作业后复核当前建议")

    expected = {item.rubric_item_id: item for item in resolved.definition.items}
    seen: set[str] = set()
    for item in items:
        if item["rubric_item_id"] not in expected or item["rubric_item_id"] in seen:
            raise ValidationError("复核项必须逐项引用当前 rubric 且不能重复")
        suggested = item.get("suggested_score")
        if suggested is not None and (
            suggested > expected[item["rubric_item_id"]].max_score + 0.01
        ):
            raise ValidationError("复核建议分不能超过该项满分")
        seen.add(item["rubric_item_id"])
    if seen != set(expected):
        raise ValidationError("复核项未完整覆盖当前 rubric")

    sub.assessment_review = {
        "verdict": verdict,
        "summary": summary,
        "confidence": confidence,
        "items": [
            {
                "rubric_item_id": item["rubric_item_id"],
                "criterion": expected[item["rubric_item_id"]].criterion,
                "max_score": expected[item["rubric_item_id"]].max_score,
                "verdict": item["verdict"],
                "comment": item["comment"],
                "suggested_score": item.get("suggested_score"),
            }
            for item in items
        ],
        "reviewed_revision": sub.grading_revision,
        "client": client,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    # 状态不变(仍为 ready_for_review);复用状态事件让前端刷新详情看到复核结果
    notify_submission_status(db, sub.id, sub.status.value)
    db.commit()
    logger.info(
        "grading assessment review saved [submission=%s, revision=%s, verdict=%s]",
        sub.id,
        sub.grading_revision,
        verdict,
    )
    return sub


def save_question_rubric(
    db: Session,
    question_id: str,
    handle_token: str,
    *,
    status_value: str,
    items: list[dict],
    total_max_score: float,
) -> dict:
    """保存 agent 从题目 OCR 提取的 rubric（服务端确定性校验）。

    - 校验 rubric 提取句柄有效、绑定 submission/question、OCR 未变化
    - ``complete`` 时逐项 source_quote 必须是 OCR 子串、分项=总分、
      条目不重复,通过后写入题目级权威快照
    - ``absent_or_ambiguous`` 时持久化识别结果,后续评分走配置 rubric 或
      内置默认,避免重复询问 agent
    - 保存后旧评分句柄失效,agent 必须重新 ``open_grading_package``
      以新 rubric 快照评分
    """
    from app.services.question_rubric import (
        RubricExtraction,
        persist_question_rubric,
        validate_extraction,
    )

    handle = load_workflow_handle(db, handle_token, kind="rubric_extraction")
    if handle.question_id != question_id:
        raise ConflictError("rubric 提取句柄与题目不匹配")
    question = db.get(Question, question_id, with_for_update=True)
    if question is None or not question.ocr_text:
        raise ConflictError("题目 OCR 内容不存在")
    sub = db.get(Submission, handle.submission_id, with_for_update=True)
    handle = load_workflow_handle(db, handle_token, kind="rubric_extraction", lock=True)
    current_ocr_hash = (
        "sha256:" + hashlib.sha256(question.ocr_text.encode()).hexdigest()
    )
    if handle.ocr_hash != current_ocr_hash:
        raise ConflictError("题目 OCR 已变化，请重新打开作业提取 rubric")
    # 绑定 submission 仍处于 awaiting_grading(评分句柄未生成时才有提取句柄)
    if sub is None or sub.status != SubmissionStatus.awaiting_grading:
        raise ConflictError("作业状态已变化，请重新打开作业")

    extraction = RubricExtraction(
        status=status_value,
        items=items,
        total_max_score=total_max_score,
    )
    validated = validate_extraction(extraction, question.ocr_text)
    if status_value == "complete" and not validated:
        raise ValidationError(
            "rubric 提取校验失败：引用必须是 OCR 原文子串且包含分项满分"
            "（多个明确等级区间可推断整体 100 分）、分项之和必须等于总分、条目不重复"
        )

    result = persist_question_rubric(
        db,
        question,
        status=status_value,
        validated=validated,
        ocr_text=question.ocr_text,
    )
    # 提取句柄是一次性的:保存后立即失效,agent 必须重新打开作业
    db.delete(handle)
    db.commit()
    snapshot_id = None
    if result["status"] == "complete":
        config = get_config_dict(db)
        snapshot_id = resolve_rubric(question, config).snapshot_id
    return {
        "status": result["status"],
        "question_id": question.id,
        "question_name": question.name,
        "rubric_snapshot_id": snapshot_id,
    }


def assessment_receipt_response(
    db: Session,
    submission_id: int,
    payload: GradingAssessmentRequest,
    grading_handle: str,
) -> GradingAssessmentResponse | None:
    receipt = db.execute(
        select(GradingAssessmentReceipt).where(
            GradingAssessmentReceipt.submission_id == submission_id,
            GradingAssessmentReceipt.request_id == str(payload.request_id),
        )
    ).scalar_one_or_none()
    if receipt is None:
        return None
    if receipt.payload_hash != assessment_payload_hash(
        payload
    ) or receipt.handle_hash != token_hash(grading_handle):
        raise ConflictError("request_id 已用于另一份评分内容")
    saved = GradingAssessmentResponse.model_validate(receipt.response)
    saved.idempotent = True
    return saved


def save_assessment(
    db: Session,
    submission_id: int,
    payload: GradingSaveAssessmentRequest,
    *,
    run_id: int | None = None,
) -> GradingAssessmentResponse:
    """保存评分：回执幂等，锁内绑定句柄上下文与版本。"""
    replay = assessment_receipt_response(
        db, submission_id, payload.assessment, payload.grading_handle
    )
    if replay is not None:
        return replay
    handle = load_workflow_handle(db, payload.grading_handle, kind="grading")
    if handle.submission_id != submission_id or not handle.context_complete:
        raise ConflictError("评分句柄与作业不匹配，请重新打开作业")
    return save_agent_assessment(
        submission_id,
        payload.assessment,
        db,
        grading_handle=payload.grading_handle,
        client=payload.client,
        run_id=run_id,
    )
