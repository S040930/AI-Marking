"""子 agent 的提交工具：逐项评分、证据修正、rubric 提取。

这些结果都不是通过写数据库提交，而是由模型调用工具；节点从返回的
function_call 参数中收集结果，再交给服务端确定性校验/落库。这样
子 agent 分支始终是只读的，只有 supervisor 系列节点能写。
"""

from __future__ import annotations

from typing import Any

from app.agent.models import ToolResult, ToolSpec
from app.agent.tools import ToolRegistry

TOOL_SUBMIT_ITEM_SCORE = "submit_item_score"
TOOL_SUBMIT_EVIDENCE_FIX = "submit_evidence_fix"
TOOL_SUBMIT_QUESTION_RUBRIC = "submit_question_rubric"

SUBMIT_ITEM_SCORE_SPEC = ToolSpec(
    name=TOOL_SUBMIT_ITEM_SCORE,
    description=(
        "提交一个 rubric 项的评分结果。一次只提交一项；多项请多次调用。"
        "得分大于 0 必须提供可定位证据。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "rubric_item_id": {"type": "string"},
            "score": {"type": "number", "minimum": 0},
            "comment": {"type": "string"},
            "evidence_refs": {
                "type": "array",
                "items": {"type": "object"},
            },
            "evidence_status": {
                "type": "string",
                "enum": ["supported", "not_found"],
            },
        },
        "required": [
            "rubric_item_id",
            "score",
            "comment",
            "evidence_refs",
            "evidence_status",
        ],
        "additionalProperties": False,
    },
)


def _submit_item_score(args: dict[str, Any]) -> ToolResult:
    item_id = str(args.get("rubric_item_id") or "").strip()
    if not item_id:
        return ToolResult(ok=False, error="rubric_item_id 不能为空")
    try:
        score = float(args.get("score"))
    except (TypeError, ValueError):
        return ToolResult(ok=False, error="score 必须是数字")
    if score < 0:
        return ToolResult(ok=False, error="score 不能为负")
    refs = args.get("evidence_refs")
    if not isinstance(refs, list):
        return ToolResult(ok=False, error="evidence_refs 必须是数组")
    status = str(args.get("evidence_status") or "supported")
    if status not in ("supported", "not_found"):
        return ToolResult(
            ok=False, error="evidence_status 只能是 supported / not_found"
        )
    if status == "not_found" and score > 0:
        return ToolResult(ok=False, error="not_found 状态下得分必须为 0")
    if status == "not_found" and refs:
        return ToolResult(ok=False, error="not_found 状态不得引用证据")
    if score > 0 and not refs:
        return ToolResult(ok=False, error="得分大于 0 必须提供至少一条证据")
    return ToolResult(ok=True, output={"accepted": True, "rubric_item_id": item_id})


def add_scoring_tool(registry: ToolRegistry) -> ToolRegistry:
    registry.register(SUBMIT_ITEM_SCORE_SPEC, _submit_item_score)
    return registry


SUBMIT_EVIDENCE_FIX_SPEC = ToolSpec(
    name=TOOL_SUBMIT_EVIDENCE_FIX,
    description=(
        "提交单个评分项的修正证据引用（不改分数与评语）。证据必须能在服务端"
        "上下文中逐字定位；找不到依据时用 evidence_status=not_found 且不引证据。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "rubric_item_id": {"type": "string"},
            "evidence_refs": {"type": "array", "items": {"type": "object"}},
            "evidence_status": {
                "type": "string",
                "enum": ["supported", "not_found"],
            },
        },
        "required": ["rubric_item_id", "evidence_refs", "evidence_status"],
        "additionalProperties": False,
    },
)


def _submit_evidence_fix(args: dict[str, Any]) -> ToolResult:
    item_id = str(args.get("rubric_item_id") or "").strip()
    if not item_id:
        return ToolResult(ok=False, error="rubric_item_id 不能为空")
    refs = args.get("evidence_refs")
    if not isinstance(refs, list):
        return ToolResult(ok=False, error="evidence_refs 必须是数组")
    status = str(args.get("evidence_status") or "supported")
    if status not in ("supported", "not_found"):
        return ToolResult(
            ok=False, error="evidence_status 只能是 supported / not_found"
        )
    if status == "not_found" and refs:
        return ToolResult(ok=False, error="not_found 状态不得引用证据")
    return ToolResult(ok=True, output={"accepted": True, "rubric_item_id": item_id})


def add_evidence_tool(registry: ToolRegistry) -> ToolRegistry:
    registry.register(SUBMIT_EVIDENCE_FIX_SPEC, _submit_evidence_fix)
    return registry


SUBMIT_QUESTION_RUBRIC_SPEC = ToolSpec(
    name=TOOL_SUBMIT_QUESTION_RUBRIC,
    description=(
        "提交从题目 OCR 提取的 rubric。status=complete 时逐项给出 criterion/"
        "max_score/details/source_quote（引用必须是 OCR 原文连续子串且含该项满分），"
        "total_max_score 等于各项满分之和；status=absent_or_ambiguous 时题目没有明确"
        "标准，items 留空。服务端会逐条校验，返回错误时按错误信息修正后重试。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "status": {
                "type": "string",
                "enum": ["complete", "absent_or_ambiguous"],
            },
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "criterion": {"type": "string"},
                        "max_score": {"type": "number"},
                        "details": {"type": "string"},
                        "source_quote": {"type": "string"},
                    },
                    "required": ["criterion", "max_score", "details", "source_quote"],
                    "additionalProperties": False,
                },
            },
            "total_max_score": {"type": "number"},
        },
        "required": ["status"],
        "additionalProperties": False,
    },
)


def _submit_question_rubric(args: dict[str, Any], ocr_text: str) -> ToolResult:
    status = str(args.get("status") or "")
    items = args.get("items") or []
    if status not in ("complete", "absent_or_ambiguous"):
        return ToolResult(
            ok=False, error="status 只能是 complete / absent_or_ambiguous"
        )
    if not isinstance(items, list):
        return ToolResult(ok=False, error="items 必须是数组")
    from app.services.question_rubric import RubricExtraction, validate_extraction

    try:
        extraction = RubricExtraction(
            status=status,
            items=items,
            total_max_score=args.get("total_max_score"),
        )
    except ValueError as exc:
        return ToolResult(ok=False, error=f"rubric 格式错误: {exc}")
    if status == "complete" and not validate_extraction(extraction, ocr_text):
        return ToolResult(
            ok=False,
            error=(
                "rubric 校验失败：source_quote 必须是题目 OCR 原文连续子串且包含该项满分，"
                "各项满分之和必须等于 total_max_score，criterion 不得重复。"
            ),
        )
    return ToolResult(ok=True, output={"accepted": True, "status": status})


def add_rubric_tool(registry: ToolRegistry, *, ocr_text: str) -> ToolRegistry:
    registry.register(
        SUBMIT_QUESTION_RUBRIC_SPEC,
        lambda args: _submit_question_rubric(args, ocr_text),
    )
    return registry
