"""提示词加载:用 ``Path(__file__)`` 相对定位,不依赖进程工作目录。"""

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
def grading_instructions() -> str:
    """批改 agent 的系统指令(唯一入口;缓存避免每 run 重复读盘)。"""
    return load_prompt("grading")


@lru_cache(maxsize=4)
def review_instructions() -> str:
    """独立复核 agent 的系统指令(v1 未接入 run)。"""
    return load_prompt("review")
