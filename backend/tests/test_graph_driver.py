"""P4 主图端到端：fan-out 逐项评分 → 服务端组装 → 预检 → 主管提交。"""

from __future__ import annotations

import json

from sqlalchemy.orm import sessionmaker

from app.agent.graph.driver import execute_graph
from app.agent.provider import (
    ModelOption,
    ProviderAuthStatus,
    ProviderTurnCompleted,
)
from app.domain.grading_run import GradingRunStatus
from app.models.grading_run import GradingRun
from app.models.question import Question, QuestionStatus
from app.models.submission import Submission, SubmissionGradingMode, SubmissionStatus
from app.services.config import upsert_config


class BranchProvider:
    """按工具集判断角色：评分分支提交逐项结果，主管分支提交反馈。"""

    name = "scripted"
    default_model = "scripted-model"

    def __init__(self) -> None:
        self.requests = []
        self.turn_index = 0

    def create_turn(self, request):
        self.requests.append(request)
        tool_names = {tool.name for tool in request.tools}
        items: list[dict] = []
        if "submit_item_score" in tool_names and self.turn_index == 0:
            brief = json.loads(request.items[0]["content"])
            for item in brief["rubric_items"]:
                items.append(
                    {
                        "type": "function_call",
                        "call_id": f"c-{item['rubric_item_id']}",
                        "name": "submit_item_score",
                        "arguments": json.dumps(
                            {
                                "rubric_item_id": item["rubric_item_id"],
                                "score": item["max_score"],
                                "comment": "报告与数据一致。",
                                "evidence_refs": [
                                    {"type": "report_quote", "quote": "92% accuracy"}
                                ],
                                "evidence_status": "supported",
                            },
                            ensure_ascii=False,
                        ),
                    }
                )
        elif "submit_item_score" in tool_names:
            items.append({"role": "assistant", "content": "已提交"})
        else:
            items.append(
                {
                    "type": "function_call",
                    "call_id": "c-final",
                    "name": "save_assessment",
                    "arguments": json.dumps(
                        {"feedback": "整体完成度较好。", "confidence": 0.9},
                        ensure_ascii=False,
                    ),
                }
            )
        self.turn_index += 1

        async def _gen():
            yield ProviderTurnCompleted(
                stop_reason="tool_calls",
                response_items=items,
                usage={"input_tokens": 40, "output_tokens": 10},
            )

        return _gen()

    async def list_models(self):
        return [ModelOption(id="scripted-model")]

    def auth_status(self):
        return ProviderAuthStatus(ready=True)

    async def aclose(self):
        pass


def _configure_rubric(db) -> None:
    upsert_config(
        db,
        {
            "rubric_definition": json.dumps(
                {
                    "items": [
                        {
                            "criterion": "结果准确性",
                            "max_score": 60,
                            "details": "报告结论与数据一致",
                        },
                        {
                            "criterion": "分析深度",
                            "max_score": 40,
                            "details": "有解释与讨论",
                        },
                    ],
                    "total_max_score": 100,
                },
                ensure_ascii=False,
            )
        },
    )
    db.commit()


def _submission(db) -> Submission:
    _configure_rubric(db)
    question = Question(
        name="图测试题",
        original_filename="q.pdf",
        file_path="/tmp/q.pdf",
        ocr_text="请分析数据并写报告",
        status=QuestionStatus.ready,
    )
    db.add(question)
    sub = Submission(
        original_filename="answer.pdf",
        file_path="/tmp/a.pdf",
        question=question,
        ocr_text="报告内容 92% accuracy，结论合理。",
        status=SubmissionStatus.awaiting_grading,
        grading_mode=SubmissionGradingMode.agent,
    )
    db.add(sub)
    db.commit()
    return sub


async def test_graph_fanout_scores_all_items_and_completes(db_session) -> None:
    sub = _submission(db_session)
    run = GradingRun(
        submission_id=sub.id,
        provider="openai_api",
        status=GradingRunStatus.running,
    )
    db_session.add(run)
    db_session.commit()
    factory = sessionmaker(bind=db_session.bind, expire_on_commit=False)

    providers: list[BranchProvider] = []

    def provider_factory(agent: str) -> BranchProvider:
        provider = BranchProvider()
        providers.append(provider)
        return provider

    status = await execute_graph(
        session_factory=factory,
        run_id=run.id,
        submission_id=sub.id,
        question_name="图测试题",
        provider_factory=provider_factory,
        provider_name="openai_api",
        model="scripted-model",
        reasoning_effort=None,
        claim_token=None,
        should_cancel=None,
        config_getter=None,
    )

    db_session.expire_all()
    db_session.refresh(run)
    db_session.refresh(sub)
    assert status == GradingRunStatus.completed, run.error_message
    assert run.status == GradingRunStatus.completed
    assert sub.status == SubmissionStatus.ready_for_review
    suggestion = sub.assessment_suggestion or {}
    details = suggestion.get("details") or []
    assert len(details) == 2
    assert [detail["score"] for detail in details] == [60.0, 40.0]
    assert suggestion["feedback"] == "整体完成度较好。"
    # 两个评分分支 + 一个主管分支，各自独立 provider
    assert len(providers) == 3
