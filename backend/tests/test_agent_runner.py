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
    from app.application import grading_workflow as workflow

    package = workflow.open_grading_package(db_session, submission, None)
    header = json.loads(package.content.split("\n", 1)[1].split("\n\n", 1)[0])
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


def _patch_provider(monkeypatch, provider: ScriptedProvider) -> None:
    monkeypatch.setattr(
        runner, "_build_provider", lambda *args, **kwargs: provider
    )


async def test_runner_completes_after_open_and_save(db_session, monkeypatch):
    env = _make_env(db_session)
    save_args = _prepare_save_arguments(db_session, env.submission)
    provider = ScriptedProvider(
        [
            [_turn(_function_call("c1", "open_grading_package", {}))],
            [_turn(_function_call("c2", "save_assessment", save_args))],
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
    # 两个回合:打开评分包 + 保存
    assert len(provider.requests) == 2


async def test_runner_checkpoint_then_resume(db_session, monkeypatch):
    """含代码作业:请求教师确认 → waiting_for_teacher → 答复续跑 → 保存完成。"""
    env = _make_env(db_session, with_code=True)
    save_args = _prepare_save_arguments(db_session, env.submission)
    provider = ScriptedProvider(
        [
            [
                _turn(
                    _function_call(
                        "c1",
                        "open_grading_package",
                        {},
                    )
                )
            ],
            [
                _turn(
                    _function_call(
                        "c2",
                        "request_teacher_confirmation",
                        {"message": "请确认本地运行表现与报告描述是否一致"},
                    )
                )
            ],
            # 续跑:确认一致 + 保存
            [
                _turn(
                    _function_call(
                        "c3", "confirm_visual_review", {"verdict": "consistent"}
                    )
                )
            ],
            [_turn(_function_call("c4", "save_assessment", save_args))],
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
    assert status == GradingRunStatus.waiting_for_teacher
    with env.factory() as db:
        run = db.get(GradingRun, run_id)
        assert run.status == GradingRunStatus.waiting_for_teacher
        assert run.checkpoint is not None
        assert "一致" in run.checkpoint["message"]
        assert run.agent_state and run.agent_state["items"]
        # worker 关闭常驻回合后释放所有权(模拟 worker 行为)
        assert run_repo.release_waiting_run(db, run_id, token)

    # 教师答复(worker_id 已释放 → 恢复为 queued 等待续跑)
    with env.factory() as db:
        run = db.get(GradingRun, run_id)
        assert checkpoint_repo.answer_checkpoint(
            db, run, verdict="consistent", note=None
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
    assert status == GradingRunStatus.completed
    with env.factory() as db:
        run = db.get(GradingRun, run_id)
        assert run.status == GradingRunStatus.completed
        sub = db.get(Submission, env.submission.id)
        assert sub.status == SubmissionStatus.ready_for_review


async def test_runner_incomplete_marks_failed(db_session, monkeypatch):
    env = _make_env(db_session)
    # 第一回合打开评分包;第二回合直接 end_turn(未保存)
    provider = ScriptedProvider(
        [
            [_turn(_function_call("c1", "open_grading_package", {}))],
            [_turn()],
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
    assert status == GradingRunStatus.failed
    with env.factory() as db:
        run = db.get(GradingRun, run_id)
        assert run.status == GradingRunStatus.failed
        assert "未在回合内保存" in (run.error_message or "")


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


def test_resume_prompt_mentions_reopen():
    run = GradingRun(
        submission_id=1,
        provider="openai_api",
        teacher_verdict="mismatch",
        teacher_note="第 2 题输出与报告不一致",
    )
    prompt = checkpoint_repo.build_resume_prompt(run)
    assert "mismatch" in prompt
    assert "第 2 题" in prompt
    assert "open_grading_package" in prompt
