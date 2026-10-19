"""LangGraph 批改图：状态、限制、checkpointer 与后续节点实现。

本包只依赖 ``app.agent`` 与 ``app.core``，不反向依赖 ``app.api``；
模型调用走 ``ModelGateway``（复用现有 AgentProvider），不实现
``BaseChatModel``，以保留 Responses item 原文与 ``x-codex-turn-state``。
"""

from app.agent.graph.limits import (
    CHUNK_SIZE,
    FANOUT_ITEMS,
    HARD_ITEM_LIMIT,
    MAX_AGENT_INPUT_CHARS,
    MAX_BRIEF_CHARS,
    MAX_FINALIZE_TURNS,
    MAX_PARALLEL_CHATGPT_SUB,
    MAX_PARALLEL_OPENAI,
    MAX_ROUNDS,
    MAX_TOOL_OUTPUT_CHARS,
    RUN_TOKEN_BUDGET,
    max_parallel_for,
)
from app.agent.graph.state import (
    GradingState,
    ItemResult,
    PackageDigest,
    TeacherAnswer,
    merge_by_key,
    merge_item_results,
    merge_usage,
)

__all__ = [
    "CHUNK_SIZE",
    "FANOUT_ITEMS",
    "HARD_ITEM_LIMIT",
    "MAX_AGENT_INPUT_CHARS",
    "MAX_BRIEF_CHARS",
    "MAX_FINALIZE_TURNS",
    "MAX_PARALLEL_CHATGPT_SUB",
    "MAX_PARALLEL_OPENAI",
    "MAX_ROUNDS",
    "MAX_TOOL_OUTPUT_CHARS",
    "RUN_TOKEN_BUDGET",
    "GradingState",
    "ItemResult",
    "PackageDigest",
    "TeacherAnswer",
    "max_parallel_for",
    "merge_by_key",
    "merge_item_results",
    "merge_usage",
]
