"""ACP skill 物化裁剪测试: 剔除 MCP 专属块 + 指针一致性守卫。"""

from __future__ import annotations

import pytest

from app.acp.workspace import _skill_source, _trim_skill_for_acp

START = "<!-- @@mcp-only -->"
END = "<!-- @@/mcp-only -->"


# ---------------------------------------------------------------------------
# _trim_skill_for_acp 单元测试
# ---------------------------------------------------------------------------


def test_trim_without_markers_unchanged():
    text = "共享正文 A\n共享正文 B\n"
    assert _trim_skill_for_acp(text) == text


def test_trim_removes_mcp_block_keeps_shared():
    text = (
        "共享开头\n"
        f"{START}\n"
        "MCP 专属第一行\nMCP 专属第二行\n"
        f"{END}\n"
        "共享结尾\n"
    )
    assert _trim_skill_for_acp(text) == "共享开头\n\n共享结尾\n"


def test_trim_multiple_blocks():
    text = (
        f"{START}块1{END}\n"
        "中间共享\n"
        f"{START}块2{END}\n"
        "末尾共享\n"
    )
    assert _trim_skill_for_acp(text) == "\n中间共享\n\n末尾共享\n"


def test_trim_marker_inline_with_content():
    text = f"公式{START}被删{END}保留\n"
    assert _trim_skill_for_acp(text) == "公式保留\n"


def test_trim_unclosed_start_raises():
    text = f"共享\n{START}\n未闭合\n"
    with pytest.raises(ValueError, match="未闭合"):
        _trim_skill_for_acp(text)


# ---------------------------------------------------------------------------
# 真实 SKILL.md 守卫
# ---------------------------------------------------------------------------


def test_real_skill_has_mcp_appendix_and_pointer_not_dangling():
    skill = _skill_source()
    appendix = skill.parent / "references" / "mcp-only.md"
    assert skill.is_file()
    assert appendix.is_file(), "SKILL.md 指针指向的 references/mcp-only.md 必须存在"
    assert "references/mcp-only.md" in skill.read_text(encoding="utf-8")


def test_real_skill_trim_removes_mcp_only_keeps_shared():
    raw = _skill_source().read_text(encoding="utf-8")
    trimmed = _trim_skill_for_acp(raw)

    assert trimmed.strip(), "裁剪结果不能为空"
    assert "@@mcp-only" not in trimmed
    assert "references/mcp-only.md" not in trimmed
    assert "list_pending" not in trimmed
    # MCP 专属约束（上传 ZIP/CSV）不进入 ACP 版
    assert "不要上传 ZIP" not in trimmed

    # 共享骨架必须保留
    assert "### 模式判定" in trimmed
    assert "看一眼当前工作区根目录" in trimmed
    # 评分流程步骤 5 起可读
    assert "打开并读完评分包" in trimmed
    # rubric 提取章节保留
    assert "提取题目 rubric" in trimmed
    # ACP 专属 bullet 保留
    assert "ACP 模式" in trimmed
