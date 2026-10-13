"""批改 run 的执行编排:启动/续跑/检查点/终态。

与 worker 的分工:
- ``grading_worker``:领取 run、维持租约、崩溃重排、清理工作区;
- 本模块:单次执行——构造 provider 与工具,驱动 ``AgentEngine``,把引擎
  事件落库,并在终态驱动 run 状态机(替代原 ``orchestrator.execute_run``)。

设计要点:
- provider/工具/引擎全部进程内构造,无子进程协议;
- 检查点:``CheckpointRequested`` 由工具抛出,runner 落
  ``waiting_for_teacher`` + 持久化 ``agent_state``(完整 items 与句柄);
- 续跑:教师答复后由 worker 重新领取,从 ``agent_state.items`` 继续,
  并注入答复消息(含句柄失效重开提示);
- 取消:2s 缓存轮询 ``claim_control_state``(cancelling)并中止回合;
- 运行中切 model/effort:每回合开始前应用 ``pending_config``(next turn)。
"""

from __future__ import annotations

import json
import logging
import time
from contextlib import suppress
from typing import Any

from sqlalchemy.orm import sessionmaker

from app.agent import run_store as run_repo
from app.agent.checkpoints import build_resume_prompt, build_task_prompt
from app.agent.engine import AgentEngine
from app.agent.models import EngineEvent, EngineOutcome, EngineStatus, user_message
from app.agent.prompts.loader import grading_instructions
from app.agent.provider import AgentProviderError
from app.agent.providers import create_provider
from app.agent.tools.grading import GradingToolContext, build_grading_tools
from app.domain.grading_run import GradingRunStatus
from app.models.grading_run import GradingRun
from app.models.submission import Submission, SubmissionStatus

logger = logging.getLogger(__name__)

LEASE_SECONDS = 90
MAX_ROUNDS = 40
TURN_TIMEOUT_SECONDS = 900.0
CANCEL_POLL_SECONDS = 2.0

_TERMINAL_SUBMISSION_STATUSES = (
    SubmissionStatus.ready_for_review,
    SubmissionStatus.reviewed,
)


class _CancelWatcher:
    """取消/失权轮询;2s 内复用上次结果,避免高频读库。"""

    def __init__(
        self, session_factory: sessionmaker, run_id: int, claim_token: str | None
    ) -> None:
        self._factory = session_factory
        self._run_id = run_id
        self._claim_token = claim_token or ""
        self._cache_until = 0.0
        self._state = "active"

    def should_cancel(self) -> bool:
        now = time.monotonic()
        if now < self._cache_until:
            return self._state != "active"
        self._cache_until = now + CANCEL_POLL_SECONDS
        with self._factory() as db:
            self._state = run_repo.claim_control_state(
                db, self._run_id, self._claim_token
            )
        return self._state != "active"

    @property
    def state(self) -> str:
        return self._state


def _build_provider(
    session_factory: sessionmaker,
    provider_name: str,
    model: str | None,
    reasoning_effort: str | None,
):
    """从 system_config 读取凭证构造 provider;凭证缺失抛 RuntimeError。"""
    from app.services.provider_config import (
        CHATGPT_DEFAULT_MODEL_KEY,
        CHATGPT_TOKENS_KEY,
        OPENAI_API_KEY_KEY,
        OPENAI_BASE_URL_KEY,
        OPENAI_DEFAULT_MODEL_KEY,
        OPENAI_REASONING_EFFORT_KEY,
        get_provider_config,
    )

    with session_factory() as db:
        config = get_provider_config(db)
    if provider_name == "chatgpt_sub":
        raw_tokens = config.get(CHATGPT_TOKENS_KEY) or ""
        tokens: dict[str, Any] | None = None
        if raw_tokens:
            with suppress(json.JSONDecodeError):
                tokens = json.loads(raw_tokens)
        return create_provider(
            provider_name,
            tokens=tokens,
            default_model=model or config.get(CHATGPT_DEFAULT_MODEL_KEY) or None,
            reasoning_effort=reasoning_effort or None,
        )
    return create_provider(
        provider_name,
        api_key=config.get(OPENAI_API_KEY_KEY) or None,
        base_url=config.get(OPENAI_BASE_URL_KEY) or None,
        default_model=model or config.get(OPENAI_DEFAULT_MODEL_KEY) or None,
        reasoning_effort=reasoning_effort or config.get(OPENAI_REASONING_EFFORT_KEY) or None,
    )


def _resolve_turn_config(
    session_factory: sessionmaker, run_id: int, claim_token: str | None
):
    """每回合开始调用:应用 pending 配置(next turn 语义)并返回当前配置。"""

    def getter() -> tuple[str | None, str | None]:
        with session_factory() as db:
            run = db.get(GradingRun, run_id)
            if run is None:
                return (None, None)
            if run.pending_config:
                config = dict(run.pending_config)
                with suppress(ValueError):
                    run_repo.mark_configuration_applied(
                        db, run, config, claim_token=claim_token
                    )
                    run_repo.append_event(
                        db,
                        run_id,
                        kind="configuration_applied",
                        payload={"config": config},
                    )
                run = db.get(GradingRun, run_id)
            if run is None:
                return (None, None)
            return (run.model, run.reasoning_effort)

    return getter


async def execute_run(
    run_id: int,
    *,
    session_factory: sessionmaker,
    submission_id: int,
    question_name: str = "",
    claim_token: str | None = None,
) -> GradingRunStatus:
    """执行一次 run;返回最终状态(异常时写 failed)。"""
    with session_factory() as db:
        run = db.get(GradingRun, run_id)
        if run is None:
            raise ValueError(f"run {run_id} 不存在")
        provider_name = run.provider
        desired_model = run.model
        desired_effort = run.reasoning_effort
        state = dict(run.agent_state or {})

    # worker 已领取并进入执行:queued → starting → running;
    # 检查点续跑从 waiting_for_teacher 回到 running。
    with session_factory() as db:
        run = db.get(GradingRun, run_id)
        if run is not None:
            if run.status == GradingRunStatus.queued:
                if not run_repo.transition_run(
                    db, run, GradingRunStatus.starting, claim_token=claim_token
                ):
                    return GradingRunStatus.failed
                run = db.get(GradingRun, run_id)
                run_repo.transition_run(
                    db, run, GradingRunStatus.running, claim_token=claim_token
                )
            elif run.status in (
                GradingRunStatus.starting,
                GradingRunStatus.waiting_for_teacher,
            ):
                run_repo.transition_run(
                    db, run, GradingRunStatus.running, claim_token=claim_token
                )

    try:
        provider = _build_provider(
            session_factory, provider_name, desired_model, desired_effort
        )
    except (AgentProviderError, RuntimeError) as exc:
        message = str(exc) or type(exc).__name__
        logger.warning("批改通道不可用 [run=%s]: %s", run_id, message)
        _fail_run(session_factory, run_id, claim_token, message)
        return GradingRunStatus.failed

    ctx = GradingToolContext(
        session_factory=session_factory,
        run_id=run_id,
        submission_id=submission_id,
        client="agent",
    )
    ctx.load_state(state)
    tools = build_grading_tools(ctx)

    items = list(state.get("items") or [])
    if items:
        with session_factory() as db:
            run = db.get(GradingRun, run_id)
        prompt = build_resume_prompt(run) if run is not None else "请继续批改。"
    else:
        prompt = build_task_prompt(submission_id, question_name)
    items.append(user_message(prompt))

    watcher = _CancelWatcher(session_factory, run_id, claim_token)
    engine = AgentEngine(
        provider,
        tools,
        instructions=grading_instructions(),
        model=desired_model,
        reasoning_effort=desired_effort,
        should_cancel=watcher.should_cancel,
        max_rounds=MAX_ROUNDS,
        turn_timeout_seconds=TURN_TIMEOUT_SECONDS,
        cancel_poll_seconds=CANCEL_POLL_SECONDS,
        config_getter=_resolve_turn_config(session_factory, run_id, claim_token),
    )

    async def emit(event: EngineEvent) -> None:
        with session_factory() as db:
            run_repo.append_event(
                db, run_id, kind=event.kind, payload=dict(event.payload)
            )

    try:
        outcome = await engine.run(items, emit)
    finally:
        with suppress(Exception):
            await provider.aclose()

    return _finalize(
        outcome,
        session_factory=session_factory,
        run_id=run_id,
        submission_id=submission_id,
        claim_token=claim_token,
        ctx=ctx,
        watcher=watcher,
    )


def _finalize(
    outcome: EngineOutcome,
    *,
    session_factory: sessionmaker,
    run_id: int,
    submission_id: int,
    claim_token: str | None,
    ctx: GradingToolContext,
    watcher: _CancelWatcher,
) -> GradingRunStatus:
    if outcome.status == EngineStatus.checkpoint:
        return _save_checkpoint_outcome(
            outcome,
            session_factory=session_factory,
            run_id=run_id,
            submission_id=submission_id,
            claim_token=claim_token,
            ctx=ctx,
        )
    with session_factory() as db:
        run = db.get(GradingRun, run_id)
        if run is None:
            return GradingRunStatus.failed
        if outcome.usage:
            run.usage = dict(outcome.usage)
        if outcome.status == EngineStatus.completed:
            db.commit()
            if run_repo.transition_run(
                db, run, GradingRunStatus.completed, claim_token=claim_token
            ):
                return GradingRunStatus.completed
            return run.status
        if outcome.status == EngineStatus.cancelled:
            db.commit()
            if watcher.state == "lost":
                logger.warning("run 取消时发现所有权丢失 [run=%s]", run_id)
                return run.status
            if run_repo.transition_run(
                db, run, GradingRunStatus.cancelled, claim_token=claim_token
            ):
                return GradingRunStatus.cancelled
            return run.status
        if outcome.status == EngineStatus.incomplete:
            sub = db.get(Submission, submission_id)
            if sub is not None and sub.status in _TERMINAL_SUBMISSION_STATUSES:
                db.commit()
                if run_repo.transition_run(
                    db, run, GradingRunStatus.completed, claim_token=claim_token
                ):
                    return GradingRunStatus.completed
                return run.status
            db.commit()
            error = "agent 未在回合内保存评分建议"
            if run_repo.transition_run(
                db,
                run,
                GradingRunStatus.failed,
                claim_token=claim_token,
                error=error,
            ):
                return GradingRunStatus.failed
            return run.status
        # failed
        db.commit()
        error = outcome.error or "批改运行失败"
        if run_repo.transition_run(
            db, run, GradingRunStatus.failed, claim_token=claim_token, error=error
        ):
            return GradingRunStatus.failed
        return run.status


def _save_checkpoint_outcome(
    outcome: EngineOutcome,
    *,
    session_factory: sessionmaker,
    run_id: int,
    submission_id: int,
    claim_token: str | None,
    ctx: GradingToolContext,
) -> GradingRunStatus:
    from app.application.mcp_workflow import build_context_parts

    with session_factory() as db:
        sub = db.get(Submission, submission_id)
        run = db.get(GradingRun, run_id)
        if sub is None or run is None:
            return GradingRunStatus.failed
        _, _, _, context_hash, _ = build_context_parts(db, sub)
        agent_state = {
            "items": outcome.items,
            "rounds": outcome.rounds,
            "usage": outcome.usage,
            **ctx.export_state(),
        }
        run.usage = dict(outcome.usage)
        run_repo.save_checkpoint(
            db,
            run,
            checkpoint={
                "type": "code_consistency",
                "message": outcome.checkpoint_message or "请确认运行结果与报告是否一致",
            },
            dwell_seconds=run_repo.CHECKPOINT_DWELL_SECONDS,
            confirmation_revision=sub.grading_revision,
            confirmation_context_hash=context_hash,
            agent_state=agent_state,
            claim_token=claim_token,
        )
    return GradingRunStatus.waiting_for_teacher


def _fail_run(
    session_factory: sessionmaker,
    run_id: int,
    claim_token: str | None,
    error: str,
) -> None:
    with session_factory() as db:
        run = db.get(GradingRun, run_id)
        if run is not None:
            run_repo.transition_run(
                db,
                run,
                GradingRunStatus.failed,
                claim_token=claim_token,
                error=error,
            )
