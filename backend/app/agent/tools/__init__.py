"""进程内工具注册表:模型看到的工具定义与直调 handler 的绑定。

- ``ToolRegistry.specs()`` 产出发给模型的工具定义;
- ``execute()`` 直调 handler,把业务错误(ConflictError/ValidationError/
  NotFoundError/ValueError)转成可修正的 ``ToolResult(ok=False)`` 回给模型
  (沿用原协议 422 证据回环语义);
- ``CheckpointRequested`` 由 handler 主动抛出,逐层穿透由 engine 捕获。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable

from app.agent.models import ToolResult, ToolSpec

Handler = Callable[[dict[str, Any]], ToolResult]


class CheckpointRequested(Exception):  # noqa: N818 - 契约名,与 plans/docs 一致
    """工具请求教师确认,run 应驻留为 waiting_for_teacher。"""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass
class _RegisteredTool:
    spec: ToolSpec
    handler: Handler


class ToolRegistry:
    """工具名到 handler 的显式绑定;未知工具返回可修正错误。"""

    def __init__(self) -> None:
        self._tools: dict[str, _RegisteredTool] = {}

    def register(self, spec: ToolSpec, handler: Handler) -> None:
        if spec.name in self._tools:
            raise ValueError(f"工具 {spec.name} 重复注册")
        self._tools[spec.name] = _RegisteredTool(spec=spec, handler=handler)

    def specs(self) -> list[ToolSpec]:
        return [item.spec for item in self._tools.values()]

    def tool_names(self) -> set[str]:
        return set(self._tools)

    def execute(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        tool = self._tools.get(name)
        if tool is None:
            available = "、".join(sorted(self._tools))
            return ToolResult(
                ok=False, error=f"未知工具 {name!r};可用工具: {available}"
            )
        try:
            return tool.handler(arguments)
        except CheckpointRequested:
            raise
        except Exception as exc:  # noqa: BLE001 - 业务错误转可修正线索
            return ToolResult(ok=False, error=_business_error_text(exc))


def _business_error_text(exc: BaseException) -> str:
    """把业务异常转成给模型看的中文线索。"""
    detail = getattr(exc, "detail", None)
    if detail is None:
        return str(exc) or type(exc).__name__
    if isinstance(detail, str):
        return detail
    try:
        return json.dumps(detail, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(detail)
