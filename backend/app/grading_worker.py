"""批改 worker:领取 run、维持租约、执行、崩溃重排。

运行方式:``python -m app.grading_worker``。
与 OCR worker(``python -m app.worker``)独立。
"""

from __future__ import annotations

import asyncio
import logging
import signal
import socket
import uuid
from contextlib import suppress

from app.agent import run_store as run_repo
from app.agent.graph.checkpointer import (
    create_postgres_checkpointer,
    thread_id_for_run,
)
from app.agent.run_store import ARTIFACT_RETENTION_DAYS
from app.agent.runner import LEASE_SECONDS, execute_run
from app.agent.workspace import cleanup_expired_workspaces, materialize_workspace
from app.db.session import SessionLocal, engine
from app.domain.grading_run import GradingRunStatus
from app.models.grading_run import GradingRun
from app.services.code_runner_runtime import sandbox_readiness

logger = logging.getLogger(__name__)


async def purge_expired_runs(
    checkpointer,
    *,
    retention_days: int = ARTIFACT_RETENTION_DAYS,
    session_factory=SessionLocal,
) -> int:
    """清理超过保留期的终态 run:删 artifacts 行 + 删 checkpointer thread。

    thread 删除失败只记日志(例如 saver 暂不可用),不阻断其余 run 的清理。
    """
    with session_factory() as db:
        run_ids = run_repo.expired_terminal_run_ids(db, retention_days=retention_days)
    if not run_ids:
        return 0
    for run_id in run_ids:
        try:
            await checkpointer.adelete_thread(thread_id_for_run(run_id))
        except Exception:  # noqa: BLE001 - 单个 thread 失败不应中断整批
            logger.warning("删除 checkpointer thread 失败 [run=%s]", run_id, exc_info=True)
    with session_factory() as db:
        artifacts = run_repo.delete_run_artifacts(db, run_ids)
    logger.info("已清理 %s 个过期 run 的工件(共 %s 条)", len(run_ids), artifacts)
    return len(run_ids)


async def _heartbeat(run_id: int, claim_token: str, stopped: asyncio.Event) -> None:
    interval = max(1.0, LEASE_SECONDS / 3)
    while not stopped.is_set():
        try:
            await asyncio.wait_for(stopped.wait(), timeout=interval)
            break
        except TimeoutError:
            with SessionLocal() as db:
                renewed = run_repo.renew_lease(db, run_id, claim_token, LEASE_SECONDS)
            if not renewed:
                logger.warning("run 租约已丢失 [run=%s]", run_id)
                stopped.set()
                break


async def _run_one(run_id: int, checkpointer=None) -> None:
    claim_token = ""
    with SessionLocal() as db:
        run = db.get(GradingRun, run_id)
        if run is None:
            return
        submission_id = run.submission_id
        claim_token = run.claim_token or ""

    from app.models.submission import Submission

    heartbeat_stop = asyncio.Event()
    heartbeat_task = asyncio.create_task(
        _heartbeat(run_id, claim_token, heartbeat_stop),
        name=f"grading-heartbeat-{run_id}",
    )
    try:
        with SessionLocal() as db:
            run = db.get(GradingRun, run_id)
            sub = db.get(Submission, submission_id)
            if run is None or sub is None:
                raise ValueError("批改 run 或 submission 不存在")
            run = run_repo._worker_run(db, run, claim_token)
            materialize_workspace(run, sub)
            db.commit()

        final = await execute_run(
            run_id,
            session_factory=SessionLocal,
            submission_id=submission_id,
            question_name=sub.question_name or sub.original_filename,
            claim_token=claim_token,
            checkpointer=checkpointer,
        )
        if final == GradingRunStatus.waiting_for_teacher:
            with SessionLocal() as db:
                run_repo.release_waiting_run(db, run_id, claim_token)
        logger.info("run 完成 [run=%s, status=%s]", run_id, final.value)
    except Exception as exc:
        logger.exception("批改 run 未处理异常 [run=%s]", run_id)
        with SessionLocal() as db:
            run = db.get(GradingRun, run_id)
            if run is not None:
                run_repo.transition_run(
                    db,
                    run,
                    GradingRunStatus.failed,
                    claim_token=claim_token,
                    error=f"{type(exc).__name__}: {exc}",
                )
    finally:
        heartbeat_stop.set()
        await heartbeat_task


async def run_worker() -> None:
    worker_id = f"grading-{socket.gethostname()}-{uuid.uuid4().hex[:12]}"
    sandbox_ready, sandbox_detail = sandbox_readiness()
    if not sandbox_ready:
        logger.warning(
            "代码自动测试运行环境未就绪：%s 代码批改将 fail-closed。",
            sandbox_detail,
        )
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    checkpointer = await create_postgres_checkpointer()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)

    poll_interval = 1.0
    poll_max = 5.0
    active: set[asyncio.Task[None]] = set()
    next_recovery_scan = loop.time()
    next_cleanup = loop.time()
    logger.info("批改 worker 已启动 [worker=%s, concurrency=1]", worker_id)

    try:
        while not stop.is_set():
            # 崩溃重排:低频扫描租约过期
            if loop.time() >= next_recovery_scan:
                with SessionLocal() as db:
                    requeued = run_repo.requeue_expired_leases(db)
                if requeued:
                    logger.warning("已重排 %s 个租约过期 run", requeued)
                next_recovery_scan = loop.time() + 30.0
            if loop.time() >= next_cleanup:
                deleted = cleanup_expired_workspaces()
                if deleted:
                    logger.info("已清理 %s 个过期批改工作区", deleted)
                with suppress(Exception):
                    await purge_expired_runs(checkpointer.saver)
                next_cleanup = loop.time() + 3600.0

            if not active:
                with SessionLocal() as db:
                    run = run_repo.claim_next_run(
                        db,
                        worker_id=worker_id,
                        lease_seconds=LEASE_SECONDS,
                        max_concurrent=1,
                    )
                if run is not None:
                    task = asyncio.create_task(
                        _run_one(run.id, checkpointer.saver),
                        name=f"grading-run-{run.id}",
                    )
                    active.add(task)

                    def _done(done: asyncio.Task[None]) -> None:
                        active.discard(done)
                        with suppress(asyncio.CancelledError):
                            error = done.exception()
                            if error:
                                logger.error("批改 run task 异常", exc_info=error)

                    task.add_done_callback(_done)
                    poll_interval = 1.0
                    continue

            try:
                await asyncio.wait_for(stop.wait(), timeout=poll_interval)
            except TimeoutError:
                poll_interval = min(poll_interval * 2, poll_max)
    finally:
        logger.info("批改 worker 正在关闭 [worker=%s]", worker_id)
        for task in active:
            task.cancel()
        if active:
            await asyncio.gather(*active, return_exceptions=True)
        with SessionLocal() as db:
            count = run_repo.requeue_worker_runs(db, worker_id)
        if count:
            logger.info("已重新排队 %s 个未完成 run", count)
        await checkpointer.aclose()
        engine.dispose()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
