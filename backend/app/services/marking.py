"""批改任务编排服务。

将 OCR 与受约束 Agent 串联为完整流水线,通过 SubmissionStatus 状态机
记录每一步进度,任一阶段失败立即转 ``failed`` 并写入 ``error_message``。

性能/稳定性行为:
- 题目 OCR 已由题目库缓存，本流水线只识别学生作业
- 执行并发由专用任务 worker 统一控制
- 配置(API Key、Base URL、Model、Rubric)在流水线开始时一次性从数据库读取,
  通过参数注入给 OCR 与 LLM 服务,避免重复读 DB
- 每次 ``_update_status`` 调用 ``PG NOTIFY`` 推送状态变更,P1 SSE 端点订阅
  ``submission_status`` 频道,前端无需 2s 轮询。
"""

import logging

from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models.submission import (
    Submission,
    SubmissionGradingMode,
    SubmissionStatus,
)
from app.services.agent import AgentError, run_marking_agent
from app.services.config import get_config_dict
from app.services.errors import BusinessError
from app.services.events import notify_submission_status
from app.services.ocr import OCRError, ocr_pdf
from app.services.rubric import resolve_rubric

logger = logging.getLogger(__name__)


def _update_status(
    db: Session, submission_id: int, status: SubmissionStatus, **fields
) -> Submission | None:
    """更新 submission 状态与额外字段,返回更新后的对象。

    在同一事务内发送 PG NOTIFY,事务提交后监听方(P1 SSE)立即收到。
    非 PG 后端(SQLite 测试环境)为 no-op。
    """
    sub = db.get(Submission, submission_id, with_for_update=True)
    if sub is None:
        return None
    sub.status = status
    for k, v in fields.items():
        setattr(sub, k, v)
    notify_submission_status(db, submission_id, status.value)
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


def _update_status_standalone(
    submission_id: int, status: SubmissionStatus, **fields
) -> Submission | None:
    """用独立短会话写入状态迁移,提交后立即释放连接。

    供 Agent 状态回调与流水线各阶段使用,避免整条批改流水线
    在 OCR / LLM 调用期间恒持一个数据库连接。
    """
    with SessionLocal() as db:
        return _update_status(db, submission_id, status, **fields)


async def _async_update_status(
    submission_id: int, status: SubmissionStatus
) -> Submission | None:
    """适配 Agent 的异步状态回调;每次回调独立短会话,不持长连接。"""
    return _update_status_standalone(submission_id, status)


async def run_marking_pipeline(submission_id: int) -> None:
    """批改流水线:作业 OCR → Agent 评分与复核,带状态机。

    状态流转:pending → ocr_processing → ocr_done → agent_grading → done
    任何阶段失败:status=failed, error_message 写入。

    连接策略:每个阶段使用短生命周期 Session,读快照/写状态后立即释放;
    OCR 与 Agent LLM 调用期间不持有任何数据库连接(与 question_ocr /
    question_replace 保持一致),避免 TASK_CONCURRENCY 个并发流水线
    长期占满连接池。
    """
    # 第一阶段:读快照(文件路径、题目 OCR、配置),标 ocr_processing 后释放
    with SessionLocal() as db:
        sub = db.get(Submission, submission_id)
        if sub is None:
            logger.error("Submission %s 不存在", submission_id)
            return

        # 一次性读取配置(API Key、Base URL、Model、Rubric 等)
        config = get_config_dict(db, profile_id=sub.question.config_profile_id)
        paddleocr_api_url = config.get("paddleocr_api_url", "") or ""
        paddleocr_token = config.get("paddleocr_token", "") or ""
        file_path = sub.file_path
        question_ocr_text = sub.question.ocr_text if sub.question else None
        resolved_rubric = resolve_rubric(
            sub.question,
            config,
        )
        config = {**config, "_resolved_rubric": resolved_rubric}
        review_enabled = (
            sub.review_enabled
            if sub.review_enabled is not None
            else (config.get("review_enabled", "true") or "true").lower() == "true"
        )

        # 题目已在入库时完成 OCR，此处只识别学生作业。
        _update_status(db, submission_id, SubmissionStatus.ocr_processing)
        if not question_ocr_text:
            _update_status(
                db,
                submission_id,
                SubmissionStatus.failed,
                error_message="关联题目 OCR 内容不存在",
            )
            raise BusinessError("关联题目 OCR 内容不存在")

    # 第二阶段:作业 OCR(不持有任何数据库连接)
    try:
        ocr_text = await ocr_pdf(
            file_path,
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

    # 第三阶段:写 ocr_done,并重新读取 grading_mode(避免复用期间可能
    # 被 retry/重置的过期 ORM 对象)
    with SessionLocal() as db:
        current = db.get(Submission, submission_id)
        if current is None:
            logger.error("Submission %s 不存在", submission_id)
            return
        grading_mode = current.grading_mode
        _update_status(
            db,
            submission_id,
            SubmissionStatus.ocr_done,
            ocr_text=ocr_text,
        )

        # Codex 模式在 OCR 完成后停止，等待外部 Codex MCP 读取上下文并
        # 写入经过服务端校验的评分建议。不要在这里读取或调用后端 LLM。
        if grading_mode == SubmissionGradingMode.codex:
            _update_status(
                db,
                submission_id,
                SubmissionStatus.awaiting_codex,
            )
            logger.info(
                "Codex 批改等待中 [submission=%s, status=awaiting_codex]",
                submission_id,
            )
            return

    # === Agent 评分与复核阶段(状态回调走独立短会话) ===
    try:
        result = await run_marking_agent(
            ocr_text,
            config,
            on_status=lambda status: _async_update_status(submission_id, status),
            question_text=question_ocr_text or "",
            review_enabled=review_enabled,
            cached_rubric="",
        )
    except AgentError as exc:
        _mark_failed(submission_id, f"Agent 评分失败: {exc}")
        raise BusinessError(f"Agent 评分失败: {exc}") from exc
    except Exception as exc:  # noqa: BLE00 - 未预期异常同样需落库 failed
        _mark_failed(submission_id, f"批改流水线异常: {exc}")
        raise

    draft = result["draft"]
    # 复核关闭时图中跳过 critic,此时 critic 可能缺失,需兼容处理
    critic = result.get("critic")
    critic_dict = critic or {}
    # 人机协同模式:无论 Agent outcome 是 done 还是 review_required,
    # 统一置 ready_for_review,等待教师审阅。ai_suggestion 写入完整快照,
    # 供前端协同页面展示 AI 建议分与详情。
    ai_suggestion = {
        "score": float(draft["score"]),
        "max_score": float(draft["max_score"]),
        "feedback": draft["feedback"],
        "details": draft["details"],
        "confidence": float(critic_dict["confidence"]) if critic_dict else 0.0,
        "outcome": result.get("outcome"),
        "review_reason": result.get("review_reason") or None,
        "critic_summary": (critic_dict.get("summary") if critic_dict else "") or "",
        "critic_issues": (critic_dict.get("issues") if critic_dict else []) or [],
        "agent_trace": result.get("trace", []),
        "rubric_source": resolved_rubric.source,
        "rubric_snapshot_id": resolved_rubric.snapshot_id,
        "rubric_snapshot": resolved_rubric.text,
    }

    # 第四阶段:最终评分写入 ready_for_review(短会话,立即提交释放)
    with SessionLocal() as db:
        _update_status(
            db,
            submission_id,
            SubmissionStatus.ready_for_review,
            score=float(draft["score"]),
            max_score=float(draft["max_score"]),
            confidence=float(critic_dict["confidence"]) if critic_dict else 0.0,
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
