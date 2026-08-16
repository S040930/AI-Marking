"""共享的评分明细模型。

MCP 客户端评分与教师最终确认共用 ``ScoreDetail``。MCP 路径的严格性由
``schemas/mcp.py:McpAssessmentRequest`` 外层 ``extra="forbid"`` 保留;
rubric-item 覆盖校验由 ``services/rubric.py:validate_assessment_details``
统一负责。
"""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator


class ScoreDetail(BaseModel):
    """单项评分:MCP 与教师最终确认共用。"""

    criterion: str = Field(min_length=1, max_length=200)
    # MCP 必须携带并引用当前 rubric 的 item ID;教师最终确认可省略。
    rubric_item_id: str | None = Field(default=None, min_length=5, max_length=100)
    score: float = Field(ge=0)
    max_score: float = Field(gt=0)
    comment: str = Field(min_length=1)
    # 证据引用不设数量上限:AI 可能引用多条原文/源码支撑同一评分项。
    evidence: list[str] = Field(default_factory=list)
    # 仅外部编程助手代码作业使用:结构化证据引用(报告引用/源码行),同样不设上限。
    evidence_refs: list[dict] = Field(default_factory=list)

    @model_validator(mode="after")
    def score_within_maximum(self):
        if self.score > self.max_score:
            raise ValueError("单项得分不能超过该项满分")
        return self
