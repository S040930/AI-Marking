"""批改工具直调测试:handler 与服务端注入行为。"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import sessionmaker

from app.agent.tools import CheckpointRequested
from app.agent.tools.grading import GradingToolContext, build_grading_tools
from app.models.question import Question, QuestionStatus
from app.models.submission import Submission, SubmissionStatus
from app.services.config import upsert_config


def _configure_rubric(db, maximum=100.0) -> None:
    upsert_config(
        db,
        {
            "rubric_definition": {
                "items": [
                    {
                        "criterion": "Task",
                        "max_score": maximum,
                        "details": "Completion",
                    }
                ],
                "total_max_score": maximum,
            }
        },
    )


@pytest.fixture
def grading_env(db_session):
    """等待批改的作业 + 已配置 rubric;返回 (ctx, sub, rubric_item_id)。"""
    question = Question(
        id="tool-test",
        name="Tool test",
        original_filename="tool.pdf",
        file_path="/tmp/tool.pdf",
        ocr_text="完成实验并提交报告",
        status=QuestionStatus.ready,
    )
    sub = Submission(
        question=question,
        original_filename="answer.pdf",
        file_path="/tmp/answer.pdf",
        ocr_text="Report evidence content",
        status=SubmissionStatus.awaiting_mcp,
    )
    db_session.add(sub)
    db_session.commit()
    _configure_rubric(db_session)
    factory = sessionmaker(bind=db_session.bind, expire_on_commit=False)
    ctx = GradingToolContext(
        session_factory=factory, run_id=1, submission_id=sub.id
    )
    item_id = _rubric_item_id(db_session, sub)
    return ctx, sub, item_id


def _rubric_item_id(db, sub) -> str:
    from app.application.mcp_workflow import resolve_submission_rubric

    return resolve_submission_rubric(db, sub).definition.items[0].rubric_item_id


def _open(ctx):
    return build_grading_tools(ctx).execute("open_grading_package", {})


def test_open_package_issues_grading_handle(grading_env):
    ctx, _sub, _item = grading_env
    registry = build_grading_tools(ctx)
    result = registry.execute("open_grading_package", {})
    assert result.ok, result.error
    assert result.output["context_complete"] is True
    assert ctx.grading_handle
    assert result.output["grading_handle"] == ctx.grading_handle


def test_save_assessment_zero_not_found_succeeds(grading_env):
    ctx, sub, item_id = grading_env
    registry = build_grading_tools(ctx)
    assert _open(ctx).ok
    result = registry.execute(
        "save_assessment",
        {
            "assessment": {
                "confidence": 0.8,
                "feedback": "内容缺失",
                "details": [
                    {
                        "rubric_item_id": item_id,
                        "score": 0,
                        "comment": "报告未包含对应内容",
                        "evidence_status": "not_found",
                    }
                ],
            }
        },
    )
    assert result.ok, result.error
    assert result.output["status"] == "ready_for_review"
    assert result.output["grading_revision"] == 1


def test_save_assessment_positive_score_requires_evidence(grading_env):
    ctx, _sub, item_id = grading_env
    registry = build_grading_tools(ctx)
    assert _open(ctx).ok
    result = registry.execute(
        "save_assessment",
        {
            "assessment": {
                "confidence": 0.5,
                "feedback": "部分完成",
                "details": [
                    {
                        "rubric_item_id": item_id,
                        "score": 10,
                        "comment": "完成了主要工作",
                    }
                ],
            }
        },
    )
    assert not result.ok
    assert "证据" in (result.error or "")


def test_save_assessment_unlocatable_quote_is_correctable(grading_env):
    ctx, _sub, item_id = grading_env
    registry = build_grading_tools(ctx)
    assert _open(ctx).ok
    result = registry.execute(
        "save_assessment",
        {
            "assessment": {
                "confidence": 0.5,
                "feedback": "部分完成",
                "details": [
                    {
                        "rubric_item_id": item_id,
                        "score": 10,
                        "comment": "完成了主要工作",
                        "evidence_refs": [
                            {"type": "report_quote", "quote": "不存在的引用文本"}
                        ],
                    }
                ],
            }
        },
    )
    assert not result.ok
    assert "定位" in (result.error or "")


def test_save_assessment_accepts_located_quote(grading_env):
    ctx, _sub, item_id = grading_env
    registry = build_grading_tools(ctx)
    assert _open(ctx).ok
    result = registry.execute(
        "save_assessment",
        {
            "assessment": {
                "confidence": 0.9,
                "feedback": "完成度好",
                "details": [
                    {
                        "rubric_item_id": item_id,
                        "score": 90,
                        "comment": "完成主要任务",
                        "evidence_refs": [
                            {
                                "type": "report_quote",
                                "quote": "Report evidence content",
                            }
                        ],
                    }
                ],
            }
        },
    )
    assert result.ok, result.error


def test_save_assessment_idempotent_same_payload(grading_env):
    ctx, _sub, item_id = grading_env
    registry = build_grading_tools(ctx)
    assert _open(ctx).ok
    payload = {
        "assessment": {
            "confidence": 0.8,
            "feedback": "内容缺失",
            "details": [
                {
                    "rubric_item_id": item_id,
                    "score": 0,
                    "comment": "报告未包含对应内容",
                    "evidence_status": "not_found",
                }
            ],
        }
    }
    first = registry.execute("save_assessment", payload)
    second = registry.execute("save_assessment", payload)
    assert first.ok and second.ok
    assert first.output["idempotent"] is False
    assert second.output["idempotent"] is True


def test_save_assessment_rejects_request_id(grading_env):
    ctx, _sub, item_id = grading_env
    registry = build_grading_tools(ctx)
    assert _open(ctx).ok
    result = registry.execute(
        "save_assessment",
        {
            "assessment": {
                "request_id": "00000000-0000-0000-0000-000000000000",
                "confidence": 0.8,
                "feedback": "x",
                "details": [
                    {
                        "rubric_item_id": item_id,
                        "score": 0,
                        "comment": "无",
                        "evidence_status": "not_found",
                    }
                ],
            }
        },
    )
    assert not result.ok
    assert "request_id" in (result.error or "")


def test_save_assessment_requires_open_first(grading_env):
    ctx, _sub, item_id = grading_env
    registry = build_grading_tools(ctx)
    result = registry.execute(
        "save_assessment",
        {
            "assessment": {
                "confidence": 0.8,
                "feedback": "x",
                "details": [
                    {
                        "rubric_item_id": item_id,
                        "score": 0,
                        "comment": "无",
                        "evidence_status": "not_found",
                    }
                ],
            }
        },
    )
    assert not result.ok
    assert "评分句柄" in (result.error or "")


def test_request_confirmation_raises_checkpoint(grading_env):
    ctx, _sub, _item = grading_env
    registry = build_grading_tools(ctx)
    with pytest.raises(CheckpointRequested) as excinfo:
        registry.execute(
            "request_teacher_confirmation", {"message": "请确认一致性"}
        )
    assert excinfo.value.message == "请确认一致性"


def test_request_confirmation_requires_message(grading_env):
    ctx, _sub, _item = grading_env
    registry = build_grading_tools(ctx)
    result = registry.execute("request_teacher_confirmation", {"message": ""})
    assert not result.ok


def test_confirm_visual_requires_code_submission(grading_env):
    ctx, _sub, _item = grading_env
    registry = build_grading_tools(ctx)
    assert _open(ctx).ok
    result = registry.execute(
        "confirm_visual_review", {"verdict": "consistent"}
    )
    assert not result.ok
    assert "不需要" in (result.error or "")


def test_confirm_visual_mismatch_requires_note(grading_env):
    ctx, _sub, _item = grading_env
    registry = build_grading_tools(ctx)
    assert _open(ctx).ok
    result = registry.execute("confirm_visual_review", {"verdict": "mismatch"})
    assert not result.ok
    assert "说明" in (result.error or "")


def test_save_rubric_without_handle_guides_reopen(grading_env):
    ctx, _sub, _item = grading_env
    registry = build_grading_tools(ctx)
    result = registry.execute(
        "save_question_rubric", {"status": "absent_or_ambiguous", "items": []}
    )
    assert not result.ok
    assert "open_grading_package" in (result.error or "")


def test_needs_rubric_flow_with_absent_status(db_session):
    """无配置 rubric 时 open 返回 needs_rubric,保存 absent 后可重新打开。"""
    question = Question(
        id="needs-rubric",
        name="Needs rubric",
        original_filename="nr.pdf",
        file_path="/tmp/nr.pdf",
        ocr_text="本次作业共 100 分，按实验报告质量评分",
        status=QuestionStatus.ready,
    )
    sub = Submission(
        question=question,
        original_filename="answer.pdf",
        file_path="/tmp/answer.pdf",
        ocr_text="Report body",
        status=SubmissionStatus.awaiting_mcp,
    )
    db_session.add(sub)
    db_session.commit()
    factory = sessionmaker(bind=db_session.bind, expire_on_commit=False)
    ctx = GradingToolContext(
        session_factory=factory, run_id=2, submission_id=sub.id
    )
    registry = build_grading_tools(ctx)
    opened = registry.execute("open_grading_package", {})
    assert opened.ok, opened.error
    assert opened.output.get("needs_rubric") is True
    assert ctx.rubric_handle
    saved = registry.execute(
        "save_question_rubric", {"status": "absent_or_ambiguous", "items": []}
    )
    assert saved.ok, saved.error
    assert ctx.rubric_handle is None
    reopened = registry.execute("open_grading_package", {})
    assert reopened.ok, reopened.error
    # 识别为「无明确 rubric」后回退内置默认,不再重复询问
    assert not reopened.output.get("needs_rubric")
    assert reopened.output["context_complete"] is True


def test_unknown_tool_returns_correctable_error(grading_env):
    ctx, _sub, _item = grading_env
    registry = build_grading_tools(ctx)
    result = registry.execute("no_such_tool", {})
    assert not result.ok
    assert "open_grading_package" in (result.error or "")


def test_export_and_load_state_round_trip(grading_env):
    ctx, _sub, _item = grading_env
    assert _open(ctx).ok
    state = ctx.export_state()
    restored = GradingToolContext(
        session_factory=ctx.session_factory, run_id=1, submission_id=ctx.submission_id
    )
    restored.load_state(state)
    assert restored.grading_handle == ctx.grading_handle
