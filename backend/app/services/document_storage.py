"""本机上传文件校验与持久化。所有持久化路径都位于本地 uploads 目录。"""

import ast
import hashlib
import json
import logging
import os
import shutil
import tempfile
import unicodedata
import uuid
from dataclasses import dataclass
from pathlib import Path

import aiofiles
from fastapi import UploadFile
from sqlalchemy import or_, select
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import settings
from app.core.errors import PayloadTooLargeError, ValidationError

logger = logging.getLogger(__name__)

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


@dataclass(frozen=True, slots=True)
class StoredDocument:
    """结果 of a PDF write, including whether this request created the blob."""

    original_filename: str
    path: Path
    sha256: str
    created: bool


def validate_document_upload(upload_file: UploadFile) -> str:
    """校验上传声明并返回 ``pdf``。"""
    filename = upload_file.filename or ""
    suffix = Path(filename).suffix.lower()
    if suffix == ".pdf" and upload_file.content_type == PDF_MIME_TYPE:
        return "pdf"
    raise ValidationError("文件仅支持 PDF 格式")


async def _stream_upload(
    upload_file: UploadFile, destination: Path, max_size_bytes: int
) -> tuple[int, str]:
    written = 0
    prefix = bytearray()
    digest = hashlib.sha256()
    async with aiofiles.open(destination, "wb") as output:
        while chunk := await upload_file.read(_CHUNK_SIZE):
            written += len(chunk)
            if written > max_size_bytes:
                raise PayloadTooLargeError(
                    f"文件超过最大允许大小 {max_size_bytes} 字节"
                )
            if len(prefix) < 5:
                prefix.extend(chunk[: 5 - len(prefix)])
            digest.update(chunk)
            await output.write(chunk)
    if written == 0:
        raise ValidationError("上传文件不能为空")
    if bytes(prefix) != b"%PDF-":
        raise ValidationError("文件内容不是有效 PDF")
    return written, digest.hexdigest()


def persist_pdf_blob(
    source: Path,
    *,
    original_filename: str,
    upload_dir: Path,
) -> StoredDocument:
    """把已通过 ``%PDF-`` 魔数校验的本地文件持久化为内容寻址 blob。

    直传 PDF 与 ZIP 内报告成员共用;失败时调用方负责清理 ``source``。
    """
    size = source.stat().st_size
    digest = _streaming_sha256(source)
    blob_dir = upload_dir / "documents" / digest[:2]
    saved_path = blob_dir / f"{digest}.pdf"
    blob_dir.mkdir(parents=True, exist_ok=True)
    if saved_path.exists():
        if not _same_file(saved_path, source, size):
            raise RuntimeError(f"内容哈希冲突，拒绝覆盖已有文件：{saved_path}")
        source.unlink(missing_ok=True)
        # A request may be about to create the first durable reference to an
        # old orphaned blob. Refresh its age so concurrent retention cleanup
        # cannot remove it before the surrounding transaction commits.
        saved_path.touch()
        created = False
    else:
        try:
            # Hard-link creation is atomic and never overwrites a blob
            # another concurrent uploader may have created.
            os.link(source, saved_path)
            source.unlink(missing_ok=True)
            created = True
        except FileExistsError:
            if not _same_file(saved_path, source, size):
                raise RuntimeError(f"内容哈希冲突，拒绝覆盖已有文件：{saved_path}")
            source.unlink(missing_ok=True)
            saved_path.touch()
            created = False
    return StoredDocument(original_filename, saved_path, digest, created)


def _streaming_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def _same_file(left: Path, right: Path, size: int) -> bool:
    """Compare an existing blob without loading either file into memory."""
    if left.stat().st_size != size:
        return False
    with left.open("rb") as first, right.open("rb") as second:
        while True:
            left_chunk = first.read(_CHUNK_SIZE)
            right_chunk = second.read(_CHUNK_SIZE)
            if left_chunk != right_chunk:
                return False
            if not left_chunk:
                return True


def remove_document_if_unreferenced(db, file_path: str, upload_dir: Path) -> bool:
    """Delete a PDF only after checking every question/submission reference."""
    from app.models.question import Question
    from app.models.submission import Submission

    candidate = Path(file_path).resolve()
    root = upload_dir.resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        raise ValueError(f"拒绝删除 uploads 目录外的文件：{file_path}")
    path_values = {file_path, str(candidate)}
    try:
        path_values.add(str(candidate.relative_to(Path.cwd().resolve())))
    except ValueError:
        pass
    question_ref = db.scalar(
        select(Question.id)
        .where(
            or_(
                Question.file_path.in_(path_values),
                Question.replacement_file_path.in_(path_values),
            )
        )
        .limit(1)
    )
    submission_ref = db.scalar(
        select(Submission.id)
        .where(Submission.file_path.in_(path_values))
        .limit(1)
    )
    if question_ref is not None or submission_ref is not None:
        return False
    candidate.unlink(missing_ok=True)
    return True


def discard_stored_document(db, stored: StoredDocument, upload_dir: Path) -> bool:
    """Discard a request-created blob when its database transaction did not commit.

    Reused content-addressed blobs are never removed here. Newly created blobs are
    still checked against every durable question/submission reference before unlinking.
    """
    if not stored.created:
        return False
    return remove_document_if_unreferenced(db, str(stored.path), upload_dir)


def resolve_upload_dir() -> Path:
    """解析并校验 uploads 目录(必须位于后端根目录下,防止任意目录写入)。

    各上传端点共用;目录不存在时自动创建。
    """
    backend_root = Path(__file__).resolve().parent.parent.parent
    upload_dir = Path(settings.UPLOAD_DIR).resolve()
    try:
        upload_dir.relative_to(backend_root)
    except ValueError as exc:
        raise ValueError("UPLOAD_DIR 配置非法,必须位于后端根目录下") from exc
    upload_dir.mkdir(parents=True, exist_ok=True)
    return upload_dir


def discard_uncommitted_document(db, stored: StoredDocument, upload_dir: Path) -> None:
    """尽力清理未提交事务创建的文档;失败仅记录日志,不阻断原异常传播。"""
    try:
        discard_stored_document(db, stored, upload_dir)
    except (OSError, SQLAlchemyError, ValueError) as exc:
        logger.warning("清理未提交文档失败 [%s]: %s", stored.path, exc)


async def save_document_as_pdf(
    upload_file: UploadFile,
    upload_dir: Path,
    suffix: str = "",
    max_size_bytes: int = MAX_DOCUMENT_SIZE_BYTES,
) -> StoredDocument:
    """流式接收 PDF 并按内容哈希持久化到共享 blob 目录。"""
    original_filename = upload_file.filename or ""
    temporary_dir: Path | None = None
    try:
        validate_document_upload(upload_file)
        upload_dir.mkdir(parents=True, exist_ok=True)
        temporary_dir = Path(tempfile.mkdtemp(prefix=".document-", dir=upload_dir))
        stored_stem = uuid.uuid4().hex
        source = temporary_dir / f"{stored_stem}.pdf"
        await _stream_upload(upload_file, source, max_size_bytes)
        stored = persist_pdf_blob(
            source, original_filename=original_filename, upload_dir=upload_dir
        )
        return stored
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
        raise ValidationError("代码文件必须是无目录的文件名")
    # NFKC first: homoglyphs like U+2215 (∕) normalize to "/" and must be
    # rejected as separators rather than smuggled into a nested path.
    normalized = unicodedata.normalize("NFKC", raw)
    if Path(normalized).name != normalized or "/" in normalized or "\\" in normalized:
        raise ValidationError("代码文件必须是无目录的文件名")
    suffix = Path(normalized).suffix.lower()
    if suffix not in CODE_EXTENSIONS:
        raise ValidationError("代码文件类型不受支持")
    if not normalized.strip() or normalized.startswith("."):
        raise ValidationError("代码文件名无效")
    return normalized, suffix[1:]


def validate_code_filenames(filenames: list[str]) -> list[str]:
    """Validate count and Unicode/case-insensitive uniqueness."""
    if not filenames:
        raise ValidationError("至少需要一个代码文件")
    if len(filenames) > MAX_CODE_FILES:
        raise PayloadTooLargeError(f"代码文件最多 {MAX_CODE_FILES} 个")
    normalized: list[str] = []
    seen: set[str] = set()
    for filename in filenames:
        safe, _kind = _safe_code_filename(filename)
        key = unicodedata.normalize("NFKC", safe).casefold()
        if key in seen:
            raise ValidationError("代码文件名不能重复")
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
            raise ValidationError(
                f"第 {question_number} 题必须且只能有一个代码入口文件"
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
            raise ValidationError("C/C++ 同题代码文件必须使用同一语言或头文件")
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
                raise ValidationError("不同小题代码不能互相 import")


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
        raise ValidationError("代码文件与小题映射数量不一致")
    entrypoints = entrypoints or [True] * len(upload_files)
    if len(entrypoints) != len(upload_files):
        raise ValidationError("代码入口标记数量不一致")
    filenames = validate_code_filenames([item.filename or "" for item in upload_files])
    if any(number < 1 for number in question_numbers):
        raise ValidationError("小题编号必须为正整数")

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
                    raise PayloadTooLargeError("代码文件超过大小限制")
                chunks.append(chunk)
            data = b"".join(chunks)
            if not data:
                raise ValidationError("代码文件不能为空")
            try:
                source_text = unicodedata.normalize(
                    "NFKC", data.decode("utf-8")
                ).replace("\r\n", "\n").replace("\r", "\n")
            except UnicodeDecodeError as exc:
                raise ValidationError(f"{filename} 必须是 UTF-8 文本") from exc
            if "\x00" in source_text:
                raise ValidationError(f"{filename} 含有非法 NUL 字节")
            if filename.lower().endswith(".ipynb"):
                try:
                    notebook = json.loads(source_text)
                except json.JSONDecodeError as exc:
                    raise ValidationError(f"{filename} 不是合法 Notebook") from exc
                if not isinstance(notebook, dict) or not isinstance(notebook.get("cells"), list):
                    raise ValidationError(f"{filename} 不是合法 Notebook")
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
