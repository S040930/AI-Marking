"""评分包摄取：服务端分页读包、落结构化 artifact、签发句柄。

模型不负责“是否读完”；``context_complete`` 由服务端循环保证，避免模型
自述读完但实际漏页。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from app.agent.artifacts import (
    build_package_artifact,
    make_package_digest,
    store_package_artifact,
)
from app.application import grading_package as grading_package
from app.core.errors import ApplicationError
from app.models.submission import Submission


@dataclass
class PackageRuntime:
    """单次 run 的进程内句柄与工件；不进入 checkpoint。"""

    artifact: dict[str, Any] | None = None
    grading_handle: str | None = None
    context_hash: str | None = None
    rubric_handle: str | None = None
    rubric_ocr_text: str | None = None


def ensure_package_artifact(
    session_factory: sessionmaker[Session],
    submission_id: int,
    run_id: int,
    runtime: PackageRuntime,
    *,
    allow_missing_rubric: bool = True,
) -> dict[str, Any] | None:
    if runtime.artifact is not None:
        return runtime.artifact
    with session_factory() as db:
        submission = db.get(Submission, submission_id)
        if submission is None:
            return None
        result = ingest_package(db, submission, run_id, runtime)
    if not result.get("ok") or (
        result.get("needs_rubric") and not allow_missing_rubric
    ):
        return None
    return runtime.artifact


def ingest_package(
    db: Session,
    sub: Submission,
    run_id: int,
    runtime: PackageRuntime,
) -> dict[str, Any]:
    """分页读包直到 context_complete，并保存 artifact。

    返回 ``{"ok": True, "digest": ...}``、题目缺 rubric 的
    ``{"ok": True, "needs_rubric": True}`` 或 ``{"ok": False, "error": ...}``。
    needs_rubric 时不写入 artifact，只暂存一个临时工件供 rubric 提取节点
    只读使用；rubric 保存后需重新摄包换取新工件与评分句柄。
    """
    continuation: str | None = None
    pages: list[str] = []
    needs_rubric = False
    try:
        while True:
            response = grading_package.open_grading_package(db, sub, continuation)
            if response.needs_rubric:
                runtime.rubric_handle = response.rubric_handle
                runtime.rubric_ocr_text = response.question_ocr_text or ""
                needs_rubric = True
                break
            if response.context_complete:
                runtime.grading_handle = response.grading_handle
                if response.content:
                    pages.append(response.content)
                break
            if not response.continuation_token:
                return {"ok": False, "error": "评分包分页缺少续读游标"}
            if response.content:
                pages.append(response.content)
            continuation = response.continuation_token
    except ApplicationError as exc:
        return {"ok": False, "error": str(exc.detail)}

    artifact, context_hash = build_package_artifact(db, sub)
    runtime.artifact = artifact
    runtime.context_hash = context_hash
    if needs_rubric:
        return {"ok": True, "needs_rubric": True}
    # 分页文本与工件必须来自同一上下文；不一致说明作业在读取期间变化。
    if runtime.grading_handle is None:
        return {"ok": False, "error": "评分包未签发评分句柄"}
    record = store_package_artifact(db, run_id, artifact)
    db.commit()
    return {
        "ok": True,
        "digest": make_package_digest(record),
        "chars": sum(len(page) for page in pages),
    }
