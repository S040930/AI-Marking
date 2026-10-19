"""结构化评分包工件：构建、摘要与持久化。

关键约束：``code_files[].source_text`` 直接来自提交记录，行号按单文件
切分，绝不从拼接后的评分包文本反推，避免证据行号错位。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.application.grading_workflow import (
    build_context_parts,
    iter_code_files,
)
from app.models.grading_run_artifact import GradingRunArtifact
from app.models.submission import Submission
from app.services.rubric import ResolvedRubric

ARTIFACT_KIND = "grading_package"
ARTIFACT_VERSION = 1


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def build_package_artifact(
    db: Session,
    sub: Submission,
    resolved: ResolvedRubric | None = None,
) -> tuple[dict[str, Any], str]:
    """构建评分包工件与 context hash；不落库。"""
    question_text, submission_text, source_text, context_hash, resolved = (
        build_context_parts(db, sub, resolved)
    )
    code_files = [
        {
            "filename": item["filename"],
            "question_number": item["question_number"],
            "source_text": item["source_text"],
            "sha256": item["sha256"],
            "line_count": len(item["lines"]),
        }
        for item in iter_code_files(sub)
    ]
    sections = [
        {"name": "question", "text": question_text, "chars": len(question_text)},
        {"name": "submission", "text": submission_text, "chars": len(submission_text)},
        {"name": "source", "text": source_text, "chars": len(source_text)},
    ]
    artifact: dict[str, Any] = {
        "version": ARTIFACT_VERSION,
        "question": {
            "text": question_text,
            "ocr_hash": "sha256:" + _sha256_text(question_text),
        },
        "submission": {
            "text": submission_text,
            "filename": sub.original_filename,
        },
        "rubric": {
            "source": resolved.source,
            "snapshot_id": resolved.snapshot_id,
            "text": resolved.text,
            "total_max_score": resolved.total_max_score,
            "items": resolved.items,
        },
        "code_files": code_files,
        "sections": sections,
        "has_code": bool(code_files),
        "grading_revision": sub.grading_revision,
        "context_hash": context_hash,
        "total_chars": sum(section["chars"] for section in sections),
    }
    return artifact, context_hash


def artifact_sha256(artifact: dict[str, Any]) -> str:
    """对规范化 JSON 取哈希；用于工件去重与 digest。"""
    encoded = json.dumps(
        artifact, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return _sha256_text(encoded)


def store_package_artifact(
    db: Session,
    run_id: int,
    artifact: dict[str, Any],
) -> GradingRunArtifact:
    """按 ``(run_id, kind, sha256)`` 幂等保存；调用方负责 commit。"""
    encoded = json.dumps(
        artifact, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    sha = _sha256_text(encoded)
    existing = db.execute(
        select(GradingRunArtifact).where(
            GradingRunArtifact.run_id == run_id,
            GradingRunArtifact.kind == ARTIFACT_KIND,
            GradingRunArtifact.sha256 == sha,
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    record = GradingRunArtifact(
        run_id=run_id,
        kind=ARTIFACT_KIND,
        sha256=sha,
        content=encoded,
        meta={
            "version": artifact.get("version"),
            "total_chars": artifact.get("total_chars"),
            "has_code": artifact.get("has_code"),
            "grading_revision": artifact.get("grading_revision"),
            "rubric_snapshot_id": (artifact.get("rubric") or {}).get("snapshot_id"),
        },
    )
    db.add(record)
    db.flush()
    return record


def load_artifact_content(record: GradingRunArtifact) -> dict[str, Any]:
    """读取并校验工件 JSON。"""
    content = json.loads(record.content)
    if not isinstance(content, dict):
        raise ValueError("评分包工件格式错误")
    return content


def make_package_digest(record: GradingRunArtifact) -> dict[str, Any]:
    """生成进 checkpoint 的轻量摘要（不包含任何正文）。"""
    artifact = load_artifact_content(record)
    sections = [
        str(section.get("name"))
        for section in artifact.get("sections", [])
        if isinstance(section, dict) and section.get("name")
    ]
    rubric = artifact.get("rubric") or {}
    return {
        "artifact_id": record.id,
        "sha256": record.sha256,
        "total_chars": artifact.get("total_chars", 0),
        "has_code": bool(artifact.get("has_code")),
        "grading_revision": artifact.get("grading_revision", 0),
        "rubric_snapshot_id": rubric.get("snapshot_id"),
        "sections": sections,
    }
