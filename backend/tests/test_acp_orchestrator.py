"""阶段4验收:权限策略、编排器执行链与教师检查点续跑。

以假 agent(STDIO 子进程)驱动完整 ``execute_run``:
- 正常无代码作业:turn 完成 → submission 仍 awaiting_mcp → 驻留;
- 权限请求:本地白名单裁决并落 notice 事件;
- form elicitation:进入 waiting_for_teacher,教师答复注入续跑;
- agent 不可启动:run 标记 failed。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.acp import runs as run_repo
from app.acp.domain import AcpRunStatus
from app.acp.errors import AcpProtocolError
from app.acp.models import AcpRun
from app.acp.orchestrator import (
    _resolve_permission_locally,
    build_grading_prompt,
    execute_run,
)
from app.acp.registry import AgentEntry
from app.db.base import Base
from app.models.question import Question, QuestionStatus
from app.models.submission import Submission, SubmissionStatus

PYTHON = sys.executable
BACKEND = Path(__file__).parents[1]


@pytest.fixture
def factory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        db.add(
            Question(
                id="q1",
                name="题目1",
                original_filename="q1.pdf",
                file_path="uploads/q1.pdf",
                status=QuestionStatus.ready,
            )
        )
        db.add(
            Submission(
                original_filename="s1.pdf",
                file_path="uploads/s1.pdf",
                question_id="q1",
                status=SubmissionStatus.awaiting_mcp,
            )
        )
        db.commit()
    yield factory
    Base.metadata.drop_all(engine)
    engine.dispose()


def _make_run(factory) -> int:
    with factory() as db:
        sid = db.query(Submission).first().id
        run = run_repo.create_run(
            db,
            submission_id=sid,
            agent_id="fake",
            agent_snapshot=_snapshot(),
            workspace_path=str(BACKEND),
        )
        db.commit()
        return run.id


def _snapshot(**overrides) -> dict:
    entry = {
        "agent_id": "fake",
        "distribution": "binary",
        "package": "fake-agent",
        "version": "0.1.0",
        "command": PYTHON,
        "args": ["-m", "tests.acp_fake_agent"],
        "env": {},
    }
    entry.update(overrides)
    return entry


def _entry(**overrides) -> AgentEntry:
    # fake agent 是测试专用本地二进制;不经过白名单审核(审核由
    # test_acp_registry 覆盖),直接构造启动快照。
    data = _snapshot(**overrides)
    return AgentEntry.from_dict(data)


# ---------------------------------------------------------------------------
# 权限策略
# ---------------------------------------------------------------------------


class _TC:
    def __init__(self, title: str, kind_value: str | None = None) -> None:
        self.title = title
        self.kind = kind_value


class _Opt:
    def __init__(self, option_id: str, kind: str) -> None:
        self.option_id = option_id
        self.kind = kind


def test_permission_allows_read():
    options = [_Opt("allow", "allow_once"), _Opt("reject", "reject_once")]
    decision = _resolve_permission_locally(_TC("读取报告 PDF", "read"), options)
    assert decision == "allow"


def test_permission_denies_delete():
    options = [_Opt("allow", "allow_once"), _Opt("reject", "reject_once")]
    decision = _resolve_permission_locally(_TC("rm -rf 临时目录", "execute"), options)
    assert decision == "reject"


def test_codex_launch_uses_native_workspace_sandbox():
    from app.acp.security import secure_launch_spec

    spec = secure_launch_spec(
        _entry(agent_id="codex-acp"), BACKEND, permission_mode="ask"
    )
    config = json.loads(spec.env["CODEX_CONFIG"])

    assert config["sandbox_mode"] == "workspace-write"
    assert config["sandbox_workspace_write"]["network_access"] is False


def test_permission_allows_execute_in_auto_review():
    options = [_Opt("allow", "allow_once"), _Opt("reject", "reject_once")]
    decision = _resolve_permission_locally(
        _TC("运行评分脚本", "execute"), options, allow_execute=True
    )
    assert decision == "allow"


def test_permission_denies_unknown():
    options = [_Opt("allow", "allow_once"), _Opt("reject", "reject_once")]
    decision = _resolve_permission_locally(_TC("某种未知操作", None), options)
    assert decision == "reject"


def test_permission_denies_fetch_mcp_in_ask():
    """ask 档位下 AI-Marking MCP 工具不再自动放行,需教师确认或本地拒绝。"""
    options = [_Opt("allow", "allow_once"), _Opt("reject", "reject_once")]
    decision = _resolve_permission_locally(_TC("读取评分包", "fetch_mcp"), options)
    assert decision == "reject"


def test_permission_allows_fetch_mcp_in_auto_review():
    """auto_review 档位下 fetch_mcp 自动放行(批改主流程不受影响)。"""
    options = [_Opt("allow", "allow_once"), _Opt("reject", "reject_once")]
    decision = _resolve_permission_locally(
        _TC("读取评分包", "fetch_mcp"), options, allow_mcp=True
    )
    assert decision == "allow"


def test_permission_denies_network():
    options = [_Opt("allow", "allow_once"), _Opt("reject", "reject_once")]
    decision = _resolve_permission_locally(_TC("curl http://evil", "execute"), options)
    assert decision == "reject"


# ---------------------------------------------------------------------------
# 提示词
# ---------------------------------------------------------------------------


def test_prompt_mentions_mcp_tools():
    prompt = build_grading_prompt(42, question_name="测试题目")
    assert "《测试题目》" in prompt
    assert "submission_id=42" in prompt
    assert "请勿调用 prepare_ai_marking_submission" in prompt
    # run 提示词只做入口:流程/证据契约/模式判定全部收敛到工作区 skill.md,不点名模式
    assert ("ai-marking-grader" in prompt) or ("skill.md" in prompt)
    assert "skill.md" in prompt
    assert "最终成绩由教师在网页确认" in prompt
    for forbidden in ("ACP 模式", "MCP 模式", "open_ai_marking_assignment", "needs_rubric", "source_line"):
        assert forbidden not in prompt, f"提示词不应内嵌: {forbidden}"


# ---------------------------------------------------------------------------
# 编排器端到端(假 agent)
# ---------------------------------------------------------------------------


async def test_execute_run_completes_and_persists(factory):
    run_id = _make_run(factory)
    # submission 不在 awaiting_mcp(直接到 ready_for_review)→ run completed
    with factory() as db:
        sub = db.query(Submission).first()
        sub.status = SubmissionStatus.ocr_done
        db.commit()

    final = await execute_run(
        run_id,
        session_factory=factory,
        entry=_entry(),
        submission_id=1,
        has_code=False,
        question_name="测试题目",
    )
    assert final == AcpRunStatus.completed
    with factory() as db:
        run = db.get(AcpRun, run_id)
        assert run.status == AcpRunStatus.completed
        assert run.acp_session_id
        assert run.teacher_verdict is None
        events = run_repo.list_events_after(db, run_id, 0, 1000)
        kinds = [e.kind for e in events]
        assert "tool_started" in kinds
        assert "tool_finished" in kinds
        assert kinds[-1] == "turn_completed"


async def test_execute_run_parks_when_submission_awaiting(factory):
    """A no-code turn that fails to save is an error, not teacher work."""
    run_id = _make_run(factory)
    with factory() as db:
        sid = db.query(Submission).first().id
    final = await execute_run(
        run_id,
        session_factory=factory,
        entry=_entry(),
        submission_id=sid,
        has_code=False,
        question_name="测试题目",
    )
    assert final == AcpRunStatus.failed
    with factory() as db:
        run = db.get(AcpRun, run_id)
        assert run.status == AcpRunStatus.failed


async def test_execute_run_permission_decisions_recorded(factory):
    run_id = _make_run(factory)
    with factory() as db:
        sub = db.query(Submission).first()
        sub.status = SubmissionStatus.ocr_done
        db.commit()
    await execute_run(
        run_id,
        session_factory=factory,
        entry=_entry(),
        submission_id=1,
        has_code=False,
        question_name="测试题目",
    )
    with factory() as db:
        events = run_repo.list_events_after(db, run_id, 0, 1000)
        notices = [e for e in events if e.kind == "notice"]
        assert any("权限请求自动裁决" in str(e.payload.get("text", "")) for e in notices)


async def test_execute_run_failed_when_agent_missing(factory):
    run_id = _make_run(factory)
    final = await execute_run(
        run_id,
        session_factory=factory,
        entry=_entry(command="/no/such/binary", args=[]),
        submission_id=1,
        has_code=False,
        question_name="测试题目",
    )
    assert final == AcpRunStatus.failed
    with factory() as db:
        run = db.get(AcpRun, run_id)
        assert run.status == AcpRunStatus.failed
        assert run.error_message


async def test_execute_run_elicitation_enters_checkpoint_and_reply_resolves(factory):
    """Elicitation persists a checkpoint and immediately releases the agent turn."""
    run_id = _make_run(factory)
    with factory() as db:
        sid = db.query(Submission).first().id

    final = await execute_run(
        run_id,
        session_factory=factory,
        entry=_entry(args=["-m", "tests.acp_fake_agent", "--emit-elicitation"]),
        submission_id=sid,
        has_code=True,
        question_name="测试题目",
        permission_mode="auto_review",
        dwell_seconds=10,
    )
    assert final == AcpRunStatus.waiting_for_teacher
    with factory() as db:
        run = db.get(AcpRun, run_id)
        assert run.checkpoint is not None
        assert run_repo.answer_checkpoint(
            db, run, verdict="consistent", note="一致"
        )
        assert run.teacher_verdict == "consistent"
        assert run.teacher_note == "一致"


def test_snapshot_roundtrip_preserves_entry():
    entry = _entry()
    restored = AgentEntry.from_dict(entry.to_dict())
    assert restored.command == entry.command
    assert restored.version == entry.version


async def test_apply_run_configuration_failure_degrades(factory, monkeypatch):
    """run 配置应用失败时降级继续:不抛 AcpError,清 pending 并落失败事件。"""
    from app.acp import orchestrator

    run_id = _make_run(factory)

    async def _boom(session, session_id, selection):
        raise AcpProtocolError("Agent 拒绝配置")

    monkeypatch.setattr("app.acp.model_catalog.apply_codex_selection", _boom)
    result = await orchestrator._apply_run_configuration(
        None, "sid", run_id, session_factory=factory
    )
    assert result is None
    with factory() as db:
        run = db.get(AcpRun, run_id)
        assert run is not None
        assert run.pending_codex_config is None
        kinds = [e.kind for e in run_repo.list_events_after(db, run_id, 0, 100)]
        assert "configuration_failed" in kinds
