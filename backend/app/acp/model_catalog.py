"""Runtime Codex ACP configuration discovery and application.

ACP lets an Agent describe its own configuration options.  This module is
the only place where those options become product concepts.  The web API
never accepts ACP option IDs; it accepts only stable model, reasoning and
speed fields.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from app.acp.registry import AgentEntry, acp_cache_dir

logger = logging.getLogger(__name__)

MODEL_CACHE_TTL_SECONDS = 600.0
PROBE_HANDSHAKE_TIMEOUT_SECONDS = 60.0


@dataclass(frozen=True)
class CodexValueOption:
    id: str
    label: str
    description: str | None = None
    current: bool = False


@dataclass(frozen=True)
class CodexSpeedOption:
    id: str
    label: str
    available: bool = True
    current: bool = False
    requires_confirmation: bool = False


@dataclass(frozen=True)
class CodexConfigurationCatalog:
    """A safe, normalized view of one Agent configuration snapshot."""

    agent_id: str
    agent_version: str
    catalog_version: str
    selected_model_id: str | None
    models: tuple[CodexValueOption, ...]
    reasoning_efforts: tuple[CodexValueOption, ...]
    speed_modes: tuple[CodexSpeedOption, ...]
    option_ids: Mapping[str, str]
    # Raw ACP option objects are kept only in memory for applying an update;
    # they are never serialized to the browser or persisted to the database.
    raw_options: tuple[Any, ...] = ()
    capability_error: str | None = None

    @property
    def default_reasoning_effort(self) -> str | None:
        current = next((item.id for item in self.reasoning_efforts if item.current), None)
        return current or (self.reasoning_efforts[0].id if self.reasoning_efforts else None)

    @property
    def default_speed_mode(self) -> str:
        current = next(
            (item.id for item in self.speed_modes if item.current and item.available),
            None,
        )
        available = next((item.id for item in self.speed_modes if item.available), None)
        return current or available or "standard"

    def public_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "agent_version": self.agent_version,
            "selected_model_id": self.selected_model_id,
            "models": [
                {"id": item.id, "label": item.label, "current": item.current}
                for item in self.models
            ],
            "reasoning_efforts": [
                {"id": item.id, "label": item.label, "current": item.current}
                for item in self.reasoning_efforts
            ],
            "speed_modes": [
                {
                    "id": item.id,
                    "label": item.label,
                    "available": item.available,
                    "current": item.current,
                    "requires_confirmation": item.requires_confirmation,
                }
                for item in self.speed_modes
            ],
            "capability_error": self.capability_error,
        }


@dataclass(frozen=True)
class CodexSelectionResult:
    config: dict[str, Any]
    catalog: CodexConfigurationCatalog
    adjusted: bool = False


_cache_lock = asyncio.Lock()
_configuration_cache: dict[str, tuple[float, CodexConfigurationCatalog]] = {}


def reset_model_cache() -> None:
    _configuration_cache.clear()


def _cache_key(entry: AgentEntry, model_id: str | None) -> str:
    return f"{entry.agent_id}@{entry.version}:{model_id or '<default>'}"


def cached_codex_configuration(
    entry: AgentEntry, model_id: str | None = None
) -> CodexConfigurationCatalog | None:
    cached = _configuration_cache.get(_cache_key(entry, model_id))
    if cached is None:
        return None
    timestamp, catalog = cached
    if time.monotonic() - timestamp > MODEL_CACHE_TTL_SECONDS:
        _configuration_cache.pop(_cache_key(entry, model_id), None)
        return None
    return catalog


def _value_options(option: Any) -> list[Any]:
    values: list[Any] = []
    for group_or_value in getattr(option, "options", None) or []:
        nested = getattr(group_or_value, "options", None)
        if nested is not None:
            values.extend(nested)
        else:
            values.append(group_or_value)
    return values


def _label(value: str, name: str) -> str:
    # Codex 的思考程度与模型名保留 agent 原始英文,仅把速度档位本地化为中文。
    return {
        "standard": "标准",
        "fast": "快速",
    }.get(value, name or value)


def _category(option: Any) -> str | None:
    category = getattr(option, "category", None)
    return category if isinstance(category, str) else None


def catalog_from_config_options(
    options: list[Any] | tuple[Any, ...],
    *,
    entry: AgentEntry,
    capability_error: str | None = None,
) -> CodexConfigurationCatalog:
    models: list[CodexValueOption] = []
    reasoning: list[CodexValueOption] = []
    speed: list[CodexSpeedOption] = [
        CodexSpeedOption("standard", "标准", available=True, current=True)
    ]
    option_ids: dict[str, str] = {}
    selected_model_id: str | None = None

    for option in options:
        category = _category(option)
        option_id = str(getattr(option, "id", "") or "")
        if not option_id:
            continue
        current = getattr(option, "current_value", None)
        if category == "model" and getattr(option, "type", None) == "select":
            option_ids["model"] = option_id
            selected_model_id = str(current) if current else None
            for value in _value_options(option):
                value_id = str(getattr(value, "value", "") or "")
                if value_id:
                    models.append(
                        CodexValueOption(
                            value_id,
                            _label(value_id, str(getattr(value, "name", "") or "")),
                            str(getattr(value, "description", "") or "") or None,
                            value_id == str(current),
                        )
                    )
        elif category == "thought_level" and getattr(option, "type", None) == "select":
            option_ids["reasoning_effort"] = option_id
            for value in _value_options(option):
                value_id = str(getattr(value, "value", "") or "")
                if value_id:
                    reasoning.append(
                        CodexValueOption(
                            value_id,
                            _label(value_id, str(getattr(value, "name", "") or "")),
                            str(getattr(value, "description", "") or "") or None,
                            value_id == str(current),
                        )
                    )
        elif option_id == "fast_mode" and getattr(option, "type", None) == "boolean":
            option_ids["speed_mode"] = option_id
            is_fast = bool(current)
            speed = [
                CodexSpeedOption("standard", "标准", current=not is_fast),
                CodexSpeedOption(
                    "fast", "快速", current=is_fast, requires_confirmation=True
                ),
            ]
        elif getattr(option, "type", None) == "select":
            values = [str(getattr(value, "value", "") or "") for value in _value_options(option)]
            if {"standard", "fast"}.issubset(values):
                option_ids["speed_mode"] = option_id
                current_value = str(current or "standard")
                value_objects = _value_options(option)
                speed = [
                    CodexSpeedOption(
                        value,
                        _label(
                            value,
                            next(
                                (
                                    str(getattr(item, "name", "") or "")
                                    for item in value_objects
                                    if str(getattr(item, "value", "") or "") == value
                                ),
                                value,
                            ),
                        ),
                        current=value == current_value,
                        requires_confirmation=value == "fast",
                    )
                    for value in ("standard", "fast")
                ]

    if "speed_mode" not in option_ids:
        speed = [CodexSpeedOption("standard", "标准", current=True)]

    return CodexConfigurationCatalog(
        agent_id=entry.agent_id,
        agent_version=entry.version,
        catalog_version=f"{entry.agent_id}@{entry.version}",
        selected_model_id=selected_model_id,
        models=tuple(models),
        reasoning_efforts=tuple(reasoning),
        speed_modes=tuple(speed),
        option_ids=option_ids,
        raw_options=tuple(options),
        capability_error=capability_error,
    )


def _selection_value(selection: Mapping[str, Any], key: str) -> Any:
    camel = {
        "model_id": "modelId",
        "reasoning_effort": "reasoningEffort",
        "speed_mode": "speedMode",
    }[key]
    return selection.get(key, selection.get(camel))


def validate_codex_selection(
    selection: Mapping[str, Any], catalog: CodexConfigurationCatalog
) -> dict[str, Any]:
    requested_model = _selection_value(selection, "model_id")
    requested_reasoning = _selection_value(selection, "reasoning_effort")
    requested_speed = _selection_value(selection, "speed_mode") or "standard"
    if requested_model in (None, ""):
        model_id = catalog.selected_model_id
    else:
        model_id = str(requested_model)
        if model_id not in {item.id for item in catalog.models}:
            raise ValueError("未知的 Codex 模型选项")
    if requested_reasoning in (None, ""):
        reasoning = catalog.default_reasoning_effort
    else:
        reasoning = str(requested_reasoning)
        if reasoning not in {item.id for item in catalog.reasoning_efforts}:
            raise ValueError("当前模型不支持所选思考强度")
    speed = str(requested_speed)
    if speed not in {item.id for item in catalog.speed_modes}:
        raise ValueError("当前模型不支持所选速度模式")
    selected_speed = next(item for item in catalog.speed_modes if item.id == speed)
    if not selected_speed.available:
        raise ValueError("快速模式当前不可用")
    return {
        "model_id": model_id,
        "reasoning_effort": reasoning,
        "speed_mode": speed,
    }


def normalized_snapshot(
    selection: Mapping[str, Any], catalog: CodexConfigurationCatalog
) -> dict[str, Any]:
    config = validate_codex_selection(selection, catalog)
    return {
        **config,
        "option_ids": dict(catalog.option_ids),
        "catalog_version": catalog.catalog_version,
    }


def _speed_value(catalog: CodexConfigurationCatalog, speed_mode: str) -> str | bool:
    if catalog.option_ids.get("speed_mode") == "fast_mode":
        return speed_mode == "fast"
    return speed_mode


# ACP AgentMode(kind) → 产品权限档位;codex-acp 经 category=="mode" 的
# select 配置项暴露,运行中切换即时影响下一回合的审批走向。
_MODE_KIND_TO_PERMISSION = {
    "standard": "ask",
    "auto_review": "auto_review",
}


def _mode_option(options: list[Any] | tuple[Any, ...]) -> tuple[str | None, str | None, set[str]]:
    """返回 (option_id, 当前值, kind 值集合);kind 取自选项 ``_meta.kind``。"""
    for option in options:
        if _category(option) != "mode" or getattr(option, "type", None) != "select":
            continue
        option_id = str(getattr(option, "id", "") or "")
        if not option_id:
            continue
        current = str(getattr(option, "current_value", "") or "") or None
        kinds: set[str] = set()
        for value in _value_options(option):
            meta = getattr(value, "field_meta", None)
            kind = meta.get("kind") if isinstance(meta, dict) else None
            if isinstance(kind, str) and kind:
                kinds.add(kind)
        return option_id, current, kinds
    return None, None, set()


async def apply_codex_permission_mode(
    session: Any, session_id: str, permission_mode: str
) -> bool:
    """把产品权限档位同步到运行中的 Codex 会话(mode 配置项)。

    Agent 未暴露 mode 配置项或不支持时静默跳过:权限档位仍持久化在
    会话行上,下次进程重建经 ``INITIAL_AGENT_MODE``/``CODEX_CONFIG`` 生效。
    """
    agent_id = getattr(getattr(session, "_spec", None), "agent_id", "codex-acp")
    if agent_id != "codex-acp":
        return False
    if permission_mode not in _MODE_KIND_TO_PERMISSION:
        return False
    kind = next(
        key for key, value in _MODE_KIND_TO_PERMISSION.items() if value == permission_mode
    )
    option_id, _, _ = _mode_option(session.config_options)
    if not option_id:
        return False
    try:
        await session.set_config_option(
            session_id, option_id, kind, strict=True
        )
    except Exception as exc:  # noqa: BLE001 - 权限档位同步失败不阻塞配置更新
        logger.info("权限档位同步到 agent 失败(忽略): %s", exc)
        return False
    return True


def _current_snapshot(catalog: CodexConfigurationCatalog) -> dict[str, Any]:
    return normalized_snapshot(
        {
            "model_id": catalog.selected_model_id,
            "reasoning_effort": catalog.default_reasoning_effort,
            "speed_mode": catalog.default_speed_mode,
        },
        catalog,
    )


async def apply_codex_selection(
    session: Any, session_id: str, selection: Mapping[str, Any]
) -> CodexSelectionResult:
    """Apply model → refreshed catalog → reasoning → refreshed catalog → speed."""
    agent_id = getattr(getattr(session, "_spec", None), "agent_id", "codex-acp")
    if agent_id != "codex-acp":
        raise ValueError("首期只允许 codex-acp")
    version = str(getattr(getattr(session, "_spec", None), "agent_version", "unknown"))
    entry = AgentEntry(
        agent_id="codex-acp",
        registry_version=None,
        distribution="npx",
        package="@agentclientprotocol/codex-acp",
        version=version,
        command="",
        args=[],
        env={},
    )
    catalog = catalog_from_config_options(session.config_options, entry=entry)
    requested_model = _selection_value(selection, "model_id")
    requested_reasoning = _selection_value(selection, "reasoning_effort")
    requested_speed = _selection_value(selection, "speed_mode") or "standard"
    model_switch = (
        requested_model not in (None, "")
        and str(requested_model) != catalog.selected_model_id
    )
    if model_switch:
        # The old model's reasoning/speed values are intentionally not
        # validated here: the new model is authoritative after the model
        # option has been applied and a fresh config snapshot is returned.
        model_id = str(requested_model)
        if model_id not in {item.id for item in catalog.models}:
            raise ValueError("未知的 Codex 模型选项")
        target = {
            "model_id": model_id,
            "reasoning_effort": (
                str(requested_reasoning) if requested_reasoning not in (None, "") else None
            ),
            "speed_mode": str(requested_speed),
        }
    else:
        target = validate_codex_selection(selection, catalog)
    adjusted = False

    model_id = target.get("model_id")
    model_option_id = catalog.option_ids.get("model")
    if model_id and model_option_id and catalog.selected_model_id != model_id:
        await session.set_config_option(
            session_id, model_option_id, model_id, strict=True
        )
        catalog = catalog_from_config_options(session.config_options, entry=entry)
        if target["reasoning_effort"] not in {
            item.id for item in catalog.reasoning_efforts
        }:
            target["reasoning_effort"] = catalog.default_reasoning_effort
            adjusted = True
        if target["speed_mode"] not in {item.id for item in catalog.speed_modes}:
            target["speed_mode"] = catalog.default_speed_mode
            adjusted = True
        else:
            selected_speed = next(
                item for item in catalog.speed_modes if item.id == target["speed_mode"]
            )
            if not selected_speed.available:
                target["speed_mode"] = catalog.default_speed_mode
                adjusted = True

    if target["reasoning_effort"] in (None, ""):
        target["reasoning_effort"] = catalog.default_reasoning_effort
    if target["speed_mode"] not in {item.id for item in catalog.speed_modes}:
        target["speed_mode"] = catalog.default_speed_mode
        adjusted = True

    reasoning_id = catalog.option_ids.get("reasoning_effort")
    if reasoning_id and target.get("reasoning_effort") != catalog.default_reasoning_effort:
        await session.set_config_option(
            session_id,
            reasoning_id,
            str(target["reasoning_effort"]),
            strict=True,
        )
        catalog = catalog_from_config_options(session.config_options, entry=entry)

    selected_speed = next(
        (item for item in catalog.speed_modes if item.id == target["speed_mode"]),
        None,
    )
    if selected_speed is None or not selected_speed.available:
        target["speed_mode"] = catalog.default_speed_mode
        adjusted = True

    speed_id = catalog.option_ids.get("speed_mode")
    if speed_id and target["speed_mode"] != catalog.default_speed_mode:
        await session.set_config_option(
            session_id,
            speed_id,
            _speed_value(catalog, str(target["speed_mode"])),
            strict=True,
        )
        catalog = catalog_from_config_options(session.config_options, entry=entry)

    actual = _current_snapshot(catalog)
    adjusted = adjusted or any(
        actual[key] != target.get(key)
        for key in ("model_id", "reasoning_effort", "speed_mode")
    )
    return CodexSelectionResult(config=actual, catalog=catalog, adjusted=adjusted)


async def probe_codex_configuration(
    entry: AgentEntry, *, model_id: str | None = None, workspace: Path | None = None
) -> CodexConfigurationCatalog | None:
    """Probe one installed Codex Agent with no MCP servers."""
    from app.acp.security import secure_launch_spec
    from app.acp.session import AcpSession, ClientCallbacks

    cwd = (workspace or acp_cache_dir()).resolve()
    cwd.mkdir(parents=True, exist_ok=True)

    class _ProbeCallbacks(ClientCallbacks):
        async def on_event(self, event: Any) -> None:
            return None

        async def resolve_permission(
            self, session_id: str, tool_call: Any, options: list[Any]
        ) -> str | None:
            return None

        async def resolve_elicitation(
            self, session_id: str, message: str, requested_schema: Any
        ) -> dict[str, Any] | None:
            return None

    session = AcpSession(secure_launch_spec(entry, cwd), _ProbeCallbacks())
    try:
        await session.start()
        new = await asyncio.wait_for(
            session.new_session(cwd=str(cwd), mcp_servers=[]),
            timeout=PROBE_HANDSHAKE_TIMEOUT_SECONDS,
        )
        catalog = catalog_from_config_options(new.config_options or [], entry=entry)
        if model_id and model_id != catalog.selected_model_id:
            if model_id not in {item.id for item in catalog.models}:
                return None
            option_id = catalog.option_ids.get("model")
            if not option_id:
                return None
            await session.set_config_option(
                new.session_id, option_id, model_id, strict=True
            )
            catalog = catalog_from_config_options(
                session.config_options, entry=entry
            )
        return catalog
    except Exception:  # noqa: BLE001 - probing is best effort
        logger.info("Codex 配置探测失败", exc_info=True)
        return None
    finally:
        await session.close()


async def get_codex_configuration(
    entry: AgentEntry, model_id: str | None = None
) -> CodexConfigurationCatalog | None:
    async with _cache_lock:
        cached = cached_codex_configuration(entry, model_id)
        if cached is not None:
            return cached
        catalog = await probe_codex_configuration(entry, model_id=model_id)
        if catalog is not None:
            _configuration_cache[_cache_key(entry, model_id)] = (
                time.monotonic(),
                catalog,
            )
        return catalog
