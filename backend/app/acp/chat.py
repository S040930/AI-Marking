"""交互式对话会话运行时:API 进程内的 AcpSession 注册表。

与批改 run(``acp_worker``)的分工:
- worker 面向一次性 run(claim/lease/重试),本模块面向长驻多轮对话;
- 进程由本注册表持有;权限答复依赖进程内 ``asyncio.Future``,
  **API 必须单 worker 部署**(``uvicorn --workers 1``),
  多 worker 下教师批准会路由到错误进程返回 409;
- 权限请求转 ``permission_request`` 事件落库,SSE 推送给前端,
  教师答复经 ``POST /permission`` 唤醒进程内 ``asyncio.Future``;
- 首条消息时惰性 spawn agent,进程崩溃后下一条消息自动重建
  (支持 ``load_session`` 时恢复上下文)。
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from app.acp.chat_store import (
    ChatSessionError,
    append_chat_event,
    get_chat_session,
    mark_chat_configuration_applied,
    mark_chat_configuration_failed,
    queue_chat_configuration,
    record_agent_pid,
    record_chat_session,
    transition_chat,
)
from app.acp.domain import AcpChatStatus
from app.acp.errors import AcpError
from app.acp.events import AcpEvent, AcpEventKind, TextDeltaBuffer
from app.acp.models import AcpChatSession
from app.acp.session import AcpSession, ClientCallbacks

logger = logging.getLogger(__name__)

# 权限请求驻留上限;超时视为拒绝,agent 继续运行。
PERMISSION_DWELL_SECONDS = 600.0


class ChatCallbacks(ClientCallbacks):
    """把 ACP 回调桥接到对话会话持久化。

    - 事件经 ``TextDeltaBuffer`` 合并后落库(同 RunCallbacks);
    - 权限请求持久化为 ``permission_request`` 事件,等待注册表内的
      Future 被教师答复唤醒;超时拒绝;
    - form elicitation 直接拒绝(对话面板不使用表单检查点)。
    """

    def __init__(
        self,
        chat_id: int,
        *,
        registry: "ChatSessionRegistry",
        workspace: Path | None = None,
    ) -> None:
        self.chat_id = chat_id
        self._registry = registry
        self._buffer = TextDeltaBuffer()
        self._workspace = workspace.resolve() if workspace else None
        self.permission_mode = "ask"

    async def on_config_options(
        self, session_id: str, config_options: list[Any], *, source: str
    ) -> None:
        from app.acp.model_catalog import catalog_from_config_options
        from app.acp.registry import AgentEntry

        entry = AgentEntry(
            agent_id="codex-acp",
            registry_version=None,
            distribution="npx",
            package="@agentclientprotocol/codex-acp",
            version="runtime",
            command="",
            args=[],
            env={},
        )
        catalog = catalog_from_config_options(config_options, entry=entry)
        with self._registry.session_factory() as db:
            append_chat_event(
                db,
                self.chat_id,
                kind="configuration_agent_update",
                payload={
                    "source": source,
                    "model_id": catalog.selected_model_id,
                    "reasoning_effort": catalog.default_reasoning_effort,
                    "speed_mode": catalog.default_speed_mode,
                },
            )

    async def on_event(self, event: AcpEvent) -> None:
        if event.kind == AcpEventKind.message_delta:
            full = self._buffer.add(str(event.payload.get("text", "")))
            if not full:
                return
            merged = self._buffer.flush()
            if merged is not None:
                event = merged
        else:
            await self.flush_text()
        with self._registry.session_factory() as db:
            append_chat_event(
                db,
                self.chat_id,
                kind=event.kind.value,
                payload=event.to_db_payload()["payload"],
            )

    async def flush_text(self) -> None:
        merged = self._buffer.flush()
        if merged is None:
            return
        with self._registry.session_factory() as db:
            append_chat_event(
                db,
                self.chat_id,
                kind=merged.kind.value,
                payload=merged.to_db_payload()["payload"],
            )

    async def resolve_permission(
        self, session_id: str, tool_call: Any, options: list[Any]
    ) -> str | None:
        permission_id = uuid.uuid4().hex
        kind = str(
            getattr(getattr(tool_call, "kind", None), "value", None)
            or getattr(tool_call, "kind", "")
            or ""
        )
        title = str(getattr(tool_call, "title", "") or "")
        payload = {
            "permission_id": permission_id,
            "kind": kind,
            "title": title,
            "options": [_option_to_json(opt) for opt in options],
        }
        await self.flush_text()
        with self._registry.session_factory() as db:
            row = get_chat_session(db, self.chat_id)
            if row is not None:
                transition_chat(db, row, AcpChatStatus.waiting_permission)
            append_chat_event(
                db,
                self.chat_id,
                kind=AcpEventKind.permission_request.value,
                payload=payload,
            )
        if self.permission_mode == "auto_review":
            option_id = next(
                (
                    str(getattr(option, "option_id", "") or "")
                    for option in options
                    if str(getattr(option, "kind", "") or "").startswith("allow")
                ),
                None,
            )
            with self._registry.session_factory() as db:
                row = get_chat_session(db, self.chat_id)
                if row is not None and row.status == AcpChatStatus.waiting_permission:
                    transition_chat(db, row, AcpChatStatus.running)
                append_chat_event(
                    db,
                    self.chat_id,
                    kind=AcpEventKind.permission_resolved.value,
                    payload={
                        "permission_id": permission_id,
                        "outcome": "allowed" if option_id else "denied",
                    },
                )
            return option_id
        option_id = await self._registry.wait_permission(
            self.chat_id, permission_id
        )
        with self._registry.session_factory() as db:
            row = get_chat_session(db, self.chat_id)
            if row is not None and row.status == AcpChatStatus.waiting_permission:
                transition_chat(db, row, AcpChatStatus.running)
            append_chat_event(
                db,
                self.chat_id,
                kind=AcpEventKind.permission_resolved.value,
                payload={
                    "permission_id": permission_id,
                    "outcome": "allowed" if option_id else "denied",
                },
            )
        return option_id

    async def resolve_elicitation(
        self, session_id: str, message: str, requested_schema: Any
    ) -> dict[str, Any] | None:
        await self.flush_text()
        with self._registry.session_factory() as db:
            append_chat_event(
                db,
                self.chat_id,
                kind=AcpEventKind.notice.value,
                payload={"text": "对话面板不支持表单请求，已拒绝"},
            )
        return None


def _option_to_json(option: Any) -> dict[str, Any]:
    # SDK 反序列化后 kind 是 Literal 字符串(非 Enum),兼容两种形态。
    kind = getattr(option, "kind", None)
    kind_value = getattr(kind, "value", None) or str(kind or "")
    return {
        "option_id": str(getattr(option, "option_id", "") or ""),
        "kind": kind_value or None,
        "title": str(
            getattr(option, "name", "") or getattr(option, "title", "") or ""
        ),
    }


@dataclass
class _LiveChat:
    """注册表内一个对话会话的运行时态。"""

    chat_id: int
    callbacks: ChatCallbacks
    session: AcpSession | None = None
    acp_session_id: str | None = None
    turn_task: asyncio.Task[None] | None = None
    permissions: dict[str, asyncio.Future[str | None]] = field(default_factory=dict)

    @property
    def busy(self) -> bool:
        return self.turn_task is not None and not self.turn_task.done()


class ChatSessionRegistry:
    """API 进程内的对话会话注册表;持有 agent 进程与权限 Future。"""

    def __init__(
        self,
        *,
        permission_dwell_seconds: float = PERMISSION_DWELL_SECONDS,
    ) -> None:
        self.permission_dwell_seconds = permission_dwell_seconds
        self._live: dict[int, _LiveChat] = {}
        self._session_factory: sessionmaker | None = None

    # -- 基础 --------------------------------------------------------------

    @property
    def session_factory(self) -> sessionmaker:
        if self._session_factory is None:
            from app.db.session import get_session_factory

            self._session_factory = get_session_factory()
        return self._session_factory

    def configure(self, session_factory: sessionmaker) -> None:
        """lifespan/测试注入会话工厂;不调用则惰性取全局工厂。"""
        self._session_factory = session_factory

    def get(self, chat_id: int) -> _LiveChat | None:
        return self._live.get(chat_id)

    # -- 对外操作 ------------------------------------------------------------

    async def send(
        self,
        chat_id: int,
        text: str,
        *,
        entry: Any,
        model: Any = None,
    ) -> None:
        """发送一条消息;必要时惰性 spawn agent,然后驱动一个 turn。

        ``model`` 为 ``(config_id, value)`` 元组(经 model_catalog 解析),
        在新建 ACP 会话后经 ``session/set_config_option`` 应用。
        """
        live = self._live.get(chat_id)
        if live is None:
            live = _LiveChat(
                chat_id=chat_id,
                callbacks=ChatCallbacks(chat_id, registry=self),
            )
            self._live[chat_id] = live

        with self.session_factory() as db:
            row = get_chat_session(db, chat_id)
            if row is None:
                raise ChatSessionError("对话会话不存在")
            if row.status == AcpChatStatus.waiting_permission:
                raise ChatSessionError("会话正在等待权限批准，请先处理权限请求")
            if live.busy or row.status == AcpChatStatus.running:
                raise ChatSessionError("上一条消息仍在处理中")
            if not transition_chat(db, row, AcpChatStatus.running):
                raise ChatSessionError(
                    f"会话状态 {row.status.value} 不允许发送消息"
                )
            workspace = Path(row.workspace_path or "") if row.workspace_path else None
            live.callbacks.permission_mode = row.permission_mode

        try:
            acp_sid = await self._ensure_started(
                live,
                entry=entry,
                model=model,
                workspace=workspace,
            )
        except ChatSessionError:
            with self.session_factory() as db:
                row = get_chat_session(db, chat_id)
                if row is not None:
                    transition_chat(db, row, AcpChatStatus.idle)
            raise

        live.turn_task = asyncio.create_task(
            self._run_turn(live, acp_sid, text), name=f"acp-chat-{chat_id}"
        )

    async def _ensure_started(
        self,
        live: _LiveChat,
        *,
        entry: Any,
        model: tuple[str, str] | None = None,
        workspace: Path | None,
    ) -> str:
        """确保 agent 进程与 ACP 会话就绪;返回 acp session id。

        新建(非 load 复用)会话后应用模型选择;失败仅记录,不阻塞对话。
        """
        from app.acp.orchestrator import mcp_server_config
        from app.acp.registry import audit_snapshot
        from app.acp.security import secure_launch_spec

        if live.session is not None and live.acp_session_id:
            return live.acp_session_id

        with self.session_factory() as db:
            row = get_chat_session(db, live.chat_id)
            if row is None:
                raise ChatSessionError("对话会话不存在")
            snapshot = entry.to_dict()
            previous_sid = row.acp_session_id if row.session_resumable else None
            permission_mode = row.permission_mode
            desired_config = dict(row.pending_codex_config or row.codex_config or {})

        # 与批改 run 相同的复审:白名单身份与精确版本
        audit_snapshot(row.agent_id, snapshot)

        if workspace is None or not workspace.is_dir():
            raise ChatSessionError("会话工作区不存在，请重新创建会话")

        spec = secure_launch_spec(entry, workspace, permission_mode=permission_mode)
        session = AcpSession(spec, live.callbacks)
        mcp_servers = [mcp_server_config()]
        try:
            await session.start()
            loaded = False
            if previous_sid and session.supports_load_session:
                try:
                    await session.load_session(
                        cwd=str(workspace), session_id=previous_sid,
                        mcp_servers=mcp_servers,
                    )
                    loaded = True
                except (AcpError, ConnectionError):
                    loaded = False
            if not loaded:
                new = await session.new_session(
                    cwd=str(workspace), mcp_servers=mcp_servers
                )
                sid = new.session_id
            else:
                sid = previous_sid or ""
            if entry.agent_id == "codex-acp":
                from app.acp.model_catalog import apply_codex_selection

                try:
                    result = await apply_codex_selection(session, sid, desired_config)
                except Exception as exc:  # noqa: BLE001 - 与 _apply_pending_configuration 相同的降级语义
                    logger.info(
                        "聊天首次配置应用失败 [chat=%s]: %s", live.chat_id, exc
                    )
                    with self.session_factory() as db:
                        row = get_chat_session(db, live.chat_id)
                        if row is not None:
                            mark_chat_configuration_failed(db, row)
                            append_chat_event(
                                db,
                                live.chat_id,
                                kind="configuration_failed",
                                payload={"text": "Codex 配置未被 Agent 接受"},
                            )
                else:
                    with self.session_factory() as db:
                        row = get_chat_session(db, live.chat_id)
                        if row is not None:
                            mark_chat_configuration_applied(db, row, result.config)
                            append_chat_event(
                                db,
                                live.chat_id,
                                kind=(
                                    "configuration_adjusted"
                                    if result.adjusted
                                    else "configuration_applied"
                                ),
                                payload={"codex_config": result.config},
                            )
        except (AcpError, ConnectionError, OSError, asyncio.TimeoutError, ValueError) as exc:
            await session.close()
            message = f"agent 启动失败: {type(exc).__name__}: {exc}"
            with self.session_factory() as db:
                append_chat_event(
                    db, live.chat_id, kind=AcpEventKind.error.value,
                    payload={"text": message},
                )
            raise ChatSessionError(message) from exc

        live.session = session
        live.acp_session_id = sid
        with self.session_factory() as db:
            row = get_chat_session(db, live.chat_id)
            if row is not None:
                record_chat_session(
                    db, row, acp_session_id=sid,
                    resumable=session.supports_load_session,
                )
                record_agent_pid(db, live.chat_id, session.pid)
        return sid

    async def _run_turn(self, live: _LiveChat, acp_sid: str, text: str) -> None:
        """驱动一个 turn;结束后回到 idle(失败记录错误但保持会话可续)。"""
        chat_id = live.chat_id
        assert live.session is not None
        try:
            response = await live.session.prompt(acp_sid, text)
        except asyncio.CancelledError:
            with self.session_factory() as db:
                row = get_chat_session(db, chat_id)
                if row is not None:
                    transition_chat(db, row, AcpChatStatus.idle)
            raise
        except (AcpError, ConnectionError, OSError, asyncio.TimeoutError) as exc:
            logger.exception("ACP 对话 turn 失败 [chat=%s]", chat_id)
            message = f"{type(exc).__name__}: {exc}"
            await live.callbacks.flush_text()
            with self.session_factory() as db:
                append_chat_event(
                    db, chat_id, kind=AcpEventKind.error.value,
                    payload={"text": message},
                )
                row = get_chat_session(db, chat_id)
                if row is not None:
                    transition_chat(db, row, AcpChatStatus.idle, error=message)
            if isinstance(exc, ConnectionError):
                # 进程已死:回收注册表项,下一条消息惰性重建
                await self._teardown(live)
            return
        else:
            await live.callbacks.flush_text()
            await self._apply_pending_configuration(live, acp_sid)
            with self.session_factory() as db:
                append_chat_event(
                    db, chat_id, kind=AcpEventKind.turn_completed.value,
                    payload={"stop_reason": getattr(response, "stop_reason", None)},
                )
                row = get_chat_session(db, chat_id)
                if row is not None and row.status == AcpChatStatus.running:
                    transition_chat(db, row, AcpChatStatus.idle)

    async def _apply_pending_configuration(
        self, live: _LiveChat, acp_sid: str
    ) -> None:
        if live.session is None:
            return
        from app.acp.model_catalog import apply_codex_selection

        with self.session_factory() as db:
            row = get_chat_session(db, live.chat_id)
            desired = dict(row.pending_codex_config or {}) if row is not None else {}
        if not desired:
            return
        try:
            result = await apply_codex_selection(live.session, acp_sid, desired)
        except Exception as exc:  # noqa: BLE001 - keep the last applied snapshot
            with self.session_factory() as db:
                row = get_chat_session(db, live.chat_id)
                if row is not None:
                    mark_chat_configuration_failed(db, row)
                    append_chat_event(
                        db,
                        live.chat_id,
                        kind="configuration_failed",
                        payload={"text": "Codex 配置未被 Agent 接受"},
                    )
            logger.info("聊天配置应用失败 [chat=%s]: %s", live.chat_id, exc)
            return
        with self.session_factory() as db:
            row = get_chat_session(db, live.chat_id)
            if row is not None:
                mark_chat_configuration_applied(db, row, result.config)
                append_chat_event(
                    db,
                    live.chat_id,
                    kind=("configuration_adjusted" if result.adjusted else "configuration_applied"),
                    payload={"codex_config": result.config},
                )

    async def update_configuration(
        self, chat_id: int, config: dict[str, Any], *, permission_mode: str | None = None
    ) -> None:
        live = self._live.get(chat_id)
        with self.session_factory() as db:
            row = get_chat_session(db, chat_id)
            if row is None:
                raise ChatSessionError("对话会话不存在")
            if row.status in (AcpChatStatus.closed, AcpChatStatus.error):
                raise ChatSessionError("会话已结束，不能修改配置")
            if permission_mode is not None:
                # 审批档位每回合从会话态读取:进程内 callbacks 立即生效,
                # 进程重启后由 secure_launch_spec(CODEX_CONFIG) 复现。
                live_callbacks = live.callbacks if live is not None else None
                if live_callbacks is not None:
                    live_callbacks.permission_mode = permission_mode
            pending = row.status in (
                AcpChatStatus.running,
                AcpChatStatus.waiting_permission,
            ) or (live is not None and live.busy)
            queue_chat_configuration(db, row, config, pending=pending)
            append_chat_event(
                db,
                chat_id,
                kind="configuration_queued" if pending else "configuration_updated",
                payload={
                    "codex_config": config,
                    "effective_at": "next_turn" if pending else "current",
                },
            )
            if pending or live is None or live.session is None or not live.acp_session_id:
                return
            session = live.session
            session_id = live.acp_session_id
            desired_mode = permission_mode or row.permission_mode

        if permission_mode is not None:
            from app.acp.model_catalog import apply_codex_permission_mode

            # mode 配置项切换即时生效;失败仅记录,行内档位在下次 spawn 时兜底。
            await apply_codex_permission_mode(session, session_id, desired_mode)

        from app.acp.model_catalog import apply_codex_selection

        try:
            result = await apply_codex_selection(session, session_id, config)
        except Exception as exc:  # noqa: BLE001 - preserve the previous applied value
            with self.session_factory() as db:
                row = get_chat_session(db, chat_id)
                if row is not None:
                    mark_chat_configuration_failed(db, row)
                    append_chat_event(
                        db,
                        chat_id,
                        kind="configuration_failed",
                        payload={"text": "Codex 配置未被 Agent 接受"},
                    )
            raise ChatSessionError(f"Codex 配置应用失败: {exc}") from exc
        with self.session_factory() as db:
            row = get_chat_session(db, chat_id)
            if row is not None:
                mark_chat_configuration_applied(db, row, result.config)
                append_chat_event(
                    db,
                    chat_id,
                    kind=("configuration_adjusted" if result.adjusted else "configuration_applied"),
                    payload={"codex_config": result.config},
                )

    # -- 权限 ----------------------------------------------------------------

    async def wait_permission(self, chat_id: int, permission_id: str) -> str | None:
        """挂起等待教师答复;超时返回 None(拒绝)。"""
        live = self._live.get(chat_id)
        if live is None:
            return None
        future: asyncio.Future[str | None] = (
            asyncio.get_running_loop().create_future()
        )
        live.permissions[permission_id] = future
        try:
            return await asyncio.wait_for(
                future, timeout=self.permission_dwell_seconds
            )
        except asyncio.TimeoutError:
            with self.session_factory() as db:
                append_chat_event(
                    db, chat_id, kind=AcpEventKind.notice.value,
                    payload={"text": "权限请求等待超时，已自动拒绝"},
                )
            return None
        finally:
            live.permissions.pop(permission_id, None)

    def resolve_permission(
        self, chat_id: int, permission_id: str, option_id: str | None
    ) -> bool:
        """教师答复唤醒;返回 False 表示请求不存在或已答复。"""
        live = self._live.get(chat_id)
        if live is None:
            return False
        future = live.permissions.get(permission_id)
        if future is None or future.done():
            return False
        future.set_result(option_id)
        return True

    # -- 取消与关闭 -----------------------------------------------------------

    async def cancel(self, chat_id: int) -> bool:
        """请求取消当前 turn;协议取消后 turn 未结束时强制取消任务。

        会话保持可用,下一条消息继续在同一 ACP 会话上驱动。
        """
        live = self._live.get(chat_id)
        if live is None:
            return False
        touched = False
        if live.session is not None and live.acp_session_id:
            try:
                await live.session.cancel(live.acp_session_id)
            except Exception:  # noqa: BLE001 - 取消失败由任务兜底
                logger.debug("对话 session/cancel 失败(忽略)", exc_info=True)
            touched = True
        if live.turn_task is not None and not live.turn_task.done():
            touched = True
            try:
                await asyncio.wait_for(asyncio.shield(live.turn_task), timeout=2.0)
            except Exception:  # noqa: BLE001 - 超时/任务异常都走强制取消
                pass
            if not live.turn_task.done():
                live.turn_task.cancel()
                try:
                    await live.turn_task
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
        return touched

    async def close(self, chat_id: int) -> None:
        """关闭会话:取消 turn、回收进程、置 closed。"""
        live = self._live.get(chat_id)
        if live is not None:
            await self._teardown(live)
            with self.session_factory() as db:
                row = get_chat_session(db, chat_id)
                if row is not None and row.status not in (
                    AcpChatStatus.closed,
                    AcpChatStatus.error,
                ):
                    transition_chat(db, row, AcpChatStatus.closed)
        else:
            with self.session_factory() as db:
                row = get_chat_session(db, chat_id)
                if row is not None and row.status not in (
                    AcpChatStatus.closed,
                    AcpChatStatus.error,
                ):
                    transition_chat(db, row, AcpChatStatus.closed)

    async def shutdown(self) -> None:
        """进程退出收尾:回收全部 agent 进程,行保持当前状态(启动时 reconcile)。"""
        for chat_id in list(self._live):
            live = self._live.pop(chat_id, None)
            if live is None:
                continue
            if live.turn_task is not None and not live.turn_task.done():
                live.turn_task.cancel()
                try:
                    await live.turn_task
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
            if live.session is not None:
                await live.session.close()

    async def _teardown(self, live: _LiveChat) -> None:
        self._live.pop(live.chat_id, None)
        if live.turn_task is not None and not live.turn_task.done():
            live.turn_task.cancel()
            try:
                await live.turn_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        if live.session is not None:
            await live.session.close()
            with self.session_factory() as db:
                record_agent_pid(db, live.chat_id, None)


# ---------------------------------------------------------------------------
# 模块级单例;lifespan 注入会话工厂,测试可 reset。
# ---------------------------------------------------------------------------

_registry = ChatSessionRegistry()


def get_chat_registry() -> ChatSessionRegistry:
    return _registry


def reset_chat_registry(
    *, permission_dwell_seconds: float = PERMISSION_DWELL_SECONDS
) -> ChatSessionRegistry:
    """测试/重启用:丢弃旧注册表(不回收进程,由调用方负责)。"""
    global _registry
    _registry = ChatSessionRegistry(
        permission_dwell_seconds=permission_dwell_seconds
    )
    return _registry


def reconcile_orphan_chats(db: Session, *, host_pid: int) -> int:
    """启动清理:非本进程遗留的 running/waiting 行置 error。

    agent 子进程随旧 API 进程退出而成为孤儿;此处 best-effort 按
    ``agent_pid`` 发 SIGTERM,残留进程由系统回收。
    """
    import os
    import signal

    from app.core.time import utc_now_naive

    orphans = list(
        db.query(AcpChatSession)
        .filter(
            AcpChatSession.status.in_(
                [AcpChatStatus.running, AcpChatStatus.waiting_permission]
            ),
            (AcpChatSession.host_pid != host_pid)
            | (AcpChatSession.host_pid.is_(None)),
        )
        .all()
    )
    for row in orphans:
        if row.agent_pid:
            try:
                os.killpg(row.agent_pid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError, OSError):
                pass
        row.status = AcpChatStatus.error
        row.last_error = "API 进程重启，会话中断"
        row.closed_at = utc_now_naive()
    if orphans:
        db.commit()
    return len(orphans)
