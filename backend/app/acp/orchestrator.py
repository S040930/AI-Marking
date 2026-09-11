"""ACP 批改编排用例:驱动一次 run 的完整生命周期。

与 worker 的分工:
- ``worker``:领取 run、维持租约、崩溃重排、回收进程;
- 本模块:单次协议执行(spawn agent → session → prompt → 收事件 →
  教师检查点应答 → 结束),通过 ``AcpSession`` 与回调完成。

教师检查点语义:
- 含代码作业必须获得「运行结果与报告一致」的持久确认后,agent 才
  可以保存建议;确认与 MCP 工具链共用同一持久化记录(``acp_runs``
  的 teacher_verdict 绑定 submission/revision/context hash);
- agent 的权限请求:工作区只读放行;AI-Marking MCP 工具在
  ``auto_review`` 档位自动允许,``ask`` 档位需教师确认或本地拒绝;
  网络、宿主写入、删除、移动与未知操作拒绝并转教师确认;
- 检查点驻留上限 30 分钟;超时关闭会话但保留 ``waiting_for_teacher``,
  教师稍后答复会重新排队续跑。
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from app.acp.domain import (
    AcpRunStatus,
)
from app.acp.errors import AcpAuthRequiredError, AcpCancelledError, AcpError
from app.acp.events import AcpEvent, AcpEventKind, TextDeltaBuffer
from app.acp.model_catalog import catalog_from_config_options
from app.acp.models import AcpRun
from app.acp.registry import AgentEntry
from app.acp.runs import (
    append_event,
    record_session,
    renew_lease,
    save_checkpoint,
    transition_run,
)
from app.acp.session import AcpSession, AgentLaunchSpec, ClientCallbacks

logger = logging.getLogger(__name__)

# 默认参数(PLAN:并发 1、驻留 30 分钟、检查点等待由 API 控制)
CHECKPOINT_DWELL_SECONDS = 30 * 60
LEASE_SECONDS = 90
MAX_ATTEMPTS = 3

# 权限裁决:允许的 kind/标题模式
# fetch_mcp(AI-Marking MCP 工具)不在无条件白名单:ask 档位需教师确认,
# 仅 auto_review 档位经 allow_mcp 自动放行(见 _resolve_permission_locally)。
_ALLOWED_TOOL_KINDS = {"read", "search", "think"}
_DENY_TITLE_PATTERNS = ("rm ", "delete", "删除", "curl ", "wget ", "network")


def _resolve_permission_locally(
    tool_call: Any,
    options: list[Any],
    *,
    allow_execute: bool = False,
    allow_mcp: bool = False,
) -> str | None:
    """权限策略:确定性白名单;不确定时拒绝(None →教师检查点走不到这里)。"""
    kind = getattr(tool_call, "kind", None)
    kind_value = getattr(kind, "value", None) or str(kind or "")
    title = str(getattr(tool_call, "title", "") or "").lower()
    if kind_value in _ALLOWED_TOOL_KINDS or (
        allow_execute and kind_value in {"execute", "terminal"}
    ) or (allow_mcp and kind_value == "fetch_mcp"):
        # 选第一个 allow 类选项
        for opt in options:
            if str(getattr(opt, "kind", "") or "").startswith("allow"):
                return getattr(opt, "option_id", None)
    for pattern in _DENY_TITLE_PATTERNS:
        if pattern in title:
            for opt in options:
                if str(getattr(opt, "kind", "") or "").startswith("reject"):
                    return getattr(opt, "option_id", None)
    # 未知操作:拒绝(不自动放行)
    for opt in options:
        if str(getattr(opt, "kind", "") or "").startswith("reject"):
            return getattr(opt, "option_id", None)
    return None


class RunCallbacks(ClientCallbacks):
    """把 ACP 回调桥接到 run 持久化。

    - 事件经 ``TextDeltaBuffer`` 合并后落库;
    - form elicitation 转为教师检查点驻留(等待 API 答复);
    - 权限请求按本地策略裁决;不确定的拒绝并记录 notice。
    """

    def __init__(
        self,
        run_id: int,
        *,
        session_factory: sessionmaker,
        lease_renew_seconds: int = LEASE_SECONDS,
        dwell_seconds: int = CHECKPOINT_DWELL_SECONDS,
        workspace: Path | None = None,
        permission_mode: str = "ask",
        agent_id: str = "codex-acp",
        agent_version: str = "unknown",
    ) -> None:
        self.run_id = run_id
        self._factory = session_factory
        self._buffer = TextDeltaBuffer()
        self._renew_seconds = lease_renew_seconds
        self._dwell_seconds = dwell_seconds
        self._workspace = workspace.resolve() if workspace else None
        self.checkpoint_created = False
        self.permission_mode = permission_mode
        self._agent_id = agent_id
        self._agent_version = agent_version
        # elicitation 应答由 API 写入 run.teacher_verdict 后注入
        self.elicitation_result: str | None = None

    def _db(self) -> Session:
        return self._factory()

    def _renew(self) -> None:
        with self._db() as db:
            run = db.get(AcpRun, self.run_id)
            if run is not None and run.claim_token:
                renew_lease(db, run.id, run.claim_token, self._renew_seconds)

    async def on_event(self, event: AcpEvent) -> None:
        if event.kind == AcpEventKind.message_delta:
            full = self._buffer.add(event.payload.get("text", ""))
            if not full:
                return
            merged = self._buffer.flush()
            if merged is not None:
                event = merged
        else:
            await self.flush_text()
        with self._db() as db:
            append_event(
                db,
                self.run_id,
                kind=event.kind.value,
                payload=event.to_db_payload()["payload"],
            )
        # 每 32 个事件续一次租约(事件密度与租约时间解耦)
        if event.kind in (AcpEventKind.tool_finished, AcpEventKind.turn_completed):
            self._renew()

    async def on_config_options(
        self, session_id: str, config_options: list[Any], *, source: str
    ) -> None:
        if self._agent_id != "codex-acp":
            return
        from app.acp.registry import AgentEntry

        entry = AgentEntry(
            agent_id="codex-acp",
            registry_version=None,
            distribution="npx",
            package="@agentclientprotocol/codex-acp",
            version=self._agent_version,
            command="",
            args=[],
            env={},
        )
        catalog = catalog_from_config_options(config_options, entry=entry)
        payload = {
            "source": source,
            "model_id": catalog.selected_model_id,
            "reasoning_effort": catalog.default_reasoning_effort,
            "speed_mode": catalog.default_speed_mode,
        }
        with self._db() as db:
            append_event(
                db,
                self.run_id,
                kind="configuration_agent_update",
                payload=payload,
            )

    async def flush_text(self) -> None:
        merged = self._buffer.flush()
        if merged is None:
            return
        with self._db() as db:
            append_event(
                db,
                self.run_id,
                kind=merged.kind.value,
                payload=merged.to_db_payload()["payload"],
            )

    async def resolve_permission(
        self, session_id: str, tool_call: Any, options: list[Any]
    ) -> str | None:
        decision = _resolve_permission_locally(
            tool_call,
            options,
            allow_execute=self.permission_mode == "auto_review",
            allow_mcp=self.permission_mode == "auto_review",
        )
        kind = str(
            getattr(getattr(tool_call, "kind", None), "value", None)
            or getattr(tool_call, "kind", "")
        )
        if kind in {"read", "search"} and not _tool_call_within_workspace(
            tool_call, self._workspace
        ):
            decision = None
        title = str(getattr(tool_call, "title", "") or "")
        with self._db() as db:
            append_event(
                db,
                self.run_id,
                kind=AcpEventKind.notice.value,
                payload={
                    "text": f"权限请求自动裁决: {title}",
                    "decision": "allow" if decision else "deny",
                },
            )
        return decision

    async def resolve_elicitation(
        self, session_id: str, message: str, requested_schema: Any
    ) -> dict[str, Any] | None:
        """教师检查点:驻留等待教师在网页答复。

        教师答复写入 ``acp_runs.teacher_verdict`` 后,由外部 API 线程
        调用 ``set_elicitation_result`` 唤醒;驻留超时返回 None(decline),
        run 保持 waiting_for_teacher,教师稍后答复触发续跑。
        """
        await self.flush_text()
        with self._db() as db:
            run = db.get(AcpRun, self.run_id)
            if run is None:
                return None
            from app.application.mcp_workflow import build_context_parts
            from app.models.submission import Submission

            sub = db.get(Submission, run.submission_id)
            if sub is None:
                return None
            _, _, _, context_hash, _ = build_context_parts(db, sub)
            save_checkpoint(
                db,
                run,
                checkpoint={
                    "type": "code_consistency",
                    "message": message,
                    "schema": _schema_to_json(requested_schema),
                    "acp_session_id": run.acp_session_id,
                },
                dwell_seconds=CHECKPOINT_DWELL_SECONDS,
                confirmation_revision=sub.grading_revision,
                confirmation_context_hash=context_hash,
            )
        self.checkpoint_created = True
        # 不驻留 agent；持久化后拒绝本次 elicitation，turn 结束即关闭会话。
        return None


def _tool_call_within_workspace(tool_call: Any, workspace: Path | None) -> bool:
    if workspace is None:
        return False
    candidates: list[str] = []
    for attr in ("path", "file_path", "cwd"):
        value = getattr(tool_call, attr, None)
        if isinstance(value, str):
            candidates.append(value)
    locations = getattr(tool_call, "locations", None) or []
    for location in locations:
        value = getattr(location, "path", None)
        if isinstance(value, str):
            candidates.append(value)
    if not candidates:
        return False
    for value in candidates:
        path = Path(value)
        resolved = (
            (workspace / path).resolve() if not path.is_absolute() else path.resolve()
        )
        if resolved != workspace and workspace not in resolved.parents:
            return False
    return True


def _schema_to_json(schema: Any) -> dict[str, Any]:
    if schema is None:
        return {}
    if isinstance(schema, dict):
        return schema
    dump = getattr(schema, "model_dump", None)
    if callable(dump):
        try:
            return dump(mode="json", by_alias=True, exclude_none=True)
        except TypeError:
            return dump()
    return {}


def launch_spec_from_entry(
    entry: AgentEntry, *, cwd: str, permission_mode: str = "ask"
) -> AgentLaunchSpec:
    from app.acp.security import secure_launch_spec

    return secure_launch_spec(entry, Path(cwd), permission_mode=permission_mode)


def mcp_server_config() -> dict[str, Any]:
    """AI-Marking STDIO MCP 注入配置;client 标识固定为 acp-worker。"""
    import sys

    project_root = Path(__file__).resolve().parents[3]
    launcher = project_root / "scripts" / "run-ai-marking-mcp"
    command = (
        str(launcher) if launcher.exists() else f"{sys.executable} -m app.mcp.server"
    )
    if launcher.exists():
        return {
            "name": "ai-marking",
            "command": command,
            "args": ["acp-worker"],
            "env": {},
        }
    return {
        "name": "ai-marking",
        "command": sys.executable,
        "args": ["-m", "app.mcp.server"],
        "env": {},
    }


def build_grading_prompt(submission_id: int, *, question_name: str) -> str:
    """run 提示词：入口只给题目标识，完整流程由工作区 skill.md 提供。

    ai-marking-grader skill 以 ``skill.md`` 物化在工作区根目录
    （workspace.materialize_*），是批改流程唯一权威源；提示词不再内嵌流程或
    点名模式（模式判定在 skill 内完成），避免与 SKILL.md 双份漂移。
    语义与前端 ``buildAcpChatGradingPrompt`` 对齐。
    """
    return "\n\n".join(
        [
            f"请批改 AI-Marking 作业《{question_name}》（submission_id={submission_id}）。",
            "这是已存在且已完成 OCR 的作业：先读取工作区根目录的 skill.md 获取"
            "完整批改流程（内含模式判定与评分步骤），然后严格按其评分。流程与证据"
            "契约以该文件为唯一权威；请勿调用 prepare_ai_marking_submission 或"
            " submit_prepared_ai_marking_submission。",
            "最终成绩由教师在网页确认，不要自行定稿。",
        ]
    )


async def execute_run(
    run_id: int,
    *,
    session_factory: sessionmaker,
    entry: AgentEntry,
    submission_id: int,
    has_code: bool,
    question_name: str = "",
    claim_token: str | None = None,
    workspace_root_dir: Path | None = None,
    dwell_seconds: int = CHECKPOINT_DWELL_SECONDS,
    permission_mode: str = "ask",
) -> AcpRunStatus:
    """执行一次 run;返回最终状态(异常时写 failed)。

    - 会话工作区:从数据库路径物化的 run 工作区(路径只由 run ID 派生);
    - 代码执行权限由运行时 permission_mode 决定,不依赖历史认证状态;
    - 教师答复后续跑:session/load 恢复原会话(支持时)。
    """
    buffer_factory = session_factory
    with buffer_factory() as db:
        run = db.get(AcpRun, run_id)
        if run is None:
            raise AcpError(f"run {run_id} 不存在")
        workspace = Path(run.workspace_path or (workspace_root_dir or Path("/tmp")))

    callbacks = RunCallbacks(
        run_id,
        session_factory=session_factory,
        dwell_seconds=dwell_seconds,
        workspace=workspace,
        permission_mode=permission_mode,
        agent_id=entry.agent_id,
        agent_version=entry.version,
    )
    spec = launch_spec_from_entry(
        entry, cwd=str(workspace), permission_mode=permission_mode
    )

    # worker 已领取并进入执行:queued → starting → running;
    # 教师答复后的续跑从 waiting_for_teacher 回到 running。
    with buffer_factory() as db:
        run = db.get(AcpRun, run_id)
        if run is not None:
            if run.status == AcpRunStatus.queued:
                transition_run(db, run, AcpRunStatus.starting, claim_token=claim_token)
                run = db.get(AcpRun, run_id)
                transition_run(db, run, AcpRunStatus.running, claim_token=claim_token)
            elif run.status in (
                AcpRunStatus.starting,
                AcpRunStatus.waiting_for_teacher,
            ):
                transition_run(db, run, AcpRunStatus.running, claim_token=claim_token)

    session = AcpSession(spec, callbacks)
    try:
        await session.start()
        # 新会话或恢复会话
        mcp_servers = [mcp_server_config()]
        existing_sid = _existing_session_id(session_factory, run_id)
        if existing_sid and session.supports_load_session:
            try:
                loaded = await session.load_session(
                    cwd=str(workspace), session_id=existing_sid, mcp_servers=mcp_servers
                )
            except (AcpError, ConnectionError):
                loaded = None
                with buffer_factory() as db:
                    append_event(
                        db,
                        run_id,
                        kind="notice",
                        payload={"text": "旧会话恢复失败，已创建新会话"},
                    )
            sid = existing_sid
            if loaded is None:
                sid = await _open_new_session(
                    session, session_factory, run_id, workspace, mcp_servers
                )
        else:
            sid = await _open_new_session(
                session, session_factory, run_id, workspace, mcp_servers
            )

        if entry.agent_id == "codex-acp":
            await _apply_run_configuration(
                session,
                sid,
                run_id,
                session_factory=session_factory,
            )

        # 教师已答复的续跑:把答复与前次摘要注入提示
        prompt = build_grading_prompt(submission_id, question_name=question_name)
        prior = _teacher_followup(session_factory, run_id)
        if prior:
            prompt = prior + "\n" + prompt

        response = await _prompt_with_cancel(
            session,
            sid,
            prompt,
            session_factory=session_factory,
            run_id=run_id,
            claim_token=claim_token,
        )
        await callbacks.flush_text()
        with buffer_factory() as db:
            append_event(
                db,
                run_id,
                kind=AcpEventKind.turn_completed.value,
                payload={"stop_reason": response.stop_reason},
            )
            run = db.get(AcpRun, run_id)
            if run is None:
                return AcpRunStatus.failed
            if run.status == AcpRunStatus.cancelling:
                transition_run(db, run, AcpRunStatus.cancelled, claim_token=claim_token)
                return AcpRunStatus.cancelled
            if callbacks.checkpoint_created:
                return AcpRunStatus.waiting_for_teacher
            # agent 结束但 submission 仍等待确认 → 确定性进入 waiting_for_teacher
            if response.stop_reason == "end_turn":
                from app.models.submission import Submission, SubmissionStatus

                sub = db.get(Submission, submission_id)
                if sub is not None and sub.status == SubmissionStatus.awaiting_mcp:
                    if has_code:
                        _save_deterministic_checkpoint(db, run, submission_id)
                        return AcpRunStatus.waiting_for_teacher
                    raise AcpError("agent 未保存无代码作业的评分建议")
                transition_run(db, run, AcpRunStatus.completed, claim_token=claim_token)
                return AcpRunStatus.completed
            transition_run(
                db,
                run,
                AcpRunStatus.failed,
                claim_token=claim_token,
                error=f"agent turn 结束于 {response.stop_reason}",
            )
            return AcpRunStatus.failed
    except AcpCancelledError:
        with buffer_factory() as db:
            run = db.get(AcpRun, run_id)
            if run is not None:
                transition_run(db, run, AcpRunStatus.cancelled, claim_token=claim_token)
        return AcpRunStatus.cancelled
    except AcpAuthRequiredError as exc:
        with buffer_factory() as db:
            run = db.get(AcpRun, run_id)
            if run is not None:
                transition_run(
                    db,
                    run,
                    AcpRunStatus.failed,
                    claim_token=claim_token,
                    error=str(exc),
                )
        return AcpRunStatus.failed
    except (AcpError, ConnectionError, OSError, asyncio.TimeoutError) as exc:
        logger.exception("ACP run 执行失败 [run=%s]", run_id)
        with buffer_factory() as db:
            run = db.get(AcpRun, run_id)
            if run is not None:
                transition_run(
                    db,
                    run,
                    AcpRunStatus.failed,
                    claim_token=claim_token,
                    error=f"{type(exc).__name__}: {exc}",
                )
        return AcpRunStatus.failed
    finally:
        await session.close()


async def _apply_run_configuration(
    session: AcpSession,
    session_id: str,
    run_id: int,
    *,
    session_factory: sessionmaker,
) -> dict[str, Any] | None:
    """Apply persisted desired config at a safe point before the next turn.

    配置应用失败时降级继续:保留最后一次成功快照并记录
    ``configuration_failed`` 事件,不阻塞本次批改(与 chat 路径语义一致)。
    """
    from app.acp.model_catalog import apply_codex_selection

    with session_factory() as db:
        run = db.get(AcpRun, run_id)
        if run is None:
            raise AcpError("run 不存在")
        desired = dict(run.pending_codex_config or run.codex_config or {})

    try:
        result = await apply_codex_selection(session, session_id, desired)
    except Exception as exc:  # noqa: BLE001 - 降级继续，保留最后成功快照
        with session_factory() as db:
            run = db.get(AcpRun, run_id)
            if run is not None:
                from app.acp.runs import mark_codex_configuration_failed

                mark_codex_configuration_failed(db, run)
                append_event(
                    db,
                    run_id,
                    kind="configuration_failed",
                    payload={"text": "Codex 配置未被 Agent 接受"},
                )
        logger.info("run 配置应用失败，降级继续 [run=%s]: %s", run_id, exc)
        return None

    with session_factory() as db:
        run = db.get(AcpRun, run_id)
        if run is not None:
            from app.acp.runs import mark_codex_configuration_applied

            mark_codex_configuration_applied(db, run, result.config)
            append_event(
                db,
                run_id,
                kind="configuration_adjusted" if result.adjusted else "configuration_applied",
                payload={"codex_config": result.config},
            )
    return result.config


async def _prompt_with_cancel(
    session: AcpSession,
    session_id: str,
    prompt: str,
    *,
    session_factory: sessionmaker,
    run_id: int,
    claim_token: str | None,
):
    task = asyncio.create_task(session.prompt(session_id, prompt))
    try:
        while not task.done():
            done, _ = await asyncio.wait({task}, timeout=2.0)
            if done:
                break
            if claim_token:
                from app.acp.runs import claim_control_state

                with session_factory() as db:
                    control = claim_control_state(db, run_id, claim_token)
                if control != "active":
                    await session.cancel(session_id)
                    task.cancel()
                    with suppress(asyncio.CancelledError):
                        await task
                    if control == "lost":
                        raise AcpError("worker 已失去 run 租约")
                    raise AcpCancelledError("运行已取消")
        return await task
    finally:
        if not task.done():
            task.cancel()


def _save_deterministic_checkpoint(
    db: Session, run: AcpRun, submission_id: int
) -> None:
    from app.application.mcp_workflow import build_context_parts
    from app.models.submission import Submission

    sub = db.get(Submission, submission_id)
    if sub is None:
        raise AcpError("submission 不存在")
    _, _, _, context_hash, _ = build_context_parts(db, sub)
    save_checkpoint(
        db,
        run,
        checkpoint={
            "type": "code_consistency",
            "message": "请确认本地代码运行表现是否与报告描述一致。",
            "acp_session_id": run.acp_session_id,
        },
        dwell_seconds=CHECKPOINT_DWELL_SECONDS,
        confirmation_revision=sub.grading_revision,
        confirmation_context_hash=context_hash,
    )


async def _open_new_session(
    session: AcpSession,
    session_factory: sessionmaker,
    run_id: int,
    workspace: Path,
    mcp_servers: list[dict[str, Any]],
) -> str:
    """新建 ACP 会话并把 session ID 持久化;返回 session ID。"""
    new = await session.new_session(cwd=str(workspace), mcp_servers=mcp_servers)
    with session_factory() as db:
        run = db.get(AcpRun, run_id)
        if run is not None:
            record_session(
                db,
                run,
                session_id=new.session_id,
                resumable=session.supports_load_session,
            )
    return new.session_id


def session_factory_session(factory: sessionmaker) -> Session:
    return factory()


def _existing_session_id(factory: sessionmaker, run_id: int) -> str | None:
    with factory() as db:
        run = db.get(AcpRun, run_id)
        return run.acp_session_id if run else None


def _teacher_followup(factory: sessionmaker, run_id: int) -> str | None:
    with factory() as db:
        run = db.get(AcpRun, run_id)
        if run is None or run.teacher_answered_at is None:
            return None
        note = run.teacher_note or ""
        verdict = run.teacher_verdict or ""
        return (
            f"[教师检查点答复] 运行结果与报告一致性: {verdict}。"
            f"{('教师说明: ' + note) if note else ''}"
            "请基于该答复继续评分。"
        ).strip()
