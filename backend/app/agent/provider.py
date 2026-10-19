"""AgentProvider 协议与统一错误分类。

两条通道(官方 OpenAI API、ChatGPT 订阅适配器)实现同一协议;
provider 只负责"把一次回合变成事件流",不触碰数据库与业务状态。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Literal, Protocol

from app.agent.models import TurnRequest

ErrorKind = Literal[
    "auth_required",
    "rate_limited",
    "context_length",
    "retryable",
    "invalid_request",
    "unknown",
]

_CONTEXT_MARKERS = (
    "context_length",
    "context length",
    "maximum context",
    "too many tokens",
    "too large",
)


@dataclass
class AgentProviderError(Exception):
    """provider 层统一错误;引擎按 kind/retryable 决定重试或终止。"""

    kind: ErrorKind
    message: str
    status_code: int | None = None
    retryable: bool = False
    retry_after: float | None = None

    def __str__(self) -> str:  # pragma: no cover - 展示用
        return f"[{self.kind}] {self.message}"


@dataclass(frozen=True)
class ModelOption:
    """一个可用模型及其支持的推理档位。"""

    id: str
    label: str = ""
    efforts: tuple[str, ...] = ()


@dataclass(frozen=True)
class ProviderAuthStatus:
    ready: bool
    detail: str | None = None


class ProviderEvent:
    """provider 事件基类;子类见下。"""


@dataclass(frozen=True)
class ProviderTextDelta(ProviderEvent):
    text: str


@dataclass(frozen=True)
class ProviderReasoningDelta(ProviderEvent):
    text: str


@dataclass(frozen=True)
class ProviderToolCallStarted(ProviderEvent):
    call_id: str
    name: str


@dataclass(frozen=True)
class ProviderTurnCompleted(ProviderEvent):
    """一次回合的终态:完整 item 列表(用于重放/执行)与 usage。"""

    stop_reason: str
    response_items: list[dict[str, Any]] = field(default_factory=list)
    usage: dict[str, Any] | None = None
    # 通道私有的续会话状态(订阅端为 {"turn_state": ...}),由引擎带回下一回合
    provider_state: dict[str, Any] | None = None


class AgentProvider(Protocol):
    """单个模型通道的抽象。"""

    name: str
    default_model: str | None

    def create_turn(self, request: TurnRequest) -> AsyncIterator[ProviderEvent]:
        """执行一次回合并流式产出事件;失败抛 AgentProviderError。"""
        ...

    async def list_models(self) -> list[ModelOption]:
        ...

    def auth_status(self) -> ProviderAuthStatus:
        ...

    async def aclose(self) -> None:
        ...


def classify_http_error(
    status_code: int,
    body: str = "",
    *,
    retry_after: float | None = None,
) -> AgentProviderError:
    """把 HTTP 层错误映射为统一错误类别。"""
    lowered = (body or "").lower()
    if status_code in (401, 403):
        return AgentProviderError(
            kind="auth_required",
            message="模型凭证无效或未授权",
            status_code=status_code,
        )
    if status_code == 429:
        return AgentProviderError(
            kind="rate_limited",
            message="模型通道限流",
            status_code=status_code,
            retryable=True,
            retry_after=retry_after,
        )
    if status_code in (400, 413, 422) and any(
        marker in lowered for marker in _CONTEXT_MARKERS
    ):
        return AgentProviderError(
            kind="context_length",
            message="上下文超出模型窗口",
            status_code=status_code,
        )
    if status_code >= 500:
        return AgentProviderError(
            kind="retryable",
            message=f"模型服务暂时不可用(HTTP {status_code})",
            status_code=status_code,
            retryable=True,
            retry_after=retry_after,
        )
    return AgentProviderError(
        kind="invalid_request",
        message=f"请求被模型服务拒绝(HTTP {status_code})",
        status_code=status_code,
    )


def transport_error(exc: BaseException) -> AgentProviderError:
    """网络层异常统一为 retryable。"""
    return AgentProviderError(
        kind="retryable",
        message=f"网络错误: {type(exc).__name__}: {exc}",
        retryable=True,
    )


def parse_retry_after(value: str | None) -> float | None:
    """解析 Retry-After 头(秒);非数值或负数返回 None。"""
    if not value:
        return None
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return None
    return seconds if seconds >= 0 else None
