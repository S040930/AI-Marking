"""独立题目库：上传、OCR、复用、替换与安全删除。"""

import logging
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
)
from fastapi.responses import FileResponse
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.application.questions import delete_question as delete_question_use_case
from app.application.questions import rename_question as rename_question_use_case
from app.application.uploads import (
    commit_question_create,
    commit_question_replace,
    commit_question_retry,
    preflight_question_create,
    preflight_question_replace,
    preflight_question_retry,
)
from app.core.config import settings
from app.db.session import get_db, get_session_factory
from app.models.question import (
    Question,
    QuestionReplacementStatus,
    QuestionStatus,
)
from app.models.submission import Submission
from app.schemas.question import (
    PaginatedQuestions,
    QuestionConfirmRequest,
    QuestionDetail,
    QuestionMutationResponse,
    QuestionOut,
    QuestionRenameRequest,
    QuestionReplacementResponse,
)
from app.services.document_storage import (
    discard_uncommitted_document as _discard_uncommitted_document,
)
from app.services.document_storage import (
    remove_document_if_unreferenced,
    save_document_as_pdf,
    validate_document_upload,
)
from app.services.document_storage import (
    resolve_upload_dir as _resolve_upload_dir,
)
from app.services.question_identity import build_question_id

router = APIRouter()
logger = logging.getLogger(__name__)


def _upload_dir() -> Path:
    try:
        return _resolve_upload_dir()
    except ValueError as exc:
        raise HTTPException(
            status_code=500, detail="UPLOAD_DIR 配置非法,必须位于后端根目录下"
        ) from exc


def _question_with_count(db: Session, question_id: str):
    count_subquery = (
        select(func.count(Submission.id))
        .where(Submission.question_id == Question.id)
        .correlate(Question)
        .scalar_subquery()
    )
    return (
        db.execute(
            select(Question, count_subquery.label("submission_count")).where(
                Question.id == question_id
            )
        )
    ).one_or_none()


def _serialize(question: Question, submission_count: int, *, detail: bool = False):
    schema = QuestionDetail if detail else QuestionOut
    return schema(
        id=question.id,
        name=question.name,
        original_filename=question.original_filename,
        status=question.status,
        error_message=question.error_message,
        replacement_status=question.replacement_status,
        replacement_error_message=question.replacement_error_message,
        created_at=question.created_at,
        updated_at=question.updated_at,
        last_used_at=question.last_used_at,
        submission_count=submission_count,
        **({"ocr_text": question.ocr_text} if detail else {}),
    )


@router.post("/questions", response_model=QuestionOut, status_code=201)
async def create_question(
    file: UploadFile = File(...),
    name: str | None = Form(default=None, max_length=255),
    session_factory=Depends(get_session_factory),
):
    validate_document_upload(file)
    original_filename = file.filename or ""
    display_name = (name or Path(original_filename).stem).strip()
    if not display_name:
        await file.close()
        raise HTTPException(status_code=422, detail="题目名称不能为空")
    question_id = build_question_id(original_filename)
    if not question_id:
        await file.close()
        raise HTTPException(status_code=422, detail="无法从文件名生成题目 ID")
    await run_in_threadpool(
        preflight_question_create,
        session_factory,
        question_id,
    )
    upload_dir = _upload_dir()
    stored = await save_document_as_pdf(file, upload_dir, suffix="_question")
    try:
        question = await run_in_threadpool(
            commit_question_create,
            session_factory,
            question_id=question_id,
            name=display_name,
            stored=stored,
        )
    except Exception:
        with session_factory() as cleanup_db:
            _discard_uncommitted_document(cleanup_db, stored, upload_dir)
        raise
    return _serialize(question, 0)


@router.get("/questions", response_model=PaginatedQuestions)
def list_questions(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    search: str = Query(""),
    question_status: QuestionStatus | None = Query(None, alias="status"),
    db: Session = Depends(get_db),
):
    count_subquery = (
        select(func.count(Submission.id))
        .where(Submission.question_id == Question.id)
        .correlate(Question)
        .scalar_subquery()
    )
    filters = []
    if search.strip():
        pattern = f"%{search.strip()}%"
        filters.append(
            or_(Question.name.ilike(pattern), Question.original_filename.ilike(pattern))
        )
    if question_status:
        filters.append(Question.status == question_status)
    stmt = select(Question, count_subquery.label("submission_count")).where(*filters)
    total = (
        db.execute(select(func.count()).select_from(Question).where(*filters))
    ).scalar_one()
    rows = (
        db.execute(
            stmt.order_by(
                Question.last_used_at.desc().nullslast(), Question.created_at.desc()
            )
            .offset(skip)
            .limit(limit)
        )
    ).all()
    return PaginatedQuestions(
        items=[_serialize(question, count) for question, count in rows],
        total=total,
        skip=skip,
        limit=limit,
    )


@router.get("/questions/{question_id}", response_model=QuestionDetail)
def get_question(question_id: str, db: Session = Depends(get_db)):
    row = _question_with_count(db, question_id)
    if row is None:
        raise HTTPException(status_code=404, detail="题目不存在")
    return _serialize(row[0], row[1], detail=True)


@router.get("/questions/{question_id}/pdf")
@router.head("/questions/{question_id}/pdf", include_in_schema=False)
def get_question_pdf(question_id: str, db: Session = Depends(get_db)):
    question = db.get(Question, question_id)
    if question is None:
        raise HTTPException(status_code=404, detail="题目不存在")
    path = Path(question.file_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="题目 PDF 已过期或被清理")
    return FileResponse(
        path,
        media_type="application/pdf",
        filename=f"{Path(question.original_filename).stem}.pdf",
        content_disposition_type="inline",
    )


@router.patch("/questions/{question_id}", response_model=QuestionOut)
def rename_question(
    question_id: str,
    payload: QuestionRenameRequest,
    db: Session = Depends(get_db),
):
    rename_question_use_case(db, question_id, payload.name)
    row = _question_with_count(db, question_id)
    return _serialize(row[0], row[1])


@router.post("/questions/{question_id}/retry-ocr", response_model=QuestionOut)
async def retry_question_ocr(
    question_id: str,
    file: UploadFile = File(...),
    session_factory=Depends(get_session_factory),
):
    """失败后由教师重新上传 PDF，再创建一次 OCR 任务。"""
    validate_document_upload(file)
    await run_in_threadpool(preflight_question_retry, session_factory, question_id)

    upload_dir = _upload_dir()
    stored = await save_document_as_pdf(file, upload_dir, suffix="_question")
    try:
        question, old_path, submission_count = await run_in_threadpool(
            commit_question_retry, session_factory, question_id, stored
        )
    except Exception:
        with session_factory() as cleanup_db:
            _discard_uncommitted_document(cleanup_db, stored, upload_dir)
        raise
    if old_path != str(stored.path):
        with session_factory() as cleanup_db:
            _unlink_after_commit(cleanup_db, [old_path])
    return _serialize(question, submission_count)


def _unlink_after_commit(db: Session, paths: list[str]) -> None:
    for file_path in paths:
        try:
            remove_document_if_unreferenced(db, file_path, Path(settings.UPLOAD_DIR))
        except (OSError, ValueError) as exc:
            logger.warning("清理题目库关联 PDF 失败 [%s]: %s", file_path, exc)


@router.post(
    "/questions/{question_id}/replace",
    response_model=QuestionReplacementResponse,
    status_code=202,
)
async def replace_question(
    question_id: str,
    file: UploadFile = File(...),
    confirmation_name: str = Form(..., max_length=255),
    acknowledge_deletion: bool = Form(False),
    session_factory=Depends(get_session_factory),
):
    validate_document_upload(file)
    await run_in_threadpool(
        preflight_question_replace,
        session_factory,
        question_id,
        confirmation_name,
        acknowledge_deletion=acknowledge_deletion,
    )

    upload_dir = _upload_dir()
    stored = await save_document_as_pdf(file, upload_dir, suffix="_question")
    try:
        current, affected_count, previous_staged = await run_in_threadpool(
            commit_question_replace,
            session_factory,
            question_id=question_id,
            confirmation_name=confirmation_name,
            acknowledge_deletion=acknowledge_deletion,
            stored=stored,
        )
    except Exception:
        with session_factory() as cleanup_db:
            _discard_uncommitted_document(cleanup_db, stored, upload_dir)
        raise
    if previous_staged and previous_staged != str(stored.path):
        with session_factory() as cleanup_db:
            _unlink_after_commit(cleanup_db, [previous_staged])
    return QuestionReplacementResponse(
        question_id=current.id,
        replacement_status=QuestionReplacementStatus.pending,
        affected_submission_count=affected_count,
    )


@router.delete("/questions/{question_id}", response_model=QuestionMutationResponse)
def delete_question(
    question_id: str,
    payload: QuestionConfirmRequest = Body(...),
    db: Session = Depends(get_db),
):
    deleted_count = delete_question_use_case(db, question_id, payload.confirmation_name)
    return QuestionMutationResponse(deleted_submission_count=deleted_count)
