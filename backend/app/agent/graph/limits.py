"""批改图的预算、并发与上下文上限。

这些值是“硬闸”，不是建议值：节点在超限时必须 fail-closed，而不是
静默截断后继续评分。可由 settings 覆盖的值在 P4 接入，P0 先保持常量，
避免在骨架阶段引入未验证的配置面。
"""

from __future__ import annotations

# 并发：官方通道可并行；订阅通道 turn_state 是会话级状态，默认更低。
MAX_PARALLEL_OPENAI = 3
MAX_PARALLEL_CHATGPT_SUB = 2

# fan-out：1..FANOUT_ITEMS 逐项；超过后按 CHUNK_SIZE 合并；
# 超过 HARD_ITEM_LIMIT 直接失败，提示拆分作业。
FANOUT_ITEMS = 12
CHUNK_SIZE = 3
HARD_ITEM_LIMIT = 40

# 上下文预算：评分包全文绝不进子 agent，只给 brief + 只读检索结果。
MAX_AGENT_INPUT_CHARS = 24_000
MAX_TOOL_OUTPUT_CHARS = 8_000
MAX_BRIEF_CHARS = 6_000

# 回合与预算。
MAX_FINALIZE_TURNS = 2
MAX_ROUNDS = {"rubric": 2, "scorer": 4, "verify": 3}
RUN_TOKEN_BUDGET = 1_500_000


def max_parallel_for(provider: str) -> int:
    """按通道返回 fan-out 并发；未知通道按保守值处理。"""
    if provider == "openai_api":
        return MAX_PARALLEL_OPENAI
    return MAX_PARALLEL_CHATGPT_SUB
