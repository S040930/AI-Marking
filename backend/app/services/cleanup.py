"""uploads/ 目录的定期清理服务。

批改完成后 ``ocr_text`` 已入库,PDF 文件仅在理论上的"复评"场景需要——
但当前无复评功能。为避免磁盘无限增长(50MB × 1000 = 50GB),按
``UPLOAD_RETENTION_DAYS`` 删除过期 PDF,保留 DB 记录。

实现:
- ``cleanup_uploads``:递归扫描 upload_dir,按 mtime 删除过期且无数据库引用的 PDF
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


def cleanup_uploads(
    upload_dir: Path,
    retention_days: int,
    protected_paths: set[Path] | None = None,
) -> int:
    """扫描 ``upload_dir``,删除修改时间早于 ``retention_days`` 天前的 PDF。

    Args:
        upload_dir: 上传目录(已校验存在)
        retention_days: 保留天数,< 1 时不删除任何文件(安全阀)

    Returns:
        实际删除的文件数
    """
    if retention_days < 1:
        logger.info("UPLOAD_RETENTION_DAYS=%s < 1,跳过清理", retention_days)
        return 0
    if not upload_dir.exists():
        return 0

    cutoff = time.time() - retention_days * 86400
    protected = {path.resolve() for path in (protected_paths or set())}
    deleted = 0
    for entry in upload_dir.rglob("*.pdf"):
        if not entry.is_file() or entry.suffix.lower() != ".pdf":
            continue
        if entry.resolve() in protected:
            continue
        try:
            if entry.stat().st_mtime < cutoff:
                entry.unlink(missing_ok=True)
                deleted += 1
        except OSError as exc:
            logger.warning("删除 %s 失败: %s", entry, exc)
    for directory in sorted(upload_dir.rglob("*"), reverse=True):
        if directory.is_dir():
            try:
                directory.rmdir()
            except OSError:
                pass
    if deleted:
        logger.info("清理 uploads/ 完成,删除 %s 个过期 PDF", deleted)
    return deleted


def cleanup_code_artifacts(
    upload_dir: Path,
    retention_days: int,
    protected_paths: set[Path] | None = None,
) -> int:
    """清理过期代码源文件与运行产物，同时保留数据库仍引用的路径。"""
    if retention_days < 1:
        return 0
    roots = [
        upload_dir / "code",
        upload_dir / "code-inputs",
        upload_dir / "code-artifacts",
    ]
    cutoff = time.time() - retention_days * 86400
    protected = {path.resolve() for path in (protected_paths or set())}
    deleted = 0
    for root in roots:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*"), reverse=True):
            if path.is_file():
                try:
                    if path.resolve() not in protected and path.stat().st_mtime < cutoff:
                        path.unlink(missing_ok=True)
                        deleted += 1
                except OSError as exc:
                    logger.warning("删除代码运行文件 %s 失败: %s", path, exc)
            elif path.is_dir():
                try:
                    path.rmdir()
                except OSError:
                    pass
    return deleted


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
