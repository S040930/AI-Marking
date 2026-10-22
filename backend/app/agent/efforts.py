"""思考强度(reasoning.effort)档位:各通道的实测可用集合。

官方通道保持宽松(只用于 UI 提示),订阅通道按 spike 实测白名单强校验:
目录声明的 ``ultra`` 与 ``minimal`` 实测会被 400 拒绝
(见 docs/architecture/chatgpt-sub-spike.md §A6)。
"""

from __future__ import annotations

OPENAI_EFFORTS: tuple[str, ...] = ("minimal", "low", "medium", "high")
CHATGPT_SUB_EFFORTS: tuple[str, ...] = ("low", "medium", "high", "xhigh", "max")


def effort_allowed(provider: str, effort: str | None) -> bool:
    """空值表示"用模型默认";只对订阅通道做白名单,避免误伤官方新档位。"""
    if not effort:
        return True
    if provider == "chatgpt_sub":
        return effort in CHATGPT_SUB_EFFORTS
    return True
