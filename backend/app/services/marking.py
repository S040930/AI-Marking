"""批改任务编排服务。

将 OCR 与 MCP 客户端评分串联为完整流程,通过 SubmissionStatus 状态机
记录每一步进度,任一阶段失败立即转 ``failed`` 并写入 ``error_message``。

收敛为 MCP-only 后,流水线只做作业 OCR:OCR 完成后置 ``awaiting_mcp``,
由外部 MCP 客户端读取评分包并写入经服务端校验的建议;服务端不再调用
任何 LLM。

性能/稳定性行为:
- 题目 OCR 已由题目库缓存，本流水线只识别学生作业
- 执行并发由专用任务 worker 统一控制
- 配置(OCR/Rubric)在流水线开始时一次性从数据库读取,避免重复读 DB
- 每次 ``_update_status`` 调用 ``PG NOTIFY`` 推送状态变更,P1 SSE 端点订阅
  ``submission_status`` 频道,前端无需 2s 轮询。
"""

import logging

from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models.submission import (
    Submission,
    SubmissionStatus,
)
from app.services.config import get_config_dict
from app.services.errors import BusinessError
from app.services.events import notify_submission_status
from app.services.ocr import OCRError, ocr_pdf

logger = logging.getLogger(__name__)

# 流水线自有状态：worker 崩溃重跑/租约恢复时只允许在这些状态下推进。
# ready_for_review / reviewed 属于教师侧终态，重跑一旦覆盖会静默丢弃
# 教师已确认的成绩；failed 由 retry 显式转回 pending 后才能重新入队。
_PIPELINE_OWNED_STATES = frozenset(
    {
        SubmissionStatus.pending,
        SubmissionStatus.ocr_processing,
        SubmissionStatus.ocr_done,
        SubmissionStatus.awaiting_mcp,
    }
)

# 流水线任何写入都不得覆盖的教师侧状态。
_PROTECTED_FINAL_STATES = frozenset(
    {SubmissionStatus.ready_for_review, SubmissionStatus.reviewed}
)


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

    若提交已处于教师侧终态（ready_for_review / reviewed，例如重跑时
    教师已完成确认），跳过写入，避免用 failed 覆盖已确认成绩。
    """
    try:
        with SessionLocal() as fresh_db:
            if _is_final_state(fresh_db, submission_id):
                logger.warning(
                    "跳过 failed 写入 [submission=%s]: 已处于教师侧终态",
                    submission_id,
                )
                return
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


def _is_final_state(db: Session, submission_id: int) -> bool:
    """加行锁重读提交，判断是否已进入教师侧终态（禁止流水线覆盖）。"""
    current = db.get(Submission, submission_id, with_for_update=True)
    return current is not None and current.status in _PROTECTED_FINAL_STATES


def _update_status_standalone(
    submission_id: int, status: SubmissionStatus, **fields
) -> Submission | None:
    """用独立短会话写入状态迁移,提交后立即释放连接。

    供流水线各阶段使用,避免整条批改流水线在 OCR 调用期间恒持一个
    数据库连接。
    """
    with SessionLocal() as db:
        return _update_status(db, submission_id, status, **fields)


async def run_marking_pipeline(submission_id: int) -> None:
    """批改流水线:作业 OCR → awaiting_mcp,带状态机。

    状态流转:pending → ocr_processing → ocr_done → awaiting_mcp
    确定性业务失败立即转 failed；系统失败由 worker 重试。

    连接策略:每个阶段使用短生命周期 Session,读快照/写状态后立即释放;
    OCR 调用期间不持有任何数据库连接(与 question_ocr 保持一致),避免
    TASK_CONCURRENCY 个并发流水线长期占满连接池。
    """
    # 第一阶段:读快照(文件路径、题目 OCR、配置),标 ocr_processing 后释放
    with SessionLocal() as db:
        sub = db.get(Submission, submission_id)
        if sub is None:
            logger.error("Submission %s 不存在", submission_id)
            return

        # 入口守卫:worker 崩溃后租约过期被重认领时,若提交已推进到教师侧
        # 终态（或已进入其他非流水线状态）,直接返回不再重跑。不抛异常,
        # 让 worker 在 _execute 正常返回后调用 complete_job 删除僵尸任务行,
        # 避免任务被反复认领、反复空跑。
        if sub.status not in _PIPELINE_OWNED_STATES:
            logger.info(
                "跳过已离开流水线的批改任务 [submission=%s, status=%s]",
                submission_id,
                sub.status.value,
            )
            return

        # 一次性读取配置(OCR URL/Token 等)
        config = get_config_dict(db, profile_id=sub.question.config_profile_id)
        paddleocr_api_url = config.get("paddleocr_api_url", "") or ""
        paddleocr_token = config.get("paddleocr_token", "") or ""
        file_path = sub.file_path
        question_ocr_text = sub.question.ocr_text if sub.question else None

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
        logger.warning(
            "作业 OCR 短暂失败，交由队列重试 [submission=%s]: %s",
            submission_id,
            exc,
        )
        raise
    except BusinessError as exc:
        _mark_failed(submission_id, f"作业 OCR 失败: {exc}")
        raise
    except Exception:  # 系统失败保留队列重试与死信语义
        raise

    # 第三阶段:写 ocr_done → awaiting_mcp。MCP-only 收敛后所有作业都在此
    # 停止,等待外部 MCP 客户端读取评分包并写入经过服务端校验的建议。
    _update_status_standalone(
        submission_id,
        SubmissionStatus.ocr_done,
        ocr_text=ocr_text,
    )
    _update_status_standalone(submission_id, SubmissionStatus.awaiting_mcp)
    logger.info(
        "作业 OCR 完成,等待 MCP 评分 [submission=%s, status=awaiting_mcp]",
        submission_id,
    )
