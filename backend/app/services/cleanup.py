"""uploads/ 目录的定期清理服务。

``UPLOAD_RETENTION_DAYS`` 仅是无数据库引用孤儿文件的清理宽限期。
任何仍被题目、替换暂存、作业或代码记录引用的文件都不会被删除。

实现:
- ``cleanup_referenced_*``:将过期文件候选按固定批次查库,不全表加载引用
- ``periodic_cleanup_loop``:在独立任务 worker 中以后台 task 运行,
  每隔 ``CLEANUP_INTERVAL_SECONDS`` 扫描一次

注意:
- 仅删除 ``.pdf`` 文件,避免误删其他类型文件
- 用 ``Path.stat().st_mtime`` 判断修改时间,UTC 比较
- 单文件删除失败仅记日志,不中断扫描
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

from sqlalchemy import select

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.question import Question
from app.models.submission import Submission
from app.models.submission_code_file import SubmissionCodeFile
from app.models.submission_code_input_file import SubmissionCodeInputFile

logger = logging.getLogger(__name__)

# Each cleanup query contains at most two textual variants per candidate. A
# 250-file batch therefore stays below SQLite's common 999-parameter limit in
# tests and keeps production memory bounded independently of table size.
_REFERENCE_BATCH_SIZE = 250


def _path_query_values(path: Path) -> set[str]:
    """Return canonical and cwd-relative forms used by current/legacy rows."""
    resolved = path.resolve()
    values = {str(resolved)}
    try:
        values.add(str(resolved.relative_to(Path.cwd().resolve())))
    except ValueError:
        pass
    return values


def _referenced_paths_for_batch(db, candidates: list[Path]) -> set[Path]:
    """Resolve durable references for one bounded candidate batch."""
    query_values = {
        value for candidate in candidates for value in _path_query_values(candidate)
    }
    referenced: set[str] = set()
    for row in db.execute(
        select(Question.file_path, Question.replacement_file_path).where(
            (Question.file_path.in_(query_values))
            | (Question.replacement_file_path.in_(query_values))
        )
    ):
        referenced.update(value for value in row if value)
    for model in (Submission, SubmissionCodeFile, SubmissionCodeInputFile):
        referenced.update(
            value
            for (value,) in db.execute(
                select(model.file_path).where(model.file_path.in_(query_values))
            )
            if value
        )
    return {Path(value).resolve() for value in referenced}


def _prune_empty_parents(start: Path, root: Path) -> None:
    """Remove empty parents without materializing/sorting the whole directory tree."""
    root = root.resolve()
    current = start.resolve()
    while current != root:
        try:
            current.relative_to(root)
            current.rmdir()
        except (OSError, ValueError):
            return
        current = current.parent


def _delete_unreferenced_batch(
    db,
    candidates: list[Path],
    root: Path,
) -> int:
    protected = _referenced_paths_for_batch(db, candidates)
    # The cleanup loop owns this read-only Session. End the snapshot after each
    # batch so a large disk scan does not hold one transaction indefinitely.
    db.rollback()
    deleted = 0
    for path in candidates:
        try:
            if path.resolve() in protected:
                continue
            path.unlink(missing_ok=True)
            deleted += 1
            _prune_empty_parents(path.parent, root)
        except OSError as exc:
            logger.warning("删除 %s 失败: %s", path, exc)
    return deleted


def _cleanup_candidates(db, candidates, root: Path) -> int:
    deleted = 0
    batch: list[Path] = []
    for path in candidates:
        batch.append(path)
        if len(batch) == _REFERENCE_BATCH_SIZE:
            deleted += _delete_unreferenced_batch(db, batch, root)
            batch = []
    if batch:
        deleted += _delete_unreferenced_batch(db, batch, root)
    return deleted


def _old_pdf_candidates(upload_dir: Path, cutoff: float):
    for path in upload_dir.rglob("*.pdf"):
        try:
            if (
                path.is_file()
                and path.suffix.lower() == ".pdf"
                and path.stat().st_mtime < cutoff
            ):
                yield path
        except OSError as exc:
            logger.warning("检查 %s 失败: %s", path, exc)


def _old_code_candidates(upload_dir: Path, cutoff: float):
    for root_name in ("code", "code-inputs", "code-artifacts"):
        root = upload_dir / root_name
        if not root.exists():
            continue
        for path in root.rglob("*"):
            try:
                if path.is_file() and path.stat().st_mtime < cutoff:
                    yield path
            except OSError as exc:
                logger.warning("检查 %s 失败: %s", path, exc)


def cleanup_referenced_uploads(db, upload_dir: Path, retention_days: int) -> int:
    """Delete old PDFs after bounded, database-side reference checks."""
    if retention_days < 1 or not upload_dir.exists():
        return 0
    cutoff = time.time() - retention_days * 86400
    return _cleanup_candidates(
        db,
        _old_pdf_candidates(upload_dir, cutoff),
        upload_dir,
    )


def cleanup_referenced_code_artifacts(
    db, upload_dir: Path, retention_days: int
) -> int:
    """Delete old code files/artifacts after bounded reference checks."""
    if retention_days < 1 or not upload_dir.exists():
        return 0
    cutoff = time.time() - retention_days * 86400
    return _cleanup_candidates(
        db,
        _old_code_candidates(upload_dir, cutoff),
        upload_dir,
    )


async def periodic_cleanup_loop() -> None:
    """后台定期清理循环,由独立任务 worker 启动。

    循环体:
    1. 立即执行一次清理(startup 时清理历史遗留)
    2. 以后每 ``CLEANUP_INTERVAL_SECONDS`` 秒执行一次

    取消语义:``asyncio.CancelledError`` 在 sleep 时被抛出,直接退出循环。
    """
    upload_dir = Path(settings.UPLOAD_DIR).resolve()
    interval = max(60, settings.CLEANUP_INTERVAL_SECONDS)
    retention = settings.UPLOAD_RETENTION_DAYS
    logger.info(
        "启动 uploads 清理任务: dir=%s, retention=%sd, interval=%ss",
        upload_dir,
        retention,
        interval,
    )
    try:
        while True:
            try:
                with SessionLocal() as db:
                    pdf_deleted = cleanup_referenced_uploads(
                        db, upload_dir, retention
                    )
                    code_deleted = cleanup_referenced_code_artifacts(
                        db, upload_dir, retention
                    )
                if pdf_deleted or code_deleted:
                    logger.info(
                        "uploads 清理完成: PDF=%s, 代码/产物=%s",
                        pdf_deleted,
                        code_deleted,
                    )
            except Exception:
                # 单次清理失败不中断循环,等下个周期重试
                logger.exception("uploads 清理失败,等待下个周期")
            await asyncio.sleep(interval)
    except asyncio.CancelledError:
        logger.info("uploads 清理任务已停止")
        raise
