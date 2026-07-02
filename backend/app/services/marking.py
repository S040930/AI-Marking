"""批改任务编排服务。

将 OCR 与受约束 Agent 串联为完整流水线,通过 SubmissionStatus 状态机
记录每一步进度,任一阶段失败立即转 ``failed`` 并写入 ``error_message``。

性能/稳定性行为:
- OCR 阶段:题目 PDF 与作业 PDF 并行 OCR(``asyncio.gather``)。两者均成功后
  才进入 Agent 评分,因为 grade 阶段需要题目 OCR 用来推断 rubric
- 全局并发上限:``app.services.queue.get_pipeline_semaphore`` 控制同时运行的
  流水线数量;闸门满载时由调用方返回 503 + ``Retry-After``
- 配置(API Key、Base URL、Model、Rubric)在流水线开始时一次性从数据库读取,
  通过参数注入给 OCR 与 LLM 服务,避免重复读 DB
"""

import asyncio
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import AsyncSessionLocal
from app.models.submission import Submission, SubmissionStatus
from app.services.agent import AgentError, run_marking_agent
from app.services.config import get_config_dict
from app.services.ocr import OCRError, ocr_pdf
from app.services.queue import get_pipeline_semaphore

logger = logging.getLogger(__name__)


async def _update_status(
    db: AsyncSession, submission_id: int, status: SubmissionStatus, **fields
) -> Submission | None:
    """更新 submission 状态与额外字段,返回更新后的对象。"""
    sub = await db.get(Submission, submission_id)
    if sub is None:
        return None
    sub.status = status
    for k, v in fields.items():
        setattr(sub, k, v)
    await db.commit()
    return sub


async def run_marking_pipeline(submission_id: int) -> None:
    """批改流水线:OCR(并行题目+作业) → Agent 评分与复核,带状态机。

    状态流转:pending → ocr_processing → ocr_done → agent_grading → done
    任何阶段失败:status=failed, error_message 写入。

    并发行为:进入函数后立即 acquire 全局信号量,流水线全程持有,直至终态。
    这样多个并发上传会在闸门外等待,避免 OCR/LLM 服务过载。
    """
    semaphore = get_pipeline_semaphore()
    async with semaphore:
        await _run_pipeline_with_semaphore_acquired(submission_id)


async def _run_pipeline_with_semaphore_acquired(submission_id: int) -> None:
    async with AsyncSessionLocal() as db:
        sub = await db.get(Submission, submission_id)
        if sub is None:
            logger.error("Submission %s 不存在", submission_id)
            return

        # 一次性读取配置(API Key、Base URL、Model、Rubric 等)
        config = await get_config_dict(db)
        paddleocr_api_url = config.get("paddleocr_api_url", "") or ""
        paddleocr_token = config.get("paddleocr_token", "") or ""

        # === OCR 阶段(并行)===
        await _update_status(db, submission_id, SubmissionStatus.ocr_processing)

        # 题目 OCR 与作业 OCR 并行启动。任何一条失败:整个流水线立即转 failed,
        # 并 cancel 未完成的 task 避免孤儿 task(P1-2 修复)。
        # 两份文本都需要作为完整输入给 Agent(grade 阶段依赖题目识别 rubric)。
        question_task: asyncio.Task[str] | None = None
        if sub.question_file_path:
            question_task = asyncio.create_task(
                ocr_pdf(
                    sub.question_file_path,
                    paddleocr_api_url,
                    paddleocr_token,
                ),
                name="question_ocr",
            )
        submission_task = asyncio.create_task(
            ocr_pdf(
                sub.file_path,
                paddleocr_api_url,
                paddleocr_token,
            ),
            name="submission_ocr",
        )

        try:
            question_ocr_text: str | None = None
            if question_task is not None:
                question_ocr_text = await question_task
            ocr_text = await submission_task
        except OCRError as e:
            # 任何 OCR 失败:cancel 未完成的 task,避免孤儿 task 泄漏资源
            for task in (question_task, submission_task):
                if task is not None and not task.done():
                    task.cancel()
            await asyncio.gather(
                *[t for t in (question_task, submission_task) if t is not None],
                return_exceptions=True,
            )
            # 判断失败来源(题目 vs 作业),用于 error_message 精准提示
            is_question_failure = (
                question_task is not None
                and question_task.done()
                and not question_task.cancelled()
                and isinstance(question_task.exception(), OCRError)
            )
            prefix = "题目" if is_question_failure else "作业"
            await _update_status(
                db,
                submission_id,
                SubmissionStatus.failed,
                error_message=f"{prefix} OCR 失败: {e}",
            )
            logger.error("%s OCR 失败 [submission=%s]: %s", prefix, submission_id, e)
            return
        except Exception as e:
            # 未知错误:同样 cancel 未完成任务
            for task in (question_task, submission_task):
                if task is not None and not task.done():
                    task.cancel()
            await asyncio.gather(
                *[t for t in (question_task, submission_task) if t is not None],
                return_exceptions=True,
            )
            is_question_failure = (
                question_task is not None
                and question_task.done()
                and not question_task.cancelled()
                and question_task.exception() is not None
                and not isinstance(question_task.exception(), OCRError)
            )
            prefix = "题目" if is_question_failure else "作业"
            await _update_status(
                db,
                submission_id,
                SubmissionStatus.failed,
                error_message=f"{prefix} OCR 未知错误: {e}",
            )
            logger.exception("%s OCR 未知错误 [submission=%s]", prefix, submission_id)
            return

        await _update_status(
            db,
            submission_id,
            SubmissionStatus.ocr_done,
            ocr_text=ocr_text,
            question_ocr_text=question_ocr_text,
        )

        # === Agent 评分与复核阶段 ===
        try:
            result = await run_marking_agent(
                ocr_text,
                config,
                on_status=lambda status: _update_status(db, submission_id, status),
                question_text=question_ocr_text or "",
            )
        except AgentError as e:
            await _update_status(
                db, submission_id, SubmissionStatus.failed, error_message=str(e)
            )
            logger.error("Agent 失败 [submission=%s]: %s", submission_id, e)
            return
        except Exception as e:
            await _update_status(
                db,
                submission_id,
                SubmissionStatus.failed,
                error_message=f"Agent 未知错误: {e}",
            )
            logger.exception("Agent 未知错误 [submission=%s]", submission_id)
            return

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
        await _update_status(
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
