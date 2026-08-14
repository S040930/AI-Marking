"""PDF 上传与临时文件清理测试。"""

import io

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


async def test_pdf_is_streamed_to_final_pdf(tmp_path):
    original, saved = await document_storage.save_document_as_pdf(
        _upload("answer.pdf", b"%PDF-1.4 answer", document_storage.PDF_MIME_TYPE),
        tmp_path,
    )

    assert original == "answer.pdf"
    assert saved.suffix == ".pdf"
    assert saved.read_bytes() == b"%PDF-1.4 answer"
    assert not list(tmp_path.glob(".document-*"))


@pytest.mark.parametrize(
    ("filename", "content", "content_type", "status_code"),
    [
        ("answer.txt", b"text", "text/plain", 422),
        ("answer.docx", b"not-a-zip", document_storage.PDF_MIME_TYPE, 422),
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
