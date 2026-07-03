"""独立题目库：上传、OCR、复用、替换与安全删除。"""

import asyncio
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
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.submissions import DELETABLE_SUBMISSION_STATUSES, _save_pdf
from app.core.config import settings
from app.core.time import utc_now_naive
from app.db.session import AsyncSessionLocal, get_db
from app.models.question import Question, QuestionStatus
from app.models.submission import Submission
from app.schemas.question import (
    PaginatedQuestions,
    QuestionConfirmRequest,
    QuestionDetail,
    QuestionMutationResponse,
    QuestionOut,
    QuestionRenameRequest,
)
from app.services.config import get_config_dict
from app.services.ocr import ocr_pdf
from app.services.queue import get_pipeline_semaphore

router = APIRouter()
logger = logging.getLogger(__name__)
_question_tasks: set[asyncio.Task] = set()


def _upload_dir() -> Path:
    backend_root = Path(__file__).resolve().parent.parent.parent
    upload_dir = Path(settings.UPLOAD_DIR).resolve()
    try:
        upload_dir.relative_to(backend_root)
    except ValueError as exc:
        raise HTTPException(
            status_code=500, detail="UPLOAD_DIR 配置非法,必须位于后端根目录下"
        ) from exc
    upload_dir.mkdir(parents=True, exist_ok=True)
    return upload_dir


async def _run_question_ocr(question_id: int) -> None:
    async with AsyncSessionLocal() as db:
        question = await db.get(Question, question_id)
        if question is None:
            return
        question.status = QuestionStatus.ocr_processing
        question.error_message = None
        await db.commit()
        try:
            config = await get_config_dict(db)
            async with get_pipeline_semaphore():
                text = await ocr_pdf(
                    question.file_path,
                    config.get("paddleocr_api_url", "") or "",
                    config.get("paddleocr_token", "") or "",
                )
            question.ocr_text = text
            question.status = QuestionStatus.ready
            question.error_message = None
        except Exception as exc:
            question.status = QuestionStatus.failed
            question.error_message = str(exc)[:1024]
            logger.exception("题目 OCR 失败 [question=%s]", question_id)
        question.updated_at = utc_now_naive()
        await db.commit()


def _schedule_ocr(question_id: int) -> None:
    task = asyncio.create_task(_run_question_ocr(question_id))
    _question_tasks.add(task)
    task.add_done_callback(_question_tasks.discard)


async def _question_with_count(db: AsyncSession, question_id: int):
    count_subquery = (
        select(func.count(Submission.id))
        .where(Submission.question_id == Question.id)
        .correlate(Question)
        .scalar_subquery()
    )
    return (
        await db.execute(
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
        created_at=question.created_at,
        updated_at=question.updated_at,
        last_used_at=question.last_used_at,
        submission_count=submission_count,
        **({"ocr_text": question.ocr_text} if detail else {}),
    )


@router.post("/questions", response_model=QuestionOut, status_code=201)
async def create_question(
    file: UploadFile = File(...),
    name: str | None = Form(default=None),
    db: AsyncSession = Depends(get_db),
):
    if file.content_type != "application/pdf":
        raise HTTPException(status_code=400, detail="题目文件仅支持 PDF")
    original_filename, path = await _save_pdf(file, _upload_dir(), suffix="_question")
    display_name = (name or Path(original_filename).stem).strip()
    if not display_name:
        path.unlink(missing_ok=True)
        raise HTTPException(status_code=422, detail="题目名称不能为空")
    question = Question(
        name=display_name,
        original_filename=original_filename,
        file_path=str(path),
        status=QuestionStatus.pending,
    )
    db.add(question)
    try:
        await db.commit()
        await db.refresh(question)
    except Exception:
        await db.rollback()
        path.unlink(missing_ok=True)
        raise
    _schedule_ocr(question.id)
    return _serialize(question, 0)


@router.get("/questions", response_model=PaginatedQuestions)
async def list_questions(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    search: str = Query(""),
    question_status: QuestionStatus | None = Query(None, alias="status"),
    db: AsyncSession = Depends(get_db),
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
        await db.execute(select(func.count()).select_from(Question).where(*filters))
    ).scalar_one()
    rows = (
        await db.execute(
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
async def get_question(question_id: int, db: AsyncSession = Depends(get_db)):
    row = await _question_with_count(db, question_id)
    if row is None:
        raise HTTPException(status_code=404, detail="题目不存在")
    return _serialize(row[0], row[1], detail=True)


@router.get("/questions/{question_id}/pdf")
async def get_question_pdf(question_id: int, db: AsyncSession = Depends(get_db)):
    question = await db.get(Question, question_id)
    if question is None:
        raise HTTPException(status_code=404, detail="题目不存在")
    path = Path(question.file_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="题目 PDF 已过期或被清理")
    return FileResponse(
        path, media_type="application/pdf", filename=question.original_filename,
        content_disposition_type="inline"
    )


@router.patch("/questions/{question_id}", response_model=QuestionOut)
async def rename_question(
    question_id: int,
    payload: QuestionRenameRequest,
    db: AsyncSession = Depends(get_db),
):
    question = await db.get(Question, question_id)
    if question is None:
        raise HTTPException(status_code=404, detail="题目不存在")
    question.name = payload.name.strip()
    question.updated_at = utc_now_naive()
    await db.commit()
    row = await _question_with_count(db, question_id)
    return _serialize(row[0], row[1])


@router.post("/questions/{question_id}/retry-ocr", response_model=QuestionOut)
async def retry_question_ocr(question_id: int, db: AsyncSession = Depends(get_db)):
    question = await db.get(Question, question_id, with_for_update=True)
    if question is None:
        raise HTTPException(status_code=404, detail="题目不存在")
    if question.status in (QuestionStatus.pending, QuestionStatus.ocr_processing):
        raise HTTPException(status_code=409, detail="题目 OCR 正在处理中")
    question.status = QuestionStatus.pending
    question.error_message = None
    await db.commit()
    _schedule_ocr(question.id)
    row = await _question_with_count(db, question_id)
    return _serialize(row[0], row[1])


async def _locked_submissions(db: AsyncSession, question_id: int):
    return (
        await db.execute(
            select(Submission)
            .where(Submission.question_id == question_id)
            .with_for_update()
        )
    ).scalars().all()


def _blocked_ids(submissions: list[Submission]) -> list[int]:
    return sorted(
        sub.id for sub in submissions
        if sub.status not in DELETABLE_SUBMISSION_STATUSES
    )


def _unlink_after_commit(paths: list[str]) -> None:
    for file_path in paths:
        try:
            Path(file_path).unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("清理题目库关联 PDF 失败 [%s]: %s", file_path, exc)


@router.post(
    "/questions/{question_id}/replace", response_model=QuestionMutationResponse
)
async def replace_question(
    question_id: int,
    file: UploadFile = File(...),
    confirmation_name: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    if file.content_type != "application/pdf":
        raise HTTPException(status_code=400, detail="题目文件仅支持 PDF")
    current = await db.get(Question, question_id)
    if current is None:
        raise HTTPException(status_code=404, detail="题目不存在")
    if confirmation_name != current.name:
        raise HTTPException(status_code=422, detail="题目名称确认不匹配")
    expected_name = current.name

    original_filename, new_path = await _save_pdf(
        file, _upload_dir(), suffix="_question"
    )
    try:
        config = await get_config_dict(db)
        # OCR 可能耗时数分钟，释放读取阶段事务；真正替换前再加行锁复查。
        await db.rollback()
        new_ocr_text = await ocr_pdf(
            str(new_path),
            config.get("paddleocr_api_url", "") or "",
            config.get("paddleocr_token", "") or "",
        )
    except Exception as exc:
        new_path.unlink(missing_ok=True)
        raise HTTPException(status_code=502, detail=f"新版题目 OCR 失败: {exc}") from exc

    question = await db.get(Question, question_id, with_for_update=True)
    if question is None or question.name != expected_name:
        new_path.unlink(missing_ok=True)
        raise HTTPException(status_code=409, detail="题目在操作期间已发生变化")
    submissions = await _locked_submissions(db, question_id)
    blocked = _blocked_ids(submissions)
    if blocked:
        new_path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=409,
            detail={"message": "存在正在处理的批改记录，无法更新题目",
                    "blocked_submission_ids": blocked},
        )
    old_paths = [question.file_path, *[sub.file_path for sub in submissions]]
    for sub in submissions:
        await db.delete(sub)
    question.original_filename = original_filename
    question.file_path = str(new_path)
    question.ocr_text = new_ocr_text
    question.status = QuestionStatus.ready
    question.error_message = None
    question.updated_at = utc_now_naive()
    try:
        await db.commit()
    except Exception:
        await db.rollback()
        new_path.unlink(missing_ok=True)
        raise
    _unlink_after_commit(old_paths)
    return QuestionMutationResponse(deleted_submission_count=len(submissions))


@router.delete(
    "/questions/{question_id}", response_model=QuestionMutationResponse
)
async def delete_question(
    question_id: int,
    payload: QuestionConfirmRequest = Body(...),
    db: AsyncSession = Depends(get_db),
):
    question = await db.get(Question, question_id, with_for_update=True)
    if question is None:
        raise HTTPException(status_code=404, detail="题目不存在")
    if payload.confirmation_name != question.name:
        raise HTTPException(status_code=422, detail="题目名称确认不匹配")
    submissions = await _locked_submissions(db, question_id)
    blocked = _blocked_ids(submissions)
    if blocked:
        raise HTTPException(
            status_code=409,
            detail={"message": "存在正在处理的批改记录，无法删除题目",
                    "blocked_submission_ids": blocked},
        )
    paths = [question.file_path, *[sub.file_path for sub in submissions]]
    for sub in submissions:
        await db.delete(sub)
    await db.flush()
    await db.delete(question)
    try:
        await db.commit()
    except Exception:
        await db.rollback()
        raise
    _unlink_after_commit(paths)
    return QuestionMutationResponse(deleted_submission_count=len(submissions))
