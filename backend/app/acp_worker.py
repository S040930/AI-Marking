"""持久化 ACP worker:领取 run、维持租约、执行、崩溃重排。

运行方式:``python -m app.acp_worker``。
与 OCR worker(``python -m app.worker``)独立,不在 FastAPI 进程内
持有 agent 子进程。
"""

from __future__ import annotations

import asyncio
import logging
import signal
import socket
import uuid
from contextlib import suppress

from app.acp import runs as run_repo
from app.acp.domain import AcpRunStatus
from app.acp.models import AcpRun
from app.acp.orchestrator import (
    LEASE_SECONDS,
    execute_run,
)
from app.acp.registry import (
    AgentEntry,
    acp_cache_dir,
    audit_snapshot,
    effective_whitelist,
)
from app.acp.workspace import cleanup_expired_workspaces, materialize_workspace
from app.db.session import SessionLocal, engine

logger = logging.getLogger(__name__)

ACP_CACHE_DIR = acp_cache_dir()


def _entry_from_snapshot(snapshot: dict) -> AgentEntry:
    entry = AgentEntry.from_dict(snapshot)
    # 用当前生效白名单复审(教师可能已增删名单):包 + 发行 + 版本身份。
    audit_snapshot(
        entry.agent_id,
        entry.to_dict(),
        whitelist=effective_whitelist(ACP_CACHE_DIR),
    )
    return entry


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


async def _run_one(run_id: int) -> None:
    claim_token = ""
    with SessionLocal() as db:
        run = db.get(AcpRun, run_id)
        if run is None:
            return
        snapshot = dict(run.agent_snapshot)
        submission_id = run.submission_id
        claim_token = run.claim_token or ""

    from app.models.submission import Submission

    heartbeat_stop = asyncio.Event()
    heartbeat_task = asyncio.create_task(
        _heartbeat(run_id, claim_token, heartbeat_stop),
        name=f"acp-heartbeat-{run_id}",
    )
    try:
        entry = _entry_from_snapshot(snapshot)
        with SessionLocal() as db:
            run = db.get(AcpRun, run_id)
            sub = db.get(Submission, submission_id)
            if run is None or sub is None:
                raise ValueError("ACP run 或 submission 不存在")
            materialize_workspace(run, sub)
            has_code = bool(sub.has_code)
            permission_mode = run.permission_mode
            db.commit()

        final = await execute_run(
            run_id,
            session_factory=SessionLocal,
            entry=entry,
            submission_id=submission_id,
            has_code=has_code,
            question_name=sub.question_name or sub.original_filename,
            claim_token=claim_token,
            permission_mode=permission_mode,
        )
        if final == AcpRunStatus.waiting_for_teacher:
            with SessionLocal() as db:
                run_repo.release_waiting_run(db, run_id, claim_token)
        logger.info("run 完成 [run=%s, status=%s]", run_id, final.value)
    except Exception as exc:
        logger.exception("ACP run 未处理异常 [run=%s]", run_id)
        with SessionLocal() as db:
            run = db.get(AcpRun, run_id)
            if run is not None:
                run_repo.transition_run(
                    db,
                    run,
                    AcpRunStatus.failed,
                    claim_token=claim_token,
                    error=f"{type(exc).__name__}: {exc}",
                )
    finally:
        heartbeat_stop.set()
        await heartbeat_task


async def run_worker() -> None:
    worker_id = f"acp-{socket.gethostname()}-{uuid.uuid4().hex[:12]}"
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)

    poll_interval = 1.0
    poll_max = 5.0
    active: set[asyncio.Task[None]] = set()
    next_recovery_scan = loop.time()
    next_cleanup = loop.time()
    logger.info("ACP worker 已启动 [worker=%s, concurrency=1]", worker_id)

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
                    logger.info("已清理 %s 个过期 ACP 工作区", deleted)
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
                        _run_one(run.id), name=f"acp-run-{run.id}"
                    )
                    active.add(task)

                    def _done(done: asyncio.Task[None]) -> None:
                        active.discard(done)
                        with suppress(asyncio.CancelledError):
                            error = done.exception()
                            if error:
                                logger.error("ACP run task 异常", exc_info=error)

                    task.add_done_callback(_done)
                    poll_interval = 1.0
                    continue

            try:
                await asyncio.wait_for(stop.wait(), timeout=poll_interval)
            except TimeoutError:
                poll_interval = min(poll_interval * 2, poll_max)
    finally:
        logger.info("ACP worker 正在关闭 [worker=%s]", worker_id)
        for task in active:
            task.cancel()
        if active:
            await asyncio.gather(*active, return_exceptions=True)
        with SessionLocal() as db:
            count = run_repo.requeue_worker_runs(db, worker_id)
        if count:
            logger.info("已重新排队 %s 个未完成 run", count)
        engine.dispose()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
