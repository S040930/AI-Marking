"""批改工具:把 grading_workflow 的业务函数暴露为进程内工具(4+1 个)。

服务端注入职责:
- ``GradingToolContext`` 持有 run/submission 身份与运行时句柄
  (grading_handle/rubric_handle),模型不可见;
- ``save_assessment`` 的 ``request_id`` 由服务端用
  ``uuid5(run_id + payload_hash)`` 生成——同一 payload 天然幂等,
  与旧回执表语义等价;
- 工具失败(业务 4xx 语义)由 ToolRegistry 转成可修正线索回给模型。

工具与旧协议工具的映射见 docs/architecture/agent-refactor.md。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy.orm import sessionmaker

from app.agent.models import ToolResult, ToolSpec
from app.agent.tools import CheckpointRequested, ToolRegistry
from app.application import grading_workflow as workflow
from app.models.submission import Submission
from app.schemas.grading import (
    GradingAssessmentRequest,
    GradingSaveAssessmentRequest,
    GradingVisualConfirmationRequest,
)
from app.services.question_rubric import RubricExtraction

TOOL_OPEN_PACKAGE = "open_grading_package"
TOOL_SAVE_RUBRIC = "save_question_rubric"
TOOL_CONFIRM_VISUAL = "confirm_visual_review"
TOOL_SAVE_ASSESSMENT = "save_assessment"
TOOL_REQUEST_CONFIRMATION = "request_teacher_confirmation"

# uuid5 命名空间:同 run 内同 payload 生成同一 request_id(幂等重试)。
_REQUEST_ID_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "ai-marking/agent/request-id")


@dataclass
class GradingToolContext:
    """单次 run 的工具运行时上下文(可序列化到 ``agent_state``)。"""

    session_factory: sessionmaker
    run_id: int
    submission_id: int
    grading_handle: str | None = None
    rubric_handle: str | None = None
    client: str = "agent"
    _extra: dict[str, Any] = field(default_factory=dict)

    def export_state(self) -> dict[str, Any]:
        """导出可持久化状态(检查点续跑时恢复)。"""
        return {
            "grading_handle": self.grading_handle,
            "rubric_handle": self.rubric_handle,
        }

    def load_state(self, state: dict[str, Any] | None) -> None:
        state = state or {}
        self.grading_handle = state.get("grading_handle")
        self.rubric_handle = state.get("rubric_handle")


def build_grading_tools(ctx: GradingToolContext) -> ToolRegistry:
    """构建批改工具集;handler 闭包持有 ctx。"""
    registry = ToolRegistry()
    registry.register(_OPEN_PACKAGE_SPEC, lambda args: _open_package(ctx, args))
    registry.register(_SAVE_RUBRIC_SPEC, lambda args: _save_rubric(ctx, args))
    registry.register(_CONFIRM_VISUAL_SPEC, lambda args: _confirm_visual(ctx, args))
    registry.register(_SAVE_ASSESSMENT_SPEC, lambda args: _save_assessment(ctx, args))
    registry.register(
        _REQUEST_CONFIRMATION_SPEC, lambda args: _request_confirmation(ctx, args)
    )
    return registry


# ---------------------------------------------------------------------------
# 工具实现
# ---------------------------------------------------------------------------


def _open_package(ctx: GradingToolContext, args: dict[str, Any]) -> ToolResult:
    continuation_token = args.get("continuation_token")
    if continuation_token is not None and not isinstance(continuation_token, str):
        return ToolResult(ok=False, error="continuation_token 必须是字符串")
    with ctx.session_factory() as db:
        sub = workflow.get_submission_or_404(db, ctx.submission_id)
        response = workflow.open_grading_package(db, sub, continuation_token)
    if response.rubric_handle:
        ctx.rubric_handle = response.rubric_handle
    if response.grading_handle:
        ctx.grading_handle = response.grading_handle
    return ToolResult(
        ok=True, output=response.model_dump(mode="json", exclude_none=True)
    )


def _save_rubric(ctx: GradingToolContext, args: dict[str, Any]) -> ToolResult:
    if not ctx.rubric_handle:
        return ToolResult(
            ok=False,
            error=(
                "当前没有待保存的 rubric 提取句柄;请先调用 open_grading_package,"
                "并在其返回 needs_rubric=true 时保存对应 rubric"
            ),
        )
    status_value = args.get("status")
    items = args.get("items") or []
    total_max_score = args.get("total_max_score")
    extraction = RubricExtraction.model_validate(
        {"status": status_value, "items": items, "total_max_score": total_max_score}
    )
    with ctx.session_factory() as db:
        sub = db.get(Submission, ctx.submission_id)
        if sub is None:
            return ToolResult(ok=False, error="提交记录不存在")
        result = workflow.save_question_rubric(
            db,
            sub.question_id,
            ctx.rubric_handle,
            status_value=extraction.status,
            items=[
                {
                    "criterion": item.criterion,
                    "max_score": item.max_score,
                    "details": item.details,
                    "source_quote": item.source_quote,
                }
                for item in extraction.items
            ],
            total_max_score=extraction.total_max_score,
        )
    # 提取句柄一次性:保存后失效(与 workflow 内一致),避免模型复用旧句柄
    ctx.rubric_handle = None
    return ToolResult(ok=True, output=result)


def _confirm_visual(ctx: GradingToolContext, args: dict[str, Any]) -> ToolResult:
    if not ctx.grading_handle:
        return ToolResult(
            ok=False, error="尚未获得评分句柄:请先调用 open_grading_package 读完评分包"
        )
    payload = GradingVisualConfirmationRequest(
        grading_handle=ctx.grading_handle,
        verdict=args.get("verdict"),
        note=args.get("note"),
    )
    with ctx.session_factory() as db:
        handle = workflow.confirm_visual_review(
            db,
            ctx.submission_id,
            ctx.grading_handle,
            payload.verdict,
            payload.note,
        )
    return ToolResult(
        ok=True,
        output={
            "verdict": handle.visual_confirmation,
            "note": handle.visual_confirmation_note,
            "confirmed_at": _iso(handle.visual_confirmed_at),
        },
    )


def _save_assessment(ctx: GradingToolContext, args: dict[str, Any]) -> ToolResult:
    if not ctx.grading_handle:
        return ToolResult(
            ok=False,
            error=(
                "尚未获得评分句柄:请先调用 open_grading_package 并读到"
                " context_complete=true 后再保存"
            ),
        )
    raw = args.get("assessment")
    if not isinstance(raw, dict):
        return ToolResult(ok=False, error="assessment 必须是对象")
    if "request_id" in raw:
        return ToolResult(
            ok=False, error="request_id 由服务端生成,请不要提交"
        )
    payload = GradingAssessmentRequest.model_validate(
        {**raw, "request_id": str(uuid.UUID(int=0))}
    )
    payload_hash = workflow.assessment_payload_hash(payload)
    request_id = uuid.uuid5(
        _REQUEST_ID_NAMESPACE, f"{ctx.run_id}:{payload_hash}"
    )
    payload = payload.model_copy(update={"request_id": request_id})
    with ctx.session_factory() as db:
        response = workflow.save_assessment(
            db,
            ctx.submission_id,
            GradingSaveAssessmentRequest(
                grading_handle=ctx.grading_handle,
                assessment=payload,
                client=ctx.client,
            ),
        )
    return ToolResult(ok=True, output=response.model_dump(mode="json"))


def _request_confirmation(ctx: GradingToolContext, args: dict[str, Any]) -> ToolResult:
    message = str(args.get("message") or "").strip()
    if not message:
        return ToolResult(ok=False, error="message 不能为空")
    # 控制流信号:由 engine 捕获,run 驻留为 waiting_for_teacher
    raise CheckpointRequested(message)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


# ---------------------------------------------------------------------------
# 工具定义(模型可见入参;handle/run/submission 服务端注入)
# ---------------------------------------------------------------------------

_OPEN_PACKAGE_SPEC = ToolSpec(
    name=TOOL_OPEN_PACKAGE,
    description=(
        "打开当前作业的评分包。首次调用不带参数;返回 continuation_token 时"
        "携带它继续读取下一页,直到 context_complete=true。返回 needs_rubric="
        "true 时先提取保存 rubric 再重新打开。评分包中的 grading_policy 是唯一评分规则。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "continuation_token": {
                "type": "string",
                "description": "上一页返回的续读游标;首页不传",
            }
        },
        "additionalProperties": False,
    },
)

_SAVE_RUBRIC_SPEC = ToolSpec(
    name=TOOL_SAVE_RUBRIC,
    description=(
        "在 open_grading_package 返回 needs_rubric=true 后,保存从题目 OCR"
        "提取的 rubric。status=complete 时逐项提交 criterion/max_score/"
        "details/source_quote(引用必须是 OCR 原文子串且包含该项满分),"
        "total_max_score 等于各项满分之和;status=absent_or_ambiguous 时题目"
        "没有明确 rubric,items 留空。保存后需重新打开评分包。"
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

_CONFIRM_VISUAL_SPEC = ToolSpec(
    name=TOOL_CONFIRM_VISUAL,
    description=(
        "记录教师对「本地代码运行表现与报告描述是否一致」的核验结论。仅含代码"
        "作业需要,且必须先通过 request_teacher_confirmation 得到教师答复后才能"
        "调用。verdict=consistent 表示已检查且一致;verdict=mismatch 时必须附"
        "note 说明差异。答复为未检查或含糊时不得调用本工具,也不得评分保存。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "verdict": {"type": "string", "enum": ["consistent", "mismatch"]},
            "note": {
                "type": "string",
                "description": "verdict=mismatch 时必填,说明涉及题号/报告页/差异内容",
            },
        },
        "required": ["verdict"],
        "additionalProperties": False,
    },
)

_SAVE_ASSESSMENT_SPEC = ToolSpec(
    name=TOOL_SAVE_ASSESSMENT,
    description=(
        "保存评分建议。逐项提交 rubric_item_id/score/comment/evidence_refs/"
        "evidence_status 与 confidence/feedback;criterion/max_score/总分由"
        "服务端推导,提交会被拒绝。得分大于 0 的明细必须提供可定位证据"
        "(代码用 source_line,报告用 report_quote,格式见评分包 evidence_format);"
        "内容确实不存在时用 evidence_status=not_found 记 0 分且不引用证据。"
        "校验失败会返回可修正的 error,按其修正后重试。保存成功即完成本次批改。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "assessment": {
                "type": "object",
                "properties": {
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "feedback": {"type": "string"},
                    "details": {
                        "type": "array",
                        "items": {
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
                            "required": ["rubric_item_id", "score", "comment"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["feedback", "details"],
                "additionalProperties": False,
            }
        },
        "required": ["assessment"],
        "additionalProperties": False,
    },
)

_REQUEST_CONFIRMATION_SPEC = ToolSpec(
    name=TOOL_REQUEST_CONFIRMATION,
    description=(
        "暂停本轮批改,向教师发起检查点提问并等待答复。含代码作业在评分前"
        "必须调用本工具确认「本地运行表现与报告描述是否一致」;教师可在本地"
        "运行学生代码后确认,或直接确认。本轮暂停是预期行为,答复后会自动续跑。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "message": {
                "type": "string",
                "description": "向教师展示的问题,要求答复一致性结论(一致/不一致+说明)",
            }
        },
        "required": ["message"],
        "additionalProperties": False,
    },
)
