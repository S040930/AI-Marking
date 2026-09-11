"""统一 ACP 会话封装:启动 agent 进程、协议握手、turn 驱动与进程回收。

所有 agent(Codex/Claude/Gemini/OpenCode/假 agent)共用同一编排接口;
agent 特有内容只存在于启动命令与认证配置,不进入本模块。

超时与错误约定:
- ``AcpTimeoutError``:turn 超时(默认 30 分钟)或握手超时(默认 30s);
- ``AcpLaunchError``:进程启动失败或立即退出;
- ``AcpProtocolError``:JSON-RPC 错误响应(映射 agent 返回的错误消息);
- ``ConnectionError``:EOF/连接断开,由调用方转换为领域错误。

进程回收依赖 SDK ``spawn_agent_process`` 的 finally 语义:先关 stdin,
等待宽限期,再 terminate/kill 整个传输;``terminate()`` 额外对进程组
发信号,确保 agent 派生的子进程一并退出(启动时使用 POSIX setsid)。
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from acp import PROTOCOL_VERSION, ClientSideConnection, connect_to_agent
from acp.schema import (
    AgentCapabilities,
    InitializeResponse,
    NewSessionResponse,
    PromptResponse,
    SetSessionConfigOptionResponse,
)
from acp.transports import default_environment

from app.acp.errors import (
    AcpLaunchError,
    AcpProtocolError,
    AcpTimeoutError,
)

logger = logging.getLogger(__name__)

HANDSHAKE_TIMEOUT_SECONDS = 30.0
DEFAULT_TURN_TIMEOUT_SECONDS = 30 * 60.0
# turn 超时后的进程回收宽限期。
KILL_GRACE_SECONDS = 5.0


@dataclass(frozen=True)
class AgentLaunchSpec:
    """一次 agent 启动的完整快照;由 Registry 解析结果物化而来。"""

    agent_id: str
    command: str
    args: list[str]
    env: dict[str, str]
    cwd: str
    permission_mode: str = "ask"
    agent_version: str | None = None


class ClientCallbacks:
    """ACP 客户端回调集合,由 worker 注入;此处只定义接口形状。

    - ``on_event(event)``:归一化事件回调(session/update 流);
    - ``resolve_permission(options)``:权限请求裁决,返回 option_id 或
      None(拒绝);由教师检查点机制应答;
    - ``resolve_elicitation(message, schema)``:form elicitation 应答,
      返回内容 dict 或 None(拒绝)。
    """

    async def on_event(self, event: Any) -> None:  # pragma: no cover - 接口
        raise NotImplementedError

    async def on_config_options(
        self, session_id: str, config_options: list[Any], *, source: str
    ) -> None:  # pragma: no cover - 接口
        """接收 Agent 返回的完整配置能力快照。

        ACP 的 ``session/set_config_option`` 与 ``config_option_update``
        都会返回全量选项。默认实现保持普通 ACP 客户端兼容，Codex
        编排器可覆盖它来落库安全的能力摘要。
        """
        return None

    async def resolve_permission(
        self, session_id: str, tool_call: Any, options: list[Any]
    ) -> str | None:  # pragma: no cover - 接口
        raise NotImplementedError

    async def resolve_elicitation(
        self, session_id: str, message: str, requested_schema: Any
    ) -> dict[str, Any] | None:  # pragma: no cover - 接口
        raise NotImplementedError


class AcpSession:
    """单个 agent 子进程会话;一个 run 至多同时持有一个会话。"""

    def __init__(
        self,
        spec: AgentLaunchSpec,
        callbacks: ClientCallbacks,
        *,
        turn_timeout_seconds: float = DEFAULT_TURN_TIMEOUT_SECONDS,
    ) -> None:
        self._spec = spec
        self._callbacks = callbacks
        self._turn_timeout_seconds = turn_timeout_seconds
        self._cm: Any = None
        self._conn: ClientSideConnection | None = None
        self._process: Any = None
        self._agent_capabilities: AgentCapabilities | None = None
        self._auth_methods: list[Any] = []
        self._agent_info: str | None = None
        self._closed = False
        self._stderr_task: asyncio.Task[None] | None = None
        # 正在进行的权限/elicitation 应答等待;取消时由 cancel() 唤醒。
        self._pending_permission: asyncio.Future[str | None] | None = None
        self._pending_elicitation: asyncio.Future[dict[str, Any] | None] | None = None
        self._config_options: list[Any] = []

    # -- 生命周期 ---------------------------------------------------------

    @property
    def pid(self) -> int | None:
        """agent 子进程 PID;未启动或已回收时为 None。"""
        return self._process.pid if self._process is not None else None

    @property
    def agent_capabilities(self) -> AgentCapabilities | None:
        return self._agent_capabilities

    @property
    def auth_methods(self) -> list[Any]:
        return self._auth_methods

    @property
    def agent_info(self) -> str | None:
        return self._agent_info

    @property
    def supports_load_session(self) -> bool:
        return bool(self._agent_capabilities and self._agent_capabilities.load_session)

    @property
    def config_options(self) -> list[Any]:
        """最近一次由 Agent 确认的完整配置能力快照。"""
        return list(self._config_options)

    async def start(self) -> InitializeResponse:
        """spawn 进程并完成 initialize;失败时保证回收进程。"""
        if self._conn is not None:
            raise AcpProtocolError("会话已启动")

        # SDK 以同步回调方式调用 to_client(agent);必须返回 Client 实例本身。
        def _to_client(_agent: Any) -> Any:
            return _CallbackBridge(self, _agent)

        try:
            env = dict(default_environment())
            env.update(self._spec.env)
            self._process = await asyncio.create_subprocess_exec(
                self._spec.command,
                *self._spec.args,
                env=env,
                cwd=self._spec.cwd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
            )
            if self._process.stdin is None or self._process.stdout is None:
                raise AcpLaunchError("agent stdio 管道创建失败")
            bridge = _to_client(None)
            self._conn = connect_to_agent(
                bridge,
                self._process.stdin,
                self._process.stdout,
                use_unstable_protocol=True,
            )
            self._stderr_task = asyncio.create_task(self._drain_stderr())
        except (FileNotFoundError, PermissionError, NotADirectoryError) as exc:
            raise AcpLaunchError(
                f"无法启动 agent {self._spec.agent_id}: {self._spec.command}"
            ) from exc
        except OSError as exc:
            raise AcpLaunchError(f"启动 agent 失败: {exc}") from exc

        if self._process.returncode is not None:
            await self.close()
            raise AcpLaunchError(
                f"agent {self._spec.agent_id} 启动后立即退出"
                f" (exit={self._process.returncode})"
            )

        try:
            response: InitializeResponse = await asyncio.wait_for(
                self._conn.initialize(
                    protocol_version=PROTOCOL_VERSION,
                    client_capabilities=_client_capabilities(),
                    client_info={"name": "ai-marking", "version": "1"},
                ),
                timeout=HANDSHAKE_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError as exc:
            await self.close()
            raise AcpTimeoutError(
                f"agent {self._spec.agent_id} initialize 超时"
            ) from exc
        except ConnectionError as exc:
            await self.close()
            raise AcpLaunchError(
                f"agent {self._spec.agent_id} 在握手时断开(EOF/崩溃)"
            ) from exc
        except Exception as exc:
            await self.close()
            raise AcpProtocolError(f"agent initialize 失败: {exc}") from exc

        self._agent_capabilities = response.agent_capabilities
        self._auth_methods = list(response.auth_methods or [])
        agent_info = response.agent_info
        self._agent_info = (
            f"{agent_info.name} {agent_info.version}"
            if agent_info is not None
            else None
        )
        return response

    async def close(self) -> None:
        """回收会话:唤醒挂起的回调、关闭连接、杀掉进程组。"""
        if self._closed:
            return
        self._closed = True
        self._fail_pending_futures()
        process = self._process
        conn = self._conn
        self._cm = None
        self._conn = None
        if conn is not None:
            try:
                await conn.close()
            except Exception:  # noqa: BLE001 - 回收路径不允许抛出
                logger.debug("agent 传输关闭异常(忽略)", exc_info=True)
        if process is not None and process.returncode is None:
            await _kill_process_tree(process)
        if self._stderr_task is not None:
            self._stderr_task.cancel()
            try:
                await self._stderr_task
            except asyncio.CancelledError:
                pass
            self._stderr_task = None
        self._process = None

    async def _drain_stderr(self) -> None:
        from app.acp.events import redact_text

        if self._process is None or self._process.stderr is None:
            return
        while True:
            line = await self._process.stderr.readline()
            if not line:
                return
            logger.info(
                "ACP agent stderr [%s]: %s",
                self._spec.agent_id,
                redact_text(line.decode("utf-8", errors="replace").rstrip())[:2000],
            )

    def _fail_pending_futures(self) -> None:
        for future in (self._pending_permission, self._pending_elicitation):
            if future is not None and not future.done():
                future.set_result(None)
        self._pending_permission = None
        self._pending_elicitation = None

    # -- 认证与会话 --------------------------------------------------------

    def auth_required(self) -> bool:
        """initialize 返回了认证方式且尚未认证时为 True。"""
        return bool(self._auth_methods)

    async def authenticate(self, method_id: str) -> None:
        conn = self._require_conn()
        try:
            await conn.authenticate(method_id=method_id)
        except ConnectionError:
            raise
        except Exception as exc:
            raise AcpProtocolError(f"agent 认证失败: {exc}") from exc

    async def new_session(
        self, *, cwd: str, mcp_servers: list[dict[str, Any]] | None = None
    ) -> NewSessionResponse:
        conn = self._require_conn()
        try:
            response = await asyncio.wait_for(
                conn.new_session(
                    cwd=cwd, mcp_servers=_mcp_server_models(mcp_servers or [])
                ),
                timeout=HANDSHAKE_TIMEOUT_SECONDS,
            )
            self._set_config_options(response.config_options or [])
            await self._notify_config_options(
                response.session_id, response.config_options or [], source="session/new"
            )
            return response
        except asyncio.TimeoutError as exc:
            raise AcpTimeoutError("session/new 超时") from exc
        except ConnectionError:
            raise
        except Exception as exc:
            raise AcpProtocolError(f"session/new 失败: {exc}") from exc

    async def set_config_option(
        self,
        session_id: str,
        config_id: str,
        value: str | bool,
        *,
        strict: bool = False,
    ) -> SetSessionConfigOptionResponse | None:
        """设置会话配置项并返回 Agent 的全量最新能力。

        普通兼容调用保留旧的非严格行为；Codex 产品配置必须使用
        ``strict=True``，拒绝或缺少完整响应都会暴露为协议错误。
        """
        conn = self._require_conn()
        try:
            response = await asyncio.wait_for(
                conn.set_config_option(
                    config_id=config_id, session_id=session_id, value=value
                ),
                timeout=HANDSHAKE_TIMEOUT_SECONDS,
            )
            if response is None:
                if strict:
                    raise AcpProtocolError("Agent 未返回 session/set_config_option 配置快照")
                return None
            self._set_config_options(response.config_options or [])
            await self._notify_config_options(
                session_id,
                response.config_options or [],
                source="session/set_config_option",
            )
            return response
        except asyncio.TimeoutError as exc:
            raise AcpTimeoutError("session/set_config_option 超时") from exc
        except ConnectionError:
            raise
        except Exception as exc:
            if strict:
                if isinstance(exc, AcpProtocolError):
                    raise
                raise AcpProtocolError(f"session/set_config_option 失败: {exc}") from exc
            # 不支持 set_config_option 的 agent 不阻塞普通对话主流程。
            logger.info("set_config_option 失败(忽略): %s", exc)
            return None

    async def load_session(
        self,
        *,
        cwd: str,
        session_id: str,
        mcp_servers: list[dict[str, Any]] | None = None,
    ) -> Any:
        conn = self._require_conn()
        if not self.supports_load_session:
            return None
        try:
            response = await asyncio.wait_for(
                conn.load_session(
                    cwd=cwd,
                    session_id=session_id,
                    mcp_servers=_mcp_server_models(mcp_servers or []),
                ),
                timeout=HANDSHAKE_TIMEOUT_SECONDS,
            )
            options = getattr(response, "config_options", None) or []
            self._set_config_options(options)
            await self._notify_config_options(
                session_id, options, source="session/load"
            )
            return response
        except asyncio.TimeoutError as exc:
            raise AcpTimeoutError("session/load 超时") from exc
        except ConnectionError:
            raise
        except Exception as exc:
            raise AcpProtocolError(f"session/load 失败: {exc}") from exc

    # -- turn 驱动 ---------------------------------------------------------

    async def prompt(self, session_id: str, text: str) -> PromptResponse:
        """驱动一个 agentic turn;超时时发送 cancel 并回收进程。"""
        conn = self._require_conn()
        try:
            return await asyncio.wait_for(
                conn.prompt(
                    session_id=session_id,
                    prompt=[{"type": "text", "text": text}],
                ),
                timeout=self._turn_timeout_seconds,
            )
        except asyncio.TimeoutError as exc:
            await self._cancel_turn(session_id)
            raise AcpTimeoutError(
                f"turn 超时(>{self._turn_timeout_seconds:.0f}s),已请求取消"
            ) from exc
        except ConnectionError:
            raise
        except Exception as exc:
            raise AcpProtocolError(f"session/prompt 失败: {exc}") from exc

    async def cancel(self, session_id: str) -> None:
        conn = self._require_conn()
        await self._cancel_turn(session_id)
        assert conn is not None

    async def _cancel_turn(self, session_id: str) -> None:
        conn = self._conn
        if conn is None:
            return
        try:
            await asyncio.wait_for(
                conn.cancel(session_id=session_id), timeout=KILL_GRACE_SECONDS
            )
        except Exception:  # noqa: BLE001 - 取消失败不阻塞回收
            logger.debug("session/cancel 失败(忽略)", exc_info=True)

    # -- 回调入口(由 _CallbackBridge 调用) --------------------------------

    async def handle_session_update(self, session_id: str, update: Any) -> None:
        from app.acp.events import normalize_session_update

        if getattr(update, "session_update", None) == "config_option_update":
            options = list(getattr(update, "config_options", None) or [])
            self._set_config_options(options)
            await self._notify_config_options(
                session_id, options, source="config_option_update"
            )
            return

        for event in normalize_session_update(update):
            await self._callbacks.on_event(event)

    def _set_config_options(self, options: list[Any]) -> None:
        self._config_options = list(options)

    async def _notify_config_options(
        self, session_id: str, options: list[Any], *, source: str
    ) -> None:
        callback = getattr(self._callbacks, "on_config_options", None)
        if callback is not None:
            await callback(session_id, list(options), source=source)

    async def handle_permission_request(
        self, session_id: str, tool_call: Any, options: list[Any]
    ) -> Any:
        from acp.schema import AllowedOutcome, DeniedOutcome, RequestPermissionResponse

        future: asyncio.Future[str | None] = asyncio.get_running_loop().create_future()
        self._pending_permission = future
        try:
            option_id = await self._callbacks.resolve_permission(
                session_id, tool_call, options
            )
            return RequestPermissionResponse(
                outcome=(
                    AllowedOutcome(optionId=option_id, outcome="selected")
                    if option_id is not None
                    else DeniedOutcome(outcome="cancelled")
                )
            )
        finally:
            self._pending_permission = None

    async def handle_elicitation(
        self, session_id: str, message: str, requested_schema: Any
    ) -> Any:
        from acp.schema import AcceptElicitationResponse, DeclineElicitationResponse

        future: asyncio.Future[dict[str, Any] | None] = (
            asyncio.get_running_loop().create_future()
        )
        self._pending_elicitation = future
        try:
            content = await self._callbacks.resolve_elicitation(
                session_id, message, requested_schema
            )
            if content is None:
                return DeclineElicitationResponse(action="decline")
            return AcceptElicitationResponse(action="accept", content=content)
        finally:
            self._pending_elicitation = None

    # -- 内部 --------------------------------------------------------------

    def _require_conn(self) -> ClientSideConnection:
        if self._conn is None:
            raise AcpProtocolError("会话未启动或已关闭")
        return self._conn

    async def __aenter__(self) -> "AcpSession":
        await self.start()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()


class _CallbackBridge:
    """把 SDK 对 Client 协议的调用转回 AcpSession 的处理函数。

    SDK 通过 ``to_client(callable)`` 拿到 Client 实现;桥接层持有
    AcpSession 引用,方法签名与 SDK 期望一致(关键字透传)。
    """

    def __init__(self, session: "AcpSession", agent: Any) -> None:
        self._session = session
        self._agent = agent

    async def session_update(self, **kwargs: Any) -> None:
        await self._session.handle_session_update(
            kwargs.get("session_id", ""), kwargs.get("update")
        )

    async def request_permission(self, **kwargs: Any) -> Any:
        return await self._session.handle_permission_request(
            kwargs.get("session_id", ""),
            kwargs.get("tool_call"),
            kwargs.get("options") or [],
        )

    async def create_elicitation(self, **kwargs: Any) -> Any:
        # SDK 回调签名: create_elicitation(message=..., mode=<模型实例>);
        # session_id 从 mode 提取(session 作用域)或为空(request 作用域)。
        mode = kwargs.get("mode")
        session_id = getattr(mode, "session_id", None) or kwargs.get("session_id") or ""
        return await self._session.handle_elicitation(
            session_id,
            str(kwargs.get("message", "")),
            getattr(mode, "requested_schema", None),
        )

    # 计划中声明:不代执行文件写与终端能力;这些回调显式拒绝。
    async def write_text_file(self, **kwargs: Any) -> None:
        raise AcpProtocolError("客户端不提供文件写入能力")

    async def read_text_file(self, **kwargs: Any) -> None:
        raise AcpProtocolError("客户端不提供文件读取能力")

    async def create_terminal(self, **kwargs: Any) -> None:
        raise AcpProtocolError("客户端不提供终端能力")

    async def terminal_output(self, **kwargs: Any) -> None:
        raise AcpProtocolError("客户端不提供终端能力")

    async def release_terminal(self, **kwargs: Any) -> None:
        return None

    async def wait_for_terminal_exit(self, **kwargs: Any) -> None:
        raise AcpProtocolError("客户端不提供终端能力")

    async def kill_terminal(self, **kwargs: Any) -> None:
        return None

    async def complete_elicitation(self, **kwargs: Any) -> None:
        return None

    async def ext_method(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        raise AcpProtocolError(f"不支持扩展方法: {method}")

    async def ext_notification(self, method: str, params: dict[str, Any]) -> None:
        return None


def _client_capabilities() -> Any:
    from acp.schema import ClientCapabilities, ElicitationCapabilities

    # 仅声明 form elicitation;fs/terminal 不声明。
    return ClientCapabilities(
        elicitation=ElicitationCapabilities(form=True),
    )


def _mcp_server_models(servers: list[dict[str, Any]]) -> list[Any]:
    """把 dict 配置转为 SDK 的 McpServerStdio 模型。"""
    from acp.schema import EnvVariable, McpServerStdio

    models: list[Any] = []
    for item in servers:
        if item.get("type", "stdio") != "stdio":
            # 首期只支持 stdio;http/sse 需 agent 能力声明,后续版本开放。
            raise AcpProtocolError(f"不支持的 MCP server 类型: {item.get('type')}")
        models.append(
            McpServerStdio(
                name=str(item["name"]),
                command=str(item["command"]),
                args=[str(a) for a in item.get("args", [])],
                env=[
                    EnvVariable(name=str(k), value=str(v))
                    for k, v in (item.get("env") or {}).items()
                ],
            )
        )
    return models


async def _kill_process_tree(process: Any) -> None:
    """终止 agent 进程;启动时使用 start_new_session=True(setsid),
    agent 是独立进程组 leader,这里对进程组发信号(SIGTERM → 宽限 →
    SIGKILL),确保 agent 派生的子进程一并退出。
    """
    try:
        if process.returncode is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                return
            try:
                await asyncio.wait_for(process.wait(), timeout=KILL_GRACE_SECONDS)
            except asyncio.TimeoutError:
                os.killpg(process.pid, signal.SIGKILL)
                await process.wait()
    except (ProcessLookupError, OSError):
        pass
    except Exception:  # noqa: BLE001 - 回收路径不允许抛出
        logger.debug("agent 进程回收异常(忽略)", exc_info=True)


def workspace_root(base: Path, run_id: int) -> Path:
    """run 工作区路径只由 run ID 派生,不接收外部目录。"""
    return base / f"run-{run_id}"
