"""提示词 × 工具子集矩阵测试。

约束：
- 每个接入图的提示词只允许提到它所属节点能拿到的工具（多提即漂移，
  模型会去调用不存在的工具）；
- 必须提到该节点的提交工具，否则模型无从落地；
- 所有提示词都不得出现旧 MCP 工具名与已删除的 ``request_teacher_confirmation``；
- 评分要求（grading_policy.requirements）的核心证据术语必须在评分子 agent
  提示词里有对应锚点，防止两边各自"自洽"。
"""

from __future__ import annotations

import pytest

from app.agent.graph.tool_guard import build_node_tools
from app.agent.prompts.loader import (
    PROMPTS_DIR,
    evidence_instructions,
    item_scorer_instructions,
    load_prompt,
    review_instructions,
    rubric_instructions,
    supervisor_instructions,
)
from app.agent.tools.grading import GradingToolContext, build_grading_tools
from app.agent.tools.inspection import InspectionContext, build_inspection_tools
from app.agent.tools.scoring import (
    add_evidence_tool,
    add_rubric_tool,
    add_scoring_tool,
)
from app.application.grading_package import build_grading_policy
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

REMOVED_TOOL_NAMES = ("request_teacher_confirmation",)

SCORER_ARTIFACT = {
    "version": 1,
    "sections": [{"name": "question", "text": "", "chars": 0}],
    "code_files": [],
    "rubric": {"snapshot_id": None, "items": []},
    "has_code": False,
}


def _grading_registry():
    ctx = GradingToolContext(session_factory=None, run_id=0, submission_id=0)  # type: ignore[arg-type]
    return build_grading_tools(ctx)


def _scorer_registry():
    return add_scoring_tool(
        build_inspection_tools(InspectionContext(artifact=SCORER_ARTIFACT))
    )


def _rubric_registry():
    return add_rubric_tool(
        build_inspection_tools(InspectionContext(artifact=SCORER_ARTIFACT)),
        ocr_text="",
    )


def _evidence_registry():
    return add_evidence_tool(
        build_inspection_tools(InspectionContext(artifact=SCORER_ARTIFACT))
    )


# (提示词, 图节点, 该节点真实使用的工具注册表, 必须提及的提交工具,
#  允许以「禁止调用」形式出现的写工具)
PROMPT_NODE_MATRIX = (
    ("supervisor", "supervisor_finalize", _grading_registry, "save_assessment", frozenset()),
    (
        "item_scorer",
        "score_item",
        _scorer_registry,
        "submit_item_score",
        frozenset({"save_assessment", "save_question_rubric", "confirm_visual_review"}),
    ),
    (
        "rubric",
        "rubric_extract",
        _rubric_registry,
        "submit_question_rubric",
        frozenset(
            {"save_question_rubric", "submit_item_score", "save_assessment"}
        ),
    ),
    (
        "evidence",
        "evidence_verify",
        _evidence_registry,
        "submit_evidence_fix",
        frozenset({"submit_item_score", "save_assessment", "save_question_rubric"}),
    ),
)


def _all_tool_names() -> set[str]:
    return (
        set(_grading_registry().tool_names())
        | set(_scorer_registry().tool_names())
        | set(_rubric_registry().tool_names())
        | set(_evidence_registry().tool_names())
    )


def _mentioned_tools(text: str) -> set[str]:
    return {name for name in _all_tool_names() if name in text}


@pytest.mark.parametrize(
    ("prompt_name", "node", "registry_factory", "submit_tool", "forbidden_ok"),
    PROMPT_NODE_MATRIX,
)
def test_prompt_only_mentions_tools_the_node_can_call(
    prompt_name: str,
    node: str,
    registry_factory,
    submit_tool: str,
    forbidden_ok: frozenset[str],
) -> None:
    text = load_prompt(prompt_name)
    allowed = build_node_tools(node, registry_factory()).tool_names()
    extra = _mentioned_tools(text) - allowed - forbidden_ok
    assert not extra, f"{prompt_name}.md 提到了节点 {node} 无权调用的工具: {extra}"
    assert submit_tool in text, f"{prompt_name}.md 必须写明提交工具 {submit_tool}"
    assert submit_tool in allowed


@pytest.mark.parametrize(
    ("prompt_name", "node", "registry_factory", "submit_tool", "forbidden_ok"),
    PROMPT_NODE_MATRIX,
)
def test_prompt_has_no_legacy_or_removed_tool_names(
    prompt_name: str, node: str, registry_factory, submit_tool: str, forbidden_ok: frozenset[str]
) -> None:
    del node, registry_factory, submit_tool, forbidden_ok
    text = load_prompt(prompt_name)
    for legacy in LEGACY_TOOL_NAMES + REMOVED_TOOL_NAMES:
        assert legacy not in text, f"{prompt_name}.md 不应引用工具 {legacy}"


def test_review_prompt_is_present_and_legacy_free():
    text = review_instructions()
    assert "AI-Marking" in text
    for legacy in LEGACY_TOOL_NAMES:
        assert legacy not in text


def test_prompt_dir_is_relative_to_loader():
    assert PROMPTS_DIR.is_absolute()
    for name in ("supervisor", "item_scorer", "evidence", "rubric", "review"):
        assert (PROMPTS_DIR / f"{name}.md").is_file()
    # 旧提示词（单一大文件/分阶段文件）已收敛
    for retired in ("grading", "finalize"):
        assert not (PROMPTS_DIR / f"{retired}.md").exists()


def test_loader_reads_prompts():
    assert len(supervisor_instructions()) > 200
    assert "submit_item_score" in item_scorer_instructions()
    assert "submit_evidence_fix" in evidence_instructions()
    assert "submit_question_rubric" in rubric_instructions()
    assert load_prompt("supervisor") == supervisor_instructions()
    assert load_prompt("evidence") == evidence_instructions()
    assert load_prompt("rubric") == rubric_instructions()


def test_scoring_requirements_terms_anchored_in_scorer_prompt():
    """评分包 requirements 的证据术语必须在评分子 agent 提示词中有锚点。"""
    resolved = resolve_rubric(None, {})
    requirements = " ".join(build_grading_policy(resolved)["requirements"])
    text = item_scorer_instructions()
    for term in ("evidence_status", "not_found", "source_line", "report_quote"):
        assert term in requirements, f"requirements 缺少术语 {term}"
        assert term in text, f"item_scorer.md 缺少术语锚点 {term}"


def test_supervisor_prompt_keeps_final_score_with_teacher():
    text = supervisor_instructions()
    assert "由教师在网页确认" in text
    assert "feedback" in text
    assert "confidence" in text


def test_agents_skills_directory_is_removed():
    """.agents/skills 已删除;流程指令唯一来源收敛到 prompts/。"""
    repo_root = PROMPTS_DIR.parents[3]
    assert not (repo_root / ".agents" / "skills").exists()
