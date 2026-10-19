"""P1 结构化 artifact：行号来源、幂等存储与轻量 digest。"""

from __future__ import annotations

from sqlalchemy import func, select

from app.agent.artifacts import (
    artifact_sha256,
    build_package_artifact,
    load_artifact_content,
    make_package_digest,
    store_package_artifact,
)
from app.models.grading_run import GradingRun
from app.models.grading_run_artifact import GradingRunArtifact
from app.models.question import Question, QuestionStatus
from app.models.submission import Submission, SubmissionGradingMode, SubmissionStatus
from app.models.submission_code_file import SubmissionCodeFile


def _submission(db, *, with_code: bool) -> Submission:
    question = Question(
        name="artifact 题",
        original_filename="q.pdf",
        file_path="/tmp/q.pdf",
        ocr_text="请实现并分析",
        status=QuestionStatus.ready,
    )
    db.add(question)
    sub = Submission(
        original_filename="answer.pdf",
        file_path="/tmp/a.pdf",
        question=question,
        ocr_text="报告正文 92% accuracy",
        status=SubmissionStatus.awaiting_grading,
        grading_mode=SubmissionGradingMode.agent,
    )
    db.add(sub)
    db.commit()
    if with_code:
        sub.code_files.append(
            SubmissionCodeFile(
                question_number=1,
                original_filename="task1.py",
                file_path="/tmp/task1.py",
                file_kind="py",
                source_sha256="b" * 64,
                source_text="import pandas as pd\n\ndf = pd.read_csv('data.csv')\n",
            )
        )
        db.commit()
        db.refresh(sub)
    return sub


def test_build_artifact_keeps_per_file_line_numbers(db_session) -> None:
    sub = _submission(db_session, with_code=True)
    artifact, context_hash = build_package_artifact(db_session, sub)

    assert artifact["has_code"] is True
    assert context_hash.startswith("sha256:")
    assert [section["name"] for section in artifact["sections"]] == [
        "question",
        "submission",
        "source",
    ]
    code = artifact["code_files"][0]
    assert code["filename"] == "task1.py"
    assert code["line_count"] == 3
    assert code["source_text"].splitlines()[2] == "df = pd.read_csv('data.csv')"
    assert artifact_sha256(artifact) == artifact_sha256(dict(artifact))


def test_store_artifact_is_idempotent_and_digest_has_no_body(db_session) -> None:
    sub = _submission(db_session, with_code=False)
    run = GradingRun(submission_id=sub.id, provider="openai_api")
    db_session.add(run)
    db_session.commit()

    artifact, _ = build_package_artifact(db_session, sub)
    first = store_package_artifact(db_session, run.id, artifact)
    second = store_package_artifact(db_session, run.id, artifact)
    db_session.commit()

    assert first.id == second.id
    count = db_session.scalar(
        select(func.count(GradingRunArtifact.id)).where(
            GradingRunArtifact.run_id == run.id
        )
    )
    assert count == 1

    digest = make_package_digest(first)
    assert digest["artifact_id"] == first.id
    assert digest["sections"] == ["question", "submission", "source"]
    assert "text" not in digest
    assert load_artifact_content(first)["version"] == 1
