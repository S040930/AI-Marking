"""ACP 工作区物化测试:保留名(skill.md/scratch)冲突预检。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.acp.workspace import _materialize_to


def _file_item(name: str, path: Path) -> SimpleNamespace:
    return SimpleNamespace(original_filename=name, file_path=str(path))


def _submission(
    report_path: Path,
    code_files=None,
    code_input_files=None,
) -> SimpleNamespace:
    return SimpleNamespace(
        original_filename=report_path.name,
        file_path=str(report_path),
        code_files=code_files or [],
        code_input_files=code_input_files or [],
    )


def _uploads(tmp_path, monkeypatch) -> Path:
    import app.core.config as config

    uploads = tmp_path / "uploads"
    uploads.mkdir()
    monkeypatch.setattr(config.settings, "UPLOAD_DIR", str(uploads))
    return uploads


def test_materialize_rejects_reserved_skill_md(tmp_path, monkeypatch):
    uploads = _uploads(tmp_path, monkeypatch)
    report = uploads / "report.pdf"
    report.write_bytes(b"%PDF")
    skill = uploads / "skill.md"
    skill.write_bytes(b"# student file")

    sub = _submission(report, code_files=[_file_item("skill.md", skill)])
    target = tmp_path / "run-1"
    with pytest.raises(ValueError, match="skill.md"):
        _materialize_to(sub, target)
    assert not target.exists(), "冲突应在拷贝前暴露,不残留目标目录"


def test_materialize_rejects_reserved_scratch(tmp_path, monkeypatch):
    uploads = _uploads(tmp_path, monkeypatch)
    report = uploads / "report.pdf"
    report.write_bytes(b"%PDF")
    scratch = uploads / "scratch"
    scratch.write_bytes(b"x")

    sub = _submission(report, code_files=[_file_item("scratch", scratch)])
    target = tmp_path / "run-2"
    with pytest.raises(ValueError, match="scratch"):
        _materialize_to(sub, target)
    assert not target.exists()


def test_materialize_ok_with_normal_files(tmp_path, monkeypatch):
    uploads = _uploads(tmp_path, monkeypatch)
    report = uploads / "report.pdf"
    report.write_bytes(b"%PDF")
    code = uploads / "q1.py"
    code.write_bytes(b"print(1)")
    data = uploads / "input.csv"
    data.write_bytes(b"a,b\n1,2\n")

    sub = _submission(
        report,
        code_files=[_file_item("q1.py", code)],
        code_input_files=[_file_item("input.csv", data)],
    )
    target = tmp_path / "run-3"
    materialized = _materialize_to(sub, target)
    assert materialized == target
    assert (target / "report.pdf").is_file()
    assert (target / "q1.py").is_file()
    assert (target / "input.csv").is_file()
    assert (target / "skill.md").is_file()
    assert (target / "scratch").is_dir()
