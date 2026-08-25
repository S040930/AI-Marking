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
    stored = await document_storage.save_document_as_pdf(
        _upload("answer.pdf", b"%PDF-1.4 answer", document_storage.PDF_MIME_TYPE),
        tmp_path,
    )

    assert stored.original_filename == "answer.pdf"
    assert stored.path.suffix == ".pdf"
    assert stored.path.read_bytes() == b"%PDF-1.4 answer"
    assert stored.sha256
    assert stored.created is True
    assert not list(tmp_path.glob(".document-*"))


async def test_identical_pdf_reuses_content_addressed_blob(tmp_path):
    first = await document_storage.save_document_as_pdf(
        _upload("one.pdf", b"%PDF-1.4 same", document_storage.PDF_MIME_TYPE), tmp_path
    )
    second = await document_storage.save_document_as_pdf(
        _upload("two.pdf", b"%PDF-1.4 same", document_storage.PDF_MIME_TYPE), tmp_path
    )

    assert first.path == second.path
    assert first.created is True
    assert second.created is False
    assert len(list((tmp_path / "documents").rglob("*.pdf"))) == 1


async def test_same_filename_with_different_content_keeps_two_blobs(tmp_path):
    first = await document_storage.save_document_as_pdf(
        _upload("answer.pdf", b"%PDF-1.4 one", document_storage.PDF_MIME_TYPE), tmp_path
    )
    second = await document_storage.save_document_as_pdf(
        _upload("answer.pdf", b"%PDF-1.4 two", document_storage.PDF_MIME_TYPE), tmp_path
    )

    assert first.path != second.path
    assert first.path.exists() and second.path.exists()


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


async def test_same_question_accepts_one_entrypoint_and_helper(tmp_path):
    files = await document_storage.save_code_files(
        [
            _upload("main.py", b"import helper\nprint(helper.VALUE)", "text/x-python"),
            _upload("helper.py", b"VALUE = 1\n", "text/x-python"),
        ],
        tmp_path,
        question_numbers=[1, 1],
        entrypoints=[True, False],
    )

    document_storage.validate_independent_code_entries(files)
    assert [item["question_number"] for item in files] == [1, 1]
    assert [item["entrypoint"] for item in files] == [True, False]


def test_discard_created_blob_only_when_unreferenced(db_session, tmp_path):
    from app.models.question import Question, QuestionStatus

    orphan = tmp_path / "orphan.pdf"
    orphan.write_bytes(b"%PDF-orphan")
    stored = document_storage.StoredDocument("orphan.pdf", orphan, "a" * 64, True)
    assert document_storage.discard_stored_document(db_session, stored, tmp_path)
    assert not orphan.exists()

    referenced = tmp_path / "referenced.pdf"
    referenced.write_bytes(b"%PDF-referenced")
    db_session.add(
        Question(
            name="referenced",
            original_filename="referenced.pdf",
            file_path=str(referenced),
            status=QuestionStatus.pending,
        )
    )
    db_session.commit()
    stored = document_storage.StoredDocument(
        "referenced.pdf", referenced, "b" * 64, True
    )
    assert not document_storage.discard_stored_document(
        db_session, stored, tmp_path
    )
    assert referenced.exists()


def test_discard_never_removes_reused_blob(db_session, tmp_path):
    reused = tmp_path / "reused.pdf"
    reused.write_bytes(b"%PDF-reused")
    stored = document_storage.StoredDocument("reused.pdf", reused, "c" * 64, False)

    assert not document_storage.discard_stored_document(db_session, stored, tmp_path)
    assert reused.exists()
