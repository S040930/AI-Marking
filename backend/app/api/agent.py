"""批改 agent HTTP/SSE 接口;业务规则在 application/agent 层,此处只做适配。"""

from __future__ import annotations

import asyncio
import json
import logging

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session, sessionmaker

from app.agent import checkpoints as checkpoint_service
from app.agent import run_store as run_repo
from app.agent.providers import PROVIDER_NAMES
from app.agent.providers.openai_api import DEFAULT_BASE_URL
from app.core.config import settings
from app.db.session import get_db, get_session_factory
from app.domain.grading_run import TERMINAL_RUN_STATUSES, GradingRunStatus
from app.models.grading_run import GradingRun
from app.schemas.agent import (
    AgentCheckpointOut,
    AgentConfigurationUpdateRequest,
    AgentRunCreateRequest,
    AgentRunDetailResponse,
    AgentRunEventOut,
    AgentRunOut,
    AgentRunReplyRequest,
    ProviderConfigUpdateRequest,
    ProviderDefaultUpdateRequest,
    ProviderListResponse,
    ProviderTestResponse,
)
from app.services.events import acquire_sse_slot
from app.services.provider_config import (
    CHATGPT_DEFAULT_MODEL_KEY,
    OPENAI_API_KEY_KEY,
    OPENAI_BASE_URL_KEY,
    OPENAI_DEFAULT_MODEL_KEY,
    OPENAI_REASONING_EFFORT_KEY,
    PROVIDER_CHATGPT_SUB,
    PROVIDER_DEFAULT_KEY,
    PROVIDER_OPENAI_API,
    get_provider_config,
    provider_default,
    provider_summary,
    set_provider_config,
)

router = APIRouter(prefix="/agent", tags=["agent"])
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Run 生命周期
# ---------------------------------------------------------------------------


@router.post("/runs")
def create_run(body: AgentRunCreateRequest, db: Session = Depends(get_db)) -> AgentRunDetailResponse:
    from app.models.submission import Submission, SubmissionStatus

    sub = db.get(Submission, body.submission_id)
    if sub is None:
        raise HTTPException(status_code=404, detail="提交记录不存在")
    if sub.status != SubmissionStatus.awaiting_grading:
        raise HTTPException(status_code=409, detail="该作业当前不可发起批改")
    active = run_repo.get_active_run_for_submission(db, body.submission_id)
    if active is not None:
        raise HTTPException(
            status_code=409, detail=f"该作业已有进行中的运行 (run {active.id})"
        )
    provider_name = body.provider or provider_default(db)
    _require_provider_ready(db, provider_name)
    try:
        run = run_repo.create_run(
            db,
            submission_id=body.submission_id,
            provider=provider_name,
            model=body.model,
            reasoning_effort=body.reasoning_effort,
        )
        run_repo.append_event(
            db,
            run.id,
            kind="notice",
            payload={
                "text": "已创建批改运行",
                "provider": provider_name,
                "model": body.model,
                "reasoning_effort": body.reasoning_effort,
            },
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _run_detail_response(db, run)


def _require_provider_ready(db: Session, provider_name: str) -> None:
    if provider_name == PROVIDER_CHATGPT_SUB:
        raise HTTPException(status_code=409, detail="ChatGPT 订阅通道尚未开放")
    if provider_name not in PROVIDER_NAMES:
        raise HTTPException(status_code=422, detail=f"未知通道 {provider_name}")
    config = get_provider_config(db)
    if not (config.get(OPENAI_API_KEY_KEY) or ""):
        raise HTTPException(status_code=409, detail="官方通道尚未配置 API key")


def _run_out(run: GradingRun) -> AgentRunOut:
    checkpoint = None
    if run.checkpoint is not None:
        checkpoint = AgentCheckpointOut(
            type=str(run.checkpoint.get("type", "code_consistency")),
            message=str(run.checkpoint.get("message", "")),
            asked_at=run.checkpoint_asked_at,
            expires_at=run.checkpoint_expires_at,
        )
    return AgentRunOut(
        id=run.id,
        submission_id=run.submission_id,
        provider=run.provider,
        model=run.model,
        reasoning_effort=run.reasoning_effort,
        effective_at="next_turn" if run.pending_config is not None else "current",
        status=run.status.value,
        checkpoint=checkpoint,
        teacher_verdict=run.teacher_verdict,  # type: ignore[arg-type]
        teacher_note=run.teacher_note,
        error_message=run.error_message,
        attempts=run.attempts,
        max_attempts=run_repo.MAX_ATTEMPTS,
        usage=run.usage,
        created_at=run.created_at,
        finished_at=run.finished_at,
    )


def _run_detail_response(db: Session, run: GradingRun) -> AgentRunDetailResponse:
    detail = _run_out(run)
    return AgentRunDetailResponse(
        run=detail,
        latest_seq=run_repo.latest_event_seq(db, run.id),
        applied_config=run.applied_config,
        pending_config=run.pending_config,
    )


@router.get("/runs/by-submission/{submission_id}")
def get_active_run_by_submission(
    submission_id: int, db: Session = Depends(get_db)
) -> AgentRunDetailResponse:
    """查询作业当前的活跃 run;不存在返回 404(前端视为无运行)。"""
    run = run_repo.get_active_run_for_submission(db, submission_id)
    if run is None:
        raise HTTPException(status_code=404, detail="该作业没有进行中的运行")
    return _run_detail_response(db, run)


@router.get("/runs/{run_id}")
def get_run(run_id: int, db: Session = Depends(get_db)) -> AgentRunDetailResponse:
    run = run_repo.get_run(db, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="运行不存在")
    return _run_detail_response(db, run)


@router.patch("/runs/{run_id}/configuration")
def update_run_configuration(
    run_id: int,
    body: AgentConfigurationUpdateRequest,
    db: Session = Depends(get_db),
) -> AgentRunDetailResponse:
    """切换 model/effort;queued 立即生效,运行中排队到下一回合。"""
    run = run_repo.get_run(db, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="运行不存在")
    if run.status in TERMINAL_RUN_STATUSES:
        raise HTTPException(status_code=409, detail="运行已结束，不能修改配置")
    if body.model is None and body.reasoning_effort is None:
        raise HTTPException(status_code=422, detail="至少提供一个配置字段")
    config = {
        "model": body.model if body.model is not None else run.model,
        "reasoning_effort": (
            body.reasoning_effort
            if body.reasoning_effort is not None
            else run.reasoning_effort
        ),
    }
    pending = run.status in (
        GradingRunStatus.starting,
        GradingRunStatus.running,
        GradingRunStatus.waiting_for_teacher,
    )
    run_repo.queue_configuration(db, run, config, pending=pending)
    run_repo.append_event(
        db,
        run_id,
        kind="configuration_queued" if pending else "configuration_updated",
        payload={
            "config": config,
            "effective_at": "next_turn" if pending else "current",
        },
    )
    db.refresh(run)
    return _run_detail_response(db, run)


@router.get("/runs/{run_id}/events")
def list_run_events(
    run_id: int,
    after_seq: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
) -> list[AgentRunEventOut]:
    if run_repo.get_run(db, run_id) is None:
        raise HTTPException(status_code=404, detail="运行不存在")
    events = run_repo.list_events_after(db, run_id, after_seq, limit)
    return [
        AgentRunEventOut(
            seq=e.seq, kind=e.kind, payload=e.payload, created_at=e.created_at
        )
        for e in events
    ]


@router.get("/runs/{run_id}/stream")
async def stream_run_events(
    run_id: int,
    after_seq: int = Query(default=0, ge=0),
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    factory: sessionmaker = Depends(get_session_factory),
) -> StreamingResponse:
    """SSE:先回放数据库事件,再通过 PG NOTIFY 推送增量。

    SSE ``id`` 等于事件 seq,断线重连带 ``Last-Event-ID`` 从该 seq 续读。
    """
    await acquire_sse_slot()
    try:
        resume_seq = max(after_seq, int(last_event_id or 0))
    except ValueError:
        resume_seq = after_seq
    return StreamingResponse(
        _run_event_stream(run_id, factory, after_seq=resume_seq),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


_KEEPALIVE_SECONDS = 15.0
_GRADING_RUN_CHANNEL = "grading_run_events"


async def _run_event_stream(
    run_id: int,
    factory: sessionmaker,
    release: bool = True,
    after_seq: int = 0,
):
    import time

    from app.services.events import _drain_notifies, _listen, _parse_pg_dsn

    last_seq = after_seq
    try:
        # 1) 回放历史
        with factory() as db:
            if db.get(GradingRun, run_id) is None:
                yield 'event: error\ndata: {"detail": "运行不存在"}\n\n'
                return
            while True:
                events = run_repo.list_events_after(db, run_id, last_seq, 500)
                if not events:
                    break
                for e in events:
                    last_seq = e.seq
                    yield _sse_event(e.seq, e.kind, e.payload)

        # 2) LISTEN 增量(PG);非 PG/失败退化为轮询
        conn = None
        try:
            conn = await asyncio.to_thread(
                _listen, _parse_pg_dsn(settings.DATABASE_URL), _GRADING_RUN_CHANNEL
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
                            data.get("run_id") != run_id
                            or data.get("seq", 0) <= last_seq
                        ):
                            continue
                        last_seq = data["seq"]
                        progressed = True
                        yield _sse_event(
                            data["seq"], data.get("kind", ""), data.get("payload", {})
                        )
                with factory() as db:
                    for e in run_repo.list_events_after(db, run_id, last_seq, 200):
                        last_seq = e.seq
                        progressed = True
                        yield _sse_event(e.seq, e.kind, e.payload)
                    run = db.get(GradingRun, run_id)
                    if (
                        run is not None
                        and run.status
                        in (
                            GradingRunStatus.completed,
                            GradingRunStatus.failed,
                            GradingRunStatus.cancelled,
                        )
                        and run_repo.latest_event_seq(db, run_id) <= last_seq
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
        if release:
            from app.services.events import _sse_slots

            _sse_slots.release()


def _sse_event(seq: int, kind: str, payload: dict) -> str:
    data = json.dumps(
        {"seq": seq, "kind": kind, "payload": payload}, ensure_ascii=False
    )
    return f"id: {seq}\nevent: run_event\ndata: {data}\n\n"


@router.post("/runs/{run_id}/cancel")
def cancel_run(run_id: int, db: Session = Depends(get_db)) -> dict:
    run = run_repo.get_run(db, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="运行不存在")
    if run.status in TERMINAL_RUN_STATUSES:
        return {"status": run.status.value}
    if run.status in (
        GradingRunStatus.queued,
        GradingRunStatus.waiting_for_teacher,
    ):
        ok = run_repo.transition_run(db, run, GradingRunStatus.cancelled)
        target = "cancelled"
    else:
        ok = run_repo.request_cancel(db, run)
        target = "cancelling"
    if not ok:
        raise HTTPException(status_code=409, detail="当前状态不可取消")
    run_repo.append_event(db, run_id, kind="notice", payload={"text": "运行已被取消"})
    return {"status": target}


@router.post("/runs/{run_id}/reply")
def reply_checkpoint(
    run_id: int, body: AgentRunReplyRequest, db: Session = Depends(get_db)
) -> dict:
    """教师答复检查点;答复持久化后由 worker 恢复续跑。"""
    run = run_repo.get_run(db, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="运行不存在")
    verdict = str(body.verdict)
    try:
        ok = checkpoint_service.answer_checkpoint(
            db, run, verdict=verdict, note=body.note
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not ok:
        raise HTTPException(status_code=409, detail="当前没有等待答复的检查点")
    run = run_repo.get_run(db, run_id)
    run_repo.append_event(
        db,
        run_id,
        kind="notice",
        payload={"text": f"教师检查点答复: {verdict}"},
    )
    return {
        "status": run.status.value if run is not None else "unknown",
        "verdict": verdict,
    }


# ---------------------------------------------------------------------------
# 模型通道配置
# ---------------------------------------------------------------------------


@router.get("/providers", response_model=ProviderListResponse)
def list_providers(db: Session = Depends(get_db)) -> ProviderListResponse:
    return ProviderListResponse(**provider_summary(db))


@router.put("/providers/default", response_model=ProviderListResponse)
def set_default_provider(
    body: ProviderDefaultUpdateRequest, db: Session = Depends(get_db)
) -> ProviderListResponse:
    set_provider_config(db, {PROVIDER_DEFAULT_KEY: body.provider})
    return ProviderListResponse(**provider_summary(db))


@router.put("/providers/{name}/config", response_model=ProviderListResponse)
def update_provider_config(
    name: str, body: ProviderConfigUpdateRequest, db: Session = Depends(get_db)
) -> ProviderListResponse:
    """更新通道配置;null 不改动、空串清除;密钥只写不读。"""
    if name not in PROVIDER_NAMES:
        raise HTTPException(status_code=404, detail=f"未知通道 {name}")
    updates: dict[str, object] = {}
    if name == PROVIDER_OPENAI_API:
        if body.api_key is not None:
            updates[OPENAI_API_KEY_KEY] = body.api_key
        if body.base_url is not None:
            updates[OPENAI_BASE_URL_KEY] = body.base_url
        if body.default_model is not None:
            updates[OPENAI_DEFAULT_MODEL_KEY] = body.default_model
        if body.reasoning_effort is not None:
            updates[OPENAI_REASONING_EFFORT_KEY] = body.reasoning_effort
    else:  # chatgpt_sub:仅默认模型;api_key/base_url/effort 不适用
        unsupported = [
            field
            for field, value in (
                ("api_key", body.api_key),
                ("base_url", body.base_url),
                ("reasoning_effort", body.reasoning_effort),
            )
            if value is not None
        ]
        if unsupported:
            raise HTTPException(
                status_code=422,
                detail=f"订阅通道不支持字段: {', '.join(unsupported)}",
            )
        if body.default_model is not None:
            updates[CHATGPT_DEFAULT_MODEL_KEY] = body.default_model
    set_provider_config(db, updates)
    return ProviderListResponse(**provider_summary(db))


@router.post("/providers/{name}/test", response_model=ProviderTestResponse)
async def test_provider(
    name: str, db: Session = Depends(get_db)
) -> ProviderTestResponse:
    if name not in PROVIDER_NAMES:
        raise HTTPException(status_code=404, detail=f"未知通道 {name}")
    if name == PROVIDER_CHATGPT_SUB:
        return ProviderTestResponse(
            name=name, status="unsupported", detail="订阅通道尚未开放"
        )
    config = get_provider_config(db)
    api_key = config.get(OPENAI_API_KEY_KEY) or ""
    if not api_key:
        return ProviderTestResponse(
            name=name, status="needs_auth", detail="尚未配置 API key"
        )
    base_url = (config.get(OPENAI_BASE_URL_KEY) or DEFAULT_BASE_URL).rstrip("/")
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{base_url}/models",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=15.0,
            )
    except httpx.HTTPError as exc:
        return ProviderTestResponse(
            name=name, status="failed", detail=f"{type(exc).__name__}: {exc}"[:300]
        )
    if response.status_code in (401, 403):
        return ProviderTestResponse(
            name=name, status="needs_auth", detail="API key 无效或未授权"
        )
    if response.status_code >= 400:
        return ProviderTestResponse(
            name=name,
            status="failed",
            detail=f"HTTP {response.status_code}: {response.text[:200]}",
        )
    models: list[str] = []
    try:
        payload = response.json()
        models = [
            str(item.get("id"))
            for item in payload.get("data", [])
            if isinstance(item, dict) and item.get("id")
        ][:50]
    except (ValueError, AttributeError):
        models = []
    return ProviderTestResponse(name=name, status="ready", models=models)
