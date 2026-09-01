"""题目新版暂存、OCR 与原子切换执行器。"""

import logging
from pathlib import Path

from sqlalchemy import select

from app.application.lifecycle import transition_question_replacement
from app.core.config import settings
from app.core.time import utc_now_naive
from app.db.session import SessionLocal
from app.models.question import Question, QuestionReplacementStatus
from app.models.submission import Submission
from app.services.config import get_config_dict
from app.services.document_storage import remove_document_if_unreferenced
from app.services.errors import BusinessError
from app.services.events import notify_question_status
from app.services.ocr import OCRError, ocr_pdf

logger = logging.getLogger(__name__)


def _unlink_paths(paths: list[str]) -> None:
    with SessionLocal() as db:
        for file_path in paths:
            try:
                remove_document_if_unreferenced(
                    db, file_path, Path(settings.UPLOAD_DIR)
                )
            except (OSError, ValueError) as exc:
                logger.warning("清理题目替换关联 PDF 失败 [%s]: %s", file_path, exc)


async def run_question_replace(question_id: str) -> None:
    """OCR 暂存 PDF；成功后切换，业务失败时保留旧题目。"""
    with SessionLocal() as db:
        question = db.get(Question, question_id)
        if question is None:
            return
        staged_path = question.replacement_file_path
        staged_sha256 = question.replacement_file_sha256
        staged_name = question.replacement_original_filename
        if not staged_path or not staged_name:
            raise BusinessError("题目替换任务缺少暂存 PDF")

        transition_question_replacement(
            question, QuestionReplacementStatus.processing
        )
        question.replacement_error_message = None
        question.updated_at = utc_now_naive()
        # 题目主 status 不变(仍为 ready),通知里携带 replacement_status
        # 让前端 QuestionsPage 触发刷新获取最新状态。
        notify_question_status(
            db,
            question_id,
            question.status.value,
            QuestionReplacementStatus.processing.value,
        )
        db.commit()

    with SessionLocal() as db:
        config = get_config_dict(db, profile_id=question.config_profile_id)

    try:
        new_ocr_text = await ocr_pdf(
            staged_path,
            config.get("paddleocr_api_url", "") or "",
            config.get("paddleocr_token", "") or "",
        )
    except BusinessError as exc:
        message = f"新版题目 OCR 失败: {exc}"
        with SessionLocal() as db:
            question = db.get(Question, question_id, with_for_update=True)
            if question is not None:
                transition_question_replacement(
                    question, QuestionReplacementStatus.failed
                )
                question.replacement_file_path = None
                question.replacement_file_sha256 = None
                question.replacement_original_filename = None
                question.replacement_error_message = message[:1024]
                question.updated_at = utc_now_naive()
                notify_question_status(
                    db,
                    question_id,
                    question.status.value,
                    QuestionReplacementStatus.failed.value,
                )
                db.commit()
        _unlink_paths([staged_path])
        raise BusinessError(message) from exc
    except OCRError as exc:
        logger.warning(
            "新版题目 OCR 短暂失败，保留暂存文件并交由队列重试 [%s]: %s",
            question_id,
            exc,
        )
        raise

    # 新版题目 OCR 后,旧的 extracted_rubric 快照随新 OCR 作废(ocr_hash 不匹配)。
    # 首次评分时由 MCP 客户端通过 save_ai_marking_question_rubric 重新提取。
    with SessionLocal() as db:
        question = db.get(Question, question_id, with_for_update=True)
        if question is None:
            _unlink_paths([staged_path])
            return
        if (
            question.replacement_file_path != staged_path
            or question.replacement_status != QuestionReplacementStatus.processing
        ):
            # 状态在执行期间被外部改写(如用户发起新一轮替换):旧任务确定性
            # 失败,清理本任务的暂存 PDF 后抛 BusinessError 让 worker 直接
            # 删除任务而不重试。不改写 question 行,保留新一轮 pending 状态。
            _unlink_paths([staged_path])
            raise BusinessError("题目替换状态在执行期间发生变化")

        submissions = (
            (
                db.execute(
                    select(Submission)
                    .where(Submission.question_id == question_id)
                    .order_by(Submission.id)
                    .with_for_update()
                )
            )
            .scalars()
            .all()
        )
        old_paths = [question.file_path, *[sub.file_path for sub in submissions]]
        for submission in submissions:
            db.delete(submission)
        question.original_filename = staged_name
        question.file_path = staged_path
        question.file_sha256 = staged_sha256
        question.ocr_text = new_ocr_text
        question.extracted_rubric = None
        question.extracted_rubric_items = None
        question.extracted_rubric_ocr_hash = None
        question.extracted_rubric_version = None
        question.extracted_rubric_at = None
        transition_question_replacement(question, None)
        question.replacement_file_path = None
        question.replacement_file_sha256 = None
        question.replacement_original_filename = None
        question.replacement_error_message = None
        question.updated_at = utc_now_naive()
        # 切换完成,主 OCR 状态变为 ready;通知用 ready 让前端刷新题目列表。
        notify_question_status(db, question_id, "ready", None)
        db.commit()
        _unlink_paths(old_paths)
