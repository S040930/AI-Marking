"""共享的评分明细与评分结果模型。

收敛三处重复定义:
- ``agent.py:ScoreDetail`` / ``GradingResult``(后端 Agent 评分)
- ``schemas/mcp.py:McpAssessmentDetail`` / ``McpAssessmentRequest``(Codex MCP 评分)
- ``schemas/submission.py:ReviewDetail`` / ``FinalizeRequest``(教师最终确认)

取三端约束的最大宽容度:`evidence` 允许为空、`rubric_item_id` 可选
(教师最终确认不携带)、`evidence_refs` 默认空列表、不限制 details 条数。
模型保持宽松(不设 ``extra="forbid"``),因为 Agent 的 LLM 输出经
``_json_completion`` 解析,未知字段应被容忍而不是直接失败;MCP 路径的
严格性由 ``schemas/mcp.py:McpAssessmentRequest`` 外层 ``extra="forbid"``
保留。rubric-item 覆盖校验由 ``services/rubric.py:validate_assessment_details``
统一负责。
"""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator


class ScoreDetail(BaseModel):
    """单项评分:Agent、MCP 与教师最终确认共用。"""

    criterion: str = Field(min_length=1, max_length=200)
    # Agent 与 MCP 必须携带并引用当前 rubric 的 item ID;教师最终确认可省略。
    rubric_item_id: str | None = Field(default=None, min_length=5, max_length=100)
    score: float = Field(ge=0)
    max_score: float = Field(gt=0)
    comment: str = Field(min_length=1)
    # 证据引用不设数量上限:AI 可能引用多条原文/源码支撑同一评分项。
    evidence: list[str] = Field(default_factory=list)
    # 仅 Codex 代码作业使用:结构化证据引用(报告引用/源码行),同样不设上限。
    evidence_refs: list[dict] = Field(default_factory=list)

    @model_validator(mode="after")
    def score_within_maximum(self):
        if self.score > self.max_score:
            raise ValueError("单项得分不能超过该项满分")
        return self


class GradingResult(BaseModel):
    """评分结果核心:总分/满分/反馈/评分项列表及汇总一致性校验。"""

    score: float = Field(ge=0)
    max_score: float = Field(gt=0)
    feedback: str = Field(min_length=1)
    details: list[ScoreDetail] = Field(min_length=1)

    @model_validator(mode="after")
    def totals_are_consistent(self):
        if self.score > self.max_score:
            raise ValueError("总分不能超过满分")
        if abs(sum(item.score for item in self.details) - self.score) > 0.01:
            raise ValueError("各评分项得分之和必须等于总分")
        if abs(sum(item.max_score for item in self.details) - self.max_score) > 0.01:
            raise ValueError("各评分项满分之和必须等于总满分")
        return self
