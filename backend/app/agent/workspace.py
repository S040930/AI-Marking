"""批改工作区:有界物化与保留期清理。

由旧工作区模块裁剪:删除 skill.md 物化(流程已收敛到
prompts/grading.md)与交互式对话工作区(功能删除);根目录改为
``uploads/grading-workspaces``。
"""

from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

from app.core.config import settings
from app.models.grading_run import GradingRun
from app.models.submission import Submission

MAX_WORKSPACE_BYTES = 150 * 1024 * 1024
WORKSPACE_RETENTION_SECONDS = 24 * 60 * 60


def workspace_root() -> Path:
    return Path(settings.UPLOAD_DIR).resolve() / "grading-workspaces"


def _trusted_source(raw: str) -> Path:
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = Path(__file__).resolve().parents[2] / candidate
    if candidate.is_symlink():
        raise ValueError(f"批改输入文件不能是符号链接: {candidate.name}")
    path = candidate.resolve()
    upload_root = Path(settings.UPLOAD_DIR)
    if not upload_root.is_absolute():
        upload_root = (Path(__file__).resolve().parents[2] / upload_root).resolve()
    else:
        upload_root = upload_root.resolve()
    if path == upload_root or upload_root not in path.parents:
        raise ValueError("批改输入文件不在受控 uploads 目录")
    if not path.is_file():
        raise ValueError(f"批改输入文件不存在或不是普通文件: {path.name}")
    return path


def _materialize_to(submission: Submission, target: Path) -> Path:
    """把 submission 的报告/代码/输入物化到 ``target``;有界流式拷贝。"""
    records = [(submission.original_filename, submission.file_path)]
    records.extend(
        (item.original_filename, item.file_path) for item in submission.code_files
    )
    records.extend(
        (item.original_filename, item.file_path) for item in submission.code_input_files
    )
    names: set[str] = set()
    sources: list[tuple[str, Path, int]] = []
    total = 0
    # scratch 为运行目录的保留名;学生文件同名在拷贝前直接拒绝。
    reserved = {"scratch"}
    for original_name, raw_path in records:
        name = Path(original_name).name
        if not name or name in names or name in reserved:
            raise ValueError(f"批改工作区文件名冲突: {name or '<empty>'}")
        names.add(name)
        source = _trusted_source(raw_path)
        size = source.stat().st_size
        total += size
        if total > MAX_WORKSPACE_BYTES:
            raise ValueError("批改工作区超过 150MB 上限")
        sources.append((name, source, size))

    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True, mode=0o700)
    (target / "scratch").mkdir(mode=0o700)
    try:
        for name, source, _ in sources:
            destination = target / name
            with source.open("rb") as src, destination.open("xb") as dst:
                shutil.copyfileobj(src, dst, length=1024 * 1024)
            os.chmod(destination, 0o400)
    except Exception:
        shutil.rmtree(target, ignore_errors=True)
        raise
    return target


def materialize_workspace(run: GradingRun, submission: Submission) -> Path:
    """Copy report, code and declared input files using bounded streaming."""
    target = _materialize_to(submission, workspace_root() / f"run-{run.id}")
    run.workspace_path = str(target)
    return target


def cleanup_expired_workspaces(now: float | None = None) -> int:
    root = workspace_root()
    if not root.exists():
        return 0
    cutoff = (time.time() if now is None else now) - WORKSPACE_RETENTION_SECONDS
    deleted = 0
    for path in root.iterdir():
        try:
            if (
                path.is_dir()
                and not path.is_symlink()
                and path.stat().st_mtime < cutoff
            ):
                shutil.rmtree(path)
                deleted += 1
        except OSError:
            continue
    return deleted
