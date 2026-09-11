"""阶段1验收:ACP 协议内核(会话封装 + 事件归一化)。

覆盖:正常流(分块消息、工具调用、权限、elicitation)、EOF、乱码、
超时取消、文本合并窗口与脱敏。
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from app.acp.errors import AcpLaunchError, AcpProtocolError, AcpTimeoutError
from app.acp.events import (
    AcpEventKind,
    TextDeltaBuffer,
    normalize_session_update,
    redact_text,
)
from app.acp.session import AcpSession, AgentLaunchSpec, ClientCallbacks

PYTHON = sys.executable
FAKE_AGENT_MODULE = "tests.acp_fake_agent"


class RecordingCallbacks(ClientCallbacks):
    """记录事件;权限默认放行,elicitation 可配置应答。"""

    def __init__(
        self,
        *,
        permission_option: str | None = "allow",
        elicitation_content: dict | None = None,
    ) -> None:
        self.events: list = []
        self.config_updates: list[tuple[str, list]] = []
        self.permission_option = permission_option
        self.elicitation_content = elicitation_content
        self.permission_requests = 0
        self.elicitation_requests = 0
        self._permission_gate = asyncio.Event()

    async def on_event(self, event) -> None:
        self.events.append(event)
        if event.kind == AcpEventKind.checkpoint:
            self._permission_gate.set()

    async def on_config_options(self, session_id, config_options, *, source):
        self.config_updates.append((source, list(config_options)))

    async def resolve_permission(self, session_id, tool_call, options):
        self.permission_requests += 1
        return self.permission_option

    async def resolve_elicitation(self, session_id, message, requested_schema):
        self.elicitation_requests += 1
        return self.elicitation_content


def _spec(tmp_path: Path, *extra_args: str) -> AgentLaunchSpec:
    return AgentLaunchSpec(
        agent_id="fake",
        command=PYTHON,
        args=["-m", FAKE_AGENT_MODULE, *extra_args],
        env={"PYTHONPATH": str(tmp_path)},
        cwd=str(tmp_path),
    )


@pytest.fixture
def project_root() -> Path:
    """backend 目录,保证 ``-m tests.acp_fake_agent`` 可导入。"""
    return Path(__file__).parents[1]


@pytest.fixture
def callbacks() -> RecordingCallbacks:
    return RecordingCallbacks()


@pytest.fixture
async def session(project_root: Path, callbacks: RecordingCallbacks) -> AsyncIterator[AcpSession]:
    spec = AgentLaunchSpec(
        agent_id="fake",
        command=PYTHON,
        args=["-m", FAKE_AGENT_MODULE],
        env={"PYTHONPATH": str(project_root)},
        cwd=str(project_root),
    )
    session = AcpSession(spec, callbacks, turn_timeout_seconds=15)
    await session.start()
    yield session
    await session.close()


async def test_normal_flow_emits_events(session: AcpSession, callbacks: RecordingCallbacks):
    new = await session.new_session(cwd="/tmp")
    assert new.session_id.startswith("fake-session-")
    response = await session.prompt(new.session_id, "批改这份作业")
    assert response.stop_reason == "end_turn"

    kinds = [e.kind for e in callbacks.events]
    # 分块消息合并为一个 message_delta;工具 start/finish 各一条
    assert kinds.count(AcpEventKind.message_delta) >= 1
    assert AcpEventKind.tool_started in kinds
    assert AcpEventKind.tool_finished in kinds
    finished = next(e for e in callbacks.events if e.kind == AcpEventKind.tool_finished)
    assert finished.payload["status"] == "completed"
    assert callbacks.permission_requests == 1
    assert not session.auth_required()


async def test_prompt_includes_merged_text(session: AcpSession, callbacks: RecordingCallbacks):
    new = await session.new_session(cwd="/tmp")
    await session.prompt(new.session_id, "x")
    texts = [
        e.payload.get("text", "") for e in callbacks.events if e.kind == AcpEventKind.message_delta
    ]
    joined = "".join(texts)
    assert "批改" in joined and "完成" in joined


async def test_denied_permission_reaches_end(session: AcpSession):
    session._callbacks = RecordingCallbacks(permission_option=None)
    new = await session.new_session(cwd="/tmp")
    response = await session.prompt(new.session_id, "x")
    assert response.stop_reason == "end_turn"


async def test_config_option_updates_return_full_snapshot(
    project_root: Path, callbacks: RecordingCallbacks
):
    spec = AgentLaunchSpec(
        agent_id="codex-acp",
        command=PYTHON,
        args=["-m", FAKE_AGENT_MODULE, "--dynamic-config-update"],
        env={"PYTHONPATH": str(project_root)},
        cwd=str(project_root),
        agent_version="0.1.0",
    )
    config_session = AcpSession(spec, callbacks, turn_timeout_seconds=15)
    await config_session.start()
    try:
        new = await config_session.new_session(cwd=str(project_root))
        response = await config_session.set_config_option(
            new.session_id, "model", "fake-model-b", strict=True
        )
        assert response is not None
        assert any(
            getattr(option, "id", None) == "fast_mode"
            for option in response.config_options
        )
        assert any(source == "config_option_update" for source, _ in callbacks.config_updates)
        assert any(source == "session/set_config_option" for source, _ in callbacks.config_updates)
    finally:
        await config_session.close()


async def test_strict_config_rejection_is_protocol_error(
    project_root: Path, callbacks: RecordingCallbacks
):
    spec = AgentLaunchSpec(
        agent_id="codex-acp",
        command=PYTHON,
        args=["-m", FAKE_AGENT_MODULE, "--reject-config"],
        env={"PYTHONPATH": str(project_root)},
        cwd=str(project_root),
        agent_version="0.1.0",
    )
    config_session = AcpSession(spec, callbacks, turn_timeout_seconds=15)
    await config_session.start()
    try:
        new = await config_session.new_session(cwd=str(project_root))
        with pytest.raises(AcpProtocolError):
            await config_session.set_config_option(
                new.session_id, "model", "fake-model-b", strict=True
            )
    finally:
        await config_session.close()


async def test_elicitation_completed(
    project_root: Path, callbacks: RecordingCallbacks
):
    spec = AgentLaunchSpec(
        agent_id="fake",
        command=PYTHON,
        args=["-m", FAKE_AGENT_MODULE, "--emit-elicitation"],
        env={"PYTHONPATH": str(project_root)},
        cwd=str(project_root),
    )
    elicitation_session = AcpSession(spec, callbacks, turn_timeout_seconds=15)
    await elicitation_session.start()
    try:
        new = await elicitation_session.new_session(cwd="/tmp")
        await elicitation_session.prompt(new.session_id, "x")
        assert callbacks.elicitation_requests == 1
    finally:
        await elicitation_session.close()


async def test_agent_eof_maps_to_launch_error(project_root: Path, callbacks: RecordingCallbacks):
    """agent 在握手后立即退出:prompt 应得到 ConnectionError(EOF)。"""
    spec = AgentLaunchSpec(
        agent_id="fake",
        command=PYTHON,
        args=["-m", FAKE_AGENT_MODULE, "--garbage"],
        env={"PYTHONPATH": str(project_root)},
        cwd=str(project_root),
    )
    garbage_session = AcpSession(spec, callbacks, turn_timeout_seconds=5)
    with pytest.raises((AcpProtocolError, ConnectionError, AcpLaunchError)):
        await garbage_session.start()
    await garbage_session.close()


async def test_hang_prompt_times_out(project_root: Path, callbacks: RecordingCallbacks):
    spec = AgentLaunchSpec(
        agent_id="fake",
        command=PYTHON,
        args=["-m", FAKE_AGENT_MODULE, "--hang-prompt"],
        env={"PYTHONPATH": str(project_root)},
        cwd=str(project_root),
    )
    hang_session = AcpSession(spec, callbacks, turn_timeout_seconds=1)
    await hang_session.start()
    try:
        new = await hang_session.new_session(cwd="/tmp")
        with pytest.raises(AcpTimeoutError):
            await hang_session.prompt(new.session_id, "x")
    finally:
        await hang_session.close()
    # 会话关闭后进程应已回收
    assert hang_session._process is None


async def test_nonexistent_command_raises_launch_error(callbacks: RecordingCallbacks, tmp_path):
    spec = AgentLaunchSpec(
        agent_id="missing",
        command=str(tmp_path / "definitely-not-here"),
        args=[],
        env={},
        cwd=str(tmp_path),
    )
    session = AcpSession(spec, callbacks)
    with pytest.raises(AcpLaunchError):
        await session.start()
    await session.close()


# ---------------------------------------------------------------------------
# 事件归一化单元测试
# ---------------------------------------------------------------------------


def _chunk(text: str):
    from acp.schema import AgentMessageChunk, TextContentBlock

    return AgentMessageChunk(
        session_update="agent_message_chunk",
        content=TextContentBlock(type="text", text=text),
    )


def test_normalize_message_chunk():
    events = normalize_session_update(_chunk("hello"))
    assert len(events) == 1
    assert events[0].kind == AcpEventKind.message_delta
    assert events[0].payload["text"] == "hello"


def test_normalize_thought_is_hidden():
    from acp.schema import AgentThoughtChunk, TextContentBlock

    update = AgentThoughtChunk(
        session_update="agent_thought_chunk",
        content=TextContentBlock(type="text", text="secret reasoning"),
    )
    events = normalize_session_update(update)
    assert len(events) == 1
    assert events[0].kind == AcpEventKind.thought_delta
    assert "secret" not in str(events[0].payload)


def test_text_buffer_merges():
    buf = TextDeltaBuffer()
    assert buf.add("a") is False
    text = "该作业整体完成较好，" * 500  # 超过 4KB 触发合并窗口
    assert buf.add(text) is True
    event = buf.flush()
    assert event is not None
    assert event.payload["text"].startswith("a该作业整体完成较好")
    assert len(event.payload["text"]) == 1 + len(text)
    assert buf.flush() is None


def test_redact_tokens_and_paths():
    text = "token Bearer abcdef1234567890abcdef at /Users/teacher/AI-Marking/backend/x.py"
    out = redact_text(text)
    assert "abcdef1234567890" not in out
    assert "/Users/teacher" not in out
    assert "[REDACTED]" in out
    assert "[PATH]" in out
