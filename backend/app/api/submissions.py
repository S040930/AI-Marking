"""Submission 路由:上传 PDF、列表、详情。

上传时在同一事务中创建持久化任务，由独立 worker 执行 OCR → awaiting_mcp，
评分由 MCP 客户端完成，教师在网页确认最终成绩。
"""

import logging
import shutil
import tempfile
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Literal

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
from sqlalchemy.orm import Session, selectinload
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool

from app.application.submissions import (
    batch_delete_submissions as batch_delete_submissions_use_case,
)
from app.application.submissions import (
    finalize_submission as finalize_submission_use_case,
)
from app.application.uploads import (
    commit_submission_create,
    commit_submission_retry,
    preflight_submission_create,
    preflight_submission_retry,
)
from app.core.config import settings
from app.db.session import get_db, get_session_factory
from app.models.question import (
    Question,
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
    remove_document_if_unreferenced,
    resolve_upload_dir,
    save_code_files,
    save_document_as_pdf,
    validate_document_upload,
    validate_independent_code_entries,
)
from app.services.document_storage import (
    discard_uncommitted_document as _discard_uncommitted_document,
)
from app.services.events import (
    acquire_sse_slot,
    submission_event_stream,
)
from app.services.result_export import (
    XLSX_MEDIA_TYPE,
    build_results_workbook,
    delete_export_file,
    safe_export_filename,
)
from app.services.submission_zip import extract_and_classify_zip

router = APIRouter()
logger = logging.getLogger(__name__)


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
    file: UploadFile = File(..., description="学生作业 PDF 或 ZIP(PDF+代码+数据集)"),
    question_id: str = Form(..., description="题目 ID(文件名 slug)"),
    code_files: list[UploadFile] = File(
        default=[], description="可选多语言代码文件(仅纯 PDF 上传)"
    ),
    code_manifest: str | None = Form(
        default=None,
        max_length=50_000,
        description="代码文件小题映射 JSON(仅纯 PDF 上传)",
    ),
    session_factory=Depends(get_session_factory),
):
    """上传学生作业:纯报告 PDF(+可选散装代码),或一个 ZIP 包。

    ZIP 由后端解包自动分类:恰好一份报告 PDF、``q<n>.<ext>`` 命名的代码、
    其余为数据集(写入 ``submission_code_input_files``,批改工作区会物化)。
    收敛为 MCP-only 后固定使用外部编程助手评分模式：上传后只做 OCR，
    进入 ``awaiting_mcp``，由 MCP 客户端评分。
    """
    upload_kind = _sniff_upload_kind(file)
    if upload_kind == "zip":
        return await _create_submission_from_zip(
            file=file,
            question_id=question_id,
            session_factory=session_factory,
        )

    validate_document_upload(file)
    preflight = await run_in_threadpool(
        preflight_submission_create, session_factory, question_id
    )
    if len(code_files) > MAX_CODE_FILES:
        raise HTTPException(status_code=413, detail=f"代码文件最多 {MAX_CODE_FILES} 个")
    code_mapping = _parse_code_manifest(
        code_manifest,
        [item.filename or "" for item in code_files],
        preflight.question_text,
    )

    # 解析并校验上传目录(必须位于后端根目录下,防止任意目录写入)
    upload_dir = _resolve_upload_dir_or_500()
    upload_dir.mkdir(parents=True, exist_ok=True)

    stored = await save_document_as_pdf(file, upload_dir)
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

        submission = await run_in_threadpool(
            commit_submission_create,
            session_factory,
            question_id=question_id,
            stored=stored,
            code_metadata=code_metadata,
        )
    except Exception:
        _discard_code_storage(code_metadata)
        with session_factory() as cleanup_db:
            _discard_uncommitted_document(cleanup_db, stored, upload_dir)
        raise

    return submission


def _sniff_upload_kind(file: UploadFile) -> Literal["pdf", "zip"]:
    suffix = Path(file.filename or "").suffix.lower()
    if suffix == ".zip":
        return "zip"
    if suffix == ".pdf":
        return "pdf"
    raise HTTPException(status_code=422, detail="文件仅支持 PDF 或 ZIP 格式")


def _resolve_upload_dir_or_500() -> Path:
    try:
        return resolve_upload_dir()
    except ValueError as exc:
        raise HTTPException(
            status_code=500, detail="UPLOAD_DIR 配置非法,必须位于后端根目录下"
        ) from exc


async def _create_submission_from_zip(
    *,
    file: UploadFile,
    question_id: str,
    session_factory,
):
    """ZIP 分支:线程池解包分类,再走同一提交事务。"""
    preflight = await run_in_threadpool(
        preflight_submission_create, session_factory, question_id
    )
    _ = preflight  # 预检即校验;question_text 由解包规则隐式使用(仅 qN 命名)
    upload_dir = _resolve_upload_dir_or_500()
    upload_dir.mkdir(parents=True, exist_ok=True)

    package = await run_in_threadpool(
        extract_and_classify_zip, file, upload_dir
    )
    try:
        submission = await run_in_threadpool(
            commit_submission_create,
            session_factory,
            question_id=question_id,
            stored=package.stored,
            code_metadata=package.code_metadata,
            input_metadata=package.input_metadata,
        )
    except Exception:
        for storage_dir in package.storage_dirs:
            shutil.rmtree(storage_dir, ignore_errors=True)
        with session_factory() as cleanup_db:
            _discard_uncommitted_document(cleanup_db, package.stored, upload_dir)
        raise
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
    session_factory=Depends(get_session_factory),
):
    """在原记录上重试失败作业，可选替换学生 PDF。"""
    if file is not None:
        validate_document_upload(file)
    question_id = await run_in_threadpool(
        preflight_submission_retry,
        session_factory,
        submission_id,
        replacing_file=file is not None,
    )

    stored = None
    upload_dir = Path(settings.UPLOAD_DIR).resolve()
    if file is not None:
        try:
            upload_dir = resolve_upload_dir()
        except ValueError as exc:
            raise HTTPException(
                status_code=500, detail="UPLOAD_DIR 配置非法,必须位于后端根目录下"
            ) from exc
        stored = await save_document_as_pdf(file, upload_dir)
    try:
        sub, old_path = await run_in_threadpool(
            commit_submission_retry,
            session_factory,
            submission_id=submission_id,
            question_id=question_id,
            stored=stored,
        )
    except Exception:
        if stored is not None:
            with session_factory() as cleanup_db:
                _discard_uncommitted_document(cleanup_db, stored, upload_dir)
        raise
    if stored is not None and old_path != str(stored.path):
        with session_factory() as cleanup_db:
            try:
                remove_document_if_unreferenced(cleanup_db, old_path, upload_dir)
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


@router.get("/submissions/export.xlsx")
def export_submission_results(
    question_id: str = Query(..., min_length=1, description="题目 ID"),
    pass_threshold: float = Query(
        ..., gt=0, le=100, description="本次导出的及格得分率百分比"
    ),
    locale: Literal["zh-CN", "en-US"] = Query(
        "zh-CN", description="工作簿标题语言"
    ),
    db: Session = Depends(get_db),
):
    """Export teacher-reviewed results for one question as an Excel workbook."""
    question = db.get(Question, question_id)
    if question is None:
        raise HTTPException(status_code=404, detail="题目不存在")

    reviewed_count = db.execute(
        select(func.count())
        .select_from(Submission)
        .where(
            Submission.question_id == question_id,
            Submission.status == SubmissionStatus.reviewed,
        )
    ).scalar_one()
    if reviewed_count == 0:
        raise HTTPException(status_code=409, detail="该题目暂无已审阅成绩，无法导出")

    rows = db.execute(
        select(
            Submission.id,
            Submission.original_filename,
            Submission.score,
            Submission.max_score,
            Submission.feedback,
            Submission.details,
            Submission.assessment_suggestion,
            Submission.reviewed_by,
            Submission.reviewed_at,
            Submission.uploaded_at,
        )
        .where(
            Submission.question_id == question_id,
            Submission.status == SubmissionStatus.reviewed,
        )
        .order_by(func.lower(Submission.original_filename), Submission.id)
        .execution_options(yield_per=500)
    ).yield_per(500)

    generated_at = datetime.now().astimezone()
    temp_file = tempfile.NamedTemporaryFile(
        prefix="ai-marking-grade-export-", suffix=".xlsx", delete=False
    )
    temp_path = Path(temp_file.name)
    temp_file.close()
    try:
        build_results_workbook(
            temp_path,
            question_name=question.name,
            rows=rows,
            pass_threshold=pass_threshold,
            locale=locale,
            generated_at=generated_at,
        )
    except Exception:
        delete_export_file(str(temp_path))
        raise

    return FileResponse(
        temp_path,
        media_type=XLSX_MEDIA_TYPE,
        filename=safe_export_filename(question.id, generated_at),
        background=BackgroundTask(delete_export_file, str(temp_path)),
    )


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
    - 不存在的 ID 安全跳过,不影响其他记录的删除
    - 文件删除失败不阻断流程(如文件已被清理),仅记录日志
    """
    deleted_count = batch_delete_submissions_use_case(db, payload.ids)
    return BatchDeleteResponse(deleted_count=deleted_count)


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


@router.get("/submissions/{submission_id}/pdf")
@router.head("/submissions/{submission_id}/pdf", include_in_schema=False)
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
    sub = db.get(
        Submission,
        submission_id,
        options=[selectinload(Submission.question)],
    )
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
def finalize_submission(
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
    return finalize_submission_use_case(
        db,
        submission_id,
        score=payload.score,
        max_score=payload.max_score,
        feedback=payload.feedback,
        details=[item.model_dump() for item in payload.details],
        reviewer_name=payload.reviewer_name,
    )
