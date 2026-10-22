"""批改 runner 集成测试:执行链、检查点续跑与状态机终态。

provider 由 monkeypatch 注入脚本化实现;工具、工作流、run_store 均走
真实实现(SQLite 内存库),覆盖"打开评分包 → 保存/检查点 → 终态"全链路。
"""

from __future__ import annotations

import asyncio
import inspect
import json
from types import SimpleNamespace

from sqlalchemy.orm import sessionmaker

from app.agent import checkpoints as checkpoint_repo
from app.agent import run_store as run_repo
from app.agent import runner
from app.agent.provider import (
    ModelOption,
    ProviderAuthStatus,
    ProviderTurnCompleted,
)
from app.domain.grading_run import GradingRunStatus
from app.models.grading_run import GradingRun
from app.models.question import Question, QuestionStatus
from app.models.submission import Submission, SubmissionStatus
from app.models.submission_code_file import SubmissionCodeFile
from app.services.config import upsert_config


class ScriptedProvider:
    """按脚本逐回合产出事件;供 runner 测试注入。"""

    name = "scripted"
    default_model = "scripted-model"

    def __init__(self, script):
        self.script = list(script)
        self.requests = []

    def create_turn(self, request):
        self.requests.append(request)
        turn = self.script.pop(0)

        async def _gen():
            if isinstance(turn, BaseException):
                raise turn
            for event in turn:
                if inspect.isawaitable(event):
                    await event
                else:
                    yield event

        return _gen()

    async def list_models(self):
        return [ModelOption(id="scripted-model")]

    def auth_status(self):
        return ProviderAuthStatus(ready=True)

    async def aclose(self):
        pass


def _function_call(call_id: str, name: str, arguments: dict) -> dict:
    return {
        "type": "function_call",
        "call_id": call_id,
        "name": name,
        "arguments": json.dumps(arguments, ensure_ascii=False),
    }


def _turn(*items: dict, usage: dict | None = None) -> ProviderTurnCompleted:
    return ProviderTurnCompleted("completed", list(items), usage)


def _make_env(db_session, *, with_code: bool = False) -> SimpleNamespace:
    question = Question(
        id=f"runner-{int(with_code)}",
        name="Runner 题",
        original_filename="q.pdf",
        file_path="/tmp/q.pdf",
        ocr_text="Task",
        status=QuestionStatus.ready,
    )
    sub = Submission(
        question=question,
        original_filename="a.pdf",
        file_path="/tmp/a.pdf",
        ocr_text="Report evidence",
        status=SubmissionStatus.awaiting_grading,
    )
    if with_code:
        sub.code_files.append(
            SubmissionCodeFile(
                question_number=1,
                original_filename="q1.py",
                file_path="/tmp/q1.py",
                file_kind="py",
                source_text="print(1)",
                source_sha256="a" * 64,
            )
        )
    db_session.add(sub)
    db_session.commit()
    upsert_config(
        db_session,
        {
            "rubric_definition": {
                "items": [
                    {"criterion": "Task 1", "max_score": 100, "details": "完成度"}
                ],
                "total_max_score": 100,
            }
        },
    )
    factory = sessionmaker(bind=db_session.bind, expire_on_commit=False)
    return SimpleNamespace(
        factory=factory, submission=sub, db=db_session, question=question
    )


def _claim_run(factory, submission_id: int, worker: str = "test-worker") -> tuple[int, str]:
    with factory() as db:
        run_repo.create_run(db, submission_id=submission_id, provider="openai_api")
        db.commit()
        claimed = run_repo.claim_next_run(
            db, worker_id=worker, lease_seconds=90, max_concurrent=1
        )
        return claimed.id, claimed.claim_token


def _prepare_save_arguments(db_session, submission) -> dict:
    """通过真实 workflow 打开评分包,构造合法 save_assessment 参数。"""
    from app.application import grading_package

    grading_package_response = grading_package.open_grading_package(db_session, submission, None)
    header = json.loads(grading_package_response.content.split("\n", 1)[1].split("\n\n", 1)[0])
    item = header["grading_policy"]["resolved_rubric"]["items"][0]
    return {
        "assessment": {
            "confidence": 0.9,
            "feedback": "完成主要要求。",
            "details": [
                {
                    "rubric_item_id": item["rubric_item_id"],
                    "score": item["max_score"],
                    "comment": "完成。",
                    "evidence_refs": [
                        {"type": "report_quote", "quote": "Report evidence"}
                    ],
                }
            ],
        }
    }


def _prepare_item_arguments(db_session, submission) -> dict:
    """通过真实 workflow 打开评分包,构造逐项提交参数。"""
    from app.application import grading_package

    grading_package_response = grading_package.open_grading_package(db_session, submission, None)
    header = json.loads(grading_package_response.content.split("\n", 1)[1].split("\n\n", 1)[0])
    item = header["grading_policy"]["resolved_rubric"]["items"][0]
    return {
        "rubric_item_id": item["rubric_item_id"],
        "score": item["max_score"],
        "comment": "完成。",
        "evidence_refs": [{"type": "report_quote", "quote": "Report evidence"}],
        "evidence_status": "supported",
    }


def _patch_provider(monkeypatch, provider: ScriptedProvider) -> None:
    monkeypatch.setattr(
        runner, "_build_provider", lambda *args, **kwargs: provider
    )


async def test_runner_completes_after_open_and_save(db_session, monkeypatch):
    env = _make_env(db_session)
    item_args = _prepare_item_arguments(db_session, env.submission)
    provider = ScriptedProvider(
        [
            [_turn(_function_call("c1", "submit_item_score", item_args))],
            [_turn()],
            [
                _turn(
                    _function_call(
                        "c2",
                        "save_assessment",
                        {"feedback": "完成主要要求。", "confidence": 0.9},
                    )
                )
            ],
        ]
    )
    _patch_provider(monkeypatch, provider)
    run_id, token = _claim_run(env.factory, env.submission.id)

    status = await runner.execute_run(
        run_id,
        session_factory=env.factory,
        submission_id=env.submission.id,
        question_name="Runner 题",
        claim_token=token,
    )
    assert status == GradingRunStatus.completed
    with env.factory() as db:
        run = db.get(GradingRun, run_id)
        assert run.status == GradingRunStatus.completed
        assert run.finished_at is not None
        events = run_repo.list_events_after(db, run_id, 0, 500)
        kinds = {e.kind for e in events}
        assert "tool_started" in kinds
        assert "tool_finished" in kinds
        sub = db.get(Submission, env.submission.id)
        assert sub.status == SubmissionStatus.ready_for_review
    # 评分分支两回合（提交 + 结束）+ 主管一回合（保存）
    assert len(provider.requests) == 3


async def test_runner_checkpoint_then_resume(db_session, monkeypatch):
    """代码作业先审批测试集、运行测试，再过代码一致性门并保存。"""
    env = _make_env(db_session, with_code=True)
    item_args = _prepare_item_arguments(db_session, env.submission)
    provider = ScriptedProvider(
        [
            [
                _turn(
                    _function_call(
                        "tests-1",
                        "submit_code_test_suite",
                        {
                            "tests": [
                                {
                                    "question_number": 1,
                                    "title": "打印结果",
                                    "mode": "program_io",
                                    "language": "python",
                                    "input": "",
                                    "expected_stdout": "1\\n",
                                }
                            ]
                        },
                    )
                )
            ],
            [_turn(_function_call("c1", "submit_item_score", item_args))],
            [_turn()],
            [
                _turn(
                    _function_call(
                        "c2",
                        "save_assessment",
                        {"feedback": "完成主要要求。", "confidence": 0.9},
                    )
                )
            ],
        ]
    )
    _patch_provider(monkeypatch, provider)
    monkeypatch.setattr(
        "app.services.code_runner.run_submission_tests",
        lambda *_args, **_kwargs: [
            {
                "id": "q1-t1",
                "question_number": 1,
                "title": "打印结果",
                "mode": "program_io",
                "language": "python",
                "status": "passed",
                "output": "标准输出与预期一致",
            }
        ],
    )
    run_id, token = _claim_run(env.factory, env.submission.id)

    status = await runner.execute_run(
        run_id,
        session_factory=env.factory,
        submission_id=env.submission.id,
        question_name="Runner 题",
        claim_token=token,
    )
    with env.factory() as db:
        run = db.get(GradingRun, run_id)
        assert status == GradingRunStatus.waiting_for_teacher, run.error_message
    assert len(provider.requests) == 1
    generation_message = provider.requests[0].items[0]["content"]
    assert "Task" in generation_message
    assert "print(1)" not in generation_message
    with env.factory() as db:
        run = db.get(GradingRun, run_id)
        assert run.status == GradingRunStatus.waiting_for_teacher
        assert run.checkpoint is not None
        assert run.checkpoint["type"] == "code_test_suite_approval"
        assert run.checkpoint_request_id
        # 图驱动已在返回前释放所有权，worker 无需再次释放。
        assert run.worker_id is None

    # 教师批准测试集(worker_id 已释放 → 恢复为 queued 等待续跑)
    with env.factory() as db:
        run = db.get(GradingRun, run_id)
        assert checkpoint_repo.answer_checkpoint(
            db, run, action="approve_test_suite"
        )
        assert run.status == GradingRunStatus.queued

    with env.factory() as db:
        claimed = run_repo.claim_next_run(
            db, worker_id="resume-worker", lease_seconds=90, max_concurrent=1
        )
        assert claimed is not None and claimed.id == run_id
        token2 = claimed.claim_token
        claimed_run_id = claimed.id
    status = await runner.execute_run(
        claimed_run_id,
        session_factory=env.factory,
        submission_id=env.submission.id,
        question_name="Runner 题",
        claim_token=token2,
    )
    assert status == GradingRunStatus.waiting_for_teacher
    assert len(provider.requests) == 1
    with env.factory() as db:
        run = db.get(GradingRun, run_id)
        assert run.checkpoint["type"] == "code_consistency", (
            run.checkpoint,
            run.error_message,
            run.checkpoint_answer,
        )
        assert run.worker_id is None

    # 教师确认代码与报告一致后，run 才继续评分。
    with env.factory() as db:
        run = db.get(GradingRun, run_id)
        assert checkpoint_repo.answer_checkpoint(
            db, run, verdict="consistent", note=None
        )
        assert run.status == GradingRunStatus.queued
    with env.factory() as db:
        claimed = run_repo.claim_next_run(
            db, worker_id="final-worker", lease_seconds=90, max_concurrent=1
        )
        token3 = claimed.claim_token
    status = await runner.execute_run(
        run_id,
        session_factory=env.factory,
        submission_id=env.submission.id,
        question_name="Runner 题",
        claim_token=token3,
    )
    assert status == GradingRunStatus.completed, "评分或最终提交未完成"
    assert len(provider.requests) == 4
    with env.factory() as db:
        run = db.get(GradingRun, run_id)
        assert run.status == GradingRunStatus.completed
        from app.services.code_testing import load_run_results

        assert load_run_results(db, run_id)["results"][0]["status"] == "passed"
        sub = db.get(Submission, env.submission.id)
        assert sub.status == SubmissionStatus.ready_for_review


async def test_runner_incomplete_marks_failed(db_session, monkeypatch):
    env = _make_env(db_session)
    # 评分分支两轮都不提交；rescore 一轮后仍缺项，给出可读错误。
    provider = ScriptedProvider([[_turn()], [_turn()]])
    _patch_provider(monkeypatch, provider)
    run_id, token = _claim_run(env.factory, env.submission.id)

    status = await runner.execute_run(
        run_id,
        session_factory=env.factory,
        submission_id=env.submission.id,
        question_name="Runner 题",
        claim_token=token,
    )
    assert status == GradingRunStatus.failed
    with env.factory() as db:
        run = db.get(GradingRun, run_id)
        assert run.status == GradingRunStatus.failed
        assert "缺少评分项" in (run.error_message or "")


async def test_runner_cancel_marks_cancelled(db_session, monkeypatch):
    env = _make_env(db_session)
    provider = ScriptedProvider(
        [[_turn(_function_call("c1", "open_grading_package", {})), asyncio.Event().wait()]]
    )
    _patch_provider(monkeypatch, provider)
    run_id, token = _claim_run(env.factory, env.submission.id)

    async def cancel_soon() -> None:
        await asyncio.sleep(0.2)
        with env.factory() as db:
            run = db.get(GradingRun, run_id)
            run_repo.request_cancel(db, run)

    cancel_task = asyncio.create_task(cancel_soon())
    try:
        status = await runner.execute_run(
            run_id,
            session_factory=env.factory,
            submission_id=env.submission.id,
            question_name="Runner 题",
            claim_token=token,
        )
    finally:
        await cancel_task
    assert status == GradingRunStatus.cancelled
    with env.factory() as db:
        run = db.get(GradingRun, run_id)
        assert run.status == GradingRunStatus.cancelled


async def test_runner_provider_missing_key_fails(db_session, monkeypatch):
    env = _make_env(db_session)
    # 不注入脚本 provider:真实 _build_provider 在未配置 key 时直接失败
    run_id, token = _claim_run(env.factory, env.submission.id)
    status = await runner.execute_run(
        run_id,
        session_factory=env.factory,
        submission_id=env.submission.id,
        question_name="Runner 题",
        claim_token=token,
    )
    assert status == GradingRunStatus.failed
    with env.factory() as db:
        run = db.get(GradingRun, run_id)
        assert run.status == GradingRunStatus.failed
        assert "API key" in (run.error_message or "") or "未配置" in (
            run.error_message or ""
        )


async def test_build_provider_wires_cross_process_refresh_lease(db_session):
    """订阅通道必须拿到租约回调,官方通道不注入。"""
    from app.services.provider_config import save_chatgpt_tokens, set_provider_config

    save_chatgpt_tokens(
        db_session,
        {
            "access_token": "access-1",
            "refresh_token": "refresh-1",
            "account_id": "acct-1234",
            "plan": "plus",
            "expires_at": 4102444800,
        },
    )
    set_provider_config(db_session, {"openai_api_key": "sk-test"})
    factory = sessionmaker(bind=db_session.bind, expire_on_commit=False)

    provider = runner._build_provider(factory, "chatgpt_sub", None, None)
    try:
        assert provider._lease_acquire is not None
        assert provider._lease_release is not None
        # SQLite 下租约退化为放行,但仍走真实服务层
        assert provider._lease_acquire(60.0) is True
    finally:
        await provider.aclose()

    official = runner._build_provider(factory, "openai_api", None, None)
    try:
        assert getattr(official, "_lease_acquire", None) is None
    finally:
        await official.aclose()
