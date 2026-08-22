"""基于 PostgreSQL LISTEN/NOTIFY 的事件推送(P1 SSE 后端)。

设计要点:
- ``notify_*`` 在业务事务内 ``EXECUTE NOTIFY``,事务提交后监听方才收到,
  与状态写入保持原子语义。非 PG 后端(SQLite 测试环境)为 no-op。
- ``submission_event_stream`` / ``question_event_stream`` 是 async generator,
  供 ``StreamingResponse`` 直接消费;每条 ``yield`` 输出符合 SSE 规范的事件块。
- LISTEN 使用独立 psycopg2 连接(非 SQLAlchemy 池),AUTOCOMMIT 隔离级别,
  避免 LISTEN 长连接占用业务连接池的 ``pool_size=5`` 配额。
- 启动时先推一次当前状态,避免客户端在两次状态切换之间打开 SSE 错过事件。
- 每 15s 注释行 keepalive,避免 nginx/uvicorn 误判空闲断连。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator

import psycopg2
from fastapi import HTTPException
from psycopg2 import extensions as pg_ext
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import SessionLocal

logger = logging.getLogger(__name__)

CHANNEL_SUBMISSION = "submission_status"
CHANNEL_QUESTION = "question_status"

# SSE 客户端默认 30s 网络空闲可能被中间代理断开,15s 发一次注释行保活。
_KEEPALIVE_INTERVAL_SECONDS = 15.0
# LISTEN 轮询间隔。PostgreSQL NOTIFY 不会主动唤醒阻塞的 psycopg2 连接,
# 需要 poll() 拉取;500ms 在批改场景延迟可接受,且单连接 CPU 占用极低。
_POLL_INTERVAL_SECONDS = 0.5

# 每个 LISTEN 连接占用一条独立 psycopg2 连接,防止本地资源被大量
# SSE 客户端耗尽;达到上限时路由直接返回 503,不排队等待。
# 上限可用环境变量 MAX_SSE_CLIENTS 调整(须远小于 PG max_connections)。
_MAX_SSE_CLIENTS = settings.MAX_SSE_CLIENTS
_sse_slots = asyncio.Semaphore(_MAX_SSE_CLIENTS)


async def acquire_sse_slot() -> None:
    """原子非阻塞获取一个 SSE 客户端槽位。

    槽位满时立即抛出 503，由调用方路由转换为 HTTP 响应；槽位在
    ``*_event_stream`` 的 ``finally`` 中释放。

    不使用 ``asyncio.wait_for(acquire(), timeout=0)``：CPython 中该写法
    在信号量可用时也可能立即抛 ``TimeoutError``。信号量未锁定且无等待者时
    ``acquire()`` 同步完成、不挂起事件循环，因此「先查 locked 再 acquire」
    之间不存在可被其他协程插入的挂起点，整段保持原子。
    """
    if _sse_slots.locked():
        raise HTTPException(
            status_code=503, detail="实时事件连接数已达上限，请稍后重试"
        )
    await _sse_slots.acquire()


def _is_postgres(db: Session) -> bool:
    """判断当前 Session 绑定的引擎是否为 PostgreSQL。

    SQLite 测试环境下 NOTIFY 语句不存在,需跳过。
    """
    bind = db.get_bind()
    return bind.dialect.name == "postgresql"


def _parse_pg_dsn(url: str) -> str:
    """将 SQLAlchemy DSN 转为 psycopg2 原生 DSN。

    ``postgresql+psycopg2://...`` → ``postgresql://...``
    """
    if url.startswith("postgresql+psycopg2://"):
        return url.replace("postgresql+psycopg2://", "postgresql://", 1)
    return url


# ---------------------------------------------------------------------------
# 发送端:在业务事务内调用
# ---------------------------------------------------------------------------


def notify_submission_status(
    db: Session, submission_id: int, status: str
) -> None:
    """在当前事务内发送 submission 状态变更 NOTIFY。

    必须在 ``db.commit()`` 之前调用,NOTIFY 会随事务一起提交。
    非 PG 后端为 no-op,保证测试可运行。
    """
    if not _is_postgres(db):
        return
    payload = json.dumps({"submission_id": submission_id, "status": status})
    db.execute(
        text(f"NOTIFY {CHANNEL_SUBMISSION}, :payload"),
        {"payload": payload},
    )


def notify_question_status(
    db: Session, question_id: str, status: str
) -> None:
    """在当前事务内发送 question 状态变更 NOTIFY。"""
    if not _is_postgres(db):
        return
    payload = json.dumps({"question_id": question_id, "status": status})
    db.execute(
        text(f"NOTIFY {CHANNEL_QUESTION}, :payload"),
        {"payload": payload},
    )


# ---------------------------------------------------------------------------
# 接收端:SSE 流
# ---------------------------------------------------------------------------


def _sse_format(payload: str) -> str:
    """格式化为 SSE 事件块(data 行 + 空行结束)。"""
    return f"data: {payload}\n\n"


def _sse_keepalive() -> str:
    """SSE 注释行,客户端忽略,仅用于保活。"""
    return ": keepalive\n\n"


def _listen(dsn: str, channel: str) -> "psycopg2.extensions.connection":
    """建立独立 psycopg2 连接并执行 LISTEN。"""
    conn = psycopg2.connect(dsn)
    conn.set_isolation_level(pg_ext.ISOLATION_LEVEL_AUTOCOMMIT)
    cur = conn.cursor()
    cur.execute(f"LISTEN {channel};")
    cur.close()
    return conn


def _drain_notifies(
    conn: "psycopg2.extensions.connection",
) -> list[str]:
    """poll 一次并返回所有已收到的 notify payload 列表。"""
    try:
        conn.poll()
    except psycopg2.OperationalError:
        logger.exception("SSE LISTEN 连接异常,关闭流")
        raise
    payloads: list[str] = []
    while conn.notifies:
        notify = conn.notifies.pop(0)
        payloads.append(notify.payload)
    return payloads


async def _event_loop(
    conn: "psycopg2.extensions.connection",
    match_key: str,
    match_value: int | str,
    initial_payload: str | None,
) -> AsyncIterator[str]:
    """通用 SSE 事件循环:先推初始状态,再持续 poll NOTIFY 并过滤匹配 ID。

    匹配到的事件以 SSE data 行输出;每 ``_KEEPALIVE_INTERVAL_SECONDS``
    发一次注释行保活。客户端断开时 ``StreamingResponse`` 会调用
    ``aclose()``,``finally`` 由调用方负责关闭 conn。
    """
    if initial_payload is not None:
        yield _sse_format(initial_payload)
    last_keepalive = time.monotonic()
    while True:
        for payload in _drain_notifies(conn):
            try:
                data = json.loads(payload)
            except json.JSONDecodeError:
                continue
            if data.get(match_key) == match_value:
                yield _sse_format(payload)
        now = time.monotonic()
        if now - last_keepalive >= _KEEPALIVE_INTERVAL_SECONDS:
            yield _sse_keepalive()
            last_keepalive = now
        await asyncio.sleep(_POLL_INTERVAL_SECONDS)


async def submission_event_stream(submission_id: int) -> AsyncIterator[str]:
    """SSE 流:推送指定 submission 的状态变更事件。"""
    from app.models.submission import Submission  # 延迟导入避免循环依赖

    # 先在 SQLAlchemy Session 内读取当前状态,立即释放连接后再 yield。
    initial_payload: str | None = None
    with SessionLocal() as db:
        sub = db.get(Submission, submission_id)
        if sub is not None:
            initial_payload = json.dumps(
                {
                    "submission_id": submission_id,
                    "status": sub.status.value,
                }
            )

    conn = _listen(
        _parse_pg_dsn(settings.DATABASE_URL), CHANNEL_SUBMISSION
    )
    try:
        async for chunk in _event_loop(
            conn, "submission_id", submission_id, initial_payload
        ):
            yield chunk
    finally:
        _sse_slots.release()
        conn.close()


async def question_event_stream(question_id: str) -> AsyncIterator[str]:
    """SSE 流:推送指定 question 的状态变更事件。"""
    from app.models.question import Question  # 延迟导入避免循环依赖

    initial_payload: str | None = None
    with SessionLocal() as db:
        question = db.get(Question, question_id)
        if question is not None:
            initial_payload = json.dumps(
                {
                    "question_id": question_id,
                    "status": question.status.value,
                }
            )

    conn = _listen(_parse_pg_dsn(settings.DATABASE_URL), CHANNEL_QUESTION)
    try:
        async for chunk in _event_loop(
            conn, "question_id", question_id, initial_payload
        ):
            yield chunk
    finally:
        _sse_slots.release()
        conn.close()
