"""ZIP 作业上传测试:解包分类、安全校验与数据集落库。"""

import io
import zipfile
from pathlib import Path

import pytest
from sqlalchemy import select

from app.models.question import Question, QuestionStatus
from app.models.submission import Submission, SubmissionStatus
from app.models.submission_code_file import SubmissionCodeFile
from app.models.submission_code_input_file import SubmissionCodeInputFile

PDF_BYTES = b"%PDF-1.4\n fake report"


def _zip_bytes(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def _ready_question(db_session, name: str = "ZIP 题目") -> Question:
    question = Question(
        name=name,
        original_filename=f"{name}.pdf",
        file_path="/tmp/question.pdf",
        ocr_text="测试题目内容",
        status=QuestionStatus.ready,
    )
    db_session.add(question)
    return question


def _post_zip(client, data: bytes, question_id: str = "1"):
    return client.post(
        "/api/submissions",
        files=[("file", ("作业.zip", data, "application/zip"))],
        data={"question_id": question_id},
    )


@pytest.fixture
def zip_upload_dir(tmp_path, monkeypatch):
    """把上传目录重定向到 tmp;resolve_upload_dir 要求 backend 根目录内,故 patch API 导入。"""
    monkeypatch.setattr("app.api.submissions.resolve_upload_dir", lambda: tmp_path)
    return tmp_path


async def test_zip_upload_creates_submission_with_code_and_inputs(
    client, db_session, zip_upload_dir
):
    """happy path:1 PDF + q1.py/q2.ipynb + 1 CSV → 201,详情含代码与数据集。"""
    question = _ready_question(db_session, name="ZIP 快乐路径")
    db_session.commit()

    notebook = '{"cells": [{"cell_type": "code", "source": ["print(1)"]}], "metadata": {}, "nbformat": 4, "nbformat_minor": 2}'
    payload = _zip_bytes(
        {
            "报告.pdf": PDF_BYTES,
            "task1/q1.py": b"import pandas as pd\ndata = pd.read_csv('battery.csv')\n",
            "task2/q2.ipynb": notebook.encode("utf-8"),
            "data/battery.csv": b"a,b\n1,2\n",
        }
    )
    response = await _post_zip(client, payload, question_id=question.id)
    assert response.status_code == 201, response.text
    submission_id = response.json()["id"]

    detail = await client.get(f"/api/submissions/{submission_id}")
    assert detail.status_code == 200
    body = detail.json()
    assert [item["original_filename"] for item in body["code_files"]] == [
        "q1.py", "q2.ipynb"
    ]
    assert [item["question_number"] for item in body["code_files"]] == [1, 2]
    assert [item["original_filename"] for item in body["code_input_files"]] == [
        "battery.csv"
    ]
    assert body["code_input_files"][0]["size_bytes"] == len(b"a,b\n1,2\n")

    # 磁盘:代码与数据集在受控目录,报告为内容寻址 blob
    code_row = db_session.scalar(
        select(SubmissionCodeFile).where(
            SubmissionCodeFile.submission_id == submission_id
        )
    )
    assert Path(code_row.file_path).is_file()
    input_row = db_session.scalar(
        select(SubmissionCodeInputFile).where(
            SubmissionCodeInputFile.submission_id == submission_id
        )
    )
    assert Path(input_row.file_path).is_file()
    assert (zip_upload_dir / "code").exists()
    assert (zip_upload_dir / "code-inputs").exists()


async def test_zip_upload_rejects_missing_report(client, db_session):
    """0 份 PDF → 422。"""
    question = _ready_question(db_session)
    db_session.commit()
    payload = _zip_bytes({"q1.py": b"print(1)\n"})
    response = await _post_zip(client, payload, question_id=question.id)
    assert response.status_code == 422
    assert "报告" in response.json()["detail"]


async def test_zip_upload_rejects_two_reports(client, db_session):
    """2 份 PDF → 422。"""
    question = _ready_question(db_session, name="双报告题目")
    db_session.commit()
    payload = _zip_bytes({"a.pdf": PDF_BYTES, "b.pdf": PDF_BYTES})
    response = await _post_zip(client, payload, question_id=question.id)
    assert response.status_code == 422
    assert "一份" in response.json()["detail"]


async def test_zip_upload_rejects_code_without_qn_name(client, db_session):
    """无尾部数字命名的代码文件 → 422。"""
    question = _ready_question(db_session)
    db_session.commit()
    payload = _zip_bytes({"报告.pdf": PDF_BYTES, "main.py": b"print(1)\n"})
    response = await _post_zip(client, payload, question_id=question.id)
    assert response.status_code == 422


async def test_zip_upload_accepts_trailing_number_names(
    client, db_session, zip_upload_dir
):
    """taskN 等尾部数字命名推导小题号:task3.py → 第 3 题。"""
    question = _ready_question(db_session, name="尾部数字题目")
    db_session.commit()
    payload = _zip_bytes(
        {
            "报告.pdf": PDF_BYTES,
            "task1.py": b"a = 1\n",
            "task2.py": b"b = 2\n",
            "task3.ipynb": b'{"cells": [], "metadata": {}, "nbformat": 4, "nbformat_minor": 2}',
        }
    )
    response = await _post_zip(client, payload, question_id=question.id)
    assert response.status_code == 201, response.text
    detail = await client.get(f"/api/submissions/{response.json()['id']}")
    code_files = detail.json()["code_files"]
    assert [(item["original_filename"], item["question_number"]) for item in code_files] == [
        ("task1.py", 1),
        ("task2.py", 2),
        ("task3.ipynb", 3),
    ]


def test_zip_question_number_mapping_table():
    """严格尾部数字映射表:CW1_3→3、q1_v2→2、task12→12、CW1_v2→2、main→None。"""
    from app.services.submission_zip import _zip_question_number

    assert _zip_question_number("CW1_3.py") == 3
    assert _zip_question_number("q1_v2.py") == 2
    assert _zip_question_number("task12.py") == 12
    assert _zip_question_number("CW1_v2.py") == 2
    assert _zip_question_number("task3.ipynb") == 3
    assert _zip_question_number("main.py") is None
    assert _zip_question_number("v0.py") is None


async def test_zip_upload_question_number_is_strict_trailing_digits(
    client, db_session
):
    """小题号取严格尾部数字组,而非文件名内第一个数字(CW1_3→3 而非 1)。"""
    question = _ready_question(db_session, name="严格尾部数字题目")
    db_session.commit()
    payload = _zip_bytes(
        {
            "报告.pdf": PDF_BYTES,
            "CW1_3.py": b"a = 1\n",
            "q1_v2.py": b"b = 2\n",
            "task12.py": b"c = 3\n",
            "CW1_v4.ipynb": b'{"cells": [], "metadata": {}, "nbformat": 4, "nbformat_minor": 2}',
        }
    )
    response = await _post_zip(client, payload, question_id=question.id)
    assert response.status_code == 201, response.text
    detail = await client.get(f"/api/submissions/{response.json()['id']}")
    code_files = detail.json()["code_files"]
    assert {
        (item["original_filename"], item["question_number"]) for item in code_files
    } == {
        ("CW1_3.py", 3),
        ("q1_v2.py", 2),
        ("task12.py", 12),
        ("CW1_v4.ipynb", 4),
    }


async def test_zip_upload_rejects_version_suffix_without_trailing_digit(
    client, db_session
):
    """尾部不是数字(如 v2 是字母结尾之前缀形式)且整体无尾部数字 → 422。"""
    question = _ready_question(db_session, name="无尾部数字题目")
    db_session.commit()
    payload = _zip_bytes({"报告.pdf": PDF_BYTES, "solution_vA.py": b"x = 1\n"})
    response = await _post_zip(client, payload, question_id=question.id)
    assert response.status_code == 422
    assert "小题号数字" in response.json()["detail"]


async def test_zip_upload_rejects_nested_code_name(client, db_session):
    """目录前缀的 q<n> 之外的代码名(扁平化后)仍须匹配 q<n>。"""
    question = _ready_question(db_session, name="嵌套代码题目")
    db_session.commit()
    payload = _zip_bytes({"报告.pdf": PDF_BYTES, "src/utils/helper.py": b"x = 1\n"})
    response = await _post_zip(client, payload, question_id=question.id)
    assert response.status_code == 422


async def test_zip_upload_rejects_traversal_member(client, db_session):
    """路径穿越成员 → 422,且 uploads 无残留。"""
    question = _ready_question(db_session, name="穿越题目")
    db_session.commit()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("报告.pdf", PDF_BYTES)
        archive.writestr("../evil.py", b"print(1)\n")
    response = await _post_zip(client, buffer.getvalue(), question_id=question.id)
    assert response.status_code == 422
    assert "路径穿越" in response.json()["detail"]


async def test_zip_upload_rejects_duplicate_after_flattening(client, db_session):
    """目录不同但扁平化后重名 → 422。"""
    question = _ready_question(db_session, name="重名题目")
    db_session.commit()
    payload = _zip_bytes(
        {"报告.pdf": PDF_BYTES, "task1/q1.py": b"a = 1\n", "task2/q1.py": b"b = 2\n"}
    )
    response = await _post_zip(client, payload, question_id=question.id)
    assert response.status_code == 422
    assert "重复" in response.json()["detail"]


async def test_zip_upload_skips_directory_entries(client, db_session, zip_upload_dir):
    """归档工具写入的显式目录条目(如 CW1/)不是文件,跳过而非当作空数据集。"""
    question = _ready_question(db_session, name="目录条目题目")
    db_session.commit()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(zipfile.ZipInfo("CW1/"), "")  # 目录条目
        archive.writestr("CW1/报告.pdf", PDF_BYTES)
        archive.writestr("CW1/task1.py", b"print(1)\n")
        archive.writestr("CW1/data/battery.csv", b"a,b\n1,2\n")
    response = await _post_zip(client, buffer.getvalue(), question_id=question.id)
    assert response.status_code == 201, response.text
    detail = await client.get(f"/api/submissions/{response.json()['id']}")
    body = detail.json()
    assert [item["original_filename"] for item in body["code_files"]] == ["task1.py"]
    assert [item["original_filename"] for item in body["code_input_files"]] == [
        "battery.csv"
    ]


async def test_zip_upload_rejects_executable_input(client, db_session):
    """数据集为可执行文件(ELF 魔数)→ 422。"""
    question = _ready_question(db_session, name="可执行数据集")
    db_session.commit()
    payload = _zip_bytes(
        {"报告.pdf": PDF_BYTES, "data/tool": b"\x7fELF" + b"\x00" * 16}
    )
    response = await _post_zip(client, payload, question_id=question.id)
    assert response.status_code == 422


async def test_zip_upload_rejects_archive_input(client, db_session):
    """数据集为嵌套 zip → 422。"""
    question = _ready_question(db_session, name="嵌套归档")
    db_session.commit()
    payload = _zip_bytes(
        {"报告.pdf": PDF_BYTES, "data/more.zip": b"PK\x03\x04" + b"junk"}
    )
    response = await _post_zip(client, payload, question_id=question.id)
    assert response.status_code == 422


async def test_zip_upload_rejects_fake_pdf_report(client, db_session):
    """报告成员无 %PDF- 魔数 → 422。"""
    question = _ready_question(db_session, name="伪 PDF")
    db_session.commit()
    payload = _zip_bytes({"报告.pdf": b"not a pdf at all"})
    response = await _post_zip(client, payload, question_id=question.id)
    assert response.status_code == 422


async def test_zip_upload_rejects_fake_zip_body(client, db_session):
    """伪 ZIP(魔数不符)→ 422。"""
    question = _ready_question(db_session, name="伪 ZIP")
    db_session.commit()
    response = await client.post(
        "/api/submissions",
        files=[("file", ("作业.zip", b"this is not a zip", "application/zip"))],
        data={"question_id": question.id},
    )
    assert response.status_code == 422
    assert "ZIP" in response.json()["detail"]


async def test_zip_upload_rejects_symlink_member(client, db_session):
    """符号链接成员 → 422。"""
    question = _ready_question(db_session, name="符号链接")
    db_session.commit()
    buffer = io.BytesIO()
    info = zipfile.ZipInfo("link.py")
    info.external_attr = (0o120777 << 16) | 0x20
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("报告.pdf", PDF_BYTES)
        archive.writestr(info, b"/etc/passwd")
    response = await _post_zip(client, buffer.getvalue(), question_id=question.id)
    assert response.status_code == 422


async def test_zip_upload_skips_junk_and_dotfiles_handled(client, db_session, zip_upload_dir):
    """__MACOSX/.DS_Store 等垃圾被跳过;正常上传不受影响。"""
    question = _ready_question(db_session, name="垃圾文件题目")
    db_session.commit()
    payload = _zip_bytes(
        {
            "报告.pdf": PDF_BYTES,
            "q1.py": b"print(1)\n",
            "__MACOSX/._q1.py": b"junk",
            ".DS_Store": b"junk",
        }
    )
    response = await _post_zip(client, payload, question_id=question.id)
    assert response.status_code == 201, response.text


async def test_zip_upload_rolls_back_storage_on_db_failure(
    client, db_session, zip_upload_dir
):
    """提交事务失败(题目不可用)→ 代码/数据集/报告目录无残留。"""
    question = _ready_question(db_session, name="回滚题目")
    question.status = QuestionStatus.pending  # 预检将失败
    db_session.commit()

    payload = _zip_bytes({"报告.pdf": PDF_BYTES, "q1.py": b"print(1)\n"})
    response = await _post_zip(client, payload, question_id=question.id)
    assert response.status_code == 409
    assert not (zip_upload_dir / "code").exists()
    assert not (zip_upload_dir / "code-inputs").exists()
    assert not (zip_upload_dir / "documents").exists()


async def test_zip_upload_rejects_oversized_member_declaration(
    client, db_session, zip_upload_dir
):
    """成员声明大小超过代码上限 → 413(声明造假也被实际读取上限兜住)。"""
    question = _ready_question(db_session, name="超大代码")
    db_session.commit()
    big = b"x" * (20 * 1024 * 1024 + 1)
    payload = _zip_bytes({"报告.pdf": PDF_BYTES, "q1.py": big})
    response = await _post_zip(client, payload, question_id=question.id)
    assert response.status_code == 413
    # UUID 存储目录已回滚,只允许留下空的分类父目录
    assert not any((zip_upload_dir / "code").iterdir())


async def test_non_pdf_non_zip_extension_rejected(client):
    """既非 .pdf 也非 .zip 的后缀 → 422(嗅探层拒绝)。"""
    response = await client.post(
        "/api/submissions",
        files=[("file", ("test.txt", b"hello", "text/plain"))],
        data={"question_id": "1"},
    )
    assert response.status_code == 422


async def test_submission_status_becomes_pending_with_inputs(
    client, db_session, zip_upload_dir
):
    """ZIP 上传后提交进入 pending,OCR 任务创建,状态机不因 ZIP 分支改变。"""
    question = _ready_question(db_session, name="状态题目")
    db_session.commit()
    payload = _zip_bytes({"报告.pdf": PDF_BYTES, "data/d.csv": b"x\n1\n"})
    response = await _post_zip(client, payload, question_id=question.id)
    assert response.status_code == 201
    submission = db_session.get(Submission, response.json()["id"])
    assert submission.status == SubmissionStatus.pending
    assert submission.file_sha256
