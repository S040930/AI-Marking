"""提示词加载:用 ``Path(__file__)`` 相对定位,不依赖进程工作目录。

每个可调用模型图节点对应一个提示词文件（见 graph/tool_guard.py 的工具白名单）：
- ``supervisor.md``：主管最终提交（supervisor_finalize）；
- ``item_scorer.md``：逐项评分子 agent（score_item）；
- ``evidence.md``：独立证据核查 agent（evidence_verify，只修证据引用）；
- ``rubric.md``：rubric 提取 agent（rubric_extract，只读题目 OCR）；
- ``review.md``：独立复核 agent（当前未接入图，保留供后续启用）。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parent


def load_prompt(name: str) -> str:
    """读取 prompts/<name>.md 全文。"""
    path = PROMPTS_DIR / f"{name}.md"
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        raise ValueError(f"提示词文件为空: {path.name}")
    return text


@lru_cache(maxsize=4)
def supervisor_instructions() -> str:
    """主管最终提交节点的系统指令（缓存避免每 run 重复读盘）。"""
    return load_prompt("supervisor")


@lru_cache(maxsize=4)
def item_scorer_instructions() -> str:
    """逐项评分子 agent 的系统指令。"""
    return load_prompt("item_scorer")


@lru_cache(maxsize=4)
def evidence_instructions() -> str:
    """独立证据核查 agent 的系统指令（只修证据引用）。"""
    return load_prompt("evidence")


@lru_cache(maxsize=4)
def rubric_instructions() -> str:
    """rubric 提取 agent 的系统指令（只读题目 OCR）。"""
    return load_prompt("rubric")


@lru_cache(maxsize=4)
def review_instructions() -> str:
    """独立复核 agent 的系统指令（当前未接入 run）。"""
    return load_prompt("review")
