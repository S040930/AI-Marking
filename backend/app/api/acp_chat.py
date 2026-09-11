"""ACP 交互式对话 HTTP/SSE 接口。

会话进程由 API 进程内的 ``ChatSessionRegistry`` 持有(见 app/acp/chat.py);
本模块只做 HTTP 适配:建会话校验、消息转发、权限答复、SSE 回放/推送。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session, sessionmaker

from app.acp import chat_store
from app.acp.chat import (
    ChatSessionError,
    get_chat_registry,
    reconcile_orphan_chats,
)
from app.acp.domain import AcpChatStatus
from app.acp.models import AcpAgentInstallation, AcpChatSession
from app.acp.registry import AgentEntry, acp_cache_dir
from app.acp.runs import get_active_run_for_submission
from app.core.config import settings
from app.db.session import get_db, get_session_factory
from app.domain.lifecycle import SubmissionStatus
from app.models.submission import Submission
from app.schemas.acp import CodexConfigSnapshot
from app.schemas.acp_chat import (
    AcpChatConfigurationUpdateRequest,
    AcpChatCreateRequest,
    AcpChatEventOut,
    AcpChatMessageRequest,
    AcpChatPermissionRequest,
    AcpChatSessionListResponse,
    AcpChatSessionOut,
)
from app.services.events import acquire_sse_slot

router = APIRouter(prefix="/acp/chat", tags=["acp-chat"])
logger = logging.getLogger(__name__)

# 目录定义统一在 app.acp.registry.acp_cache_dir();保留模块级别名,
# 测试通过 monkeypatch _CACHE_DIR 覆盖。
_CACHE_DIR = acp_cache_dir()

# 面板可用状态:awaiting_mcp 允许进入,教师在 AI 助手里确认模型/思考强度/权限
# 档位后手动发送批改指令,由助手经 MCP 工具完成评分并保存建议。面板已不再直接
# 发起批改 run,但 run 与对话的互斥闸门保留(见 create_chat_session 与
# send_chat_message):历史 run 仍可能活跃,一旦活跃就冻结对话,避免 MCP 写工具互相踩。
_CHAT_ALLOWED_SUBMISSION_STATUSES = (
    SubmissionStatus.awaiting_mcp,
    SubmissionStatus.ready_for_review,
    SubmissionStatus.reviewed,
    SubmissionStatus.failed,
)


def _reject_if_marking(db: Session, submission_id: int) -> None:
    """批改 run 进行中时拒绝对话写操作,避免与 run 争用 MCP 写工具。

    建会话与发消息都要过这道闸:run 可能在会话创建之后才发起,仅靠建会话
    时的检查挡不住已存在的会话继续发消息。
    """
    if get_active_run_for_submission(db, submission_id) is not None:
        raise HTTPException(
            status_code=409, detail="作业存在进行中的批改运行，暂不可发起或继续对话"
        )


def _session_out(db: Session, row: AcpChatSession) -> AcpChatSessionOut:
    out = AcpChatSessionOut.model_validate(row)
    out.codex_config = _snapshot_model(row.codex_config)
    out.applied_codex_config = (
        _snapshot_model(row.applied_codex_config)
        if row.applied_codex_config is not None
        else None
    )
    out.pending_codex_config = (
        _snapshot_model(row.pending_codex_config)
        if row.pending_codex_config is not None
        else None
    )
    out.desired_config = out.codex_config
    out.applied_config = out.applied_codex_config
    out.pending_config = out.pending_codex_config
    out.effective_at = "next_turn" if row.pending_codex_config is not None else "current"
    out.latest_seq = chat_store.latest_chat_event_seq(db, row.id)
    return out


def _snapshot_dict(raw: dict | None) -> dict:
    raw = dict(raw or {})
    return {
        "model_id": raw.get("model_id"),
        "reasoning_effort": raw.get("reasoning_effort"),
        "speed_mode": raw.get("speed_mode") or "standard",
        "option_ids": dict(raw.get("option_ids") or {}),
        "catalog_version": raw.get("catalog_version"),
    }


def _snapshot_model(raw: dict | None) -> CodexConfigSnapshot:
    return CodexConfigSnapshot.model_validate(_snapshot_dict(raw))


def _require_chat(db: Session, chat_id: int) -> AcpChatSession:
    row = chat_store.get_chat_session(db, chat_id)
    if row is None:
        raise HTTPException(status_code=404, detail="对话会话不存在")
    return row


def _require_installed_entry(db: Session, agent_id: str) -> AgentEntry:
    if agent_id != "codex-acp":
        raise HTTPException(status_code=422, detail="首期仅支持 codex-acp")
    installation = db.get(AcpAgentInstallation, agent_id)
    if installation is None:
        raise HTTPException(status_code=409, detail="该助手尚未安装")
    return AgentEntry.from_dict(installation.launch_snapshot)


def _require_codex_entry(db: Session) -> AgentEntry:
    return _require_installed_entry(db, "codex-acp")


# ---------------------------------------------------------------------------
# 会话
# ---------------------------------------------------------------------------


@router.post("/sessions", response_model=AcpChatSessionOut)
async def create_chat_session(
    body: AcpChatCreateRequest, db: Session = Depends(get_db)
) -> AcpChatSessionOut:
    submission = db.get(Submission, body.submission_id)
    if submission is None:
        raise HTTPException(status_code=404, detail="作业不存在")
    if submission.status not in _CHAT_ALLOWED_SUBMISSION_STATUSES:
        raise HTTPException(
            status_code=409,
            detail="作业处于批改流程中，暂不可发起对话",
        )
    _reject_if_marking(db, body.submission_id)

    from app.api.acp import _resolve_codex_config

    config = await _resolve_codex_config(db, body.codex_config)

    from app.acp.workspace import materialize_chat_workspace

    row = chat_store.create_chat_session(
        db,
        submission_id=body.submission_id,
        agent_id="codex-acp",
        permission_mode=body.permission_mode,
        codex_config=config,
        workspace_path="",
        host_pid=os.getpid(),
    )
    # 工作区路径只由 chat ID 派生;先建行拿到 ID 再物化。
    try:
        workspace = materialize_chat_workspace(row.id, submission)
    except ValueError as exc:
        db.delete(row)
        db.commit()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    row.workspace_path = str(workspace)
    db.commit()
    db.refresh(row)
    return _session_out(db, row)


@router.get("/sessions", response_model=AcpChatSessionListResponse)
def list_chat_sessions(
    submission_id: int = Query(ge=1),
    db: Session = Depends(get_db),
) -> AcpChatSessionListResponse:
    return AcpChatSessionListResponse(
        sessions=[_session_out(db, r) for r in chat_store.list_chat_sessions(db, submission_id)]
    )


@router.get("/sessions/{chat_id}", response_model=AcpChatSessionOut)
def get_chat_session_detail(
    chat_id: int, db: Session = Depends(get_db)
) -> AcpChatSessionOut:
    return _session_out(db, _require_chat(db, chat_id))


@router.patch(
    "/sessions/{chat_id}/codex-configuration",
    response_model=AcpChatSessionOut,
)
async def update_chat_codex_configuration(
    chat_id: int,
    body: AcpChatConfigurationUpdateRequest,
    db: Session = Depends(get_db),
) -> AcpChatSessionOut:
    row = _require_chat(db, chat_id)
    if body.permission_mode is not None and body.permission_mode != row.permission_mode:
        from app.acp.chat_store import set_chat_permission_mode

        set_chat_permission_mode(db, row, body.permission_mode)
    from app.api.acp import _resolve_codex_config

    # 未提供的模型字段以会话当前值补全:单独切权限档位/思考度时
    # 不应把其余字段重置回 Agent 默认。
    current = row.codex_config or row.applied_codex_config
    provided = body.model_fields_set
    body = body.model_copy(
        update={
            key: (current or {}).get(key)
            for key in ("model_id", "reasoning_effort", "speed_mode")
            if key not in provided
        }
    )
    config = await _resolve_codex_config(
        db,
        body,
        current=current,
        allow_adjustment=True,
    )
    try:
        await get_chat_registry().update_configuration(
            chat_id, config, permission_mode=row.permission_mode
        )
    except ChatSessionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    db.expire_all()
    return _session_out(db, _require_chat(db, chat_id))


# ---------------------------------------------------------------------------
# 消息与事件
# ---------------------------------------------------------------------------


@router.post("/sessions/{chat_id}/messages", response_model=AcpChatSessionOut)
async def send_chat_message(
    chat_id: int, body: AcpChatMessageRequest, db: Session = Depends(get_db)
) -> AcpChatSessionOut:
    row = _require_chat(db, chat_id)
    if row.status in (AcpChatStatus.closed, AcpChatStatus.error):
        raise HTTPException(status_code=409, detail="会话已结束，请新建会话")
    _reject_if_marking(db, row.submission_id)
    entry = _require_codex_entry(db)

    chat_store.append_chat_event(
        db,
        chat_id,
        kind="user_message",
        payload={"text": body.text},
    )

    registry = get_chat_registry()
    try:
        await registry.send(chat_id, body.text, entry=entry)
    except ChatSessionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    db.refresh(row)
    return _session_out(db, row)


@router.get("/sessions/{chat_id}/events", response_model=list[AcpChatEventOut])
def list_chat_events(
    chat_id: int,
    after_seq: int = Query(default=0, ge=0),
    limit: int = Query(default=200, ge=1, le=500),
    db: Session = Depends(get_db),
) -> list[AcpChatEventOut]:
    _require_chat(db, chat_id)
    events = chat_store.list_chat_events_after(db, chat_id, after_seq, limit)
    return [
        AcpChatEventOut(
            seq=e.seq, kind=e.kind, payload=e.payload, created_at=e.created_at
        )
        for e in events
    ]


@router.get("/sessions/{chat_id}/stream")
async def stream_chat_events(
    chat_id: int,
    after_seq: int = Query(default=0, ge=0),
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    factory: sessionmaker = Depends(get_session_factory),
) -> StreamingResponse:
    """SSE:先回放数据库事件,再通过 PG NOTIFY 推送增量。

    SSE ``id`` 等于事件 seq;会话进入终态且读尽后发送 ``: done``。
    """
    await acquire_sse_slot()
    try:
        resume_seq = max(after_seq, int(last_event_id or 0))
    except ValueError:
        resume_seq = after_seq
    return StreamingResponse(
        _chat_event_stream(chat_id, factory, after_seq=resume_seq),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


_KEEPALIVE_SECONDS = 15.0


async def _chat_event_stream(
    chat_id: int,
    factory: sessionmaker,
    *,
    after_seq: int = 0,
):
    from app.services.events import (
        CHANNEL_ACP_CHAT,
        _drain_notifies,
        _listen,
        _parse_pg_dsn,
    )

    last_seq = after_seq
    try:
        # 1) 回放历史
        with factory() as db:
            if chat_store.get_chat_session(db, chat_id) is None:
                yield 'event: error\ndata: {"detail": "对话会话不存在"}\n\n'
                return
            while True:
                events = chat_store.list_chat_events_after(db, chat_id, last_seq, 500)
                if not events:
                    break
                for e in events:
                    last_seq = e.seq
                    yield _chat_sse_event(e.seq, e.kind, e.payload)

        # 2) LISTEN 增量(PG);非 PG/失败退化为轮询
        conn = None
        try:
            conn = await asyncio.to_thread(
                _listen, _parse_pg_dsn(settings.DATABASE_URL), CHANNEL_ACP_CHAT
            )
        except Exception:  # noqa: BLE001 - SQLite 测试环境无 NOTIFY
            conn = None

        last_keepalive = time.monotonic()
        try:
            while True:
                progressed = False
                if conn is not None:
                    for payload in await asyncio.to_thread(_drain_notifies, conn):
                        try:
                            data = json.loads(payload)
                        except (json.JSONDecodeError, TypeError):
                            continue
                        if (
                            data.get("session_id") != chat_id
                            or data.get("seq", 0) <= last_seq
                        ):
                            continue
                        last_seq = data["seq"]
                        progressed = True
                        yield _chat_sse_event(
                            data["seq"], data.get("kind", ""), data.get("payload", {})
                        )
                with factory() as db:
                    for e in chat_store.list_chat_events_after(
                        db, chat_id, last_seq, 200
                    ):
                        last_seq = e.seq
                        progressed = True
                        yield _chat_sse_event(e.seq, e.kind, e.payload)
                    row = chat_store.get_chat_session(db, chat_id)
                    if row is None:
                        # 会话已被永久删除:告知客户端流终止,避免空转报错。
                        yield ": done\n\n"
                        return
                    if (
                        row.status
                        in (AcpChatStatus.closed, AcpChatStatus.error)
                        and chat_store.latest_chat_event_seq(db, chat_id) <= last_seq
                    ):
                        yield ": done\n\n"
                        return
                if (
                    not progressed
                    and time.monotonic() - last_keepalive >= _KEEPALIVE_SECONDS
                ):
                    yield ": keepalive\n\n"
                    last_keepalive = time.monotonic()
                await asyncio.sleep(0.5 if conn is not None else 1.0)
        finally:
            if conn is not None:
                await asyncio.to_thread(conn.close)
    finally:
        from app.services.events import _sse_slots

        _sse_slots.release()


def _chat_sse_event(seq: int, kind: str, payload: dict) -> str:
    data = json.dumps(
        {"seq": seq, "kind": kind, "payload": payload}, ensure_ascii=False
    )
    return f"id: {seq}\nevent: chat_event\ndata: {data}\n\n"


# ---------------------------------------------------------------------------
# 权限 / 取消 / 关闭
# ---------------------------------------------------------------------------


@router.post("/sessions/{chat_id}/permission")
def answer_chat_permission(
    chat_id: int, body: AcpChatPermissionRequest, db: Session = Depends(get_db)
) -> dict:
    _require_chat(db, chat_id)
    registry = get_chat_registry()
    option_id = body.option_id
    if body.allow and not option_id:
        option_id = _default_allow_option(db, chat_id, body.permission_id)
    ok = registry.resolve_permission(chat_id, body.permission_id, option_id)
    if not ok:
        raise HTTPException(status_code=409, detail="权限请求不存在或已答复")
    return {"status": "running", "allowed": body.allow}


def _default_allow_option(
    db: Session, chat_id: int, permission_id: str
) -> str | None:
    """未显式给 option_id 时,取该权限请求的第一个 allow 类选项。"""
    events = chat_store.list_chat_events_after(db, chat_id, 0, 500)
    for e in reversed(events):
        if e.kind != "permission_request":
            continue
        payload = e.payload or {}
        if payload.get("permission_id") != permission_id:
            continue
        for option in payload.get("options", []):
            if str(option.get("kind") or "").startswith("allow"):
                return str(option.get("option_id") or "")
        return None
    return None


@router.post("/sessions/{chat_id}/cancel")
async def cancel_chat_turn(chat_id: int, db: Session = Depends(get_db)) -> dict:
    row = _require_chat(db, chat_id)
    registry = get_chat_registry()
    cancelled = await registry.cancel(chat_id)
    if not cancelled:
        # 没有活跃 turn:直接回 idle
        if row.status == AcpChatStatus.running:
            chat_store.transition_chat(db, row, AcpChatStatus.idle)
        return {"status": row.status.value, "cancelled": False}
    chat_store.append_chat_event(
        db, chat_id, kind="notice", payload={"text": "教师请求停止当前回合"}
    )
    return {"status": "running", "cancelled": True}


@router.delete("/sessions/{chat_id}")
async def close_chat_session(chat_id: int, db: Session = Depends(get_db)) -> dict:
    _require_chat(db, chat_id)
    registry = get_chat_registry()
    await registry.close(chat_id)
    return {"status": "closed"}


@router.delete("/sessions/{chat_id}/permanent")
async def delete_chat_session_permanently(
    chat_id: int, db: Session = Depends(get_db)
) -> dict:
    """永久删除对话:停止运行中的会话,删除会话行、事件转录与专属工作区。

    与上面的"关闭会话"不同,此操作不可恢复。
    """
    from app.acp.workspace import cleanup_chat_workspace

    row = _require_chat(db, chat_id)
    # 先停止并回收可能运行中的 ACP 会话(turn 任务、agent 进程组)。
    await get_chat_registry().close(chat_id)
    workspace_path = row.workspace_path
    chat_store.delete_chat_session(db, chat_id)
    removed = cleanup_chat_workspace(chat_id, expected_path=workspace_path or None)
    if not removed:
        logger.info("对话 %s 工作区不存在或校验未通过,跳过目录清理", chat_id)
    return {"status": "deleted"}


# ---------------------------------------------------------------------------
# lifespan 辅助(main.py 调用)
# ---------------------------------------------------------------------------


def startup_reconcile_chats(factory: sessionmaker) -> int:
    with factory() as db:
        return reconcile_orphan_chats(db, host_pid=os.getpid())
