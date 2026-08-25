"""Submission 路由:上传 PDF、列表、详情。

上传时在同一事务中创建持久化任务，由独立 worker 执行 OCR → awaiting_mcp，
评分由 MCP 客户端完成，教师在网页确认最终成绩。
"""

import logging
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
from sqlalchemy import exists, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, selectinload

from app.core.config import settings
from app.core.time import utc_now_naive
from app.db.session import get_db
from app.models.question import (
    Question,
    QuestionReplacementStatus,
    QuestionStatus,
)
from app.models.submission import (
    Submission,
    SubmissionStatus,
)
from app.models.submission_code_file import SubmissionCodeFile
from app.schemas.submission import (
    BatchDeleteRequest,
    BatchDeleteResponse,
    FinalizeRequest,
    PaginatedSubmissions,
    SubmissionCreateResponse,
    SubmissionDetail,
    SubmissionOut,
    SubmissionStatusOut,
)
from app.services.code_manifest import parse_code_manifest_json
from app.services.document_storage import (
    MAX_CODE_FILES,
    discard_stored_document,
    remove_document_if_unreferenced,
    save_code_files,
    save_document_as_pdf,
    validate_document_upload,
    validate_independent_code_entries,
)
from app.services.events import (
    acquire_sse_slot,
    notify_submission_status,
    submission_event_stream,
)
from app.services.queue import (
    new_submission_ocr_job,
    reset_submission_ocr_job,
)

router = APIRouter()
logger = logging.getLogger(__name__)

DELETABLE_SUBMISSION_STATUSES = {
    SubmissionStatus.awaiting_mcp,
    SubmissionStatus.ready_for_review,
    SubmissionStatus.reviewed,
    SubmissionStatus.failed,
}


def _discard_uncommitted_document(db: Session, stored, upload_dir: Path) -> None:
    try:
        discard_stored_document(db, stored, upload_dir)
    except (OSError, SQLAlchemyError, ValueError) as exc:
        logger.warning("清理未提交学生 PDF 失败 [%s]: %s", stored.path, exc)


def _discard_code_storage(code_metadata: list[dict]) -> None:
    storage_roots = {Path(item["path"]).parent for item in code_metadata}
    for storage_root in storage_roots:
        shutil.rmtree(storage_root, ignore_errors=True)


@router.post(
    "/submissions",
    response_model=SubmissionCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_submission(
    file: UploadFile = File(..., description="学生作业 PDF"),
    question_id: str = Form(..., description="题目 ID(文件名 slug)"),
    code_files: list[UploadFile] = File(
        default=[], description="可选多语言代码文件"
    ),
    code_manifest: str | None = Form(
        default=None,
        max_length=50_000,
        description="代码文件小题映射 JSON",
    ),
    db: Session = Depends(get_db),
):
    """上传学生报告 PDF 及可选的多语言代码文件。

    收敛为 MCP-only 后固定使用外部编程助手评分模式：上传后只做 OCR，
    进入 ``awaiting_mcp``，由 MCP 客户端评分。
    """
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

    stored = await save_document_as_pdf(file, upload_dir)
    original_filename, saved_path = stored.original_filename, stored.path
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

        # 创建记录
        submission = Submission(
            original_filename=original_filename,
            file_path=str(saved_path),
            file_sha256=stored.sha256,
            question_id=question.id,
            status=SubmissionStatus.pending,
        )
        question.last_used_at = utc_now_naive()
        db.add(submission)
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
        db.add(new_submission_ocr_job(submission.id))
        db.commit()
    except Exception:
        db.rollback()
        _discard_code_storage(code_metadata)
        _discard_uncommitted_document(db, stored, upload_dir)
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

    if file is None and not Path(sub.file_path).exists():
        raise HTTPException(
            status_code=409,
            detail="原学生作业文件已过期，请重新选择 PDF 后重试",
        )
    if sub.code_files and any(not Path(item.file_path).exists() for item in sub.code_files):
        raise HTTPException(
            status_code=409,
            detail="原代码文件已过期，请通过编程助手（MCP）重新提交 PDF 与全部代码文件",
        )
    if sub.code_input_files and any(
        not Path(item.file_path).exists() for item in sub.code_input_files
    ):
        raise HTTPException(
            status_code=409,
            detail="原数据集文件已过期，请通过编程助手（MCP）重新提交 PDF、代码与数据集",
        )

    stored = None
    new_path: Path | None = None
    original_filename = sub.original_filename
    upload_dir = Path(settings.UPLOAD_DIR).resolve()
    if file is not None:
        backend_root = Path(__file__).resolve().parent.parent.parent
        try:
            upload_dir.relative_to(backend_root)
        except ValueError as exc:
            raise HTTPException(
                status_code=500, detail="UPLOAD_DIR 配置非法,必须位于后端根目录下"
            ) from exc
        stored = await save_document_as_pdf(file, upload_dir)
        original_filename, new_path = stored.original_filename, stored.path

    old_path = sub.file_path
    if new_path is not None:
        sub.file_path = str(new_path)
        sub.file_sha256 = stored.sha256
        sub.original_filename = original_filename
    for field in (
        "ocr_text",
        "score",
        "max_score",
        "confidence",
        "feedback",
        "details",
        "assessment_suggestion",
        "assessment_review",
        "reviewed_by",
        "reviewed_at",
        "completed_at",
        "error_message",
        "graded_at",
    ):
        setattr(sub, field, None)
    for code_file in sub.code_files:
        code_file.execution_status = "pending"
        code_file.execution_result = None
        code_file.artifacts = None
        code_file.visual_reviews = None
    sub.grading_revision = 0
    sub.status = SubmissionStatus.pending
    reset_submission_ocr_job(db, submission_id)
    try:
        db.commit()
        db.refresh(sub)
    except Exception:
        db.rollback()
        if stored is not None:
            _discard_uncommitted_document(db, stored, upload_dir)
        raise
    if new_path is not None and stored is not None and old_path != str(new_path):
        try:
            remove_document_if_unreferenced(
                db, old_path, Path(settings.UPLOAD_DIR)
            )
        except (OSError, ValueError) as exc:
            logger.warning("清理旧学生作业 PDF 失败 [%s]: %s", old_path, exc)
    shutil.rmtree(
        Path(settings.UPLOAD_DIR).resolve() / "code-artifacts" / str(submission_id),
        ignore_errors=True,
    )
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
            remove_document_if_unreferenced(db, file_path, Path(settings.UPLOAD_DIR))
        except (OSError, ValueError) as exc:
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
    await acquire_sse_slot()
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
        SubmissionStatus.awaiting_mcp,
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
