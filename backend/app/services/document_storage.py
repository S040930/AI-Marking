"""上传文档的校验、DOCX 转换与 PDF 持久化。"""

import asyncio
import os
import shutil
import signal
import tempfile
import uuid
import zipfile
from pathlib import Path

import aiofiles
from fastapi import HTTPException, UploadFile

PDF_MIME_TYPE = "application/pdf"
DOCX_MIME_TYPE = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)
MAX_DOCUMENT_SIZE_BYTES = 50 * 1024 * 1024
CONVERSION_TIMEOUT_SECONDS = 120
MAX_DOCX_UNCOMPRESSED_BYTES = 500 * 1024 * 1024
MAX_DOCX_XML_BYTES = 50 * 1024 * 1024
_CHUNK_SIZE = 1024 * 1024


def validate_document_upload(upload_file: UploadFile) -> str:
    """校验上传声明并返回 ``pdf`` 或 ``docx``。"""
    filename = upload_file.filename or ""
    suffix = Path(filename).suffix.lower()
    if suffix == ".pdf" and upload_file.content_type == PDF_MIME_TYPE:
        return "pdf"
    if suffix == ".docx" and upload_file.content_type == DOCX_MIME_TYPE:
        return "docx"
    raise HTTPException(status_code=422, detail="文件仅支持 PDF 或 DOCX 格式")


async def _stream_upload(
    upload_file: UploadFile, destination: Path, max_size_bytes: int
) -> None:
    written = 0
    async with aiofiles.open(destination, "wb") as output:
        while chunk := await upload_file.read(_CHUNK_SIZE):
            written += len(chunk)
            if written > max_size_bytes:
                raise HTTPException(
                    status_code=413,
                    detail=f"文件超过最大允许大小 {max_size_bytes} 字节",
                )
            await output.write(chunk)
    if written == 0:
        raise HTTPException(status_code=422, detail="上传文件不能为空")


def _validate_docx(path: Path) -> None:
    try:
        with zipfile.ZipFile(path) as archive:
            members = set(archive.namelist())
            required = {"[Content_Types].xml", "_rels/.rels", "word/document.xml"}
            if not required.issubset(members):
                raise HTTPException(status_code=422, detail="DOCX 文件结构无效")
            if sum(info.file_size for info in archive.infolist()) > (
                MAX_DOCX_UNCOMPRESSED_BYTES
            ):
                raise HTTPException(status_code=422, detail="DOCX 解压后内容过大")
            if archive.getinfo("word/document.xml").file_size > MAX_DOCX_XML_BYTES:
                raise HTTPException(status_code=422, detail="DOCX 文档结构内容过大")
            # 读取核心部件，确保其 CRC 和压缩数据可正常解析。
            archive.read("[Content_Types].xml")
            archive.read("word/document.xml")
    except (zipfile.BadZipFile, OSError, RuntimeError, KeyError) as exc:
        raise HTTPException(status_code=422, detail="DOCX 文件损坏或结构无效") from exc


def _soffice_executable() -> str:
    executable = shutil.which("soffice")
    if executable:
        return executable
    macos_executable = Path("/Applications/LibreOffice.app/Contents/MacOS/soffice")
    if macos_executable.is_file() and os.access(macos_executable, os.X_OK):
        return str(macos_executable)
    raise HTTPException(
        status_code=503,
        detail="服务器未安装或无法执行 LibreOffice 26.2.4",
    )


async def _terminate_process_group(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    await process.communicate()


async def _convert_docx_to_pdf(source: Path, output_dir: Path, profile: Path) -> Path:
    executable = _soffice_executable()
    try:
        process = await asyncio.create_subprocess_exec(
            executable,
            "--headless",
            "--nologo",
            "--nodefault",
            "--nolockcheck",
            "--nofirststartwizard",
            f"-env:UserInstallation={profile.as_uri()}",
            "--convert-to",
            "pdf",
            "--outdir",
            str(output_dir),
            str(source),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
    except OSError as exc:
        raise HTTPException(
            status_code=503,
            detail="服务器无法启动 LibreOffice 26.2.4",
        ) from exc
    try:
        _stdout, _stderr = await asyncio.wait_for(
            process.communicate(), timeout=CONVERSION_TIMEOUT_SECONDS
        )
    except TimeoutError as exc:
        await _terminate_process_group(process)
        raise HTTPException(status_code=504, detail="DOCX 转换 PDF 超时") from exc
    except asyncio.CancelledError:
        await _terminate_process_group(process)
        raise

    converted = output_dir / f"{source.stem}.pdf"
    if process.returncode != 0 or not converted.is_file():
        raise HTTPException(status_code=422, detail="DOCX 无法转换为 PDF")
    with converted.open("rb") as converted_file:
        header = converted_file.read(5)
    if converted.stat().st_size == 0 or header != b"%PDF-":
        raise HTTPException(status_code=422, detail="DOCX 转换结果不是有效 PDF")
    return converted


async def save_document_as_pdf(
    upload_file: UploadFile,
    upload_dir: Path,
    suffix: str = "",
    max_size_bytes: int = MAX_DOCUMENT_SIZE_BYTES,
) -> tuple[str, Path]:
    """流式接收 PDF/DOCX，并只持久化可供 OCR 使用的 PDF。"""
    original_filename = upload_file.filename or ""
    temporary_dir: Path | None = None
    try:
        kind = validate_document_upload(upload_file)
        upload_dir.mkdir(parents=True, exist_ok=True)
        temporary_dir = Path(tempfile.mkdtemp(prefix=".document-", dir=upload_dir))
        stored_stem = f"{uuid.uuid4().hex}{suffix}"
        source = temporary_dir / f"{stored_stem}.{kind}"
        await _stream_upload(upload_file, source, max_size_bytes)

        if kind == "docx":
            await asyncio.to_thread(_validate_docx, source)
            converted = await _convert_docx_to_pdf(
                source,
                temporary_dir,
                temporary_dir / "libreoffice-profile",
            )
        else:
            converted = source

        saved_path = upload_dir / f"{stored_stem}.pdf"
        converted.replace(saved_path)
        return original_filename, saved_path
    finally:
        await upload_file.close()
        if temporary_dir is not None:
            shutil.rmtree(temporary_dir, ignore_errors=True)
