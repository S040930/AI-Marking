"""题目 OCR 任务执行器。"""

import logging

from app.core.time import utc_now_naive
from app.db.session import SessionLocal
from app.models.question import Question, QuestionStatus
from app.services.config import get_config_dict
from app.services.errors import BusinessError
from app.services.events import notify_question_status
from app.services.ocr import OCRError, ocr_pdf

logger = logging.getLogger(__name__)


async def run_question_ocr(question_id: str) -> None:
    """执行一次题目 OCR；可安全地由租约恢复后重新运行。"""
    with SessionLocal() as db:
        question = db.get(Question, question_id)
        if question is None:
            return
        question.status = QuestionStatus.ocr_processing
        question.error_message = None
        notify_question_status(db, question_id, QuestionStatus.ocr_processing.value)
        db.commit()

    with SessionLocal() as db:
        config = get_config_dict(db, profile_id=question.config_profile_id)

    try:
        text = await ocr_pdf(
            question.file_path,
            config.get("paddleocr_api_url", "") or "",
            config.get("paddleocr_token", "") or "",
        )
    except BusinessError as exc:
        message = f"题目 OCR 失败: {exc}"
        with SessionLocal() as db:
            question = db.get(Question, question_id, with_for_update=True)
            if question is not None:
                question.status = QuestionStatus.failed
                question.error_message = message[:1024]
                question.updated_at = utc_now_naive()
                notify_question_status(db, question_id, QuestionStatus.failed.value)
                db.commit()
        raise BusinessError(message) from exc
    except OCRError as exc:
        logger.warning("题目 OCR 短暂失败，交由队列重试 [%s]: %s", question_id, exc)
        raise

    # rubric 提取由 MCP 客户端在首次评分时完成（save_ai_marking_question_rubric），
    # 服务端不再在 OCR 阶段调用 LLM 提取；此处只保存题目 OCR 文本。
    with SessionLocal() as db:
        question = db.get(Question, question_id, with_for_update=True)
        if question is not None:
            question.ocr_text = text
            question.status = QuestionStatus.ready
            question.error_message = None
            question.updated_at = utc_now_naive()
            notify_question_status(db, question_id, QuestionStatus.ready.value)
            db.commit()
