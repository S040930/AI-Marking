"""提示词与工具契约同步测试。

约束:
- grading.md 只描述流程并显式委派评分规则(唯一权威 = 评分包内的
  grading_policy.requirements),不复述规则;
- grading.md 提到的工具名集合 == ToolRegistry.specs() 的实际工具名;
- 旧 MCP 工具名不得残留;
- 评分包 requirements 的核心术语必须在 grading.md 中有对应锚点,
  防止两边漂移而各自"自洽"。
"""

from __future__ import annotations

from app.agent.prompts.loader import (
    PROMPTS_DIR,
    grading_instructions,
    load_prompt,
    review_instructions,
)
from app.agent.tools.grading import GradingToolContext, build_grading_tools
from app.application.grading_workflow import build_grading_policy
from app.services.rubric import resolve_rubric

LEGACY_TOOL_NAMES = (
    "open_ai_marking_assignment",
    "save_ai_marking_question_rubric",
    "confirm_ai_marking_visual_review",
    "save_ai_marking_assessment",
    "save_ai_marking_assessment_review",
    "list_pending_ai_marking_assignments",
    "prepare_ai_marking_submission",
)

DELEGATION_ANCHOR = "grading_policy.requirements"


def _tool_names() -> set[str]:
    ctx = GradingToolContext(session_factory=None, run_id=0, submission_id=0)  # type: ignore[arg-type]
    return build_grading_tools(ctx).tool_names()


def test_prompt_dir_is_relative_to_loader():
    assert PROMPTS_DIR.is_absolute()
    assert (PROMPTS_DIR / "grading.md").is_file()
    assert (PROMPTS_DIR / "review.md").is_file()


def test_loader_reads_prompts():
    assert len(grading_instructions()) > 500
    assert "AI-Marking" in review_instructions()
    assert load_prompt("grading") == grading_instructions()


def test_grading_prompt_delegates_scoring_rules():
    text = grading_instructions()
    assert DELEGATION_ANCHOR in text
    assert "唯一权威" in text
    # 流程文档不复述具体评分规则:不得出现 rubric item 的自由定义
    assert "resolved_rubric" in text  # 只引用评分包字段名


def test_grading_prompt_tool_names_match_registry():
    text = grading_instructions()
    names = _tool_names()
    assert names == {
        "open_grading_package",
        "save_question_rubric",
        "confirm_visual_review",
        "save_assessment",
        "request_teacher_confirmation",
    }
    for name in names:
        assert name in text, f"grading.md 缺少工具 {name}"


def test_grading_prompt_has_no_legacy_tool_names():
    text = grading_instructions() + review_instructions()
    for legacy in LEGACY_TOOL_NAMES:
        assert legacy not in text, f"提示词不应引用旧工具 {legacy}"


def test_requirements_terms_are_anchored_in_prompt():
    """评分包 requirements 的核心术语必须在 grading.md 有对应锚点。"""
    resolved = resolve_rubric(None, {})
    requirements = " ".join(build_grading_policy(resolved)["requirements"])
    text = grading_instructions()
    for term in (
        "resolved_rubric",
        "evidence_status",
        "not_found",
        "source_line",
        "report_quote",
    ):
        assert term in requirements, f"requirements 缺少术语 {term}"
        assert term in text, f"grading.md 缺少术语锚点 {term}"


def test_grading_prompt_marks_pause_as_expected():
    text = grading_instructions()
    assert "预期行为" in text
    assert "不得" in text


def test_agents_skills_directory_is_removed():
    """.agents/skills 已删除;流程指令唯一来源收敛到 prompts/。"""
    repo_root = PROMPTS_DIR.parents[3]
    assert not (repo_root / ".agents" / "skills").exists()
