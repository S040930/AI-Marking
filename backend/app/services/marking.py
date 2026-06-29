"""批改任务编排服务。

将 OCR 与 LLM 两个阶段串联为完整流水线,通过 SubmissionStatus 状态机
记录每一步进度,任一阶段失败立即转 ``failed`` 并写入 ``error_message``。

配置(API Key、Base URL、Model、Rubric)在流水线开始时一次性从数据库读取,
通过参数注入给 OCR 与 LLM 服务,避免重复读 DB。
"""

import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models.submission import Submission, SubmissionStatus
from app.services.config import get_config_dict
from app.services.llm import LLMError, mark_submission
from app.services.ocr import OCRError, ocr_pdf

logger = logging.getLogger(__name__)


def _update_status(
    db: Session, submission_id: int, status: SubmissionStatus, **fields
) -> Submission | None:
    """更新 submission 状态与额外字段,返回更新后的对象。"""
    sub = db.get(Submission, submission_id)
    if sub is None:
        return None
    sub.status = status
    for k, v in fields.items():
        setattr(sub, k, v)
    db.commit()
    db.refresh(sub)
    return sub


async def run_marking_pipeline(submission_id: int) -> None:
    """批改流水线:OCR → LLM,带状态机。

    状态流转:pending → ocr_processing → ocr_done → llm_processing → done
    任何阶段失败:status=failed, error_message 写入。

    配置从数据库读取一次,在 OCR/LLM 调用间共享。
    """
    db = SessionLocal()
    try:
        sub = db.get(Submission, submission_id)
        if sub is None:
            logger.error("Submission %s 不存在", submission_id)
            return

        # 一次性读取配置(API Key、Base URL、Model、Rubric 等)
        config = get_config_dict(db)
        paddleocr_api_url = config.get("paddleocr_api_url", "") or ""
        paddleocr_token = config.get("paddleocr_token", "") or ""

        # === OCR 阶段 ===
        _update_status(db, submission_id, SubmissionStatus.ocr_processing)
        try:
            ocr_text = await ocr_pdf(sub.file_path, paddleocr_api_url, paddleocr_token)
        except OCRError as e:
            _update_status(
                db, submission_id, SubmissionStatus.failed, error_message=str(e)
            )
            logger.error("OCR 失败 [submission=%s]: %s", submission_id, e)
            return
        except Exception as e:
            _update_status(
                db,
                submission_id,
                SubmissionStatus.failed,
                error_message=f"OCR 未知错误: {e}",
            )
            logger.exception("OCR 未知错误 [submission=%s]", submission_id)
            return

        _update_status(db, submission_id, SubmissionStatus.ocr_done, ocr_text=ocr_text)

        # === LLM 阶段 ===
        _update_status(db, submission_id, SubmissionStatus.llm_processing)
        try:
            result = await mark_submission(ocr_text, config)
        except LLMError as e:
            _update_status(
                db, submission_id, SubmissionStatus.failed, error_message=str(e)
            )
            logger.error("LLM 失败 [submission=%s]: %s", submission_id, e)
            return
        except Exception as e:
            _update_status(
                db,
                submission_id,
                SubmissionStatus.failed,
                error_message=f"LLM 未知错误: {e}",
            )
            logger.exception("LLM 未知错误 [submission=%s]", submission_id)
            return

        _update_status(
            db,
            submission_id,
            SubmissionStatus.done,
            score=float(result.get("score", 0)),
            feedback=result.get("feedback", ""),
            details=result.get("details"),
            completed_at=datetime.now(timezone.utc),
        )
        logger.info(
            "批改完成 [submission=%s, score=%s]", submission_id, result.get("score")
        )
    finally:
        db.close()
