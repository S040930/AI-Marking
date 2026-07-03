"""uploads/ 目录的定期清理服务。

批改完成后 ``ocr_text`` 已入库,PDF 文件仅在理论上的"复评"场景需要——
但当前无复评功能。为避免磁盘无限增长(50MB × 1000 = 50GB),按
``UPLOAD_RETENTION_DAYS`` 删除过期 PDF,保留 DB 记录。

实现:
- ``cleanup_uploads``:扫描一次 upload_dir,按 mtime 删除过期 PDF
- ``periodic_cleanup_loop``:在 FastAPI lifespan 中以后台 task 运行,
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
from app.db.session import AsyncSessionLocal
from app.models.question import Question

logger = logging.getLogger(__name__)


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
    for entry in upload_dir.iterdir():
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
    if deleted:
        logger.info("清理 uploads/ 完成,删除 %s 个过期 PDF", deleted)
    return deleted


async def periodic_cleanup_loop() -> None:
    """后台定期清理循环,由 FastAPI lifespan 启动。

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
                async with AsyncSessionLocal() as db:
                    question_paths = (
                        await db.execute(select(Question.file_path))
                    ).scalars().all()
                cleanup_uploads(
                    upload_dir,
                    retention,
                    {Path(file_path) for file_path in question_paths if file_path},
                )
            except Exception:
                # 单次清理失败不中断循环,等下个周期重试
                logger.exception("uploads 清理失败,等待下个周期")
            await asyncio.sleep(interval)
    except asyncio.CancelledError:
        logger.info("uploads 清理任务已停止")
        raise
