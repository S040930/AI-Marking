"""Bounded materialization and retention for ACP run workspaces."""

from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

from app.acp.models import AcpRun
from app.core.config import settings
from app.models.submission import Submission

MAX_WORKSPACE_BYTES = 150 * 1024 * 1024
WORKSPACE_RETENTION_SECONDS = 24 * 60 * 60


def workspace_root() -> Path:
    return Path(settings.UPLOAD_DIR).resolve() / "acp-workspaces"


def chat_workspace_root() -> Path:
    """交互式对话会话的独立根目录。

    与 ``acp-workspaces`` 分开,避免 ``cleanup_expired_workspaces`` 把
    长驻的对话会话工作区按 24h 保留期误删。
    """
    return Path(settings.UPLOAD_DIR).resolve() / "acp-chat-workspaces"


def _trusted_source(raw: str) -> Path:
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = Path(__file__).resolve().parents[2] / candidate
    if candidate.is_symlink():
        raise ValueError(f"ACP 输入文件不能是符号链接: {candidate.name}")
    path = candidate.resolve()
    upload_root = Path(settings.UPLOAD_DIR)
    if not upload_root.is_absolute():
        upload_root = (Path(__file__).resolve().parents[2] / upload_root).resolve()
    else:
        upload_root = upload_root.resolve()
    if path == upload_root or upload_root not in path.parents:
        raise ValueError("ACP 输入文件不在受控 uploads 目录")
    if not path.is_file():
        raise ValueError(f"ACP 输入文件不存在或不是普通文件: {path.name}")
    return path


def _skill_source() -> Path:
    """ai-marking-grader skill 全文（ACP 模式唯一流程权威源）。"""
    project_root = Path(__file__).resolve().parents[3]
    path = project_root / ".agents" / "skills" / "ai-marking-grader" / "SKILL.md"
    return path.resolve()


def _trim_skill_for_acp(text: str) -> str:
    """ACP 版 skill:剔除 MCP 专属块,避免 ACP 每次 run 为 MCP 内容付 token,
    也杜绝指向 ACP 工作区不存在的 references/ 的悬空指针。"""
    start, end = "<!-- @@mcp-only -->", "<!-- @@/mcp-only -->"
    out, i = [], 0
    while True:
        j = text.find(start, i)
        if j < 0:
            out.append(text[i:])
            break
        out.append(text[i:j])
        k = text.find(end, j)
        if k < 0:
            raise ValueError("SKILL.md 存在未闭合的 @@mcp-only 标记")
        i = k + len(end)
    return "".join(out)


def _materialize_to(submission: Submission, target: Path) -> Path:
    """把 submission 的报告/代码/输入物化到 ``target``;有界流式拷贝。

    同时把 ai-marking-grader skill 以 ``skill.md`` 物化到工作区根目录:
    ACP 模式提示词只做入口,完整批改流程由助手读取该文件获得,
    避免提示词内嵌流程与 SKILL.md 双份漂移(见 SKILL.md「两种模式」)。
    """
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
    # 物化会创建这些保留名(skill.md 为 ACP 流程权威源,scratch 为运行目录),
    # 学生文件同名会在拷贝后才暴露冲突;收集阶段直接拒绝,避免先拷贝再回滚。
    reserved = {"skill.md", "scratch"}
    for original_name, raw_path in records:
        name = Path(original_name).name
        if not name or name in names or name in reserved:
            raise ValueError(f"ACP 工作区文件名冲突: {name or '<empty>'}")
        names.add(name)
        source = _trusted_source(raw_path)
        size = source.stat().st_size
        total += size
        if total > MAX_WORKSPACE_BYTES:
            raise ValueError("ACP 工作区超过 150MB 上限")
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
        skill_src = _skill_source()
        if not skill_src.is_file():
            raise ValueError(f"ACP 技能文件缺失: {skill_src}")
        skill_dest = target / "skill.md"
        trimmed = _trim_skill_for_acp(skill_src.read_text(encoding="utf-8"))
        if not trimmed.strip():
            raise ValueError("ACP 技能文件裁剪后为空，拒绝物化")
        try:
            with skill_dest.open("x", encoding="utf-8") as dst:
                dst.write(trimmed)
        except FileExistsError:
            raise ValueError("学生文件与 ACP skill.md 重名，工作区无法物化") from None
        os.chmod(skill_dest, 0o400)
    except Exception:
        shutil.rmtree(target, ignore_errors=True)
        raise
    return target


def materialize_workspace(run: AcpRun, submission: Submission) -> Path:
    """Copy report, code and declared input files using bounded streaming."""
    target = _materialize_to(submission, workspace_root() / f"run-{run.id}")
    run.workspace_path = str(target)
    return target


def materialize_chat_workspace(chat_id: int, submission: Submission) -> Path:
    """物化对话会话工作区;路径只由 chat ID 派生,不接收外部目录。"""
    return _materialize_to(submission, chat_workspace_root() / f"chat-{chat_id}")


def cleanup_chat_workspace(chat_id: int, *, expected_path: str | None = None) -> bool:
    """永久删除对话时清理其专属工作区。

    仅删除与该 chat ID 精确对应、位于受控 chat-workspace 根目录下的目录:
    resolve 后拒绝符号链接,校验父目录即受控根,且与行内记录的
    ``workspace_path`` 一致(未记录时按派生路径兜底)。任何不满足都跳过删除。
    """
    derived = chat_workspace_root() / f"chat-{chat_id}"
    if expected_path:
        try:
            recorded = Path(expected_path).resolve()
        except OSError:
            return False
        if recorded != derived.resolve():
            return False
    if not derived.exists() or derived.is_symlink():
        return False
    resolved = derived.resolve()
    root = chat_workspace_root()
    if resolved.parent != root or resolved == root:
        return False
    try:
        shutil.rmtree(resolved)
        return True
    except OSError:
        return False


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
