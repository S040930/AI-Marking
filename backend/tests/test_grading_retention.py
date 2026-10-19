"""过期批改运行的 artifacts / checkpointer thread 清理。

``updated_at`` 作为终态时间代理;只有终态且超期的 run 会被清理。
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import create_engine, select, update
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.agent import run_store as run_repo
from app.core.time import utc_now_naive
from app.db.base import Base
from app.domain.grading_run import GradingRunStatus
from app.grading_worker import purge_expired_runs
from app.models.grading_run import GradingRun
from app.models.grading_run_artifact import GradingRunArtifact
from app.models.question import Question, QuestionStatus
from app.models.submission import Submission, SubmissionStatus


@pytest.fixture
def factory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        db.add(
            Question(
                id="q1",
                name="题目1",
                original_filename="q1.pdf",
                file_path="uploads/q1.pdf",
                status=QuestionStatus.ready,
            )
        )
        db.add(
            Submission(
                id=1,
                original_filename="s1.pdf",
                file_path="uploads/s1.pdf",
                question_id="q1",
                status=SubmissionStatus.awaiting_grading,
            )
        )
        db.commit()
    yield factory
    Base.metadata.drop_all(engine)
    engine.dispose()


def _make_run(factory, *, status: GradingRunStatus, age_days: float) -> int:
    with factory() as db:
        run = run_repo.create_run(db, submission_id=1, provider="openai_api")
        run.status = status
        db.add(
            GradingRunArtifact(
                run_id=run.id,
                kind="package",
                sha256="a" * 64,
                content="{}",
            )
        )
        db.commit()
        run_id = run.id
        # 显式写 updated_at：列带 onupdate，普通 ORM 赋值会被覆盖
        db.execute(
            update(GradingRun)
            .where(GradingRun.id == run_id)
            .values(updated_at=utc_now_naive() - timedelta(days=age_days))
        )
        db.commit()
        return run_id


class _FakeSaver:
    def __init__(self):
        self.deleted: list[str] = []

    async def adelete_thread(self, thread_id: str) -> None:
        self.deleted.append(thread_id)


async def test_purge_removes_artifacts_and_thread_for_old_terminal_runs(factory):
    old_done = _make_run(
        factory, status=GradingRunStatus.completed, age_days=8
    )
    fresh_done = _make_run(
        factory, status=GradingRunStatus.completed, age_days=1
    )
    old_active = _make_run(
        factory, status=GradingRunStatus.failed, age_days=30
    )
    saver = _FakeSaver()

    purged = await purge_expired_runs(saver, retention_days=7, session_factory=factory)

    assert purged == 2
    assert set(saver.deleted) == {
        f"grading-run-{old_done}",
        f"grading-run-{old_active}",
    }
    with factory() as db:
        remaining = db.execute(select(GradingRunArtifact.run_id)).scalars().all()
        assert list(remaining) == [fresh_done]


async def test_purge_keeps_non_terminal_runs_artifacts(factory):
    _make_run(factory, status=GradingRunStatus.queued, age_days=30)
    saver = _FakeSaver()

    purged = await purge_expired_runs(saver, retention_days=7, session_factory=factory)

    assert purged == 0
    assert saver.deleted == []
    with factory() as db:
        assert db.execute(select(GradingRunArtifact)).scalars().all()


async def test_purge_tolerates_thread_delete_failure(factory):
    run_id = _make_run(factory, status=GradingRunStatus.completed, age_days=8)

    class _BrokenSaver:
        async def adelete_thread(self, thread_id: str) -> None:
            raise RuntimeError("saver 不可用")

    purged = await purge_expired_runs(_BrokenSaver(), retention_days=7, session_factory=factory)

    assert purged == 1
    with factory() as db:
        assert db.get(GradingRun, run_id) is not None
        assert db.execute(select(GradingRunArtifact)).scalars().all() == []
