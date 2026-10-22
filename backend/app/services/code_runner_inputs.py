"""Stage uploaded student code and datasets into read-only per-question roots."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any, Callable

from app.core.config import settings
from app.models.submission import Submission
from app.services.code_runner_errors import (
    CodeRunnerCancelledError,
    CodeRunnerInfrastructureError,
)
from app.services.submission_zip import (
    MAX_CODE_INPUT_FILE_BYTES,
    MAX_CODE_INPUT_FILES,
    MAX_CODE_INPUT_TOTAL_BYTES,
)


def stage_code_inputs(
    root: Path,
    code_files: list[Any],
    shared_data: list[tuple[Path, str, str]],
    should_cancel: Callable[[], bool] | None = None,
) -> list[tuple[str, str]]:
    staged_names: set[str] = set()
    for item in code_files:
        if should_cancel and should_cancel():
            raise CodeRunnerCancelledError("批改运行已取消")
        name = Path(item.original_filename).name
        if not name or name in {".", ".."}:
            raise CodeRunnerInfrastructureError("代码文件名不安全")
        if name in staged_names:
            raise CodeRunnerInfrastructureError(f"小题内代码文件重名：{name}")
        staged_names.add(name)
        target = root / name
        target.write_text(item.source_text or "", encoding="utf-8")
        target.chmod(0o444)
    bindings: list[tuple[str, str]] = []
    if shared_data:
        data_dir = root / "assets"
        data_dir.mkdir(mode=0o755)
        for source, asset_name, filename in shared_data:
            if should_cancel and should_cancel():
                raise CodeRunnerCancelledError("批改运行已取消")
            try:
                os.link(source, data_dir / asset_name)
            except OSError as exc:
                raise CodeRunnerInfrastructureError("作业数据集无法隔离暂存") from exc
            bindings.append((asset_name, filename))
    return bindings


def stage_data_inputs(
    root: Path,
    submission: Submission,
    should_cancel: Callable[[], bool] | None = None,
) -> list[tuple[Path, str, str]]:
    bindings: list[tuple[Path, str, str]] = []
    question_inputs = list(submission.code_input_files)
    if not question_inputs:
        return bindings
    if len(question_inputs) > MAX_CODE_INPUT_FILES:
        raise CodeRunnerInfrastructureError("作业数据集文件数超过暂存限制")
    data_dir = root / "shared-assets"
    data_dir.mkdir(mode=0o700)
    data_names: set[str] = set()
    try:
        upload_root = Path(settings.UPLOAD_DIR).resolve(strict=True)
    except OSError as exc:
        raise CodeRunnerInfrastructureError("上传目录不可读取") from exc
    total_size = 0
    for index, item in enumerate(question_inputs, start=1):
        if should_cancel and should_cancel():
            raise CodeRunnerCancelledError("批改运行已取消")
        source = Path(item.file_path)
        try:
            absolute_source = source.absolute()
            resolved_source = source.resolve(strict=True)
            resolved_source.relative_to(upload_root)
        except (OSError, ValueError) as exc:
            raise CodeRunnerInfrastructureError(
                "作业数据集文件不在受控上传目录内"
            ) from exc
        if (
            not resolved_source.is_file()
            or absolute_source != resolved_source
            or source.is_symlink()
        ):
            raise CodeRunnerInfrastructureError("作业数据集文件不可读取")
        declared_size = int(item.size_bytes)
        if declared_size > MAX_CODE_INPUT_FILE_BYTES or total_size + declared_size > MAX_CODE_INPUT_TOTAL_BYTES:
            raise CodeRunnerInfrastructureError("作业数据集超过暂存大小限制")
        filename = Path(item.original_filename).name
        if not filename or filename in {".", ".."} or filename in data_names:
            raise CodeRunnerInfrastructureError("作业数据集文件名无效或重复")
        data_names.add(filename)
        asset_name = f"asset-{index}"
        target = data_dir / asset_name
        digest = hashlib.sha256()
        copied_size = 0
        try:
            with resolved_source.open("rb") as source_file, target.open("xb") as target_file:
                while chunk := source_file.read(1024 * 1024):
                    if should_cancel and should_cancel():
                        raise CodeRunnerCancelledError("批改运行已取消")
                    copied_size += len(chunk)
                    if copied_size > declared_size or total_size + copied_size > MAX_CODE_INPUT_TOTAL_BYTES:
                        raise CodeRunnerInfrastructureError("作业数据集超过暂存大小限制")
                    digest.update(chunk)
                    target_file.write(chunk)
            target.chmod(0o444)
        except OSError as exc:
            raise CodeRunnerInfrastructureError("作业数据集无法暂存") from exc
        if copied_size != declared_size or digest.hexdigest() != item.sha256:
            raise CodeRunnerInfrastructureError("作业数据集内容与上传记录不匹配")
        total_size += copied_size
        bindings.append((target, asset_name, filename))
    return bindings
