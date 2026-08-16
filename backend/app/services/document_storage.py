"""本机上传文件校验与持久化。所有持久化路径都位于本地 uploads 目录。"""

import ast
import hashlib
import json
import shutil
import tempfile
import unicodedata
import uuid
from pathlib import Path

import aiofiles
from fastapi import HTTPException, UploadFile

PDF_MIME_TYPE = "application/pdf"
MAX_DOCUMENT_SIZE_BYTES = 50 * 1024 * 1024
CODE_EXTENSIONS = {
    ".py", ".ipynb", ".r", ".java", ".c", ".cc", ".cpp", ".cxx",
    ".h", ".hh", ".hpp", ".hxx",
}
ENTRYPOINT_EXTENSIONS = {".py", ".ipynb", ".r", ".java", ".c", ".cc", ".cpp", ".cxx"}
MAX_CODE_FILES = 20
MAX_CODE_TOTAL_BYTES = 100 * 1024 * 1024
MAX_CODE_FILE_BYTES = 20 * 1024 * 1024
_CHUNK_SIZE = 1024 * 1024


def validate_document_upload(upload_file: UploadFile) -> str:
    """校验上传声明并返回 ``pdf``。"""
    filename = upload_file.filename or ""
    suffix = Path(filename).suffix.lower()
    if suffix == ".pdf" and upload_file.content_type == PDF_MIME_TYPE:
        return "pdf"
    raise HTTPException(status_code=422, detail="文件仅支持 PDF 格式")


async def _stream_upload(
    upload_file: UploadFile, destination: Path, max_size_bytes: int
) -> None:
    written = 0
    prefix = bytearray()
    async with aiofiles.open(destination, "wb") as output:
        while chunk := await upload_file.read(_CHUNK_SIZE):
            written += len(chunk)
            if written > max_size_bytes:
                raise HTTPException(
                    status_code=413,
                    detail=f"文件超过最大允许大小 {max_size_bytes} 字节",
                )
            if len(prefix) < 5:
                prefix.extend(chunk[: 5 - len(prefix)])
            await output.write(chunk)
    if written == 0:
        raise HTTPException(status_code=422, detail="上传文件不能为空")
    if bytes(prefix) != b"%PDF-":
        raise HTTPException(status_code=422, detail="文件内容不是有效 PDF")


async def save_document_as_pdf(
    upload_file: UploadFile,
    upload_dir: Path,
    suffix: str = "",
    max_size_bytes: int = MAX_DOCUMENT_SIZE_BYTES,
) -> tuple[str, Path]:
    """流式接收 PDF 并持久化到上传目录。"""
    original_filename = upload_file.filename or ""
    temporary_dir: Path | None = None
    try:
        validate_document_upload(upload_file)
        upload_dir.mkdir(parents=True, exist_ok=True)
        temporary_dir = Path(tempfile.mkdtemp(prefix=".document-", dir=upload_dir))
        stored_stem = f"{uuid.uuid4().hex}{suffix}"
        source = temporary_dir / f"{stored_stem}.pdf"
        await _stream_upload(upload_file, source, max_size_bytes)

        saved_path = upload_dir / f"{stored_stem}.pdf"
        source.replace(saved_path)
        return original_filename, saved_path
    finally:
        await upload_file.close()
        if temporary_dir is not None:
            shutil.rmtree(temporary_dir, ignore_errors=True)


def _safe_code_filename(filename: str | None) -> tuple[str, str]:
    """Return a normalized flat code filename and extension.

    Code uploads intentionally do not preserve directories: a submitted file is
    one member of a per-question source group. Rejecting separators also prevents a
    multipart client from smuggling an arbitrary destination path.
    """
    raw = filename or ""
    if not raw:
        raise HTTPException(status_code=422, detail="代码文件必须是无目录的文件名")
    # NFKC first: homoglyphs like U+2215 (∕) normalize to "/" and must be
    # rejected as separators rather than smuggled into a nested path.
    normalized = unicodedata.normalize("NFKC", raw)
    if Path(normalized).name != normalized or "/" in normalized or "\\" in normalized:
        raise HTTPException(status_code=422, detail="代码文件必须是无目录的文件名")
    suffix = Path(normalized).suffix.lower()
    if suffix not in CODE_EXTENSIONS:
        raise HTTPException(status_code=422, detail="代码文件类型不受支持")
    if not normalized.strip() or normalized.startswith("."):
        raise HTTPException(status_code=422, detail="代码文件名无效")
    return normalized, suffix[1:]


def validate_code_filenames(filenames: list[str]) -> list[str]:
    """Validate count and Unicode/case-insensitive uniqueness."""
    if not filenames:
        raise HTTPException(status_code=422, detail="至少需要一个代码文件")
    if len(filenames) > MAX_CODE_FILES:
        raise HTTPException(
            status_code=413, detail=f"代码文件最多 {MAX_CODE_FILES} 个"
        )
    normalized: list[str] = []
    seen: set[str] = set()
    for filename in filenames:
        safe, _kind = _safe_code_filename(filename)
        key = unicodedata.normalize("NFKC", safe).casefold()
        if key in seen:
            raise HTTPException(status_code=422, detail="代码文件名不能重复")
        seen.add(key)
        normalized.append(safe)
    return normalized


def validate_independent_code_entries(files: list[dict]) -> None:
    """Reject imports between submitted entry points while retaining normal imports.

    Files in the same question may import one another; imports across question
    groups remain rejected because each group is executed independently.
    """
    modules = {Path(item["filename"]).stem: int(item["question_number"]) for item in files}
    by_question: dict[int, list[dict]] = {}
    for item in files:
        by_question.setdefault(int(item["question_number"]), []).append(item)
    for question_number, group in by_question.items():
        entrypoints = [item for item in group if item.get("entrypoint", False)]
        if len(entrypoints) != 1:
            raise HTTPException(
                status_code=422,
                detail=f"第 {question_number} 题必须且只能有一个代码入口文件",
            )
        language = Path(entrypoints[0]["filename"]).suffix.lower()
        if language in {".c", ".cc", ".cpp", ".cxx"} and any(
            Path(item["filename"]).suffix.lower() not in (
                {".c", ".h", ".hh", ".hpp", ".hxx"}
                if language == ".c"
                else {".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx"}
            )
            for item in group
        ):
            raise HTTPException(status_code=422, detail="C/C++ 同题代码文件必须使用同一语言或头文件")
    for item in files:
        source = item.get("source_text", "")
        if item.get("kind") == "ipynb":
            try:
                notebook = json.loads(source)
                source = "\n".join(
                    "".join(cell.get("source", []))
                    if isinstance(cell.get("source"), list)
                    else str(cell.get("source", ""))
                    for cell in notebook.get("cells", [])
                    if isinstance(cell, dict) and cell.get("cell_type") == "code"
                )
            except (json.JSONDecodeError, AttributeError):
                continue
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            imported: list[str] = []
            if isinstance(node, ast.Import):
                imported = [alias.name.split(".", 1)[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported = [node.module.split(".", 1)[0]]
            if any(
                name in modules
                and modules[name] != int(item["question_number"])
                for name in imported
            ):
                raise HTTPException(
                    status_code=422,
                    detail="不同小题代码不能互相 import",
                )


async def save_code_files(
    upload_files: list[UploadFile],
    upload_dir: Path,
    *,
    question_numbers: list[int],
    entrypoints: list[bool] | None = None,
) -> list[dict]:
    """Persist multiple flat Python files and return audit metadata.

    The entire batch is written beneath a UUID directory. Callers must remove
    ``storage_dir`` if the surrounding database transaction fails.
    """
    if len(upload_files) != len(question_numbers):
        raise HTTPException(status_code=422, detail="代码文件与小题映射数量不一致")
    entrypoints = entrypoints or [True] * len(upload_files)
    if len(entrypoints) != len(upload_files):
        raise HTTPException(status_code=422, detail="代码入口标记数量不一致")
    filenames = validate_code_filenames([item.filename or "" for item in upload_files])
    if len(set(question_numbers)) != len(question_numbers) or any(
        number < 1 for number in question_numbers
    ):
        raise HTTPException(status_code=422, detail="小题编号必须为正整数且不能重复")

    storage_dir = upload_dir / "code" / uuid.uuid4().hex
    storage_dir.mkdir(parents=True, exist_ok=True)
    written = 0
    result: list[dict] = []
    try:
        for upload_file, filename, question_number, entrypoint in zip(
            upload_files, filenames, question_numbers, entrypoints, strict=True
        ):
            destination = storage_dir / filename
            size = 0
            chunks: list[bytes] = []
            async for chunk in _read_upload_chunks(upload_file):
                size += len(chunk)
                written += len(chunk)
                if size > MAX_CODE_FILE_BYTES or written > MAX_CODE_TOTAL_BYTES:
                    raise HTTPException(status_code=413, detail="代码文件超过大小限制")
                chunks.append(chunk)
            data = b"".join(chunks)
            if not data:
                raise HTTPException(status_code=422, detail="代码文件不能为空")
            try:
                source_text = unicodedata.normalize(
                    "NFKC", data.decode("utf-8")
                ).replace("\r\n", "\n").replace("\r", "\n")
            except UnicodeDecodeError as exc:
                raise HTTPException(
                    status_code=422, detail=f"{filename} 必须是 UTF-8 文本"
                ) from exc
            if "\x00" in source_text:
                raise HTTPException(status_code=422, detail=f"{filename} 含有非法 NUL 字节")
            if filename.lower().endswith(".ipynb"):
                try:
                    notebook = json.loads(source_text)
                except json.JSONDecodeError as exc:
                    raise HTTPException(status_code=422, detail=f"{filename} 不是合法 Notebook") from exc
                if not isinstance(notebook, dict) or not isinstance(notebook.get("cells"), list):
                    raise HTTPException(status_code=422, detail=f"{filename} 不是合法 Notebook")
            destination.write_bytes(data)
            result.append(
                {
                    "filename": filename,
                    "question_number": question_number,
                    "entrypoint": bool(entrypoint),
                    "kind": Path(filename).suffix.lower()[1:],
                    "path": str(destination),
                    "source_text": source_text,
                    "source_sha256": hashlib.sha256(data).hexdigest(),
                }
            )
        return result
    except Exception:
        shutil.rmtree(storage_dir, ignore_errors=True)
        raise
    finally:
        for upload_file in upload_files:
            await upload_file.close()


async def _read_upload_chunks(upload_file: UploadFile):
    while chunk := await upload_file.read(_CHUNK_SIZE):
        yield chunk
