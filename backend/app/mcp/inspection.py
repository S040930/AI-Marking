"""MCP 客户端提交前的本地文件检查：报告与代码文件的哈希、编码与独立性校验。"""

from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path

from app.mcp.errors import McpApiError
from app.services.code_manifest import auto_question_number
from app.services.document_storage import (
    CODE_EXTENSIONS,
    MAX_CODE_FILE_BYTES,
    MAX_CODE_FILES,
    MAX_CODE_TOTAL_BYTES,
    validate_code_filenames,
)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def require_absolute_file(raw: str, *, label: str) -> Path:
    path = Path(raw).expanduser()
    if not path.is_absolute():
        raise McpApiError(f"{label} 必须是本机绝对路径")
    path = path.resolve()
    if not path.is_file():
        raise McpApiError(f"{label} 必须指向存在的普通文件")
    return path


def inspect_report(raw: str) -> dict:
    path = require_absolute_file(raw, label="report_path")
    if path.suffix.lower() != ".pdf":
        raise McpApiError("report_path 只接受 PDF 文件")
    size = path.stat().st_size
    if size <= 0 or size > 50 * 1024 * 1024:
        raise McpApiError("PDF 大小必须在 1 到 50 MB 之间")
    with path.open("rb") as handle:
        if handle.read(5) != b"%PDF-":
            raise McpApiError("文件内容不是有效 PDF")
    return {"path": str(path), "filename": path.name, "sha256": file_sha256(path)}


def notebook_source(value: str) -> str:
    try:
        notebook = json.loads(value)
    except json.JSONDecodeError as exc:
        raise McpApiError("Notebook 不是合法 JSON") from exc
    if not isinstance(notebook, dict) or not isinstance(notebook.get("cells"), list):
        raise McpApiError("Notebook 不是合法 Notebook")
    pieces: list[str] = []
    for cell in notebook["cells"]:
        if not isinstance(cell, dict) or cell.get("cell_type") != "code":
            continue
        source = cell.get("source", "")
        pieces.append("".join(source) if isinstance(source, list) else str(source))
    return "\n".join(pieces)


def reject_cross_file_imports(files: list[dict]) -> None:
    modules = {
        Path(item["filename"]).stem: number
        for item in files
        if (number := auto_question_number(item["filename"])) is not None
    }
    for item in files:
        source = item["source"]
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue  # Syntax errors are reported in the submitted source context.
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name.split(".", 1)[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module.split(".", 1)[0]]
            current_match = auto_question_number(item["filename"])
            if current_match is not None and any(
                name in modules and modules[name] != current_match for name in names
            ):
                raise McpApiError("每题代码必须独立，不同小题不能互相 import")


def inspect_code_files(paths: list[str]) -> list[dict]:
    if len(paths) > MAX_CODE_FILES:
        raise McpApiError(f"代码文件最多 {MAX_CODE_FILES} 个")
    inspected: list[dict] = []
    total_size = 0
    names: list[str] = []
    for raw in paths:
        path = require_absolute_file(raw, label="code_file_paths")
        if path.suffix.lower() not in CODE_EXTENSIONS:
            raise McpApiError("代码文件类型不受支持")
        size = path.stat().st_size
        total_size += size
        if size <= 0 or size > MAX_CODE_FILE_BYTES or total_size > MAX_CODE_TOTAL_BYTES:
            raise McpApiError("代码文件超过大小限制")
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise McpApiError(f"代码文件 {path.name} 必须是 UTF-8 文本") from exc
        if "\x00" in text:
            raise McpApiError(f"代码文件 {path.name} 含有非法 NUL 字节")
        source = notebook_source(text) if path.suffix.lower() == ".ipynb" else text
        names.append(path.name)
        inspected.append(
            {"path": str(path), "filename": path.name, "sha256": file_sha256(path), "source": source}
        )
    try:
        validate_code_filenames(names)
    except Exception as exc:  # FastAPI validation is converted to MCP text.
        raise McpApiError(str(getattr(exc, "detail", exc))) from exc
    reject_cross_file_imports(inspected)
    return inspected
