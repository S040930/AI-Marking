"""Question-versioned code tests and run-scoped evidence snapshots."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.time import utc_now_naive
from app.models.code_test_suite import CodeTestSuite
from app.models.grading_run_artifact import GradingRunArtifact
from app.models.question import Question

MAX_CODE_TESTS = 20
MAX_CASE_TIMEOUT_SECONDS = 10
MAX_SUBMISSION_RUNTIME_SECONDS = 180
MAX_CASE_OUTPUT_BYTES = 16 * 1024


class CodeTestCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_number: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=160)
    mode: Literal["program_io", "notebook_assertion"]
    language: Literal[
        "any", "python", "r", "java", "notebook_python", "notebook_r"
    ]
    input: str | None = Field(default=None, max_length=8_000)
    expected_stdout: str | None = Field(default=None, max_length=8_000)
    assertion: str | None = Field(default=None, max_length=4_000)

    @model_validator(mode="after")
    def validate_mode(self) -> "CodeTestCase":
        if self.mode == "program_io":
            if self.language not in ("any", "python", "r", "java"):
                raise ValueError("program_io 语言必须是 any/python/r/java")
            if self.input is None or self.expected_stdout is None:
                raise ValueError("program_io 必须提供 input 和 expected_stdout")
            if self.assertion is not None:
                raise ValueError("program_io 不得包含 assertion")
        else:
            if self.language not in ("notebook_python", "notebook_r"):
                raise ValueError("notebook_assertion 必须声明 Notebook kernel")
            if not self.assertion or self.input is not None or self.expected_stdout is not None:
                raise ValueError("notebook_assertion 只接受 assertion")
        return self

    def serialized(self, index: int) -> dict[str, Any]:
        return {"id": f"q{self.question_number}-t{index}", **self.model_dump(exclude_none=True)}


def source_fingerprint(question: Question, rubric_snapshot_id: str) -> str:
    """Bind suites to the PDF/OCR version and trusted rubric used to generate them."""
    payload = {
        "file_sha256": question.file_sha256,
        "ocr_sha256": hashlib.sha256((question.ocr_text or "").encode()).hexdigest(),
        "rubric_snapshot_id": rubric_snapshot_id,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def get_current_suite(
    db: Session, question_id: str, fingerprint: str
) -> CodeTestSuite | None:
    suites = list(
        db.execute(
            select(CodeTestSuite)
            .where(
                CodeTestSuite.question_id == question_id,
                CodeTestSuite.status.in_(["draft", "approved"]),
            )
            .order_by(CodeTestSuite.version.desc())
        ).scalars()
    )
    for suite in suites:
        if suite.source_fingerprint == fingerprint:
            return suite
        suite.status = "invalidated"
        suite.invalidated_at = utc_now_naive()
    return None


def create_draft_suite(
    db: Session,
    *,
    question_id: str,
    fingerprint: str,
    cases: list[CodeTestCase],
    generated_by_run_id: int,
) -> CodeTestSuite:
    if not cases or len(cases) > MAX_CODE_TESTS:
        raise ValueError(f"测试项数量必须在 1 到 {MAX_CODE_TESTS} 之间")
    current = list(
        db.execute(
            select(CodeTestSuite)
            .where(
                CodeTestSuite.question_id == question_id,
                CodeTestSuite.status.in_(["draft", "approved"]),
            )
            .with_for_update()
        ).scalars()
    )
    for suite in current:
        if suite.source_fingerprint != fingerprint:
            suite.status = "invalidated"
            suite.invalidated_at = utc_now_naive()
        elif suite.status == "draft":
            suite.status = "superseded"
    max_version = db.execute(
        select(CodeTestSuite.version)
        .where(CodeTestSuite.question_id == question_id)
        .order_by(CodeTestSuite.version.desc())
        .limit(1)
    ).scalar_one_or_none()
    suite = CodeTestSuite(
        question_id=question_id,
        version=(max_version or 0) + 1,
        source_fingerprint=fingerprint,
        status="draft",
        tests=[case.serialized(index) for index, case in enumerate(cases, start=1)],
        generated_by_run_id=generated_by_run_id,
    )
    db.add(suite)
    db.flush()
    return suite


def approve_suite(
    db: Session, suite_id: int, *, question_id: str, fingerprint: str
) -> CodeTestSuite:
    suite = db.execute(
        select(CodeTestSuite)
        .where(CodeTestSuite.id == suite_id, CodeTestSuite.question_id == question_id)
        .with_for_update()
    ).scalar_one_or_none()
    if suite is None or suite.status != "draft" or suite.source_fingerprint != fingerprint:
        raise ValueError("测试集已失效或版本已变化，请重新生成")
    for old in db.execute(
        select(CodeTestSuite)
        .where(
            CodeTestSuite.question_id == question_id,
            CodeTestSuite.id != suite.id,
            CodeTestSuite.status == "approved",
        )
        .with_for_update()
    ).scalars():
        old.status = "superseded"
    suite.status = "approved"
    suite.approved_at = utc_now_naive()
    db.flush()
    return suite


def invalidate_question_suites(db: Session, question_id: str) -> None:
    for suite in db.execute(
        select(CodeTestSuite).where(
            CodeTestSuite.question_id == question_id,
            CodeTestSuite.status.in_(["draft", "approved"]),
        )
    ).scalars():
        suite.status = "invalidated"
        suite.invalidated_at = utc_now_naive()


def persist_run_snapshot(
    db: Session, run_id: int, suite: CodeTestSuite | dict[str, Any]
) -> dict[str, Any]:
    if isinstance(suite, dict):
        suite_id = suite.get("id") or suite.get("suite_id")
        version = suite.get("version")
        fingerprint = suite.get("source_fingerprint")
        tests = suite.get("tests")
        status = suite.get("status")
    else:
        suite_id = suite.id
        version = suite.version
        fingerprint = suite.source_fingerprint
        tests = suite.tests
        status = suite.status
    payload = {
        "suite_id": suite_id,
        "version": version,
        "source_fingerprint": fingerprint,
        "tests": tests,
        "status": status,
    }
    _store_run_artifact(db, run_id, "code_test_suite", payload)
    return payload


def persist_run_results(
    db: Session,
    run_id: int,
    snapshot: dict[str, Any],
    results: list[dict[str, Any]],
) -> dict[str, Any]:
    passed = sum(result.get("status") == "passed" for result in results)
    payload = {
        **snapshot,
        "results": results,
        "summary": {
            "total": len(results),
            "passed": passed,
            "failed": len(results) - passed,
        },
    }
    _store_run_artifact(db, run_id, "code_test_results", payload)
    return payload


def _store_run_artifact(db: Session, run_id: int, kind: str, payload: dict[str, Any]) -> None:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    sha = hashlib.sha256(encoded.encode()).hexdigest()
    existing = db.execute(
        select(GradingRunArtifact).where(
            GradingRunArtifact.run_id == run_id,
            GradingRunArtifact.kind == kind,
            GradingRunArtifact.sha256 == sha,
        )
    ).scalar_one_or_none()
    if existing is None:
        db.add(
            GradingRunArtifact(
                run_id=run_id,
                kind=kind,
                sha256=sha,
                content=encoded,
                meta={"suite_version": payload.get("version")},
            )
        )
    db.flush()


def load_run_results(db: Session, run_id: int) -> dict[str, Any] | None:
    record = db.execute(
        select(GradingRunArtifact)
        .where(
            GradingRunArtifact.run_id == run_id,
            GradingRunArtifact.kind == "code_test_results",
        )
        .order_by(GradingRunArtifact.id.desc())
        .limit(1)
    ).scalar_one_or_none()
    if record is None:
        return None
    payload = json.loads(record.content)
    return payload if isinstance(payload, dict) else None


def load_run_suite_snapshot(db: Session, run_id: int) -> dict[str, Any] | None:
    record = db.execute(
        select(GradingRunArtifact)
        .where(
            GradingRunArtifact.run_id == run_id,
            GradingRunArtifact.kind == "code_test_suite",
        )
        .order_by(GradingRunArtifact.id.desc())
        .limit(1)
    ).scalar_one_or_none()
    if record is None:
        return None
    payload = json.loads(record.content)
    return payload if isinstance(payload, dict) else None


def evidence_quote(result: dict[str, Any]) -> str:
    output = " ".join(str(result.get("output") or "").split())
    detail = f" — {output}" if output else ""
    return f"Q{result.get('question_number')} {result.get('status')}: {result.get('title')}{detail}"[:MAX_CASE_OUTPUT_BYTES]


def test_evidence_map(db: Session, run_id: int) -> dict[str, dict[str, Any]]:
    payload = load_run_results(db, run_id)
    if payload is None:
        return {}
    version = int(payload.get("version") or 0)
    return {
        str(result.get("id")): {
            "suite_version": version,
            "quote": evidence_quote(result),
        }
        for result in payload.get("results", [])
        if isinstance(result, dict) and result.get("id")
    }


def run_test_detail(db: Session, run_id: int) -> dict[str, Any] | None:
    return load_run_results(db, run_id)

