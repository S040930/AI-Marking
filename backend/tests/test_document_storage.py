"""PDF/DOCX 上传、转换与临时文件清理测试。"""

import io
import zipfile
from pathlib import Path

import pytest
from fastapi import HTTPException, UploadFile
from starlette.datastructures import Headers

from app.services import document_storage


def _upload(filename: str, content: bytes, content_type: str) -> UploadFile:
    return UploadFile(
        file=io.BytesIO(content),
        filename=filename,
        headers=Headers({"content-type": content_type}),
    )


def _docx_bytes(*, include_document: bool = True) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("_rels/.rels", "<Relationships/>")
        if include_document:
            archive.writestr("word/document.xml", "<w:document/>")
    return buffer.getvalue()


async def test_pdf_is_streamed_to_final_pdf(tmp_path):
    original, saved = await document_storage.save_document_as_pdf(
        _upload("answer.pdf", b"%PDF-1.4 answer", document_storage.PDF_MIME_TYPE),
        tmp_path,
    )

    assert original == "answer.pdf"
    assert saved.suffix == ".pdf"
    assert saved.read_bytes() == b"%PDF-1.4 answer"
    assert not list(tmp_path.glob(".document-*"))


async def test_docx_is_validated_converted_and_original_name_kept(
    tmp_path, monkeypatch
):
    async def fake_convert(source: Path, output_dir: Path, profile: Path) -> Path:
        assert source.suffix == ".docx"
        assert profile.parent == output_dir
        converted = output_dir / f"{source.stem}.pdf"
        converted.write_bytes(b"%PDF-1.4 converted")
        return converted

    monkeypatch.setattr(document_storage, "_convert_docx_to_pdf", fake_convert)
    original, saved = await document_storage.save_document_as_pdf(
        _upload("answer.docx", _docx_bytes(), document_storage.DOCX_MIME_TYPE),
        tmp_path,
    )

    assert original == "answer.docx"
    assert saved.suffix == ".pdf"
    assert saved.read_bytes() == b"%PDF-1.4 converted"
    assert list(tmp_path.iterdir()) == [saved]


@pytest.mark.parametrize(
    ("filename", "content", "content_type", "status_code"),
    [
        ("answer.txt", b"text", "text/plain", 422),
        ("fake.docx", b"not-a-zip", document_storage.DOCX_MIME_TYPE, 422),
        (
            "incomplete.docx",
            _docx_bytes(include_document=False),
            document_storage.DOCX_MIME_TYPE,
            422,
        ),
        ("empty.pdf", b"", document_storage.PDF_MIME_TYPE, 422),
    ],
)
async def test_invalid_documents_are_rejected_and_cleaned(
    tmp_path, filename, content, content_type, status_code
):
    with pytest.raises(HTTPException) as caught:
        await document_storage.save_document_as_pdf(
            _upload(filename, content, content_type), tmp_path
        )

    assert caught.value.status_code == status_code
    assert list(tmp_path.iterdir()) == []


async def test_oversized_upload_is_rejected_and_cleaned(tmp_path):
    with pytest.raises(HTTPException) as caught:
        await document_storage.save_document_as_pdf(
            _upload("large.pdf", b"1234", document_storage.PDF_MIME_TYPE),
            tmp_path,
            max_size_bytes=3,
        )

    assert caught.value.status_code == 413
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("status_code", [503, 504, 422])
async def test_conversion_errors_are_preserved_and_cleaned(
    tmp_path, monkeypatch, status_code
):
    async def failed_convert(source: Path, output_dir: Path, profile: Path) -> Path:
        raise HTTPException(status_code=status_code, detail="conversion failed")

    monkeypatch.setattr(document_storage, "_convert_docx_to_pdf", failed_convert)
    with pytest.raises(HTTPException) as caught:
        await document_storage.save_document_as_pdf(
            _upload("answer.docx", _docx_bytes(), document_storage.DOCX_MIME_TYPE),
            tmp_path,
        )

    assert caught.value.status_code == status_code
    assert list(tmp_path.iterdir()) == []


def test_missing_soffice_returns_503(monkeypatch):
    monkeypatch.setattr(document_storage.shutil, "which", lambda _: None)
    monkeypatch.setattr(Path, "is_file", lambda _: False)

    with pytest.raises(HTTPException) as caught:
        document_storage._soffice_executable()

    assert caught.value.status_code == 503
