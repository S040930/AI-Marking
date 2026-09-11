"""假 ACP agent:测试专用,实现最小 agent 方法面。

作为 STDIO 进程运行::

    python -m tests.acp_fake_agent [--garbage] [--hang-prompt] [--delay-prompt[=SECONDS]]
        [--emit-elicitation]
        [--no-permission] [--expect-model-env NAME]

行为由 argv 控制:
- 默认:initialize → new_session → prompt 输出分块消息 + 工具调用,
  发起一次权限请求(等待客户端裁决)后以 end_turn 结束;
- ``--garbage``:stdout 输出乱码(协议错误场景);
- ``--hang-prompt``:prompt 永不结束(超时取消场景);
- ``--delay-prompt[=SECONDS]``:prompt 开始前延迟指定秒数(默认 1 秒);
- ``--emit-elicitation``:prompt 中额外发起 form elicitation;
- ``--fail-tool``:工具调用以 failed 状态结束;
- ``--no-permission``:跳过权限请求(多轮纯文本对话场景);
- ``--expect-model-env NAME``:校验环境变量存在,并在输出中回显
  ``MODEL_ENV_OK:<value>`` / ``MODEL_ENV_MISSING:<name>``。

SDK 路由以 snake_case 关键字解包模型字段调用 agent 方法(如
``prompt(session_id=..., prompt=...)``);同样地,回调 client 方法使用
``session_update(session_id=..., update=...)`` 与
``request_permission(session_id=..., tool_call=..., options=...)``。
"""

from __future__ import annotations

import asyncio
import os
import sys

from acp import run_agent
from acp.schema import (
    AgentCapabilities,
    AuthenticateResponse,
    ConfigOptionUpdate,
    InitializeResponse,
    LoadSessionResponse,
    NewSessionResponse,
    PromptResponse,
    SessionConfigOptionBoolean,
    SessionConfigOptionSelect,
    SessionConfigSelectOption,
    SetSessionConfigOptionResponse,
)

_MODE = {arg for arg in sys.argv[1:] if arg.startswith("--")}

# 假模型目录:new_session 时广播;model 切换后思考强度与快速模式能力随之变化。
FAKE_MODELS = [
    {
        "id": "fake-model-a",
        "name": "Fake Model A",
        "reasoning": ("low", "medium"),
        "fast": False,
    },
    {
        "id": "fake-model-b",
        "name": "Fake Model B",
        "reasoning": ("medium", "high", "xhigh"),
        "fast": True,
    },
]


class FakeAgent:
    """实现 agent 方法面;SDK 通过 to_agent(callable) 包装。"""

    def __init__(self) -> None:
        self._client = None
        self._sessions: set[str] = set()
        self._counter = 0
        # session → 当前生效配置(set_config_option 更新)
        self._model_by_session: dict[str, str] = {}
        self._reasoning_by_session: dict[str, str] = {}
        self._fast_by_session: dict[str, bool] = {}

    async def initialize(
        self, protocol_version: int = 1, client_capabilities=None, client_info=None
    ) -> InitializeResponse:
        return InitializeResponse(
            protocolVersion=protocol_version or 1,
            agentCapabilities=AgentCapabilities(loadSession=True),
            authMethods=[],
            agentInfo={"name": "fake-agent", "version": "0.1.0"},
        )

    async def authenticate(self, method_id: str = "") -> AuthenticateResponse | None:
        return AuthenticateResponse()

    async def new_session(self, cwd: str = ".", mcp_servers=None, **kw) -> NewSessionResponse:
        self._counter += 1
        session_id = f"fake-session-{self._counter}"
        self._sessions.add(session_id)
        self._model_by_session[session_id] = "fake-model-a"
        self._reasoning_by_session[session_id] = "medium"
        self._fast_by_session[session_id] = False
        response = NewSessionResponse(sessionId=session_id)
        if "--no-config-options" not in _MODE:
            response.config_options = self._config_options(session_id)
        return response

    def _model(self, session_id: str) -> dict[str, object]:
        model_id = self._model_by_session.get(session_id, "fake-model-a")
        return next((model for model in FAKE_MODELS if model["id"] == model_id), FAKE_MODELS[0])

    def _config_options(self, session_id: str) -> list[object]:
        if "--no-config-options" in _MODE:
            return []
        model = self._model(session_id)
        options: list[object] = [
            SessionConfigOptionSelect(
                id="model",
                name="Model",
                type="select",
                category="model",
                currentValue=str(model["id"]),
                options=[
                    SessionConfigSelectOption(value=str(item["id"]), name=str(item["name"]))
                    for item in FAKE_MODELS
                ],
            ),
            SessionConfigOptionSelect(
                id="thought",
                name="Thought level",
                type="select",
                category="thought_level",
                currentValue=self._reasoning_by_session.get(session_id, "medium"),
                options=[
                    SessionConfigSelectOption(value=value, name=value.title())
                    for value in model["reasoning"]  # type: ignore[index]
                ],
            ),
        ]
        if bool(model["fast"]):
            options.append(
                SessionConfigOptionBoolean(
                    id="fast_mode",
                    name="Fast mode",
                    type="boolean",
                    currentValue=self._fast_by_session.get(session_id, False),
                )
            )
        return options

    async def load_session(self, session_id: str = "", **kw) -> LoadSessionResponse | None:
        if session_id in self._sessions:
            return LoadSessionResponse()
        return None

    async def prompt(self, session_id: str = "", prompt=None, **kw) -> PromptResponse:
        if "--hang-prompt" in _MODE:
            await asyncio.sleep(3600)
            return PromptResponse(stopReason="end_turn")
        delay_arg = next(
            (arg for arg in sys.argv[1:] if arg.startswith("--delay-prompt=")),
            None,
        )
        if "--delay-prompt" in _MODE or delay_arg is not None:
            seconds = 1.0
            if delay_arg is not None:
                try:
                    seconds = max(0.0, float(delay_arg.split("=", 1)[1]))
                except ValueError:
                    seconds = 1.0
            await asyncio.sleep(seconds)

        await self._emit(
            session_id,
            {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "批改"}},
        )
        await self._emit(
            session_id,
            {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "进行中…"}},
        )
        # 回显当前生效模型(set_config_option 选择结果)
        await self._emit(
            session_id,
            {
                "sessionUpdate": "agent_message_chunk",
                "content": {
                    "type": "text",
                    "text": f"MODEL_ACTIVE:{self._model_by_session.get(session_id, 'fake-model-a')}",
                },
            },
        )
        for name in [a for a in sys.argv[1:] if a.startswith("--expect-model-env=")]:
            env_name = name.split("=", 1)[1]
            value = os.environ.get(env_name)
            await self._emit(
                session_id,
                {
                    "sessionUpdate": "agent_message_chunk",
                    "content": {
                        "type": "text",
                        "text": (
                            f"MODEL_ENV_OK:{value}"
                            if value is not None
                            else f"MODEL_ENV_MISSING:{env_name}"
                        ),
                    },
                },
            )
        await self._emit(
            session_id,
            {
                "sessionUpdate": "tool_call",
                "toolCallId": "call-1",
                "title": "读取报告 PDF",
                "kind": "read",
                "status": "in_progress",
            },
        )
        await self._emit(
            session_id,
            {
                "sessionUpdate": "tool_call_update",
                "toolCallId": "call-1",
                "title": "读取报告 PDF",
                "status": ("failed" if "--fail-tool" in _MODE else "completed"),
                "rawOutput": "ok",
            },
        )
        if "--no-permission" not in _MODE:
            await self._client.request_permission(
                session_id=session_id,
                tool_call={"toolCallId": "call-2", "title": "写入评分建议"},
                options=[
                    {"optionId": "allow", "name": "允许", "kind": "allow_once"},
                    {"optionId": "reject", "name": "拒绝", "kind": "reject_once"},
                ],
            )
        if "--emit-elicitation" in _MODE:
            from acp.schema import ElicitationFormRequestMode

            await self._client.create_elicitation(
                message="确认运行结果一致?",
                mode=ElicitationFormRequestMode.model_validate(
                    {
                        "requestId": 42,
                        "requestedSchema": {
                            "type": "object",
                            "properties": {"verdict": {"type": "string"}},
                        },
                    }
                ),
            )
        await self._emit(
            session_id,
            {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "完成"}},
        )
        return PromptResponse(stopReason="end_turn")

    async def _emit(self, session_id: str, update: dict[str, object]) -> None:
        await self._client.session_update(session_id=session_id, update=update)

    async def cancel(self, session_id: str = "") -> None:
        return None

    async def set_session_mode(self, session_id: str = "", mode_id: str = "") -> None:
        return None

    async def set_config_option(
        self, config_id: str = "", session_id: str = "", value=None
    ) -> SetSessionConfigOptionResponse:
        if "--reject-config" in _MODE:
            raise RuntimeError("fake agent rejected configuration")
        if config_id == "model" and isinstance(value, str):
            if value not in {str(model["id"]) for model in FAKE_MODELS}:
                raise ValueError("unknown fake model")
            self._model_by_session[session_id] = value
            self._reasoning_by_session[session_id] = "medium"
            self._fast_by_session[session_id] = False
        elif config_id == "thought" and isinstance(value, str):
            supported = self._model(session_id)["reasoning"]
            if value not in supported:  # type: ignore[operator]
                raise ValueError("unsupported fake reasoning")
            self._reasoning_by_session[session_id] = value
        elif config_id == "fast_mode" and isinstance(value, bool):
            if not bool(self._model(session_id)["fast"]) and value:
                raise ValueError("fake fast mode unavailable")
            self._fast_by_session[session_id] = value
        else:
            raise ValueError(f"unknown fake config option: {config_id}")
        options = self._config_options(session_id)
        if "--dynamic-config-update" in _MODE:
            await self._client.session_update(
                session_id=session_id,
                update=ConfigOptionUpdate(
                    sessionUpdate="config_option_update", configOptions=options
                ),
            )
        return SetSessionConfigOptionResponse(configOptions=options)

    async def close_session(self, session_id: str = "") -> None:
        return None

    async def list_sessions(self, cwd: str | None = None, cursor: str | None = None):
        from acp.schema import ListSessionsResponse

        return ListSessionsResponse(sessions=[])

    async def fork_session(self, session_id: str = "", cwd: str = ".", **kw):
        raise NotImplementedError

    async def resume_session(self, session_id: str = "", cwd: str = ".", **kw):
        raise NotImplementedError


def main() -> None:
    if "--garbage" in _MODE:
        import time

        sys.stdout.write("this is not json-rpc\n")
        sys.stdout.flush()
        time.sleep(0.3)
        return

    def to_agent(client: object) -> FakeAgent:
        agent = FakeAgent()
        agent._client = client
        return agent

    asyncio.run(run_agent(to_agent))


if __name__ == "__main__":
    main()
