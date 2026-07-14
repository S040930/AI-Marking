"""批改任务编排服务。

将 OCR 与受约束 Agent 串联为完整流水线,通过 SubmissionStatus 状态机
记录每一步进度,任一阶段失败立即转 ``failed`` 并写入 ``error_message``。

性能/稳定性行为:
- 题目 OCR 已由题目库缓存，本流水线只识别学生作业
- 执行并发由专用任务 worker 统一控制
- 配置(API Key、Base URL、Model、Rubric)在流水线开始时一次性从数据库读取,
  通过参数注入给 OCR 与 LLM 服务,避免重复读 DB
"""

import logging

from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models.submission import Submission, SubmissionStatus
from app.services.agent import AgentError, run_marking_agent
from app.services.config import get_config_dict
from app.services.errors import BusinessError
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
    return sub


def _mark_failed(submission_id: int, message: str) -> None:
    """流水线任一阶段抛异常时,用独立会话立即落库 ``failed``。

    使用独立 SessionLocal 防止主会话因异常进入不可用状态；确定性业务失败
    随后会抛出 ``BusinessError``，由 worker 删除任务而不是进入队列级重试。
    """
    try:
        with SessionLocal() as fresh_db:
            _update_status(
                fresh_db,
                submission_id,
                SubmissionStatus.failed,
                error_message=message[:1024],
            )
    except Exception:  # noqa: BLE00
        logger.exception(
            "标记 submission 失败状态时再次异常 [submission=%s]",
            submission_id,
        )


async def _async_update_status(
    db: Session, submission_id: int, status: SubmissionStatus
) -> Submission | None:
    """适配 Agent 的异步状态回调；数据库写入本身保持同步。"""
    return _update_status(db, submission_id, status)


async def run_marking_pipeline(submission_id: int) -> None:
    """批改流水线:作业 OCR → Agent 评分与复核,带状态机。

    状态流转:pending → ocr_processing → ocr_done → agent_grading → done
    任何阶段失败:status=failed, error_message 写入。
    """
    with SessionLocal() as db:
        sub = db.get(Submission, submission_id)
        if sub is None:
            logger.error("Submission %s 不存在", submission_id)
            return

        # 一次性读取配置(API Key、Base URL、Model、Rubric 等)
        config = get_config_dict(db)
        paddleocr_api_url = config.get("paddleocr_api_url", "") or ""
        paddleocr_token = config.get("paddleocr_token", "") or ""

        # 题目已在入库时完成 OCR，此处只识别学生作业。
        _update_status(db, submission_id, SubmissionStatus.ocr_processing)
        question_ocr_text = sub.question.ocr_text if sub.question else None
        if not question_ocr_text:
            _update_status(
                db,
                submission_id,
                SubmissionStatus.failed,
                error_message="关联题目 OCR 内容不存在",
            )
            raise BusinessError("关联题目 OCR 内容不存在")
        try:
            ocr_text = await ocr_pdf(
                sub.file_path,
                paddleocr_api_url,
                paddleocr_token,
            )
        except OCRError as exc:
            _mark_failed(submission_id, f"作业 OCR 失败: {exc}")
            raise BusinessError(f"作业 OCR 失败: {exc}") from exc
        except BusinessError as exc:
            _mark_failed(submission_id, f"作业 OCR 失败: {exc}")
            raise
        except Exception as exc:  # noqa: BLE00 - 未预期异常同样需落库 failed
            _mark_failed(submission_id, f"作业 OCR 失败: {exc}")
            raise

        _update_status(
            db,
            submission_id,
            SubmissionStatus.ocr_done,
            ocr_text=ocr_text,
        )

        # === Agent 评分与复核阶段 ===
        try:
            result = await run_marking_agent(
                ocr_text,
                config,
                on_status=lambda status: _async_update_status(
                    db, submission_id, status
                ),
                question_text=question_ocr_text or "",
            )
        except AgentError as exc:
            _mark_failed(submission_id, f"Agent 评分失败: {exc}")
            raise BusinessError(f"Agent 评分失败: {exc}") from exc
        except Exception as exc:  # noqa: BLE00 - 未预期异常同样需落库 failed
            _mark_failed(submission_id, f"批改流水线异常: {exc}")
            raise

        draft = result["draft"]
        critic = result["critic"]
        # 人机协同模式:无论 Agent outcome 是 done 还是 review_required,
        # 统一置 ready_for_review,等待教师审阅。ai_suggestion 写入完整快照,
        # 供前端协同页面展示 AI 建议分与详情。
        ai_suggestion = {
            "score": float(draft["score"]),
            "max_score": float(draft["max_score"]),
            "feedback": draft["feedback"],
            "details": draft["details"],
            "confidence": float(critic["confidence"]),
            "outcome": result["outcome"],
            "review_reason": result.get("review_reason") or None,
            "critic_summary": critic.get("summary") or "",
            "critic_issues": critic.get("issues") or [],
            "agent_trace": result.get("trace", []),
        }
        _update_status(
            db,
            submission_id,
            SubmissionStatus.ready_for_review,
            score=float(draft["score"]),
            max_score=float(draft["max_score"]),
            confidence=float(critic["confidence"]),
            feedback=draft["feedback"],
            details=draft["details"],
            ai_result=draft,
            ai_suggestion=ai_suggestion,
            agent_trace=result.get("trace", []),
            review_reason=result.get("review_reason") or None,
        )
        logger.info(
            "Agent 批改结束 [submission=%s, status=ready_for_review, score=%s]",
            submission_id,
            draft["score"],
        )
