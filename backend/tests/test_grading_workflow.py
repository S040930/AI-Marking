"""评分包 / 保存 / rubric 提取 / 独立复核 的业务层契约测试。

直调 application 层(不依赖已删除的 /api/mcp HTTP 层);断言与原
MCP API 契约测试等价,HTTP 状态码语义映射为应用层异常。
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError as PydanticValidationError

from app.application import grading_workflow as workflow
from app.application.grading_workflow import build_grading_policy
from app.core.config import settings
from app.core.errors import ConflictError, ValidationError
from app.models.question import Question, QuestionStatus
from app.models.submission import Submission, SubmissionGradingMode, SubmissionStatus
from app.models.submission_code_file import SubmissionCodeFile
from app.schemas.grading import GradingAssessmentRequest, GradingSaveAssessmentRequest
from app.services.rubric import resolve_rubric

REQUEST_ID = "11111111-1111-4111-8111-111111111111"


def _configure_default_rubric(db) -> None:
    """为默认配置项目写入结构化 rubric,使评分包走 configured 分支而非 needs_rubric。"""
    from app.services.config import upsert_config

    upsert_config(
        db,
        {
            "rubric_definition": json.dumps(
                {
                    "items": [
                        {"criterion": "Task 1", "max_score": 100, "details": "完成度"}
                    ],
                    "total_max_score": 100,
                },
                ensure_ascii=False,
            )
        },
    )
    db.commit()


def _add_submission(db, question, *, ocr_text="学生内容") -> Submission:
    sub = Submission(
        original_filename="answer.pdf",
        file_path="/tmp/a.pdf",
        question=question,
        ocr_text=ocr_text,
        status=SubmissionStatus.awaiting_grading,
        grading_mode=SubmissionGradingMode.agent,
    )
    db.add(sub)
    db.commit()
    return sub


def _header_of(package) -> dict:
    return json.loads(package.content.split("\n", 1)[1].split("\n\n", 1)[0])


def _save(db, sub, package, details: list[dict], request_id: str = REQUEST_ID):
    return workflow.save_assessment(
        db,
        sub.id,
        GradingSaveAssessmentRequest(
            grading_handle=package.grading_handle,
            assessment=GradingAssessmentRequest(
                request_id=request_id,
                confidence=0.8,
                feedback="完成主要要求。",
                details=details,
            ),
        ),
    )


def test_grading_policy_requires_manual_visual_confirmation():
    policy = build_grading_policy(resolve_rubric(None, {}))
    requirements = "\n".join(policy["requirements"])
    assert "提交含代码时" in requirements
    assert "已检查且一致" in requirements
    assert "存在不一致" in requirements
    assert "尚未检查、含糊回答或未回答时必须暂停" in requirements
    assert "读取视觉资产" in requirements
    # v12:单次证据化评分是正常路径,不再有第二遍复核要求
    assert "review_required" not in policy
    assert "第二遍" not in requirements
    assert "not_found" in requirements


def test_package_policy_covers_code_without_visual_assets(db_session):
    question = Question(
        name="图片核验题",
        original_filename="q.pdf",
        file_path="/tmp/q.pdf",
        ocr_text="Task 1: 100 points",
        status=QuestionStatus.ready,
    )
    sub = _add_submission(db_session, question, ocr_text="报告文字")
    sub.code_files.append(
        SubmissionCodeFile(
            question_number=1,
            original_filename="Q1.py",
            file_path="/tmp/Q1.py",
            file_kind="py",
            source_sha256="b" * 64,
            source_text="print('ok')",
        )
    )
    _configure_default_rubric(db_session)
    db_session.commit()

    package = workflow.open_grading_package(db_session, sub, None)
    assert "运行表现" in package.content
    assert package.context_complete is True
    assert package.grading_handle


def test_package_paginates_and_compact_save_enforces_handle(db_session):
    question = Question(
        name="评分题",
        original_filename="q.pdf",
        file_path="/tmp/q.pdf",
        ocr_text="Task 1: 100 points",
        status=QuestionStatus.ready,
    )
    _configure_default_rubric(db_session)
    sub = _add_submission(db_session, question, ocr_text="证据 " * 25_000)

    first = workflow.open_grading_package(db_session, sub, None)
    assert first.context_complete is False
    assert first.grading_handle is None
    assert first.continuation_token
    second = workflow.open_grading_package(db_session, sub, first.continuation_token)
    assert second.context_complete is True
    assert second.grading_handle

    resolved = _header_of(first)["grading_policy"]["resolved_rubric"]
    # v12:客户端只提交 rubric_item_id/score/comment;0 分且无证据合法。
    details = [
        {
            "rubric_item_id": item["rubric_item_id"],
            "score": 0,
            "comment": "待改进。",
            "evidence_status": "not_found",
        }
        for item in resolved["items"]
    ]
    saved = _save(db_session, sub, second, details)
    assert saved.grading_revision == 1
    db_session.expire_all()
    row = db_session.get(Submission, sub.id)
    assert row.assessment_suggestion["agent_metadata"]["source"] == "agent"
    # 服务端推导总分:score=明细之和,max_score=权威 rubric 满分
    assert row.score == 0
    assert row.max_score == resolved["total_max_score"]
    assert row.details[0]["criterion"] == resolved["items"][0]["criterion"]
    assert row.details[0]["max_score"] == resolved["items"][0]["max_score"]


def test_package_hard_limit_raises_actionable_error(db_session, monkeypatch):
    question = Question(
        name="小题",
        original_filename="q.pdf",
        file_path="/tmp/q.pdf",
        ocr_text="题目细则",
        status=QuestionStatus.ready,
    )
    _configure_default_rubric(db_session)
    sub = _add_submission(db_session, question)
    monkeypatch.setattr(settings, "GRADING_MAX_CONTEXT_CHARS", 1)
    with pytest.raises(ValidationError) as excinfo:
        workflow.open_grading_package(db_session, sub, None)
    assert "拆分作业或缩减提交内容" in str(excinfo.value.detail)


def test_save_derives_totals_and_rejects_bad_details(db_session):
    """v12:总分由服务端推导;未知/覆盖不全/超满分明细与旧版字段被拒绝。"""
    question = Question(
        name="评分题",
        original_filename="q.pdf",
        file_path="/tmp/q.pdf",
        ocr_text="Task 1: 100 points",
        status=QuestionStatus.ready,
    )
    _configure_default_rubric(db_session)
    sub = _add_submission(db_session, question)
    package = workflow.open_grading_package(db_session, sub, None)
    resolved = _header_of(package)["grading_policy"]["resolved_rubric"]
    item_id = resolved["items"][0]["rubric_item_id"]
    item_max = resolved["items"][0]["max_score"]

    def _detail(**overrides: object) -> dict:
        detail = {
            "rubric_item_id": item_id,
            "score": 70,
            "comment": "待改进。",
            "evidence_refs": [{"type": "report_quote", "quote": "学生内容"}],
        }
        detail.update(overrides)
        return detail

    # 单项得分超过该项满分
    with pytest.raises(ValidationError) as over:
        _save(db_session, sub, package, [_detail(score=item_max + 1)])
    assert "不能超过该项满分" in str(over.value.detail)

    # 未知 rubric_item_id
    with pytest.raises(ValidationError):
        _save(db_session, sub, package, [_detail(rubric_item_id="rubric_item_unknown")])

    # 未完整覆盖当前 rubric(重复项)
    with pytest.raises(ValidationError):
        _save(
            db_session,
            sub,
            package,
            [_detail(score=0, evidence_refs=[]), _detail(score=0, evidence_refs=[])],
        )

    # v12 起客户端提交服务端可推导字段(旧版契约)一律拒绝(extra=forbid)
    with pytest.raises(PydanticValidationError):
        GradingAssessmentRequest.model_validate(
            {
                "request_id": REQUEST_ID,
                "rubric_snapshot_id": resolved["snapshot_id"],
                "rubric_source": "configured",
                "score": 70,
                "max_score": 100,
                "confidence": 0.8,
                "feedback": "完成主要要求。",
                "details": [_detail()],
                "self_check": {"second_pass_completed": True},
            }
        )

    # 合法 slim 保存:服务端推导 score/max_score 并回填 criterion
    saved = _save(db_session, sub, package, [_detail()])
    assert saved.grading_revision == 1
    db_session.expire_all()
    row = db_session.get(Submission, sub.id)
    assert row.score == 70
    assert row.max_score == 100
    assert row.details[0]["criterion"] == resolved["items"][0]["criterion"]
    assert row.details[0]["max_score"] == item_max
    # 服务端从 evidence_refs 投影可读证据,教师页无需感知结构化引用
    assert row.details[0]["evidence"] == ["学生内容"]
    assert row.assessment_suggestion["agent_metadata"]["rubric_source"] == "configured"


def test_package_returns_needs_rubric_with_single_use_handle(db_session):
    """无配置 rubric 且无题目提取快照时,评分包返回 needs_rubric 与提取句柄。

    同一题目只允许一个有效提取句柄;再次打开会撤销旧句柄。
    """
    question = Question(
        name="无 rubric 题",
        original_filename="q.pdf",
        file_path="/tmp/q.pdf",
        ocr_text="内容理解 60分\n论证分析 40分",
        status=QuestionStatus.ready,
    )
    sub = _add_submission(db_session, question)

    first = workflow.open_grading_package(db_session, sub, None)
    assert first.needs_rubric is True
    assert first.question_id == question.id
    assert first.question_ocr_text == question.ocr_text
    assert first.rubric_handle
    assert first.content is None

    # 再次打开:旧提取句柄被撤销,返回新句柄
    second = workflow.open_grading_package(db_session, sub, None)
    assert second.needs_rubric is True
    assert second.rubric_handle != first.rubric_handle

    # 旧句柄已失效
    with pytest.raises(ConflictError):
        workflow.save_question_rubric(
            db_session,
            question.id,
            first.rubric_handle,
            status_value="absent_or_ambiguous",
            items=[],
            total_max_score=None,
        )


def test_save_question_rubric_complete_persists_authoritative_snapshot(db_session):
    """提取的完整 rubric 经服务端校验后写入题目级权威快照。"""
    question = Question(
        name="期末作文",
        original_filename="q.pdf",
        file_path="/tmp/q.pdf",
        ocr_text="内容理解 60分\n论证分析 40分\n总分100分",
        status=QuestionStatus.ready,
    )
    sub = _add_submission(db_session, question)

    package = workflow.open_grading_package(db_session, sub, None)
    body = workflow.save_question_rubric(
        db_session,
        question.id,
        package.rubric_handle,
        status_value="complete",
        items=[
            {
                "criterion": "内容理解",
                "max_score": 60,
                "details": "准确理解题目要求",
                "source_quote": "内容理解 60分",
            },
            {
                "criterion": "论证分析",
                "max_score": 40,
                "details": "论证清晰有证据",
                "source_quote": "论证分析 40分",
            },
        ],
        total_max_score=100,
    )
    assert body["status"] == "complete"
    assert body["question_id"] == question.id
    assert body["rubric_snapshot_id"]

    db_session.refresh(question)
    assert question.extracted_rubric_items is not None
    assert len(question.extracted_rubric_items) == 2

    # 保存后再次打开:不再 needs_rubric,评分包使用题目提取快照
    again = workflow.open_grading_package(db_session, sub, None)
    assert again.needs_rubric is False
    assert body["rubric_snapshot_id"] in again.content


def test_save_question_rubric_rejects_invalid_quote(db_session):
    """source_quote 不是 OCR 原文子串或未含满分时,确定性校验拒绝写入。"""
    question = Question(
        name="作文题",
        original_filename="q.pdf",
        file_path="/tmp/q.pdf",
        ocr_text="内容理解 60分",
        status=QuestionStatus.ready,
    )
    sub = _add_submission(db_session, question)
    package = workflow.open_grading_package(db_session, sub, None)

    with pytest.raises(ValidationError):
        workflow.save_question_rubric(
            db_session,
            question.id,
            package.rubric_handle,
            status_value="complete",
            items=[
                {
                    "criterion": "内容理解",
                    "max_score": 60,
                    "details": "准确理解题目要求",
                    "source_quote": "杜撰的引用文本",
                }
            ],
            total_max_score=60,
        )
    db_session.refresh(question)
    assert question.extracted_rubric is None
    assert question.extracted_rubric_items is None


def test_save_question_rubric_absent_avoids_repeat_extraction(db_session):
    """题目无明确 rubric 时持久化 absent_or_ambiguous,后续不再询问。"""
    question = Question(
        name="无标准题",
        original_filename="q.pdf",
        file_path="/tmp/q.pdf",
        ocr_text="请完成课程设计",
        status=QuestionStatus.ready,
    )
    sub = _add_submission(db_session, question)
    package = workflow.open_grading_package(db_session, sub, None)

    body = workflow.save_question_rubric(
        db_session,
        question.id,
        package.rubric_handle,
        status_value="absent_or_ambiguous",
        items=[],
        total_max_score=None,
    )
    assert body["status"] == "absent_or_ambiguous"
    assert body["rubric_snapshot_id"] is None

    db_session.refresh(question)
    assert question.extracted_rubric is None
    assert question.extracted_rubric_version is not None

    # 再次打开直接走内置默认 rubric,不再 needs_rubric
    again = workflow.open_grading_package(db_session, sub, None)
    assert again.needs_rubric is False
    assert "built_in_default" in again.content


def test_save_question_rubric_rejects_after_ocr_change(db_session):
    """题目 OCR 变化后旧提取句柄失效,拒绝保存。"""
    question = Question(
        name="替换题",
        original_filename="q.pdf",
        file_path="/tmp/q.pdf",
        ocr_text="内容理解 60分",
        status=QuestionStatus.ready,
    )
    sub = _add_submission(db_session, question)
    package = workflow.open_grading_package(db_session, sub, None)

    db_session.get(Question, question.id).ocr_text = "新的题目内容 80分"
    db_session.commit()

    with pytest.raises(ConflictError):
        workflow.save_question_rubric(
            db_session,
            question.id,
            package.rubric_handle,
            status_value="absent_or_ambiguous",
            items=[],
            total_max_score=None,
        )


# ---------- 独立复核 ----------


def _configure_two_item_rubric(db) -> None:
    from app.services.config import upsert_config

    upsert_config(
        db,
        {
            "rubric_definition": json.dumps(
                {
                    "items": [
                        {"criterion": "Task 1", "max_score": 60, "details": "完成度"},
                        {"criterion": "Task 2", "max_score": 40, "details": "分析质量"},
                    ],
                    "total_max_score": 100,
                },
                ensure_ascii=False,
            )
        },
    )
    db.commit()


def _prepare_reviewed_suggestion(db):
    """创建作业、保存建议并返回 (submission, 评分用 rubric, 复核用新句柄)。"""
    question = Question(
        name="复核题",
        original_filename="q.pdf",
        file_path="/tmp/q.pdf",
        ocr_text="Task 1: 60 points. Task 2: 40 points.",
        status=QuestionStatus.ready,
    )
    _configure_two_item_rubric(db)
    sub = _add_submission(db, question)

    package = workflow.open_grading_package(db, sub, None)
    resolved = _header_of(package)["grading_policy"]["resolved_rubric"]
    _save(
        db,
        sub,
        package,
        [
            {
                "rubric_item_id": item["rubric_item_id"],
                "score": score,
                "comment": "完成。",
                "evidence_refs": [{"type": "report_quote", "quote": "学生内容"}],
            }
            for item, score in zip(resolved["items"], (50, 30), strict=True)
        ],
    )
    # 保存建议后 revision 递增,复核者需重新打开作业获取绑定当前 revision 的句柄
    reopen = workflow.open_grading_package(db, sub, None)
    return sub, resolved, reopen.grading_handle


def test_save_assessment_review_persists_verdict(db_session):
    """独立复核结论写入 assessment_review,含服务端补全的 criterion/max_score。"""
    sub, resolved, handle = _prepare_reviewed_suggestion(db_session)
    workflow.save_assessment_review(
        db_session,
        sub.id,
        handle,
        verdict="partial",
        summary="第 1 项证据充分,第 2 项扣分依据不足。",
        confidence="0.7",
        items=[
            {
                "rubric_item_id": resolved["items"][0]["rubric_item_id"],
                "verdict": "agree",
                "comment": "证据可定位,得分合理。",
            },
            {
                "rubric_item_id": resolved["items"][1]["rubric_item_id"],
                "verdict": "disagree",
                "comment": "报告包含分析内容,建议提高得分。",
                "suggested_score": 35,
            },
        ],
        client="agent",
    )
    db_session.expire_all()
    saved = db_session.get(Submission, sub.id)
    assert saved.status == SubmissionStatus.ready_for_review
    assert saved.assessment_review is not None
    assert saved.assessment_review["reviewed_revision"] == saved.grading_revision
    disagree = next(
        item
        for item in saved.assessment_review["items"]
        if item["verdict"] == "disagree"
    )
    assert disagree["criterion"] == "Task 2"
    assert disagree["max_score"] == 40
    assert disagree["suggested_score"] == 35


def test_save_assessment_review_rejects_stale_handle(db_session):
    """保存建议前的旧句柄 revision 不匹配,复核被拒绝。"""
    sub, resolved, _ = _prepare_reviewed_suggestion(db_session)
    stale_pkg = workflow.open_grading_package(db_session, sub, None)
    handle = stale_pkg.grading_handle

    # 模拟建议更新:直接递增 revision(等价于保存了新建议)
    db_session.expire_all()
    row = db_session.get(Submission, sub.id)
    row.grading_revision += 1
    db_session.commit()

    with pytest.raises(ConflictError):
        workflow.save_assessment_review(
            db_session,
            sub.id,
            handle,
            verdict="agree",
            summary="复核同意。",
            confidence="0.9",
            items=[
                {
                    "rubric_item_id": item["rubric_item_id"],
                    "verdict": "agree",
                    "comment": "合理。",
                }
                for item in resolved["items"]
            ],
            client=None,
        )


def test_save_assessment_review_rejects_bad_items(db_session):
    """复核项缺失、伪造 item ID 或建议分超满分时被拒绝。"""
    sub, resolved, handle = _prepare_reviewed_suggestion(db_session)
    first_id = resolved["items"][0]["rubric_item_id"]

    with pytest.raises(ValidationError) as missing:
        workflow.save_assessment_review(
            db_session,
            sub.id,
            handle,
            verdict="partial",
            summary="只复核了第一项。",
            confidence="0.5",
            items=[{"rubric_item_id": first_id, "verdict": "agree", "comment": "合理。"}],
            client=None,
        )
    assert "未完整覆盖" in str(missing.value.detail)

    with pytest.raises(ValidationError):
        workflow.save_assessment_review(
            db_session,
            sub.id,
            handle,
            verdict="agree",
            summary="伪造评分项。",
            confidence="0.5",
            items=[
                {"rubric_item_id": first_id, "verdict": "agree", "comment": "合理。"},
                {
                    "rubric_item_id": "rubric_item_unknown",
                    "verdict": "agree",
                    "comment": "伪造。",
                },
            ],
            client=None,
        )

    with pytest.raises(ValidationError) as over:
        workflow.save_assessment_review(
            db_session,
            sub.id,
            handle,
            verdict="partial",
            summary="建议分超满分。",
            confidence="0.5",
            items=[
                {"rubric_item_id": first_id, "verdict": "agree", "comment": "合理。"},
                {
                    "rubric_item_id": resolved["items"][1]["rubric_item_id"],
                    "verdict": "disagree",
                    "comment": "应得更多。",
                    "suggested_score": 999,
                },
            ],
            client=None,
        )
    assert "不能超过该项满分" in str(over.value.detail)


async def test_save_assessment_review_rejects_after_finalize(client, db_session):
    """教师确认最终成绩后复核被拒绝。"""
    sub, resolved, handle = _prepare_reviewed_suggestion(db_session)
    finalized = await client.post(
        f"/api/submissions/{sub.id}/finalize",
        json={
            "reviewer_name": "Teacher",
            "expected_revision": 1,
            "score": 80,
            "max_score": 100,
            "feedback": "确认成绩。",
            "details": [
                {
                    "criterion": "Task 1",
                    "score": 50,
                    "max_score": 60,
                    "comment": "完成。",
                    "evidence": ["学生内容"],
                },
                {
                    "criterion": "Task 2",
                    "score": 30,
                    "max_score": 40,
                    "comment": "一般。",
                    "evidence": ["学生内容"],
                },
            ],
        },
    )
    assert finalized.status_code == 200, finalized.text

    with pytest.raises(ConflictError):
        workflow.save_assessment_review(
            db_session,
            sub.id,
            handle,
            verdict="agree",
            summary="已定稿。",
            confidence="0.9",
            items=[
                {
                    "rubric_item_id": item["rubric_item_id"],
                    "verdict": "agree",
                    "comment": "合理。",
                }
                for item in resolved["items"]
            ],
            client=None,
        )


# ---------- 句柄过期批量清理 ----------


def test_purge_expired_handles_removes_only_expired_rows(db_session):
    """批量清理删除过期句柄、保留未过期句柄；force 时必须全部完成。"""
    from datetime import timedelta

    from app.application.grading_workflow import purge_expired_handles
    from app.core.time import utc_now_naive
    from app.models.grading_handle import GradingHandle

    db_session.add_all(
        [
            GradingHandle(
                token_hash="expired" + "0" * 57,
                kind="package",
                submission_id=1,
                context_hash="sha256:" + "0" * 64,
                grading_revision=0,
                expires_at=utc_now_naive() - timedelta(seconds=1),
            ),
            GradingHandle(
                token_hash="alive" + "0" * 59,
                kind="package",
                submission_id=1,
                context_hash="sha256:" + "0" * 64,
                grading_revision=0,
                expires_at=utc_now_naive() + timedelta(seconds=600),
            ),
        ]
    )
    db_session.commit()

    purge_expired_handles(db_session, force=True)
    db_session.commit()

    remaining = db_session.query(GradingHandle).all()
    assert [row.token_hash for row in remaining] == ["alive" + "0" * 59]


def test_purge_expired_handles_is_probabilistic(db_session, monkeypatch):
    """默认非 force 调用按 1/16 概率触发；低随机值时执行清理。"""
    from app.application import grading_workflow

    calls = {"count": 0}

    def count_execute(*_args, **_kwargs):
        calls["count"] += 1
        return None

    monkeypatch.setattr(grading_workflow.random, "random", lambda: 0.99)
    monkeypatch.setattr(db_session, "execute", count_execute)
    grading_workflow.purge_expired_handles(db_session)
    assert calls["count"] == 0

    monkeypatch.setattr(grading_workflow.random, "random", lambda: 0.0)
    grading_workflow.purge_expired_handles(db_session)
    assert calls["count"] == 1


def test_build_assessment_summary_bridges_legacy_metadata_key():
    """v12 及更早历史元数据键为 mcp_metadata;summary 输出统一 agent_metadata。"""
    from types import SimpleNamespace

    legacy = SimpleNamespace(
        assessment_suggestion={
            "score": 80,
            "max_score": 100,
            "mcp_metadata": {"source": "mcp"},
        }
    )
    summary = workflow.build_assessment_summary(legacy)
    assert summary["agent_metadata"] == {"source": "mcp"}
    assert "mcp_metadata" not in summary
    assert summary["score"] == 80

    current = SimpleNamespace(
        assessment_suggestion={"agent_metadata": {"source": "agent"}}
    )
    assert workflow.build_assessment_summary(current)["agent_metadata"] == {
        "source": "agent"
    }
    empty = SimpleNamespace(assessment_suggestion=None)
    assert workflow.build_assessment_summary(empty) is None


def test_handle_ttl_covers_overnight_checkpoints():
    """句柄 TTL 为 24h:检查点驻留可跨夜,教师隔日答复仍有效。"""
    assert workflow.HANDLE_TTL_SECONDS == 24 * 60 * 60
