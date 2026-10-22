"""批改 run 的执行编排：构造 provider，交给 LangGraph 主图执行。

与 worker 的分工：
- ``grading_worker``：领取 run、维持租约、崩溃重排、清理工作区；
- 本模块：单次执行——构造 provider、取消轮询、调用 ``graph.driver``；
- 图与 checkpointer：负责节点执行与恢复，不再在本模块写对话状态。
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from contextlib import suppress
from typing import Any

from sqlalchemy.orm import sessionmaker

from app.agent import run_store as run_repo
from app.agent.graph.driver import execute_graph
from app.agent.provider import AgentProviderError
from app.agent.providers import create_provider
from app.domain.grading_run import GradingRunStatus
from app.models.grading_run import GradingRun

logger = logging.getLogger(__name__)

LEASE_SECONDS = 90
CANCEL_POLL_SECONDS = 2.0


class _CancelWatcher:
    """取消/失权轮询；2s 内复用上次结果，避免高频读库。"""

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
    session_id: str | None = None,
):
    """从 system_config 读取凭证构造 provider；凭证缺失抛 RuntimeError。"""
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

        def _save_tokens(updated: dict[str, Any]) -> None:
            """刷新轮换后的 token 立即回写,避免 worker 重启后失效。"""
            from app.services.provider_config import save_chatgpt_tokens

            with session_factory() as db:
                save_chatgpt_tokens(db, updated)

        def _load_tokens() -> dict[str, Any] | None:
            """刷新前回读:另一个进程可能刚完成轮换。"""
            from app.services.provider_config import load_chatgpt_tokens

            with session_factory() as db:
                return load_chatgpt_tokens(db)

        # 跨进程单飞:本进程与 API 进程共用同一个 refresh_token,只能一方去刷新
        lease_owner = uuid.uuid4().hex

        def _acquire_lease(ttl_seconds: float) -> bool:
            from app.services.chatgpt_refresh_lease import acquire_refresh_lease

            with session_factory() as db:
                return acquire_refresh_lease(
                    db, owner=lease_owner, ttl_seconds=ttl_seconds
                )

        def _release_lease() -> None:
            from app.services.chatgpt_refresh_lease import release_refresh_lease

            with session_factory() as db:
                release_refresh_lease(db, owner=lease_owner)

        return create_provider(
            provider_name,
            tokens=tokens,
            token_sink=_save_tokens,
            token_loader=_load_tokens,
            refresh_lease_acquire=_acquire_lease,
            refresh_lease_release=_release_lease,
            default_model=model or config.get(CHATGPT_DEFAULT_MODEL_KEY) or None,
            reasoning_effort=reasoning_effort or None,
            session_id=session_id,
        )
    return create_provider(
        provider_name,
        api_key=config.get(OPENAI_API_KEY_KEY) or None,
        base_url=config.get(OPENAI_BASE_URL_KEY) or None,
        default_model=model or config.get(OPENAI_DEFAULT_MODEL_KEY) or None,
        reasoning_effort=(
            reasoning_effort or config.get(OPENAI_REASONING_EFFORT_KEY) or None
        ),
        session_id=session_id,
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
    checkpointer=None,
) -> GradingRunStatus:
    """执行一次 run；返回最终状态（异常时写 failed）。"""
    with session_factory() as db:
        run = db.get(GradingRun, run_id)
        if run is None:
            raise ValueError(f"run {run_id} 不存在")
        provider_name = run.provider
        desired_model = run.model
        desired_effort = run.reasoning_effort

    # worker 已领取并进入执行：queued → starting → running。
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
        probe = _build_provider(
            session_factory,
            provider_name,
            desired_model,
            desired_effort,
            session_id="probe",
        )
        await probe.aclose()
    except (AgentProviderError, RuntimeError) as exc:
        message = str(exc) or type(exc).__name__
        logger.warning("批改通道不可用 [run=%s]: %s", run_id, message)
        _fail_run(session_factory, run_id, claim_token, message)
        return GradingRunStatus.failed

    def provider_factory(agent: str):
        return _build_provider(
            session_factory,
            provider_name,
            desired_model,
            desired_effort,
            session_id=agent,
        )

    watcher = _CancelWatcher(session_factory, run_id, claim_token)
    return await execute_graph(
        session_factory=session_factory,
        run_id=run_id,
        submission_id=submission_id,
        question_name=question_name,
        provider_factory=provider_factory,
        provider_name=provider_name,
        model=desired_model,
        reasoning_effort=desired_effort,
        claim_token=claim_token,
        should_cancel=watcher.should_cancel,
        config_getter=_resolve_turn_config(session_factory, run_id, claim_token),
        checkpointer=checkpointer,
    )


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
