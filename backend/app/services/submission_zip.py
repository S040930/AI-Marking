"""学生 ZIP 作业的解包、安全校验与自动分类。

教师上传一个 ZIP(报告 PDF + 代码文件 + 运行数据集)。本模块只做三件事:
1. 流式落盘 ZIP 本体并拒绝超限/伪 ZIP;
2. 按成员名分类:恰好一份 PDF 报告、``q<n>.<ext>`` 代码、其余为数据集;
3. 把各成员写入受控 uploads 目录,返回供 ``commit_submission_create`` 落库的
   元数据,格式与 ``save_code_files``/``save_code_input_files`` 一致。

前端 ``zipPreview.ts`` 镜像同一套分类规则,便于上传前预览;此处是权威校验。

失败次序约定:代码/数据集目录先落盘、报告 blob 最后持久化,因此本函数内部
失败只需清掉自建目录;已持久化 blob 的引用校验删除仍由 API 层的
``discard_uncommitted_document`` 负责(与直传 PDF 分支一致)。
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import stat
import tempfile
import unicodedata
import uuid
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from app.core.errors import PayloadTooLargeError, ValidationError
from app.services.document_storage import (
    CODE_EXTENSIONS,
    MAX_CODE_FILE_BYTES,
    MAX_CODE_TOTAL_BYTES,
    StoredDocument,
    persist_pdf_blob,
    validate_independent_code_entries,
)

MAX_ZIP_SIZE_BYTES = 150 * 1024 * 1024
MAX_ZIP_MEMBERS = 200
MAX_ZIP_UNCOMPRESSED_BYTES = 150 * 1024 * 1024
MAX_REPORT_PDF_BYTES = 50 * 1024 * 1024
MAX_CODE_INPUT_FILES = 5
MAX_CODE_INPUT_TOTAL_BYTES = 100 * 1024 * 1024
MAX_CODE_INPUT_FILE_BYTES = 50 * 1024 * 1024

# 数据集是题目声明的普通输入文件;源码/可执行/归档一律拒绝,防止输入
# 通道变身程序包或再解一层嵌套归档。
FORBIDDEN_INPUT_EXTENSIONS = CODE_EXTENSIONS | {
    ".app", ".bin", ".com", ".dll", ".dylib", ".exe", ".jar", ".o", ".so",
    ".sh", ".bash", ".zsh", ".bat", ".cmd", ".ps1", ".pl", ".rb", ".go",
    ".rs", ".swift", ".kt", ".m", ".mm",
    ".zip", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".rar", ".7z",
}

_ZIP_MAGIC = b"PK\x03\x04"
_PDF_MAGIC = b"%PDF-"
_CHUNK_SIZE = 1024 * 1024

# ZIP 分支的代码命名:文件名必须以小题号数字结尾(严格取尾部数字组,
# CW1_3.py → 3、q1_v2.py → 2、task3.py → 3;main.py 无尾部数字 → 拒绝)。
# 不能放宽为"文件名内第一个数字组"——CW1_3/q1_v2 会错误映射到 1,
# 评分证据随之张冠李戴。
_TRAILING_NUMBER_PATTERN = re.compile(r"(\d+)$")

# 常见系统垃圾文件,静默跳过;其余点开头文件名直接拒绝。
_IGNORED_BASENAMES = {".DS_Store", "Thumbs.db", "desktop.ini", "__MACOSX"}


@dataclass(slots=True)
class ZipPackage:
    """解包结果:报告 blob、代码元数据、数据集元数据与本请求自建目录。

    ``code_metadata`` 字段与 ``save_code_files`` 输出对齐(question_number/
    entrypoint/kind/path/source_text/source_sha256);``input_metadata`` 对齐
    数据集落库所需(filename/path/size/sha256)。``storage_dirs`` 列出本请求
    创建的代码/数据集 UUID 目录,提交失败时由调用方整目录删除。
    """

    stored: StoredDocument
    code_metadata: list[dict] = field(default_factory=list)
    input_metadata: list[dict] = field(default_factory=list)
    storage_dirs: list[Path] = field(default_factory=list)


def extract_and_classify_zip(upload_file, upload_dir: Path) -> ZipPackage:
    """同步入口(调用方放线程池):落盘 ZIP → 校验 → 分类落盘。

    ``upload_file`` 只用其同步 ``.file`` 句柄;部分写盘后任何失败都会清空
    本函数创建的存储目录与临时文件。
    """
    temporary_dir = Path(tempfile.mkdtemp(prefix=".zip-", dir=upload_dir))
    package = ZipPackage(
        stored=StoredDocument(
            original_filename="", path=Path(), sha256="", created=False
        )
    )
    try:
        raw_zip = temporary_dir / f"{uuid.uuid4().hex}.zip"
        _stream_zip(upload_file, raw_zip)

        members = _safe_members(raw_zip)
        grouped = _classify(members)
        _validate_payload_sizes(grouped)

        code_dir = upload_dir / "code" / uuid.uuid4().hex
        code_dir.mkdir(parents=True, mode=0o700)
        package.storage_dirs.append(code_dir)
        package.code_metadata = _extract_code_members(
            raw_zip, grouped["code"], code_dir
        )
        # 跨小题 import 校验在数据集/报告落盘前完成,失败只损失代码目录。
        validate_independent_code_entries(package.code_metadata)

        if grouped["inputs"]:
            inputs_dir = upload_dir / "code-inputs" / uuid.uuid4().hex
            inputs_dir.mkdir(parents=True, mode=0o700)
            package.storage_dirs.append(inputs_dir)
            package.input_metadata = _extract_input_members(
                raw_zip, grouped["inputs"], inputs_dir
            )

        # 报告 blob 最后持久化:此后不再有可失败步骤,无需 blob 回滚。
        report_name, report_info = grouped["report"]
        package.stored = _extract_report(raw_zip, report_info, report_name, upload_dir)
        return package
    except BaseException:
        for directory in package.storage_dirs:
            shutil.rmtree(directory, ignore_errors=True)
        raise
    finally:
        shutil.rmtree(temporary_dir, ignore_errors=True)


def _stream_zip(upload_file, destination: Path) -> None:
    """流式接收 ZIP 本体并校验魔数与大小上限。"""
    written = 0
    prefix = bytearray()
    with destination.open("wb") as output:
        while chunk := upload_file.file.read(_CHUNK_SIZE):
            written += len(chunk)
            if written > MAX_ZIP_SIZE_BYTES:
                raise PayloadTooLargeError(
                    f"ZIP 超过最大允许大小 {MAX_ZIP_SIZE_BYTES} 字节"
                )
            if len(prefix) < 4:
                prefix.extend(chunk[: 4 - len(prefix)])
            output.write(chunk)
    if written == 0:
        raise ValidationError("上传文件不能为空")
    if bytes(prefix) != _ZIP_MAGIC:
        raise ValidationError("文件内容不是有效 ZIP")


def _safe_members(raw_zip: Path) -> list[tuple[str, int, zipfile.ZipInfo]]:
    """枚举成员并完成名称/链接/数量/总量校验,返回 (扁平名, 声明大小, info)。"""
    members: list[tuple[str, int, zipfile.ZipInfo]] = []
    seen: set[str] = set()
    total = 0
    with zipfile.ZipFile(raw_zip) as archive:
        infos = archive.infolist()
        if len(infos) > MAX_ZIP_MEMBERS:
            raise PayloadTooLargeError(f"ZIP 成员最多 {MAX_ZIP_MEMBERS} 个")
        for info in infos:
            basename = _member_basename(info.filename)
            if basename is None:
                continue
            mode = (info.external_attr >> 16) & 0o170000
            if mode == stat.S_IFLNK:
                raise ValidationError("ZIP 不能包含符号链接:" + info.filename)
            key = basename.casefold()
            if key in seen:
                raise ValidationError(f"ZIP 内文件名扁平化后重复:{basename}")
            seen.add(key)
            total += info.file_size
            if total > MAX_ZIP_UNCOMPRESSED_BYTES:
                raise PayloadTooLargeError("ZIP 解压后总大小超过 150MB 限制")
            members.append((basename, info.file_size, info))
    return members


def _member_basename(raw: str) -> str | None:
    """返回可安全使用的扁平文件名;垃圾/目录成员返回 None。

    - NFKC 归一化(同形字符如 U+2215 归一化为 "/" 后被分隔符规则识破);
    - 拒绝绝对路径与 ``..`` 段;
    - 只保留 basename(扁平化),与代码/数据集的扁平存储约定一致。
    """
    name = unicodedata.normalize("NFKC", raw).replace("\\", "/")
    if name.endswith("/"):
        # 归档工具(如 macOS Archive Utility)会为打包的文件夹写入
        # 显式目录条目;它们不是文件,直接跳过。
        return None
    segments = [segment for segment in name.split("/") if segment not in ("", ".")]
    if not segments:
        return None
    if name.startswith("/") or (len(name) > 1 and name[1] == ":"):
        raise ValidationError("ZIP 成员不能是绝对路径:" + raw)
    if any(segment == ".." for segment in segments):
        raise ValidationError("ZIP 成员存在路径穿越:" + raw)
    if "MACOSX" in segments[0] or segments[0].startswith("__"):
        return None
    basename = segments[-1]
    if basename in _IGNORED_BASENAMES:
        return None
    if basename.startswith("._"):
        return None
    if basename.startswith("."):
        raise ValidationError("ZIP 成员不能是隐藏文件:" + raw)
    return basename


def _zip_question_number(basename: str) -> int | None:
    """从文件名尾部数字推导小题号:CW1_3.py → 3,q1_v2.py → 2,main.py → None。"""
    stem = Path(basename).stem
    match = _TRAILING_NUMBER_PATTERN.search(stem)
    if match is None:
        return None
    number = int(match.group(1))
    return number if number >= 1 else None


def _classify(
    members: list[tuple[str, int, zipfile.ZipInfo]],
) -> dict[str, object]:
    """按扁平文件名把成员分为 report/code/input 三组。"""
    reports: list[tuple[str, zipfile.ZipInfo]] = []
    code: list[tuple[str, int, zipfile.ZipInfo]] = []
    inputs: list[tuple[str, int, zipfile.ZipInfo]] = []
    for basename, size, info in members:
        suffix = Path(basename).suffix.lower()
        if suffix == ".pdf":
            reports.append((basename, info))
        elif suffix in CODE_EXTENSIONS:
            number = _zip_question_number(basename)
            if number is None:
                raise ValidationError(
                    f"代码文件 {basename} 的文件名需以小题号数字结尾"
                    "(如 task3.py / q3.py / CW1_3.py);"
                    "复杂结构请改用报告 PDF + 散装代码上传"
                )
            code.append((basename, size, info))
        else:
            inputs.append((basename, size, info))
    if not reports:
        raise ValidationError("ZIP 中缺少报告 PDF")
    if len(reports) > 1:
        raise ValidationError("ZIP 中只能包含一份报告 PDF")
    return {"report": reports[0], "code": code, "inputs": inputs}


def _validate_payload_sizes(grouped: dict[str, object]) -> None:
    code: list[tuple[str, int, zipfile.ZipInfo]] = grouped["code"]  # type: ignore[assignment]
    inputs: list[tuple[str, int, zipfile.ZipInfo]] = grouped["inputs"]  # type: ignore[assignment]
    if len(code) > 20:
        raise PayloadTooLargeError("代码文件最多 20 个")
    if sum(size for _, size, _ in code) > MAX_CODE_TOTAL_BYTES:
        raise PayloadTooLargeError("代码文件累计超过 100MB 限制")
    if len(inputs) > MAX_CODE_INPUT_FILES:
        raise PayloadTooLargeError(f"数据集文件最多 {MAX_CODE_INPUT_FILES} 个")
    if sum(size for _, size, _ in inputs) > MAX_CODE_INPUT_TOTAL_BYTES:
        raise PayloadTooLargeError("数据集文件累计超过 100MB 限制")


def _read_member(
    archive: zipfile.ZipFile, info: zipfile.ZipInfo, max_bytes: int
) -> bytes:
    """有界读取成员内容:实际字节数必须与声明一致,防止声明造假。"""
    data = bytearray()
    with archive.open(info) as member:
        while chunk := member.read(_CHUNK_SIZE):
            data.extend(chunk)
            if len(data) > max_bytes:
                raise PayloadTooLargeError(
                    f"ZIP 成员 {info.filename} 超过大小限制"
                )
    return bytes(data)


def _extract_code_members(
    raw_zip: Path,
    code: list[tuple[str, int, zipfile.ZipInfo]],
    code_dir: Path,
) -> list[dict]:
    result: list[dict] = []
    with zipfile.ZipFile(raw_zip) as archive:
        for basename, size, info in code:
            if size > MAX_CODE_FILE_BYTES:
                raise PayloadTooLargeError("代码文件超过大小限制")
            data = _read_member(archive, info, MAX_CODE_FILE_BYTES)
            source_text = _validate_code_payload(basename, data)
            destination = code_dir / basename
            destination.write_bytes(data)
            result.append(
                {
                    "filename": basename,
                    "question_number": _zip_question_number(basename),
                    "entrypoint": True,
                    "kind": Path(basename).suffix.lower()[1:],
                    "path": str(destination),
                    "source_text": source_text,
                    "source_sha256": hashlib.sha256(data).hexdigest(),
                }
            )
    return result


def _extract_input_members(
    raw_zip: Path,
    inputs: list[tuple[str, int, zipfile.ZipInfo]],
    inputs_dir: Path,
) -> list[dict]:
    result: list[dict] = []
    with zipfile.ZipFile(raw_zip) as archive:
        for basename, size, info in inputs:
            if size > MAX_CODE_INPUT_FILE_BYTES:
                raise PayloadTooLargeError("数据集文件超过大小限制")
            _validate_input_name(basename)
            data = _read_member(archive, info, MAX_CODE_INPUT_FILE_BYTES)
            _validate_input_payload(basename, data)
            destination = inputs_dir / basename
            destination.write_bytes(data)
            destination.chmod(0o400)
            result.append(
                {
                    "filename": basename,
                    "path": str(destination),
                    "size": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                }
            )
    return result


def _extract_report(
    raw_zip: Path,
    info: zipfile.ZipInfo,
    name: str,
    upload_dir: Path,
) -> StoredDocument:
    temporary_dir = Path(tempfile.mkdtemp(prefix=".zip-report-", dir=upload_dir))
    try:
        with zipfile.ZipFile(raw_zip) as archive:
            data = _read_member(archive, info, MAX_REPORT_PDF_BYTES)
        if data[:5] != _PDF_MAGIC:
            raise ValidationError("ZIP 内的报告不是有效 PDF")
        source = temporary_dir / f"{uuid.uuid4().hex}.pdf"
        source.write_bytes(data)
        return persist_pdf_blob(
            source, original_filename=name, upload_dir=upload_dir
        )
    finally:
        shutil.rmtree(temporary_dir, ignore_errors=True)


def _validate_input_name(basename: str) -> None:
    suffix = Path(basename).suffix.lower()
    if suffix in FORBIDDEN_INPUT_EXTENSIONS:
        raise ValidationError("数据集不能是源码、可执行或归档文件:" + basename)


def _validate_code_payload(basename: str, data: bytes) -> str:
    """与 save_code_files 一致的代码体校验;返回归一化 source_text。"""
    if not data:
        raise ValidationError("代码文件不能为空:" + basename)
    try:
        source_text = (
            unicodedata.normalize("NFKC", data.decode("utf-8"))
            .replace("\r\n", "\n")
            .replace("\r", "\n")
        )
    except UnicodeDecodeError as exc:
        raise ValidationError(f"{basename} 必须是 UTF-8 文本") from exc
    if "\x00" in source_text:
        raise ValidationError(f"{basename} 含有非法 NUL 字节")
    if basename.lower().endswith(".ipynb"):
        try:
            notebook = json.loads(source_text)
        except json.JSONDecodeError as exc:
            raise ValidationError(f"{basename} 不是合法 Notebook") from exc
        if not isinstance(notebook, dict) or not isinstance(notebook.get("cells"), list):
            raise ValidationError(f"{basename} 不是合法 Notebook")
    return source_text


def _validate_input_payload(basename: str, data: bytes) -> None:
    if not data:
        raise ValidationError("数据集文件不能为空:" + basename)
    if data.startswith(b"#!") or data[:4] in {
        b"\x7fELF",
        b"\xca\xfe\xba\xbe",
        b"\xfe\xed\xfa\xce",
        b"\xce\xfa\xed\xfe",
    }:
        raise ValidationError(f"数据集 {basename} 不能是可执行文件")
