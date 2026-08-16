"""持久化后台任务 worker。

运行方式：``python -m app.worker``。
"""

from __future__ import annotations

import asyncio
import logging
import signal
import socket
import time
import uuid
from contextlib import suppress
from pathlib import Path

from sqlalchemy import func, select

from app.core.config import settings
from app.db.session import SessionLocal, engine
from app.models.background_job import BackgroundJob, BackgroundJobType
from app.models.question import (
    Question,
    QuestionReplacementStatus,
    QuestionStatus,
)
from app.models.submission import Submission, SubmissionStatus
from app.services.cleanup import periodic_cleanup_loop
from app.services.errors import BusinessError
from app.services.marking import run_marking_pipeline
from app.services.metrics import (
    dead_jobs,
    lease_lost,
    queue_depth,
    task_duration,
    task_retries,
)
from app.services.ocr import close_client as close_ocr_client
from app.services.question_ocr import run_question_ocr
from app.services.question_replace import run_question_replace
from app.services.queue import (
    ClaimedJob,
    claim_next_job,
    complete_job,
    renew_lease,
    requeue_worker_jobs,
    retry_or_dead_letter,
)

logger = logging.getLogger(__name__)


def _refresh_queue_depth_gauge() -> None:
    """读取一次各 status 的任务数,刷新 Prometheus Gauge。

    每次 claim 前后调用一次,既能反映排队堆积,又不至于过于频繁。
    单次查询用 GROUP BY,3 行结果,开销 < 1ms。
    """
    try:
        with SessionLocal() as db:
            rows = (
                db.execute(
                    select(BackgroundJob.status, func.count())
                    .group_by(BackgroundJob.status)
                )
                .all()
            )
    except Exception:  # noqa: BLE00 - 指标采集失败不影响主流程
        logger.exception("刷新 queue_depth 指标失败")
        return
    for status_, _count in rows:
        queue_depth.labels(status=status_.value).set(_count)


async def _heartbeat(
    job: ClaimedJob,
    stopped: asyncio.Event,
    owner: asyncio.Task[None] | None,
) -> None:
    interval = max(1.0, settings.TASK_LEASE_SECONDS / 3)
    while not stopped.is_set():
        try:
            await asyncio.wait_for(stopped.wait(), timeout=interval)
            break
        except TimeoutError:
            try:
                with SessionLocal() as db:
                    renewed = renew_lease(
                        db, job.id, job.claim_token, settings.TASK_LEASE_SECONDS
                    )
            except Exception:
                logger.exception("任务续租暂时失败 [job=%s]", job.id)
                continue
            if not renewed:
                logger.warning("任务租约已丢失 [job=%s]", job.id)
                lease_lost.inc()
                stopped.set()
                if owner is not None:
                    owner.cancel()
                break


async def _mark_target_failed(job: ClaimedJob, error: str) -> None:
    staged_path: str | None = None
    with SessionLocal() as db:
        if job.job_type == BackgroundJobType.question_ocr and job.question_id:
            target = db.get(Question, job.question_id)
            if target:
                target.status = QuestionStatus.failed
                target.error_message = error[:1024]
        elif job.job_type == BackgroundJobType.question_replace and job.question_id:
            target = db.get(Question, job.question_id)
            if target:
                staged_path = target.replacement_file_path
                target.replacement_status = QuestionReplacementStatus.failed
                target.replacement_file_path = None
                target.replacement_original_filename = None
                target.replacement_error_message = error[:1024]
        elif job.job_type == BackgroundJobType.submission_ocr and job.submission_id:
            target = db.get(Submission, job.submission_id)
            if target:
                # 守卫:提交已进入教师侧终态(ready_for_review / reviewed)时
                # 不覆盖为 failed,避免重跑/死信路径抹掉已确认成绩。
                if target.status in (
                    SubmissionStatus.ready_for_review,
                    SubmissionStatus.reviewed,
                ):
                    logger.warning(
                        "跳过 failed 标记 [submission=%s]: 已处于教师侧终态",
                        job.submission_id,
                    )
                else:
                    target.status = SubmissionStatus.failed
                    target.error_message = error[:1024]
        db.commit()
    if staged_path:
        try:
            Path(staged_path).unlink(missing_ok=True)
        except OSError:
            logger.warning("清理死信题目暂存 PDF 失败 [%s]", staged_path)


async def _execute(job: ClaimedJob) -> None:
    if job.job_type == BackgroundJobType.question_ocr and job.question_id is not None:
        await run_question_ocr(job.question_id)
        return
    if (
        job.job_type == BackgroundJobType.question_replace
        and job.question_id is not None
    ):
        await run_question_replace(job.question_id)
        return
    if (
        job.job_type == BackgroundJobType.submission_ocr
        and job.submission_id is not None
    ):
        await run_marking_pipeline(job.submission_id)
        return
    raise RuntimeError(f"任务 {job.id} 缺少有效目标")


async def _run_claimed(job: ClaimedJob) -> None:
    heartbeat_stopped = asyncio.Event()
    owner = asyncio.current_task()
    heartbeat = asyncio.create_task(
        _heartbeat(job, heartbeat_stopped, owner), name=f"job-heartbeat-{job.id}"
    )
    started = time.perf_counter()
    job_type_label = job.job_type.value
    try:
        await _execute(job)
        with SessionLocal() as db:
            completed = complete_job(db, job.id, job.claim_token)
        if not completed:
            logger.warning("忽略已失去租约的任务完成结果 [job=%s]", job.id)
    except asyncio.CancelledError:
        raise
    except BusinessError as exc:
        # 执行器已把确定性业务失败写入对应业务表。worker 只负责结束
        # 队列任务，避免把错误再次包装成 "BusinessError: ..." 覆盖业务文案。
        logger.warning("业务失败,结束任务且不重试 [job=%s]: %s", job.id, exc)
        with SessionLocal() as db:
            if not complete_job(db, job.id, job.claim_token):
                logger.warning("业务失败时任务已失去租约,跳过删除 [job=%s]", job.id)
    except Exception as exc:
        logger.exception("后台任务异常 [job=%s]", job.id)
        error = f"{type(exc).__name__}: {exc}"
        with SessionLocal() as db:
            is_dead = retry_or_dead_letter(
                db,
                job,
                error=error,
                max_attempts=settings.TASK_MAX_ATTEMPTS,
            )
        if is_dead:
            dead_jobs.labels(job_type=job_type_label).inc()
            await _mark_target_failed(job, error)
        else:
            task_retries.labels(job_type=job_type_label).inc()
    finally:
        task_duration.labels(job_type=job_type_label).observe(
            time.perf_counter() - started
        )
        heartbeat_stopped.set()
        heartbeat.cancel()
        with suppress(asyncio.CancelledError):
            await heartbeat


async def run_worker() -> None:
    worker_id = f"{socket.gethostname()}-{uuid.uuid4().hex[:12]}"
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)

    concurrency = max(1, settings.TASK_CONCURRENCY)
    poll_interval = max(0.1, settings.TASK_POLL_INTERVAL_SECONDS)
    active: set[asyncio.Task[None]] = set()
    cleanup_task = asyncio.create_task(
        periodic_cleanup_loop(), name="uploads-cleanup"
    )
    logger.info(
        "任务 worker 已启动 [worker=%s, concurrency=%s]", worker_id, concurrency
    )

    try:
        while not stop.is_set():
            while len(active) < concurrency and not stop.is_set():
                with SessionLocal() as db:
                    job = claim_next_job(
                        db,
                        worker_id=worker_id,
                        lease_seconds=settings.TASK_LEASE_SECONDS,
                    )
                if job is None:
                    break
                _refresh_queue_depth_gauge()
                task = asyncio.create_task(
                    _run_claimed(job), name=f"background-job-{job.id}"
                )
                active.add(task)
                task.add_done_callback(active.discard)

            if active and len(active) >= concurrency:
                await asyncio.wait(active, return_when=asyncio.FIRST_COMPLETED)
                continue
            try:
                await asyncio.wait_for(stop.wait(), timeout=poll_interval)
            except TimeoutError:
                pass
    finally:
        logger.info("任务 worker 正在关闭 [worker=%s]", worker_id)
        cleanup_task.cancel()
        with suppress(asyncio.CancelledError):
            await cleanup_task
        for task in active:
            task.cancel()
        if active:
            await asyncio.gather(*active, return_exceptions=True)
        with SessionLocal() as db:
            count = requeue_worker_jobs(db, worker_id)
        if count:
            logger.info("已重新排队 %s 个未完成任务", count)
        await close_ocr_client()
        engine.dispose()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
