"""Submission 路由:上传 PDF、列表、详情。

上传时在同一事务中创建持久化任务，由独立 worker 执行 OCR → Agent。
"""

import logging
import re
import shutil
import unicodedata
from pathlib import Path

from fastapi import (
    APIRouter,
    Body,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import delete, exists, func, select
from sqlalchemy.orm import Session, selectinload, sessionmaker

from app.core.config import settings
from app.core.time import utc_now_naive
from app.db.session import get_db, get_session_factory
from app.models.conversation import Conversation
from app.models.question import (
    Question,
    QuestionReplacementStatus,
    QuestionStatus,
)
from app.models.submission import (
    Submission,
    SubmissionGradingMode,
    SubmissionStatus,
)
from app.models.submission_code_file import SubmissionCodeFile
from app.schemas.submission import (
    BatchDeleteRequest,
    BatchDeleteResponse,
    ChatRequest,
    ChatResponse,
    ConversationOut,
    FinalizeRequest,
    PaginatedSubmissions,
    SubmissionCreateResponse,
    SubmissionDetail,
    SubmissionOut,
    SubmissionStatusOut,
    SuggestionSnapshot,
)
from app.services.agent import AgentError, chat_with_teacher, run_critic_pass
from app.services.code_manifest import parse_code_manifest_json
from app.services.config import get_config_dict
from app.services.document_storage import (
    MAX_CODE_FILES,
    save_code_files,
    save_document_as_pdf,
    validate_document_upload,
    validate_independent_code_entries,
)
from app.services.events import (
    notify_submission_status,
    submission_event_stream,
)
from app.services.queue import (
    new_submission_marking_job,
    reset_submission_marking_job,
)
from app.services.rubric import resolve_rubric

router = APIRouter()
logger = logging.getLogger(__name__)

DELETABLE_SUBMISSION_STATUSES = {
    SubmissionStatus.awaiting_codex,
    SubmissionStatus.ready_for_review,
    SubmissionStatus.reviewed,
    SubmissionStatus.failed,
}


@router.post(
    "/submissions",
    response_model=SubmissionCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_submission(
    file: UploadFile = File(..., description="学生作业 PDF"),
    question_id: int = Form(..., description="题目库 ID"),
    grading_mode: SubmissionGradingMode = Form(
        SubmissionGradingMode.backend_agent,
        description="评分模式: backend_agent 或 codex",
    ),
    code_files: list[UploadFile] = File(
        default=[], description="可选多语言代码文件"
    ),
    code_manifest: str | None = Form(
        default=None, description="代码文件小题映射 JSON"
    ),
    review_enabled: bool | None = Form(
        default=None,
        description="是否执行 critic 自动复核;为空时沿用配置项 review_enabled",
    ),
    db: Session = Depends(get_db),
):
    """上传学生报告 PDF 及可选的多语言代码文件。"""
    validate_document_upload(file)
    question = db.get(Question, question_id, with_for_update=True)
    if question is None:
        raise HTTPException(status_code=404, detail="题目不存在")
    if question.status != QuestionStatus.ready or not question.ocr_text:
        raise HTTPException(status_code=409, detail="题目尚未完成 OCR，暂不可用于批改")
    if question.replacement_status in (
        QuestionReplacementStatus.pending,
        QuestionReplacementStatus.processing,
    ):
        raise HTTPException(
            status_code=409, detail="题目新版正在处理中，暂不可用于批改"
        )
    if code_files and grading_mode != SubmissionGradingMode.codex:
        raise HTTPException(status_code=409, detail="代码文件只能通过 Codex 模式提交")
    if len(code_files) > MAX_CODE_FILES:
        raise HTTPException(status_code=413, detail=f"代码文件最多 {MAX_CODE_FILES} 个")
    code_mapping = _parse_code_manifest(
        code_manifest,
        [item.filename or "" for item in code_files],
        question.ocr_text or "",
    )

    # 解析并校验上传目录(必须位于后端根目录下,防止任意目录写入)
    backend_root = Path(__file__).resolve().parent.parent.parent
    upload_dir = Path(settings.UPLOAD_DIR).resolve()
    try:
        upload_dir.relative_to(backend_root)
    except ValueError:
        raise HTTPException(
            status_code=500, detail="UPLOAD_DIR 配置非法,必须位于后端根目录下"
        )
    upload_dir.mkdir(parents=True, exist_ok=True)

    original_filename, saved_path = await save_document_as_pdf(file, upload_dir)
    code_metadata: list[dict] = []
    try:
        if code_files:
            code_metadata = await save_code_files(
                code_files,
                upload_dir,
                question_numbers=[
                    code_mapping[unicodedata.normalize("NFKC", item.filename or "")]["question_number"]
                    for item in code_files
                ],
                entrypoints=[
                    code_mapping[unicodedata.normalize("NFKC", item.filename or "")]["entrypoint"]
                    for item in code_files
                ],
            )
            validate_independent_code_entries(code_metadata)
    except Exception:
        saved_path.unlink(missing_ok=True)
        raise

    # 创建记录
    submission = Submission(
        original_filename=original_filename,
        file_path=str(saved_path),
        question_id=question.id,
        status=SubmissionStatus.pending,
        grading_mode=grading_mode,
        review_enabled=review_enabled,
    )
    question.last_used_at = utc_now_naive()
    db.add(submission)
    try:
        db.flush()
        for metadata in code_metadata:
            db.add(
                SubmissionCodeFile(
                    submission_id=submission.id,
                    question_number=metadata["question_number"],
                    entrypoint=metadata.get("entrypoint", True),
                    original_filename=metadata["filename"],
                    file_path=metadata["path"],
                    file_kind=metadata["kind"],
                    source_sha256=metadata["source_sha256"],
                    source_text=metadata["source_text"],
                )
            )
        db.add(new_submission_marking_job(submission.id))
        db.commit()
    except Exception:
        db.rollback()
        saved_path.unlink(missing_ok=True)
        for metadata in code_metadata:
            Path(metadata["path"]).unlink(missing_ok=True)
        raise
    db.refresh(submission)

    return submission


def _parse_code_manifest(
    raw_manifest: str | None,
    filenames: list[str],
    question_text: str,
) -> dict[str, dict[str, object]]:
    """Validate a flat filename-to-question mapping without executing input."""
    if not filenames:
        return {}
    try:
        return parse_code_manifest_json(raw_manifest, filenames, question_text)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post(
    "/submissions/{submission_id}/retry",
    response_model=SubmissionCreateResponse,
)
async def retry_submission(
    submission_id: int,
    file: UploadFile | None = File(default=None),
    db: Session = Depends(get_db),
):
    """在原记录上重试失败作业，可选替换学生 PDF。"""
    if file is not None:
        validate_document_upload(file)
    sub = db.get(Submission, submission_id, with_for_update=True)
    if sub is None:
        raise HTTPException(status_code=404, detail="提交记录不存在")
    if sub.status != SubmissionStatus.failed:
        raise HTTPException(status_code=409, detail="仅失败的作业可以重新批改")
    question = db.get(Question, sub.question_id, with_for_update=True)
    if (
        question is None
        or question.status != QuestionStatus.ready
        or not question.ocr_text
    ):
        raise HTTPException(status_code=409, detail="关联题目当前不可用于批改")
    if question.replacement_status in (
        QuestionReplacementStatus.pending,
        QuestionReplacementStatus.processing,
    ):
        raise HTTPException(status_code=409, detail="题目新版正在处理中，暂不可重试")

    new_path: Path | None = None
    original_filename = sub.original_filename
    if file is not None:
        backend_root = Path(__file__).resolve().parent.parent.parent
        upload_dir = Path(settings.UPLOAD_DIR).resolve()
        try:
            upload_dir.relative_to(backend_root)
        except ValueError as exc:
            raise HTTPException(
                status_code=500, detail="UPLOAD_DIR 配置非法,必须位于后端根目录下"
            ) from exc
        original_filename, new_path = await save_document_as_pdf(file, upload_dir)
    elif not Path(sub.file_path).exists():
        raise HTTPException(
            status_code=409,
            detail="原学生作业文件已过期，请重新选择 PDF 后重试",
        )
    if sub.code_files and any(not Path(item.file_path).exists() for item in sub.code_files):
        raise HTTPException(
            status_code=409,
            detail="原代码文件已过期，请通过 Codex 重新提交 PDF 与全部代码文件",
        )
    if sub.code_input_files and any(
        not Path(item.file_path).exists() for item in sub.code_input_files
    ):
        raise HTTPException(
            status_code=409,
            detail="原数据集文件已过期，请通过 Codex 重新提交 PDF、代码与数据集",
        )

    old_path = sub.file_path
    if new_path is not None:
        sub.file_path = str(new_path)
        sub.original_filename = original_filename
    for field in (
        "ocr_text",
        "score",
        "max_score",
        "confidence",
        "feedback",
        "details",
        "ai_result",
        "agent_trace",
        "ai_suggestion",
        "review_reason",
        "reviewed_by",
        "reviewed_at",
        "completed_at",
        "error_message",
        "graded_at",
        "code_runtime",
        "code_visual_assets",
    ):
        setattr(sub, field, None)
    for code_file in sub.code_files:
        code_file.execution_status = "pending"
        code_file.execution_result = None
        code_file.artifacts = None
        code_file.visual_reviews = None
    sub.grading_revision = 0
    sub.status = SubmissionStatus.pending
    db.execute(delete(Conversation).where(Conversation.submission_id == submission_id))
    reset_submission_marking_job(db, submission_id)
    try:
        db.commit()
        db.refresh(sub)
    except Exception:
        db.rollback()
        if new_path is not None:
            new_path.unlink(missing_ok=True)
        raise
    if new_path is not None and old_path != str(new_path):
        try:
            Path(old_path).unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("清理旧学生作业 PDF 失败 [%s]: %s", old_path, exc)
    shutil.rmtree(
        Path(settings.UPLOAD_DIR).resolve() / "code-artifacts" / str(submission_id),
        ignore_errors=True,
    )
    return sub


@router.post(
    "/submissions/{submission_id}/review",
    response_model=SubmissionDetail,
)
async def review_submission(
    submission_id: int,
    session_factory: sessionmaker = Depends(get_session_factory),
):
    """评分完成后按需触发一次 AI 复核（critic）。

    - 仅 ``ready_for_review`` 且尚未复核（agent_trace 无 critic 节点）可触发
    - Codex 模式请走 MCP 订正，不在此触发
    - 复用 ``chat_with_submission`` 的连接释放模式：读快照释放连接 → LLM 调用 →
      重新开连接加锁写入，避免复核期间长期占用数据库连接
    """
    # 第一段：读快照，立即释放连接
    with session_factory() as db:
        sub = db.execute(
            select(Submission)
            .where(Submission.id == submission_id)
            .with_for_update()
            .options(selectinload(Submission.question))
        ).scalar_one_or_none()
        if sub is None:
            raise HTTPException(status_code=404, detail="提交记录不存在")
        if sub.grading_mode == SubmissionGradingMode.codex:
            raise HTTPException(
                status_code=409,
                detail="Codex 评分记录请在 Codex 中通过 MCP 复核",
            )
        if sub.status != SubmissionStatus.ready_for_review:
            raise HTTPException(
                status_code=409,
                detail="作业尚未准备好进行复核",
            )
        if any(
            (event or {}).get("node") == "critic"
            for event in (sub.agent_trace or [])
        ):
            raise HTTPException(status_code=409, detail="该作业已完成复核")
        ocr_text = sub.ocr_text or ""
        question_text = sub.question_ocr_text or ""
        draft = sub.ai_result or {}
        config = get_config_dict(db, profile_id=sub.question.config_profile_id)
        resolved_rubric = resolve_rubric(sub.question, config)
        sub.status = SubmissionStatus.agent_reviewing
        notify_submission_status(db, submission_id, SubmissionStatus.agent_reviewing.value)
        db.commit()

    # 第二段：LLM 复核调用，期间不持有任何 DB 连接
    try:
        critic_dict = await run_critic_pass(
            config,
            ocr_text=ocr_text,
            question_text=question_text,
            resolved_rubric=resolved_rubric,
            draft=draft,
        )
    except AgentError as exc:
        with session_factory() as db:
            failed = db.get(Submission, submission_id, with_for_update=True)
            if failed and failed.status == SubmissionStatus.agent_reviewing:
                failed.status = SubmissionStatus.ready_for_review
                db.commit()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"AI 复核失败: {exc}",
        ) from exc
    except Exception as exc:  # noqa: BLE001
        with session_factory() as db:
            failed = db.get(Submission, submission_id, with_for_update=True)
            if failed and failed.status == SubmissionStatus.agent_reviewing:
                failed.status = SubmissionStatus.ready_for_review
                db.commit()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"AI 复核失败: {exc}",
        ) from exc

    # 第三段：开新 Session，加锁重新校验状态后写入复核结果
    with session_factory() as db:
        sub = db.get(
            Submission,
            submission_id,
            with_for_update=True,
            options=[
                selectinload(Submission.question),
                selectinload(Submission.code_files),
                selectinload(Submission.code_input_files),
            ],
        )
        if sub is None:
            raise HTTPException(status_code=404, detail="提交记录不存在")
        if sub.status == SubmissionStatus.reviewed:
            raise HTTPException(status_code=409, detail="该作业已审阅，不可再复核")
        # 处理中（含并发复核）时拒绝，避免覆盖状态机
        if sub.status != SubmissionStatus.agent_reviewing:
            raise HTTPException(
                status_code=409,
                detail="作业状态已变更，无法写入复核结果",
            )

        suggestion = dict(sub.ai_suggestion or {})
        suggestion["confidence"] = float(critic_dict["confidence"])
        suggestion["critic_summary"] = critic_dict.get("summary") or ""
        suggestion["critic_issues"] = critic_dict.get("issues") or []
        if critic_dict.get("decision") == "review_required":
            suggestion["outcome"] = "review_required"
            suggestion["review_reason"] = critic_dict.get("summary") or ""

        trace = list(sub.agent_trace or [])
        trace.append(
            {
                "node": "critic",
                "status": "completed",
                "attempt": 1,
                "summary": critic_dict.get("summary") or "",
                "timestamp": utc_now_naive().isoformat(),
                "duration_ms": 0,
            }
        )

        sub.ai_suggestion = suggestion
        sub.agent_trace = trace
        sub.confidence = float(critic_dict["confidence"])
        sub.review_reason = suggestion.get("review_reason") or None
        sub.status = SubmissionStatus.ready_for_review
        notify_submission_status(
            db, submission_id, SubmissionStatus.ready_for_review.value
        )
        db.commit()
        db.refresh(sub)
    return sub


@router.get("/submissions", response_model=PaginatedSubmissions)
def list_submissions(
    skip: int = Query(0, ge=0, description="跳过的记录数"),
    limit: int = Query(10, ge=1, le=100, description="每页记录数"),
    include_count: bool = Query(
        True, description="是否计算 total。轮询场景传 false 跳过 count 查询"
    ),
    db: Session = Depends(get_db),
):
    """分页列出提交(按上传时间降序)。

    ``include_count=false`` 时跳过 ``COUNT(*)`` 查询,``total`` 返回 ``0``。
    用于前端处理中轮询场景:轮询只需刷新 items,总数由独立的
    ``GET /submissions/count`` 提供,避免每 15s 一次 count 往返。
    """
    has_code = (
        exists()
        .where(SubmissionCodeFile.submission_id == Submission.id)
        .label("has_code")
    )
    base_stmt = (
        select(
            Submission.id,
            Submission.original_filename,
            Question.original_filename.label("question_original_filename"),
            Submission.status,
            Submission.grading_mode,
            Submission.grading_revision,
            Submission.graded_at,
            Submission.score,
            Submission.max_score,
            Submission.confidence,
            Submission.uploaded_at,
            Submission.completed_at,
            has_code,
        )
        .join(Question, Question.id == Submission.question_id)
        .order_by(Submission.uploaded_at.desc())
    )
    rows = db.execute(base_stmt.offset(skip).limit(limit)).all()
    items = [
        SubmissionOut(
            id=row.id,
            original_filename=row.original_filename,
            question_original_filename=row.question_original_filename,
            status=row.status,
            grading_mode=row.grading_mode,
            grading_revision=row.grading_revision,
            graded_at=row.graded_at,
            score=row.score,
            max_score=row.max_score,
            confidence=row.confidence,
            uploaded_at=row.uploaded_at,
            completed_at=row.completed_at,
            has_code=bool(row.has_code),
        )
        for row in rows
    ]
    total = (
        (db.execute(select(func.count()).select_from(Submission))).scalar_one()
        if include_count
        else 0
    )
    return PaginatedSubmissions(items=items, total=total, skip=skip, limit=limit)


@router.get("/submissions/count")
def count_submissions(db: Session = Depends(get_db)) -> dict[str, int]:
    """返回提交总数。供前端在翻页/首次加载时单独拉取,避免与轮询列表耦合。"""
    total = (db.execute(select(func.count()).select_from(Submission))).scalar_one()
    return {"total": total}


@router.delete(
    "/submissions",
    response_model=BatchDeleteResponse,
    status_code=status.HTTP_200_OK,
)
def batch_delete_submissions(
    payload: BatchDeleteRequest = Body(...),
    db: Session = Depends(get_db),
):
    """批量删除提交记录。

    - 仅终态记录允许删除;存在处理中记录时整批原子拒绝
    - 先提交数据库删除,成功后再清理磁盘 PDF(学生作业 + 题目)
    - 关联的 conversations 由 DB CASCADE 自动级联删除
    - 不存在的 ID 安全跳过,不影响其他记录的删除
    - 文件删除失败不阻断流程(如文件已被清理),仅记录日志
    """
    unique_ids = list(dict.fromkeys(payload.ids))
    stmt = (
        select(Submission)
        .options(
            selectinload(Submission.code_files),
            selectinload(Submission.code_input_files),
        )
        .where(Submission.id.in_(unique_ids))
        .with_for_update()
    )
    subs = db.execute(stmt).scalars().all()

    blocked_ids = sorted(
        sub.id for sub in subs if sub.status not in DELETABLE_SUBMISSION_STATUSES
    )
    if blocked_ids:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": "正在处理的记录不可删除，请等待批改完成后重试",
                "blocked_ids": blocked_ids,
            },
        )

    file_paths = [sub.file_path for sub in subs if sub.file_path]
    code_paths = [
        code_file.file_path
        for sub in subs
        for code_file in sub.code_files
        if code_file.file_path
    ]
    input_paths = [
        input_file.file_path
        for sub in subs
        for input_file in sub.code_input_files
        if input_file.file_path
    ]
    artifact_roots = [
        Path(settings.UPLOAD_DIR).resolve() / "code-artifacts" / str(sub.id)
        for sub in subs
    ]

    for sub in subs:
        db.delete(sub)
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise

    # 数据库是删除结果的权威来源。提交成功后再清理文件,避免事务失败时
    # 出现“记录仍在但 PDF 已丢失”的不可恢复状态。
    for file_path in file_paths:
        try:
            Path(file_path).unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("删除 submission PDF 失败 [%s]: %s", file_path, exc)
    for file_path in code_paths:
        try:
            Path(file_path).unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("删除 submission 代码文件失败 [%s]: %s", file_path, exc)
    for file_path in input_paths:
        try:
            Path(file_path).unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("删除 submission 数据文件失败 [%s]: %s", file_path, exc)
    for artifact_root in artifact_roots:
        shutil.rmtree(artifact_root, ignore_errors=True)

    return BatchDeleteResponse(deleted_count=len(subs))


@router.get("/submissions/{submission_id}", response_model=SubmissionDetail)
def get_submission(submission_id: int, db: Session = Depends(get_db)):
    """获取单个提交详情。"""
    sub = db.get(
        Submission,
        submission_id,
        options=[
            selectinload(Submission.question),
            selectinload(Submission.code_files),
            selectinload(Submission.code_input_files),
        ],
    )
    if sub is None:
        raise HTTPException(status_code=404, detail="提交记录不存在")
    return sub


@router.get(
    "/submissions/{submission_id}/status",
    response_model=SubmissionStatusOut,
)
def get_submission_status(submission_id: int, db: Session = Depends(get_db)):
    """仅返回当前提交的处理状态与终态字段。

    用于前端兜底轮询场景(主路径走 SSE 推送):每 30s 拉一次轻量接口,
    进入终态后再拉完整 ``GET /submissions/{id}``。payload 体积通常
    小于 200B,远小于完整 ``SubmissionDetail`` 含 ``ocr_text`` 的几十 KB 至 MB。
    """
    sub = db.get(Submission, submission_id)
    if sub is None:
        raise HTTPException(status_code=404, detail="提交记录不存在")
    return SubmissionStatusOut.model_validate(sub)


@router.get("/submissions/{submission_id}/events")
async def submission_events(submission_id: int):
    """SSE 端点:实时推送 submission 状态变更事件(P1)。

    - 基于 PostgreSQL LISTEN/NOTIFY,worker 在 ``_update_status`` 内 NOTIFY
    - 启动时先推一次当前状态,避免客户端错过终态事件
    - 每 15s 注释行 keepalive,防止中间代理断开空闲连接
    - 前端 ``useSubmissionEvents`` hook 消费此端点,收到事件后 invalidate
      status query;30s 兜底轮询在 SSE 断开时仍能恢复
    """
    return StreamingResponse(
        submission_event_stream(submission_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # nginx 禁用缓冲,确保 SSE 即时推送
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/submissions/{submission_id}/code-assets/{asset_id:path}")
def get_code_asset(
    submission_id: int,
    asset_id: str,
    db: Session = Depends(get_db),
):
    """安全返回代码运行或报告渲染图片供教师复核。"""
    sub = db.get(Submission, submission_id, options=[selectinload(Submission.code_files)])
    if sub is None:
        raise HTTPException(status_code=404, detail="提交记录不存在")
    root = Path(settings.UPLOAD_DIR).resolve() / "code-artifacts" / str(submission_id)
    if asset_id.startswith("report:"):
        asset_name = asset_id.removeprefix("report:")
        asset = next(
            (item for item in sub.code_visual_assets or [] if item.get("asset_id") == asset_id),
            None,
        )
        if asset is None:
            raise HTTPException(status_code=404, detail="代码资产不存在")
        path = root / "report" / asset_name
        mime = asset.get("mime_type", "image/png")
    else:
        match = re.fullmatch(r"code:(\d+):([A-Za-z0-9_.-]+)", asset_id)
        if match is None:
            raise HTTPException(status_code=404, detail="代码资产不存在")
        code_file = next(
            (item for item in sub.code_files if item.id == int(match.group(1))), None
        )
        if code_file is None:
            raise HTTPException(status_code=404, detail="代码资产不存在")
        artifact_id = match.group(2)
        artifact = next(
            (item for item in code_file.artifacts or [] if item.get("artifact_id") == artifact_id),
            None,
        )
        if artifact is None or artifact.get("kind") not in {"png", "jpg", "jpeg"}:
            raise HTTPException(status_code=404, detail="代码资产不存在")
        path = root / f"q{code_file.question_number}" / artifact_id
        mime = f"image/{artifact['kind']}"
    try:
        path.resolve().relative_to(root)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="代码资产不存在") from exc
    if not path.is_file():
        raise HTTPException(status_code=404, detail="代码资产已清理")
    return FileResponse(path, media_type=mime)


@router.api_route(
    "/submissions/{submission_id}/pdf",
    methods=["GET", "HEAD"],
)
def get_submission_pdf(
    submission_id: int,
    type: str = Query("submission", description="PDF 类型: submission 或 question"),
    db: Session = Depends(get_db),
):
    """安全返回学生作业或作业题目的 PDF 文件流。

    - 仅终态记录(ready_for_review/reviewed/failed)可访问
    - 不暴露服务器文件路径,仅通过 DB 读取后本地读取
    - 使用浏览器原生 PDF 预览(iframe/object)
    """
    sub = db.get(Submission, submission_id)
    if sub is None:
        raise HTTPException(status_code=404, detail="提交记录不存在")
    if sub.status not in (
        SubmissionStatus.awaiting_codex,
        SubmissionStatus.ready_for_review,
        SubmissionStatus.reviewed,
        SubmissionStatus.failed,
    ):
        raise HTTPException(
            status_code=409,
            detail="作业尚未处理完成,无法预览 PDF",
        )

    if type == "question":
        file_path = sub.question.file_path if sub.question else None
        original_filename = sub.question_original_filename or "question.pdf"
    else:
        file_path = sub.file_path
        original_filename = sub.original_filename or "submission.pdf"

    if not file_path:
        raise HTTPException(status_code=404, detail="PDF 文件不存在")

    path = Path(file_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="PDF 文件已过期或被清理")

    return FileResponse(
        path=path,
        media_type="application/pdf",
        filename=f"{Path(original_filename).stem}.pdf",
        content_disposition_type="inline",
    )


@router.post(
    "/submissions/{submission_id}/chat",
    response_model=ChatResponse,
)
async def chat_with_submission(
    submission_id: int,
    payload: ChatRequest,
    session_factory: sessionmaker = Depends(get_session_factory),
):
    """教师就指定作业与 AI 对话。

    - 校验状态为 ready_for_review 或 reviewed
    - 持久化教师消息到 conversations 表
    - 调用 LLM 生成结构化回复(含评分快照与 finalize 意图)
    - 当 AI 判断教师意图为 finalize 时,仅返回待确认评分
    - 持久化 AI 回复文本到 conversations 表

    P0 改造:拆分 DB Session 作用域,LLM 调用 30-60s 期间不持有 DB 连接,
    避免并发聊天耗尽连接池。第三段加 ``with_for_update`` 防止并发 finalize。

    使用 ``session_factory`` 依赖(而非直接 ``SessionLocal()``)以便测试
    override 为 SQLite 工厂,与 ``get_db`` 的 override 保持一致。
    """
    # 第一段:读数据,立即释放连接
    with session_factory() as db:
        sub = db.get(Submission, submission_id)
        if sub is None:
            raise HTTPException(status_code=404, detail="提交记录不存在")
        if sub.grading_mode == SubmissionGradingMode.codex:
            raise HTTPException(
                status_code=409,
                detail="Codex 评分记录请在 Codex 中通过 MCP 修订",
            )
        if sub.status not in (
            SubmissionStatus.ready_for_review,
            SubmissionStatus.reviewed,
        ):
            raise HTTPException(
                status_code=409,
                detail="作业尚未准备好进行对话",
            )
        history_stmt = (
            select(Conversation)
            .where(Conversation.submission_id == submission_id)
            .order_by(Conversation.created_at.asc())
        )
        history = db.execute(history_stmt).scalars().all()
        config = get_config_dict(db, profile_id=sub.question.config_profile_id)
        # 拷贝纯数据,不持有 ORM 对象
        ocr_text = sub.ocr_text or ""
        question_text = sub.question_ocr_text or ""
        ai_suggestion = sub.ai_suggestion or {}
        history_dicts = [{"role": h.role, "content": h.content} for h in history]

    # 第二段:LLM 调用,期间不持有任何 DB 连接
    try:
        chat_result = await chat_with_teacher(
            teacher_message=payload.message,
            ocr_text=ocr_text,
            question_text=question_text,
            ai_suggestion=ai_suggestion,
            history=history_dicts,
            config=config,
        )
    except AgentError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"AI 回复失败: {exc}",
        ) from exc

    reply = chat_result["reply"]
    intent = chat_result["intent"]
    suggestion_raw = chat_result.get("suggestion", {})
    reviewer_name = chat_result.get("reviewer_name", "") or (
        payload.reviewer_name or ""
    )

    # 尝试解析评分快照。普通回复允许没有快照;finalize 意图必须有合法快照,
    # 否则不能返回 action=finalize,避免前端拿到无效的待确认评分。
    try:
        suggestion = SuggestionSnapshot.model_validate(suggestion_raw)
    except Exception:
        suggestion = None

    # 第三段:开新 Session,加锁重新校验状态后写入
    with session_factory() as db:
        sub = db.get(Submission, submission_id, with_for_update=True)
        if sub is None:
            raise HTTPException(status_code=404, detail="提交记录不存在")
        if sub.status not in (
            SubmissionStatus.ready_for_review,
            SubmissionStatus.reviewed,
        ):
            raise HTTPException(
                status_code=409,
                detail="作业状态已变更,无法写入对话",
            )

        # 已审阅状态下不再重复 finalize,意图降级为普通回复;
        # 此时该 assistant 消息不应再携带建议快照,避免"已回复"却存留 finalize 建议的语义不一致。
        finalized_intent_downgraded = (
            intent == "finalize" and sub.status == SubmissionStatus.reviewed
        )
        if finalized_intent_downgraded:
            intent = "reply"

        finalize_payload = None
        if intent == "finalize":
            if suggestion is None:
                raise HTTPException(
                    status_code=422,
                    detail="AI 提取的最终评分格式非法,无法生成待确认评分",
                )
            # 优先使用教师提供的姓名,其次使用 AI 从消息中提取的姓名
            final_reviewer = payload.reviewer_name or reviewer_name
            if not final_reviewer:
                raise HTTPException(
                    status_code=422,
                    detail="提交最终评分需要提供审核教师姓名",
                )
            try:
                finalize_payload = FinalizeRequest(
                    reviewer_name=final_reviewer,
                    score=suggestion.score,
                    max_score=suggestion.max_score,
                    feedback=suggestion.feedback,
                    details=suggestion.details,
                )
            except Exception as exc:
                raise HTTPException(
                    status_code=422,
                    detail=f"AI 提取的最终评分格式非法: {exc}",
                ) from exc

        # 全部校验通过后才一次性持久化教师消息与 AI 回复,避免 LLM 失败或
        # finalize 校验不通过时残留半截对话(只有 user 消息没有 assistant 回复)。
        user_msg = Conversation(
            submission_id=submission_id,
            role="user",
            content=payload.message,
        )
        assistant_msg = Conversation(
            submission_id=submission_id,
            role="assistant",
            content=reply,
            suggestion=None if finalized_intent_downgraded else suggestion_raw,
        )
        db.add(user_msg)
        db.add(assistant_msg)
        db.commit()
        db.refresh(assistant_msg)

    return ChatResponse(
        reply=reply,
        message_id=assistant_msg.id,
        action=intent,
        suggestion=suggestion,
        finalize_payload=finalize_payload,
    )


@router.get(
    "/submissions/{submission_id}/conversations",
    response_model=list[ConversationOut],
)
def list_conversations(
    submission_id: int,
    db: Session = Depends(get_db),
):
    """返回指定作业的教师-AI 对话历史(按时间升序)。"""
    sub = db.get(Submission, submission_id)
    if sub is None:
        raise HTTPException(status_code=404, detail="提交记录不存在")

    stmt = (
        select(Conversation)
        .where(Conversation.submission_id == submission_id)
        .order_by(Conversation.created_at.asc())
    )
    items = db.execute(stmt).scalars().all()
    return [ConversationOut.model_validate(item) for item in items]


@router.post(
    "/submissions/{submission_id}/finalize",
    response_model=SubmissionDetail,
)
async def finalize_submission(
    submission_id: int,
    payload: FinalizeRequest,
    db: Session = Depends(get_db),
):
    """教师确认最终评分。

    - 校验状态为 ready_for_review(已 reviewed 返回 409)
    - 校验分数一致性(由 FinalizeRequest 的 model_validator 处理)
    - 写入 score/max_score/feedback/details/reviewed_by/reviewed_at
    - 状态置为 reviewed
    """
    sub = db.get(Submission, submission_id, with_for_update=True)
    if sub is None:
        raise HTTPException(status_code=404, detail="提交记录不存在")
    if sub.status == SubmissionStatus.reviewed:
        raise HTTPException(status_code=409, detail="该作业已审阅,不可重复提交")
    if sub.status != SubmissionStatus.ready_for_review:
        raise HTTPException(
            status_code=409,
            detail="作业尚未准备好进行审阅",
        )

    now = utc_now_naive()
    sub.score = payload.score
    sub.max_score = payload.max_score
    sub.feedback = payload.feedback
    sub.details = [item.model_dump() for item in payload.details]
    sub.reviewed_by = payload.reviewer_name
    sub.reviewed_at = now
    sub.completed_at = now
    sub.status = SubmissionStatus.reviewed
    notify_submission_status(db, submission_id, SubmissionStatus.reviewed.value)
    db.commit()
    db.refresh(sub)
    return sub
