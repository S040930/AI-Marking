"""题目缺 rubric 时的图内闭环：rubric_extract → supervisor_rubric → 重新摄包评分。"""

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

QUESTION_OCR = "请完成实验报告（60分），并给出数据分析（40分）。"


class RubricProvider:
    """rubric 分支提交提取结果；评分分支提交逐项结果；主管分支保存。"""

    name = "scripted"
    default_model = "scripted-model"

    def __init__(self) -> None:
        self.turn_index = 0

    def create_turn(self, request):
        tool_names = {tool.name for tool in request.tools}
        items: list[dict] = []
        if "submit_question_rubric" in tool_names and self.turn_index == 0:
            items.append(
                {
                    "type": "function_call",
                    "call_id": "c-rubric",
                    "name": "submit_question_rubric",
                    "arguments": json.dumps(
                        {
                            "status": "complete",
                            "items": [
                                {
                                    "criterion": "实验报告",
                                    "max_score": 60,
                                    "details": "完成报告主体",
                                    "source_quote": "实验报告（60分）",
                                },
                                {
                                    "criterion": "数据分析",
                                    "max_score": 40,
                                    "details": "给出数据分析",
                                    "source_quote": "数据分析（40分）",
                                },
                            ],
                            "total_max_score": 100,
                        },
                        ensure_ascii=False,
                    ),
                }
            )
        elif "submit_question_rubric" in tool_names:
            items.append({"role": "assistant", "content": "已提交"})
        elif "submit_item_score" in tool_names and self.turn_index == 0:
            brief = json.loads(request.items[0]["content"].split("\n\n", 1)[0])
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
                        {"feedback": "完成度较好。", "confidence": 0.9},
                        ensure_ascii=False,
                    ),
                }
            )
        self.turn_index += 1

        async def _gen():
            yield ProviderTurnCompleted(
                stop_reason="tool_calls",
                response_items=items,
                usage={"input_tokens": 30, "output_tokens": 10},
            )

        return _gen()

    async def list_models(self):
        return [ModelOption(id="scripted-model")]

    def auth_status(self):
        return ProviderAuthStatus(ready=True)

    async def aclose(self):
        pass


def _submission(db) -> Submission:
    question = Question(
        name="rubric 题",
        original_filename="q.pdf",
        file_path="/tmp/q.pdf",
        ocr_text=QUESTION_OCR,
        status=QuestionStatus.ready,
    )
    db.add(question)
    sub = Submission(
        original_filename="answer.pdf",
        file_path="/tmp/a.pdf",
        question=question,
        ocr_text="报告给出 92% accuracy 的结论，数据完整。",
        status=SubmissionStatus.awaiting_grading,
        grading_mode=SubmissionGradingMode.agent,
    )
    db.add(sub)
    db.commit()
    return sub


async def test_missing_rubric_is_extracted_then_scored(db_session) -> None:
    sub = _submission(db_session)
    run = GradingRun(
        submission_id=sub.id,
        provider="openai_api",
        status=GradingRunStatus.running,
    )
    db_session.add(run)
    db_session.commit()
    factory = sessionmaker(bind=db_session.bind, expire_on_commit=False)

    agents: list[str] = []

    def provider_factory(agent: str) -> RubricProvider:
        agents.append(agent)
        return RubricProvider()

    status = await execute_graph(
        session_factory=factory,
        run_id=run.id,
        submission_id=sub.id,
        question_name="rubric 题",
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
    assert status == GradingRunStatus.completed, run.error_message
    assert "rubric" in agents
    question = db_session.get(Question, sub.question_id)
    assert question is not None
    items = question.extracted_rubric_items or []
    assert [item["max_score"] for item in items] == [60, 40]
    assert question.extracted_rubric_version == "question-rubric-v1"
    refreshed = db_session.get(Submission, sub.id)
    details = (refreshed.assessment_suggestion or {}).get("details") or []
    assert [detail["score"] for detail in details] == [60.0, 40.0]
