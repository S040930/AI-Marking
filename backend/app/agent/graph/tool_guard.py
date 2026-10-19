"""图节点的工具白名单：写工具只发给对应的 supervisor 节点。"""

from __future__ import annotations

from app.agent.tools import ToolRegistry
from app.agent.tools.grading import (
    TOOL_CONFIRM_VISUAL,
    TOOL_SAVE_ASSESSMENT,
    TOOL_SAVE_RUBRIC,
)

NODE_WRITE_TOOLS: dict[str, frozenset[str]] = {
    "supervisor_rubric": frozenset({TOOL_SAVE_RUBRIC}),
    "supervisor_confirm": frozenset({TOOL_CONFIRM_VISUAL}),
    "supervisor_finalize": frozenset({TOOL_SAVE_ASSESSMENT}),
}


def build_node_tools(node: str, registry: ToolRegistry) -> ToolRegistry:
    """只读工具全给；写工具只给该节点被允许的那一个。"""
    allowed = NODE_WRITE_TOOLS.get(node, frozenset())
    return registry.subset(
        lambda spec: (not spec.writes) or spec.name in allowed
    )


def write_tool_names() -> frozenset[str]:
    return frozenset(
        name for names in NODE_WRITE_TOOLS.values() for name in names
    )
