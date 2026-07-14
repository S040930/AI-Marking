"""题目新版暂存、OCR 与原子切换执行器。"""

import logging
from pathlib import Path

from sqlalchemy import select

from app.core.time import utc_now_naive
from app.db.session import SessionLocal
from app.models.question import Question, QuestionReplacementStatus
from app.models.submission import Submission
from app.services.config import get_config_dict
from app.services.errors import BusinessError
from app.services.ocr import OCRError, ocr_pdf

logger = logging.getLogger(__name__)


def _unlink_paths(paths: list[str]) -> None:
    for file_path in paths:
        try:
            Path(file_path).unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("清理题目替换关联 PDF 失败 [%s]: %s", file_path, exc)


async def run_question_replace(question_id: int) -> None:
    """OCR 暂存 PDF；成功后切换，业务失败时保留旧题目。"""
    with SessionLocal() as db:
        question = db.get(Question, question_id)
        if question is None:
            return
        staged_path = question.replacement_file_path
        staged_name = question.replacement_original_filename
        if not staged_path or not staged_name:
            raise RuntimeError("题目替换任务缺少暂存 PDF")

        question.replacement_status = QuestionReplacementStatus.processing
        question.replacement_error_message = None
        question.updated_at = utc_now_naive()
        db.commit()

    with SessionLocal() as db:
        config = get_config_dict(db)

    try:
        new_ocr_text = await ocr_pdf(
            staged_path,
            config.get("paddleocr_api_url", "") or "",
            config.get("paddleocr_token", "") or "",
        )
    except (BusinessError, OCRError) as exc:
        message = f"新版题目 OCR 失败: {exc}"
        with SessionLocal() as db:
            question = db.get(Question, question_id, with_for_update=True)
            if question is not None:
                question.replacement_status = QuestionReplacementStatus.failed
                question.replacement_file_path = None
                question.replacement_original_filename = None
                question.replacement_error_message = message[:1024]
                question.updated_at = utc_now_naive()
                db.commit()
        _unlink_paths([staged_path])
        raise BusinessError(message) from exc

    with SessionLocal() as db:
        question = db.get(Question, question_id, with_for_update=True)
        if question is None:
            _unlink_paths([staged_path])
            return
        if (
            question.replacement_file_path != staged_path
            or question.replacement_status
            != QuestionReplacementStatus.processing
        ):
            raise RuntimeError("题目替换状态在执行期间发生变化")

        submissions = (
            db.execute(
                select(Submission)
                .where(Submission.question_id == question_id)
                .with_for_update()
            )
        ).scalars().all()
        old_paths = [question.file_path, *[sub.file_path for sub in submissions]]
        for submission in submissions:
            db.delete(submission)
        question.original_filename = staged_name
        question.file_path = staged_path
        question.ocr_text = new_ocr_text
        question.replacement_status = None
        question.replacement_file_path = None
        question.replacement_original_filename = None
        question.replacement_error_message = None
        question.updated_at = utc_now_naive()
        db.commit()
        _unlink_paths(old_paths)
