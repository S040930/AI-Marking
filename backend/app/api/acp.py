"""ACP 批改 HTTP/SSE 接口;业务规则在 application 层,此处只做适配。"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import subprocess
from pathlib import Path

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session, sessionmaker

from app.acp import runs as run_repo
from app.acp.errors import AcpError
from app.acp.model_catalog import (
    CodexConfigurationCatalog,
    catalog_from_config_options,
    get_codex_configuration,
    normalized_snapshot,
    reset_model_cache,
    validate_codex_selection,
)
from app.acp.models import AcpAgentInstallation, AcpRun
from app.acp.registry import (
    DEFAULT_AGENT_WHITELIST,
    AgentEntry,
    RegistryClient,
    RegistryError,
    acp_cache_dir,
)
from app.core.config import settings
from app.db.session import get_db, get_session_factory
from app.schemas.acp import (
    AcpAgentListResponse,
    AcpAgentOut,
    AcpCodexConfigurationResponse,
    AcpConfigurationUpdateRequest,
    AcpInstallResponse,
    AcpRunCreateRequest,
    AcpRunDetailResponse,
    AcpRunEventOut,
    AcpRunOut,
    AcpRunReplyRequest,
    AcpTestConnectionResponse,
    AcpUninstallResponse,
    CodexConfigSnapshot,
)
from app.services.events import acquire_sse_slot

router = APIRouter(prefix="/acp", tags=["acp"])
logger = logging.getLogger(__name__)

# 必须为绝对路径:该目录会作为 ACP session 的 cwd 传给 agent,
# codex 对相对路径的 session cwd 无法启动注入的 MCP stdio 服务。
# 目录定义统一在 app.acp.registry.acp_cache_dir();保留模块级别名,
# 测试通过 monkeypatch _CACHE_DIR 覆盖。
_CACHE_DIR = acp_cache_dir()


def _registry_client() -> RegistryClient:
    return RegistryClient(_CACHE_DIR, whitelist=DEFAULT_AGENT_WHITELIST)


# ---------------------------------------------------------------------------
# Agent 目录
# ---------------------------------------------------------------------------


@router.get("/agents", response_model=AcpAgentListResponse)
async def list_agents(db: Session = Depends(get_db)) -> AcpAgentListResponse:
    """白名单 agent 目录;Registry 不可达时返回缓存或空目录(可离线浏览)。"""
    try:
        payload = await _registry_client().fetch()
        fetched_at = payload.get("fetched_at")
    except RegistryError:
        payload = {}
        fetched_at = None
    agents: list[AcpAgentOut] = []
    for agent_id, rule in DEFAULT_AGENT_WHITELIST.items():
        entry = _registry_client().lookup(payload, agent_id) or {}
        installation = db.get(AcpAgentInstallation, agent_id)
        installed = installation.version if installation else None
        available = str(entry.get("version") or "") or None
        agents.append(
            AcpAgentOut(
                agent_id=agent_id,
                display_name=str(entry.get("name") or agent_id),
                whitelist_package=rule["package"],
                distributions=sorted(rule["distributions"]),
                installed_version=installed,
                available_version=available,
                connection_status=(
                    installation.connection_status if installation else "unknown"
                ),
            )
        )
    from datetime import datetime

    return AcpAgentListResponse(
        agents=agents,
        registry_version=str(payload.get("version"))
        if payload.get("version")
        else None,
        fetched_at=(
            datetime.fromtimestamp(fetched_at)
            if isinstance(fetched_at, (int, float)) and fetched_at > 0
            else None
        ),
    )


def _save_test_status(
    db: Session, installation: AcpAgentInstallation, status: str, detail: str | None
) -> None:
    from app.core.time import utc_now_naive

    installation.connection_status = status
    installation.connection_detail = detail[:1024] if detail else None
    installation.tested_at = utc_now_naive()
    db.commit()


@router.post("/agents/refresh")
async def refresh_registry() -> dict:
    try:
        payload = await _registry_client().fetch(force_refresh=True)
    except RegistryError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"refreshed_at": payload.get("fetched_at")}


@router.post("/agents/{agent_id}/test", response_model=AcpTestConnectionResponse)
async def test_agent_connection(
    agent_id: str, db: Session = Depends(get_db)
) -> AcpTestConnectionResponse:
    """连接诊断:initialize → 能力协商 → 安全关闭;不保存认证秘密。"""
    if agent_id not in DEFAULT_AGENT_WHITELIST:
        raise HTTPException(status_code=404, detail="agent 不在白名单中")
    installation = db.get(AcpAgentInstallation, agent_id)
    if installation is None:
        return AcpTestConnectionResponse(
            agent_id=agent_id, status="failed", detail="agent 尚未安装"
        )
    entry = AgentEntry.from_dict(installation.launch_snapshot)
    from app.acp.session import AcpSession

    callbacks = _NoopCallbacks()
    session = AcpSession(_spec_for(entry), callbacks, turn_timeout_seconds=180)
    try:
        response = await session.start()
        new = await session.new_session(
            cwd=str(_CACHE_DIR),
            mcp_servers=[
                __import__(
                    "app.acp.orchestrator", fromlist=["mcp_server_config"]
                ).mcp_server_config()
            ],
        )
        prompt = await session.prompt(
            new.session_id,
            "只调用 ai-marking MCP 的 health_ai_marking 工具一次并简短回复结果。不要执行其他操作。",
        )
        if prompt.stop_reason != "end_turn":
            raise AcpError(f"MCP 诊断未正常结束: {prompt.stop_reason}")
        if not callbacks.mcp_health_seen:
            raise AcpError("agent 未调用 AI-Marking MCP 健康检查工具")
    except Exception as exc:
        detail = str(exc)
        lowered = detail.lower()
        status = (
            "needs_auth"
            if any(word in lowered for word in ("login", "auth", "登录", "认证"))
            else "failed"
        )
        _save_test_status(db, installation, status, detail)
        return AcpTestConnectionResponse(
            agent_id=agent_id,
            status=status,
            detail=detail,
            steps={"initialize": "failed"},
        )
    finally:
        await session.close()
    _save_test_status(db, installation, "ready", None)
    reset_model_cache()
    return AcpTestConnectionResponse(
        agent_id=agent_id,
        status="ready",
        agent_info=session.agent_info,
        protocol_version=response.protocol_version,
        mcp_visible=True,
        steps={"initialize": "ready", "session": "ready", "mcp": "ready"},
    )


class _NoopCallbacks:
    def __init__(self) -> None:
        self.mcp_health_seen = False

    async def on_event(self, event) -> None:
        title = str(getattr(event, "payload", {}).get("title", "")).lower()
        if "health_ai_marking" in title:
            self.mcp_health_seen = True

    async def resolve_permission(self, session_id, tool_call, options):
        return None

    async def resolve_elicitation(self, session_id, message, schema):
        return None


def _spec_for(entry):
    from app.acp.security import secure_launch_spec

    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return secure_launch_spec(entry, _CACHE_DIR)


def _resolve_installed_entry(db: Session, agent_id: str):
    installation = db.get(AcpAgentInstallation, agent_id)
    return AgentEntry.from_dict(installation.launch_snapshot) if installation else None


def _codex_installation(db: Session) -> AcpAgentInstallation:
    installation = db.get(AcpAgentInstallation, "codex-acp")
    if installation is None:
        raise HTTPException(status_code=409, detail="codex-acp 尚未安装")
    return installation


def _codex_entry(db: Session) -> AgentEntry:
    return AgentEntry.from_dict(_codex_installation(db).launch_snapshot)


def _empty_codex_catalog(entry: AgentEntry, error: str) -> CodexConfigurationCatalog:
    return catalog_from_config_options([], entry=entry, capability_error=error)


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


def _validate_fast_confirmation(
    *, requested_speed: str, current_speed: str, fast_confirmed: bool
) -> None:
    if requested_speed == "fast" and current_speed != "fast" and not fast_confirmed:
        raise HTTPException(
            status_code=422,
            detail="切换到快速模式前必须确认可能增加额度或成本",
        )


async def _resolve_codex_config(
    db: Session,
    request,
    *,
    current: dict | None = None,
    allow_adjustment: bool = False,
) -> dict:
    """Resolve a browser selection against a live Codex catalog."""
    entry = _codex_entry(db)
    requested_model = request.model_id
    catalog = await get_codex_configuration(entry, requested_model)
    if catalog is None:
        if requested_model or request.reasoning_effort or request.speed_mode == "fast":
            raise HTTPException(
                status_code=422,
                detail="无法读取当前 Codex 配置能力，暂时只能使用 Agent 默认模型与标准模式",
            )
        return {
            "model_id": None,
            "reasoning_effort": None,
            "speed_mode": "standard",
            "option_ids": {},
            "catalog_version": f"codex-acp@{entry.version}",
        }

    current_speed = str((current or {}).get("speed_mode") or "standard")
    _validate_fast_confirmation(
        requested_speed=request.speed_mode,
        current_speed=current_speed,
        fast_confirmed=bool(request.fast_confirmed),
    )
    try:
        selected = validate_codex_selection(
            {
                "model_id": request.model_id,
                "reasoning_effort": request.reasoning_effort,
                "speed_mode": request.speed_mode,
            },
            catalog,
        )
    except ValueError as exc:
        if not allow_adjustment:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        # A model switch may legitimately invalidate the old thought/speed
        # value.  Preserve the model and use the new Agent defaults.
        selected = {
            "model_id": catalog.selected_model_id,
            "reasoning_effort": catalog.default_reasoning_effort,
            "speed_mode": catalog.default_speed_mode,
        }
    return normalized_snapshot(selected, catalog)


@router.get(
    "/codex/configuration", response_model=AcpCodexConfigurationResponse
)
async def get_codex_configuration_catalog(
    model_id: str | None = Query(default=None, min_length=1, max_length=128),
    db: Session = Depends(get_db),
) -> AcpCodexConfigurationResponse:
    """Return the live Codex model/thought/speed catalog for the UI."""
    entry = _codex_entry(db)
    catalog = await get_codex_configuration(entry, model_id)
    if catalog is None:
        catalog = _empty_codex_catalog(entry, "无法读取当前 Codex 配置能力")
    return AcpCodexConfigurationResponse(**catalog.public_dict())


# ---------------------------------------------------------------------------
# 安装与默认 Agent
# ---------------------------------------------------------------------------


@router.post("/agents/{agent_id}/install", response_model=AcpInstallResponse)
async def install_agent(
    agent_id: str, db: Session = Depends(get_db)
) -> AcpInstallResponse:
    """安装白名单 agent 的精确版本;已缓存同版本时不重复下载。

    npm/uvx 发行:安装即解析并审核精确版本快照(npx/uvx 在 spawn 时按
    精确版本拉取);binary 发行:下载归档 → SHA-256 校验 → 安全解压到
    受控缓存,并把 command 指向解压出的可执行文件。
    """
    if agent_id not in DEFAULT_AGENT_WHITELIST:
        raise HTTPException(status_code=404, detail="agent 不在白名单中")
    client = _registry_client()
    try:
        payload = await client.fetch()
        raw = client.lookup(payload, agent_id)
        if raw is None:
            raise RegistryError(f"Registry 中没有 agent {agent_id}")
        version = str(raw.get("version") or "")
        installation = db.get(AcpAgentInstallation, agent_id)
        installed = installation.version if installation else None
        if version and installed == version and installation is not None:
            cached_entry = AgentEntry.from_dict(installation.launch_snapshot)
            command_exists = (
                cached_entry.distribution != "npx"
                or Path(cached_entry.command).is_file()
            )
            if command_exists:
                return AcpInstallResponse(
                    agent_id=agent_id,
                    version=version,
                    distribution=installation.distribution,
                    reinstalled=False,
                )
        entry = await client.resolve(agent_id, version)
        if entry.distribution == "binary":
            await asyncio.to_thread(_install_binary, agent_id, entry)
        elif entry.distribution == "npx":
            await asyncio.to_thread(_install_npx, agent_id, entry)
        else:
            raise RegistryError("首期审核名单不支持 uvx 发行")
    except RegistryError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    if installation is None:
        installation = AcpAgentInstallation(
            agent_id=agent_id,
            version=entry.version,
            distribution=entry.distribution,
            launch_snapshot=entry.to_dict(),
        )
        db.add(installation)
    else:
        installation.version = entry.version
        installation.distribution = entry.distribution
        installation.launch_snapshot = entry.to_dict()
        installation.connection_status = "unknown"
        installation.connection_detail = None
    db.commit()
    reset_model_cache()
    return AcpInstallResponse(
        agent_id=agent_id,
        version=entry.version,
        distribution=entry.distribution,
        reinstalled=True,
    )


def _install_npx(agent_id: str, entry: AgentEntry) -> None:
    """Install an exact npm package and execute its local bin without network."""
    from app.acp.registry import agent_cache_dir

    dest = agent_cache_dir(_CACHE_DIR, agent_id, entry.version)
    dest.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [
            "npm",
            "install",
            "--ignore-scripts",
            "--no-audit",
            "--no-fund",
            "--prefix",
            str(dest),
            f"{entry.package}@{entry.version}",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=300,
    )
    if result.returncode != 0:
        raise RegistryError(f"npm 精确版本安装失败: {result.stderr[-1000:]}")
    package_json = (
        dest / "node_modules" / Path(entry.package).parts[-1] / "package.json"
    )
    if entry.package.startswith("@"):
        scope, name = entry.package.split("/", 1)
        package_json = dest / "node_modules" / scope / name / "package.json"
    try:
        metadata = json.loads(package_json.read_text(encoding="utf-8"))
        bin_value = metadata["bin"]
        bin_name = (
            next(iter(bin_value))
            if isinstance(bin_value, dict)
            else str(metadata["name"]).split("/")[-1]
        )
    except (OSError, ValueError, KeyError, StopIteration) as exc:
        raise RegistryError("npm 包没有可审核的命令入口") from exc
    command = dest / "node_modules" / ".bin" / bin_name
    if not command.is_file():
        raise RegistryError("npm 包命令入口不存在")
    # Registry 的前两个参数是 npx 自身参数，切换成本地 bin 后移除。
    entry.command = str(command.resolve())
    entry.args = entry.args[2:]


def _install_binary(agent_id: str, entry) -> None:
    """binary 发行:下载 + 校验 + 安全解压;command 指向解压出的可执行文件。"""
    from app.acp.registry import (
        agent_cache_dir,
        download_to,
        safe_extract,
        verify_sha256,
    )

    if not entry.source_url or not entry.sha256:
        raise RegistryError(f"agent {agent_id} 的 binary 发行缺少下载地址或 SHA-256")
    dest_dir = agent_cache_dir(_CACHE_DIR, agent_id, entry.version)
    dest_dir.mkdir(parents=True, exist_ok=True)
    archive_name = entry.source_url.rsplit("/", 1)[-1] or "agent.bin"
    archive = dest_dir / archive_name
    if not archive.exists():
        download_to(entry.source_url, archive)
    verify_sha256(archive, entry.sha256)
    executable_name = Path(entry.command).name or entry.package
    extracted = safe_extract(
        archive, dest_dir / "dist", allowed_executables={executable_name}
    )
    if extracted:
        entry.command = str(extracted[0])


@router.delete("/agents/{agent_id}/install", response_model=AcpUninstallResponse)
def uninstall_agent(
    agent_id: str, db: Session = Depends(get_db)
) -> AcpUninstallResponse:
    """卸载已安装链接:删除安装标记与该版本下载缓存。

    与"移出白名单"不同:卸载后 agent 仍留在目录中(状态回到未安装),
    可随时重新安装;npx/uvx 发行不触碰本机全局包缓存。
    """
    if agent_id not in DEFAULT_AGENT_WHITELIST:
        raise HTTPException(status_code=404, detail="agent 不在白名单中")
    entry = _resolve_installed_entry(db, agent_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="agent 尚未安装")
    active = run_repo.count_active_runs_for_agent(db, agent_id)
    if active:
        raise HTTPException(
            status_code=409,
            detail=f"该助手有 {active} 个进行中的批改运行,请先取消或等待结束",
        )
    removed_cache = _remove_installed_artifacts(agent_id, entry.version)
    # 卸载后旧的连接测试结论失效;默认助手必须是已安装状态,故清除标记。
    db.delete(db.get(AcpAgentInstallation, agent_id))
    db.commit()
    reset_model_cache()
    return AcpUninstallResponse(
        agent_id=agent_id,
        version=entry.version,
        removed_cache=removed_cache,
    )


def _remove_installed_artifacts(agent_id: str, version: str) -> bool:
    """删除安装标记与 <version> 缓存目录;返回是否删除了缓存目录。"""
    agent_dir = (_CACHE_DIR / "agents" / agent_id).resolve()
    (agent_dir / "installed.json").unlink(missing_ok=True)
    if not version:
        return False
    target = (agent_dir / version).resolve()
    # 只允许删除 agent 目录内的一级版本目录,防目录穿越。
    if target.parent != agent_dir or not target.is_dir():
        return False
    shutil.rmtree(target, ignore_errors=True)
    return not target.exists()


@router.post("/agents/{agent_id}/set-default")
def set_default_agent(agent_id: str, db: Session = Depends(get_db)) -> dict:
    """教师显式选择机器级默认 agent;要求该 agent 已安装。"""
    if agent_id not in DEFAULT_AGENT_WHITELIST:
        raise HTTPException(status_code=404, detail="agent 不在白名单中")
    installation = db.get(AcpAgentInstallation, agent_id)
    if installation is None:
        raise HTTPException(status_code=409, detail="agent 尚未安装,请先安装")
    db.query(AcpAgentInstallation).update({"is_default": False})
    installation.is_default = True
    db.commit()
    return {"default_agent": agent_id}


@router.get("/default-agent")
def get_default_agent(db: Session = Depends(get_db)) -> dict:
    return {"agent_id": _default_agent_id(db)}


# ---------------------------------------------------------------------------
# Run 生命周期
# ---------------------------------------------------------------------------


@router.post("/runs")
async def create_run(
    body: AcpRunCreateRequest, db: Session = Depends(get_db)
) -> AcpRunDetailResponse:
    from app.models.submission import Submission, SubmissionStatus

    sub = db.get(Submission, body.submission_id)
    if sub is None:
        raise HTTPException(status_code=404, detail="提交记录不存在")
    if sub.status != SubmissionStatus.awaiting_mcp:
        raise HTTPException(status_code=409, detail="该作业当前不可发起 ACP 批改")
    active = run_repo.get_active_run_for_submission(db, body.submission_id)
    if active is not None:
        raise HTTPException(
            status_code=409, detail=f"该作业已有进行中的运行 (run {active.id})"
        )
    agent_id = "codex-acp"
    installation = db.get(AcpAgentInstallation, agent_id)
    if installation is None:
        raise HTTPException(status_code=409, detail=f"agent {agent_id} 尚未安装")
    if installation.connection_status != "ready":
        raise HTTPException(
            status_code=409, detail=f"agent {agent_id} 尚未通过连接诊断"
        )
    entry = AgentEntry.from_dict(installation.launch_snapshot)
    config = await _resolve_codex_config(db, body.codex_config)
    try:
        run = run_repo.create_run(
            db,
            submission_id=body.submission_id,
            agent_id=agent_id,
            agent_snapshot=entry.to_dict(),
            workspace_path="",
            permission_mode=body.permission_mode,
            codex_config=config,
        )
        run_repo.append_event(
            db,
            run.id,
            kind="notice",
            payload={
                "text": "已创建 Codex 批改运行",
                "permission_mode": body.permission_mode,
                "codex_config": _snapshot_dict(config),
            },
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _run_detail_response(db, run)


def _default_agent_id(db: Session) -> str:
    installation = (
        db.query(AcpAgentInstallation)
        .filter(AcpAgentInstallation.is_default.is_(True))
        .first()
    )
    if installation is not None:
        return installation.agent_id
    # 默认助手被卸载或从未设置:回落到白名单第一个(codex-acp)。
    return next(iter(DEFAULT_AGENT_WHITELIST), "")


def _run_out(run: AcpRun) -> AcpRunOut:
    from app.schemas.acp import AcpCheckpointOut

    checkpoint = None
    if run.checkpoint is not None:
        checkpoint = AcpCheckpointOut(
            type=str(run.checkpoint.get("type", "code_consistency")),
            message=str(run.checkpoint.get("message", "")),
            asked_at=run.checkpoint_asked_at,
            expires_at=run.checkpoint_expires_at,
        )
    return AcpRunOut(
        id=run.id,
        submission_id=run.submission_id,
        agent_id=run.agent_id,
        permission_mode=run.permission_mode,
        codex_config=_snapshot_model(run.codex_config),
        applied_codex_config=(
            _snapshot_model(run.applied_codex_config)
            if run.applied_codex_config is not None
            else None
        ),
        pending_codex_config=(
            _snapshot_model(run.pending_codex_config)
            if run.pending_codex_config is not None
            else None
        ),
        effective_at="next_turn" if run.pending_codex_config is not None else "current",
        status=run.status.value,
        acp_session_id=run.acp_session_id,
        checkpoint=checkpoint,
        teacher_verdict=run.teacher_verdict,  # type: ignore[arg-type]
        teacher_note=run.teacher_note,
        error_message=run.error_message,
        attempts=run.attempts,
        max_attempts=run_repo.MAX_ATTEMPTS,
        created_at=run.created_at,
        finished_at=run.finished_at,
    )


def _run_detail_response(db: Session, run: AcpRun) -> AcpRunDetailResponse:
    detail = _run_out(run)
    return AcpRunDetailResponse(
        run=detail,
        latest_seq=run_repo.latest_event_seq(db, run.id),
        desired_config=detail.codex_config,
        applied_config=detail.applied_codex_config,
        pending_config=detail.pending_codex_config,
        effective_at=detail.effective_at,
    )


@router.patch("/runs/{run_id}/codex-configuration")
async def update_run_codex_configuration(
    run_id: int,
    body: AcpConfigurationUpdateRequest,
    db: Session = Depends(get_db),
) -> AcpRunDetailResponse:
    run = run_repo.get_run(db, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="运行不存在")
    from app.acp.domain import TERMINAL_RUN_STATUSES, AcpRunStatus

    if run.status in TERMINAL_RUN_STATUSES:
        raise HTTPException(status_code=409, detail="运行已结束，不能修改配置")
    config = await _resolve_codex_config(
        db,
        body,
        current=run.codex_config or run.applied_codex_config,
        allow_adjustment=True,
    )
    pending = run.status in (
        AcpRunStatus.starting,
        AcpRunStatus.running,
        AcpRunStatus.waiting_for_teacher,
    )
    run_repo.queue_codex_configuration(db, run, config, pending=pending)
    run_repo.append_event(
        db,
        run_id,
        kind="configuration_queued" if pending else "configuration_updated",
        payload={
            "codex_config": _snapshot_dict(config),
            "effective_at": "next_turn" if pending else "worker_start",
        },
    )
    db.refresh(run)
    return _run_detail_response(db, run)


@router.get("/runs/by-submission/{submission_id}")
def get_active_run_by_submission(
    submission_id: int, db: Session = Depends(get_db)
) -> AcpRunDetailResponse:
    """查询作业当前的活跃 run;不存在返回 404(前端视为无运行)。"""
    run = run_repo.get_active_run_for_submission(db, submission_id)
    if run is None:
        raise HTTPException(status_code=404, detail="该作业没有进行中的运行")
    return _run_detail_response(db, run)


@router.get("/runs/{run_id}")
def get_run(run_id: int, db: Session = Depends(get_db)) -> AcpRunDetailResponse:
    run = run_repo.get_run(db, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="运行不存在")
    return _run_detail_response(db, run)


@router.get("/runs/{run_id}/events")
def list_run_events(
    run_id: int,
    after_seq: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
) -> list[AcpRunEventOut]:
    if run_repo.get_run(db, run_id) is None:
        raise HTTPException(status_code=404, detail="运行不存在")
    events = run_repo.list_events_after(db, run_id, after_seq, limit)
    return [
        AcpRunEventOut(
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
            if db.get(AcpRun, run_id) is None:
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
                _listen, _parse_pg_dsn(settings.DATABASE_URL), "acp_run_events"
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
                    run = db.get(AcpRun, run_id)
                    if (
                        run is not None
                        and run.status
                        in (
                            "completed",
                            "failed",
                            "cancelled",
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
    from app.acp.domain import AcpRunStatus

    run = run_repo.get_run(db, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="运行不存在")
    if run.status in ("completed", "failed", "cancelled"):
        return {"status": run.status.value}
    if run.status in (AcpRunStatus.queued, AcpRunStatus.waiting_for_teacher):
        ok = run_repo.transition_run(db, run, AcpRunStatus.cancelled)
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
    run_id: int, body: AcpRunReplyRequest, db: Session = Depends(get_db)
) -> dict:
    """教师答复检查点;答复持久化后由 worker 恢复会话续跑。"""
    run = run_repo.get_run(db, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="运行不存在")
    verdict = str(body.verdict)
    try:
        ok = run_repo.answer_checkpoint(db, run, verdict=verdict, note=body.note)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not ok:
        raise HTTPException(status_code=409, detail="当前没有等待答复的检查点")
    run_repo.append_event(
        db,
        run_id,
        kind="notice",
        payload={"text": f"教师检查点答复: {verdict}"},
    )
    return {"status": run.status.value, "verdict": verdict}
