"""P4 证据预检回环：首轮引文无法定位 → evidence_verify 独立复核 → 重评 → 完成。"""

from __future__ import annotations

import json
import re

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

CORRECT_QUOTE = "92% accuracy"


def _first_item_id(text: str) -> str:
    match = re.search(r'"rubric_item_id":\s*"([^"]+)"', text)
    assert match, text[:200]
    return match.group(1)


class EvidenceProvider:
    """评分分支：无复核提示时给错误引文，有提示时沿用复核结论；
    证据复核分支提交正确引文；主管分支保存。"""

    name = "scripted"
    default_model = "scripted-model"

    def __init__(self) -> None:
        self.turn_index = 0

    def create_turn(self, request):
        tool_names = {tool.name for tool in request.tools}
        content = request.items[0]["content"]
        items: list[dict] = []
        if "submit_evidence_fix" in tool_names and self.turn_index == 0:
            item_id = _first_item_id(content)
            items.append(
                {
                    "type": "function_call",
                    "call_id": "c-evidence",
                    "name": "submit_evidence_fix",
                    "arguments": json.dumps(
                        {
                            "rubric_item_id": item_id,
                            "evidence_refs": [
                                {"type": "report_quote", "quote": CORRECT_QUOTE}
                            ],
                            "evidence_status": "supported",
                        },
                        ensure_ascii=False,
                    ),
                }
            )
        elif "submit_evidence_fix" in tool_names:
            items.append({"role": "assistant", "content": "已复核"})
        elif "submit_item_score" in tool_names and self.turn_index == 0:
            item_id = _first_item_id(content)
            quote = CORRECT_QUOTE if "独立证据复核" in content else "这句话不在报告里"
            items.append(
                {
                    "type": "function_call",
                    "call_id": "c-score",
                    "name": "submit_item_score",
                    "arguments": json.dumps(
                        {
                            "rubric_item_id": item_id,
                            "score": 100,
                            "comment": "报告有数据。",
                            "evidence_refs": [
                                {"type": "report_quote", "quote": quote}
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
                        {"feedback": "整体完成。", "confidence": 0.9},
                        ensure_ascii=False,
                    ),
                }
            )
        self.turn_index += 1

        async def _gen():
            yield ProviderTurnCompleted(
                stop_reason="tool_calls",
                response_items=items,
                usage={"input_tokens": 20, "output_tokens": 5},
            )

        return _gen()

    async def list_models(self):
        return [ModelOption(id="scripted-model")]

    def auth_status(self):
        return ProviderAuthStatus(ready=True)

    async def aclose(self):
        pass


def _submission(db) -> Submission:
    upsert_config(
        db,
        {
            "rubric_definition": json.dumps(
                {
                    "items": [
                        {
                            "criterion": "结果准确性",
                            "max_score": 100,
                            "details": "报告结论与数据一致",
                        }
                    ],
                    "total_max_score": 100,
                },
                ensure_ascii=False,
            )
        },
    )
    db.commit()
    question = Question(
        name="证据题",
        original_filename="q.pdf",
        file_path="/tmp/q.pdf",
        ocr_text="请分析数据",
        status=QuestionStatus.ready,
    )
    db.add(question)
    sub = Submission(
        original_filename="answer.pdf",
        file_path="/tmp/a.pdf",
        question=question,
        ocr_text="报告内容 92% accuracy。",
        status=SubmissionStatus.awaiting_grading,
        grading_mode=SubmissionGradingMode.agent,
    )
    db.add(sub)
    db.commit()
    return sub


async def test_invalid_evidence_triggers_independent_verification(db_session) -> None:
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

    def provider_factory(agent: str) -> EvidenceProvider:
        agents.append(agent)
        return EvidenceProvider()

    status = await execute_graph(
        session_factory=factory,
        run_id=run.id,
        submission_id=sub.id,
        question_name="证据题",
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
    assert "evidence" in agents
    assert sum(1 for agent in agents if agent.startswith("item:")) >= 2
    sub = db_session.get(Submission, sub.id)
    detail = (sub.assessment_suggestion or {})["details"][0]
    assert detail["evidence_refs"][0]["quote"] == CORRECT_QUOTE
