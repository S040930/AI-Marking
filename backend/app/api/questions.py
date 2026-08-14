"""独立题目库：上传、OCR、复用、替换与安全删除。"""

import logging
import shutil
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
from sqlalchemy.orm import Session, selectinload

from app.api.submissions import DELETABLE_SUBMISSION_STATUSES
from app.core.config import settings
from app.core.time import utc_now_naive
from app.db.session import get_db
from app.models.question import (
    Question,
    QuestionReplacementStatus,
    QuestionStatus,
)
from app.models.submission import Submission
from app.schemas.question import (
    PaginatedQuestions,
    QuestionConfigProfileRequest,
    QuestionConfirmRequest,
    QuestionDetail,
    QuestionMutationResponse,
    QuestionOut,
    QuestionRenameRequest,
    QuestionReplacementResponse,
)
from app.services.config import (
    get_or_create_default_profile,
    get_profile,
)
from app.services.document_storage import (
    save_document_as_pdf,
    validate_document_upload,
)
from app.services.queue import (
    new_question_ocr_job,
    reset_question_ocr_job,
    reset_question_replace_job,
)

router = APIRouter()
logger = logging.getLogger(__name__)


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


def _question_with_count(db: Session, question_id: int):
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
        config_profile_id=question.config_profile_id,
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
    name: str | None = Form(default=None),
    config_profile_id: int | None = Form(default=None),
    db: Session = Depends(get_db),
):
    validate_document_upload(file)
    # 未指定配置项目时使用默认项目(保证题目始终有可用配置)
    if config_profile_id is None:
        default = get_or_create_default_profile(db)
        config_profile_id = default.id
    elif get_profile(db, config_profile_id) is None:
        raise HTTPException(status_code=422, detail="配置项目不存在")
    original_filename, path = await save_document_as_pdf(
        file, _upload_dir(), suffix="_question"
    )
    display_name = (name or Path(original_filename).stem).strip()
    if not display_name:
        path.unlink(missing_ok=True)
        raise HTTPException(status_code=422, detail="题目名称不能为空")
    question = Question(
        name=display_name,
        original_filename=original_filename,
        file_path=str(path),
        status=QuestionStatus.pending,
        config_profile_id=config_profile_id,
    )
    db.add(question)
    try:
        db.flush()
        db.add(new_question_ocr_job(question.id))
        db.commit()
        db.refresh(question)
    except Exception:
        db.rollback()
        path.unlink(missing_ok=True)
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
def get_question(question_id: int, db: Session = Depends(get_db)):
    row = _question_with_count(db, question_id)
    if row is None:
        raise HTTPException(status_code=404, detail="题目不存在")
    return _serialize(row[0], row[1], detail=True)


@router.api_route(
    "/questions/{question_id}/pdf",
    methods=["GET", "HEAD"],
)
def get_question_pdf(question_id: int, db: Session = Depends(get_db)):
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
    question_id: int,
    payload: QuestionRenameRequest,
    db: Session = Depends(get_db),
):
    question = db.get(Question, question_id)
    if question is None:
        raise HTTPException(status_code=404, detail="题目不存在")
    if question.replacement_status in (
        QuestionReplacementStatus.pending,
        QuestionReplacementStatus.processing,
    ):
        raise HTTPException(status_code=409, detail="题目新版正在处理中")
    question.name = payload.name.strip()
    question.updated_at = utc_now_naive()
    db.commit()
    row = _question_with_count(db, question_id)
    return _serialize(row[0], row[1])


@router.patch("/questions/{question_id}/config-profile", response_model=QuestionOut)
def change_question_config_profile(
    question_id: int,
    payload: QuestionConfigProfileRequest,
    db: Session = Depends(get_db),
):
    """切换题目使用的配置项目。"""
    if get_profile(db, payload.config_profile_id) is None:
        raise HTTPException(status_code=422, detail="配置项目不存在")
    question = db.get(Question, question_id)
    if question is None:
        raise HTTPException(status_code=404, detail="题目不存在")
    if question.status in (QuestionStatus.pending, QuestionStatus.ocr_processing):
        raise HTTPException(status_code=409, detail="题目 OCR 正在处理中")
    question.config_profile_id = payload.config_profile_id
    question.updated_at = utc_now_naive()
    db.commit()
    row = _question_with_count(db, question_id)
    return _serialize(row[0], row[1])


@router.post("/questions/{question_id}/retry-ocr", response_model=QuestionOut)
async def retry_question_ocr(
    question_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """失败后由教师重新上传 PDF，再创建一次 OCR 任务。"""
    validate_document_upload(file)
    question = db.get(Question, question_id, with_for_update=True)
    if question is None:
        raise HTTPException(status_code=404, detail="题目不存在")
    if question.status != QuestionStatus.failed:
        raise HTTPException(status_code=409, detail="仅识别失败的题目可以重新上传")

    original_filename, new_path = await save_document_as_pdf(
        file, _upload_dir(), suffix="_question"
    )
    old_path = question.file_path
    question.original_filename = original_filename
    question.file_path = str(new_path)
    question.status = QuestionStatus.pending
    question.error_message = None
    question.updated_at = utc_now_naive()
    reset_question_ocr_job(db, question.id)
    try:
        db.commit()
        db.refresh(question)
    except Exception:
        db.rollback()
        new_path.unlink(missing_ok=True)
        raise
    if old_path != str(new_path):
        _unlink_after_commit([old_path])
    row = _question_with_count(db, question_id)
    return _serialize(row[0], row[1])


def _locked_submissions(db: Session, question_id: int):
    return (
        (
            db.execute(
                select(Submission)
                .options(
                    selectinload(Submission.code_files),
                    selectinload(Submission.code_input_files),
                )
                .where(Submission.question_id == question_id)
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )


def _blocked_ids(submissions: list[Submission]) -> list[int]:
    return sorted(
        sub.id for sub in submissions if sub.status not in DELETABLE_SUBMISSION_STATUSES
    )


def _unlink_after_commit(paths: list[str]) -> None:
    for file_path in paths:
        try:
            Path(file_path).unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("清理题目库关联 PDF 失败 [%s]: %s", file_path, exc)


@router.post(
    "/questions/{question_id}/replace",
    response_model=QuestionReplacementResponse,
    status_code=202,
)
async def replace_question(
    question_id: int,
    file: UploadFile = File(...),
    confirmation_name: str = Form(...),
    acknowledge_deletion: bool = Form(False),
    db: Session = Depends(get_db),
):
    validate_document_upload(file)
    current = db.get(Question, question_id, with_for_update=True)
    if current is None:
        raise HTTPException(status_code=404, detail="题目不存在")
    if confirmation_name != current.name:
        raise HTTPException(status_code=422, detail="题目名称确认不匹配")
    if current.status != QuestionStatus.ready or not current.ocr_text:
        raise HTTPException(status_code=409, detail="只有可使用的题目可以上传新版")
    if current.replacement_status in (
        QuestionReplacementStatus.pending,
        QuestionReplacementStatus.processing,
    ):
        raise HTTPException(status_code=409, detail="题目新版正在处理中")

    submissions = _locked_submissions(db, question_id)
    blocked = _blocked_ids(submissions)
    if blocked:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "存在正在处理的批改记录，无法更新题目",
                "blocked_submission_ids": blocked,
            },
        )

    affected_count = len(submissions)
    if affected_count > 0 and not acknowledge_deletion:
        # 历史作业将被永久删除,要求调用方显式确认
        raise HTTPException(
            status_code=422,
            detail={
                "message": "替换题目将永久删除关联的历史批改记录,需二次确认",
                "affected_submission_count": affected_count,
                "acknowledge_required": True,
            },
        )

    original_filename, new_path = await save_document_as_pdf(
        file, _upload_dir(), suffix="_question"
    )
    current.replacement_status = QuestionReplacementStatus.pending
    current.replacement_file_path = str(new_path)
    current.replacement_original_filename = original_filename
    current.replacement_error_message = None
    current.updated_at = utc_now_naive()
    reset_question_replace_job(db, current.id)
    try:
        db.commit()
    except Exception:
        db.rollback()
        new_path.unlink(missing_ok=True)
        raise
    return QuestionReplacementResponse(
        question_id=current.id,
        replacement_status=QuestionReplacementStatus.pending,
        affected_submission_count=affected_count,
    )


@router.delete("/questions/{question_id}", response_model=QuestionMutationResponse)
def delete_question(
    question_id: int,
    payload: QuestionConfirmRequest = Body(...),
    db: Session = Depends(get_db),
):
    question = db.get(Question, question_id, with_for_update=True)
    if question is None:
        raise HTTPException(status_code=404, detail="题目不存在")
    if payload.confirmation_name != question.name:
        raise HTTPException(status_code=422, detail="题目名称确认不匹配")
    if question.status in (QuestionStatus.pending, QuestionStatus.ocr_processing):
        raise HTTPException(status_code=409, detail="题目 OCR 正在处理中")
    if question.replacement_status in (
        QuestionReplacementStatus.pending,
        QuestionReplacementStatus.processing,
    ):
        raise HTTPException(status_code=409, detail="题目新版正在处理中")
    submissions = _locked_submissions(db, question_id)
    blocked = _blocked_ids(submissions)
    if blocked:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "存在正在处理的批改记录，无法删除题目",
                "blocked_submission_ids": blocked,
            },
        )
    paths = [question.file_path]
    if question.replacement_file_path:
        paths.append(question.replacement_file_path)
    paths.extend(sub.file_path for sub in submissions if sub.file_path)
    paths.extend(
        code_file.file_path
        for sub in submissions
        for code_file in sub.code_files
        if code_file.file_path
    )
    paths.extend(
        input_file.file_path
        for sub in submissions
        for input_file in sub.code_input_files
        if input_file.file_path
    )
    artifact_roots = [
        Path(settings.UPLOAD_DIR).resolve() / "code-artifacts" / str(sub.id)
        for sub in submissions
    ]
    for sub in submissions:
        db.delete(sub)
    db.flush()
    db.delete(question)
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
    _unlink_after_commit(paths)
    for artifact_root in artifact_roots:
        shutil.rmtree(artifact_root, ignore_errors=True)
    return QuestionMutationResponse(deleted_submission_count=len(submissions))
