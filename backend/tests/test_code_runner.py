"""Seatbelt runner isolation, fail-closed behavior, and input staging tests."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services import (
    code_runner,
    code_runner_case,
    code_runner_errors,
    code_runner_inputs,
    code_runner_results,
    code_runner_runtime,
)
from app.services.sandbox import SandboxPolicy, build_sandbox_argv


def _submission(filename: str, source: str):
    code_file = SimpleNamespace(
        question_number=1,
        entrypoint=True,
        original_filename=filename,
        source_text=source,
    )
    return SimpleNamespace(code_files=[code_file], code_input_files=[])


def _io_case(*, expected: str = "ok\n"):
    return {
        "id": "q1-t1",
        "question_number": 1,
        "title": "output",
        "mode": "program_io",
        "language": "python",
        "input": "",
        "expected_stdout": expected,
    }


def _runtimes(tmp_path: Path) -> dict:
    executables = []
    for name in ("python", "rscript", "r", "java", "javac"):
        executable = tmp_path / "runtime" / "bin" / name
        executable.parent.mkdir(parents=True, exist_ok=True)
        executable.write_text("runtime", encoding="utf-8")
        executables.append(executable)
    return {
        "python": executables[0],
        "rscript": executables[1],
        "r_home": tmp_path / "runtime",
        "r_main": executables[2],
        "java": executables[3],
        "javac": executables[4],
        "executables": tuple(executables),
        "runtime_roots": (tmp_path / "runtime",),
        "versions": {"python": "3.12.13", "r": "4.5.2", "java": "11.0.28"},
    }


def test_code_tests_fail_closed_without_running_student_process(monkeypatch):
    monkeypatch.setattr(
        code_runner_runtime, "sandbox_readiness", lambda: (False, "Seatbelt 被禁用")
    )

    def no_host_run(*_args, **_kwargs):
        raise AssertionError("student program must not run without Seatbelt")

    monkeypatch.setattr(code_runner_case.subprocess, "Popen", no_host_run)
    with pytest.raises(code_runner_errors.CodeRunnerInfrastructureError, match="Seatbelt 被禁用"):
        code_runner.run_submission_tests(
            _submission("Q1.py", "print('ok')"), [_io_case()], run_id=1
        )


def test_notebook_is_not_executed_or_used_as_automatic_evidence(monkeypatch):
    monkeypatch.setattr(
        code_runner_runtime,
        "sandbox_readiness",
        lambda: (_ for _ in ()).throw(AssertionError("Notebook must not start a runtime")),
    )
    monkeypatch.setattr(
        code_runner_case.subprocess,
        "Popen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("Notebook code must not execute")
        ),
    )
    cases = [{**_io_case(), "mode": "notebook_assertion", "assertion": "assert True"}]
    result = code_runner.run_submission_tests(
        _submission("Q1.ipynb", "not a notebook"), cases, run_id=1
    )[0]

    assert result["status"] == "not_run"
    assert "isolation" not in result


def test_result_row_preserves_reference_bounds_output_and_reports_policy():
    from app.services.code_testing import MAX_CASE_OUTPUT_BYTES

    row = code_runner_results.build_result_row(
        2,
        3,
        {"id": "q2-t3", "title": "boundary", "mode": "program_io", "language": "r"},
        "failed",
        "x" * (MAX_CASE_OUTPUT_BYTES + 100),
        measurements={"elapsed_ms": 12},
    )

    assert row["id"] == "q2-t3"
    assert row["status"] == "failed"
    assert row["isolation"] == "seatbelt:partial"
    assert len(row["output"].encode("utf-8")) == MAX_CASE_OUTPUT_BYTES
    assert row["execution"] == {
        "elapsed_ms": 12,
        "policy_version": "seatbelt-v1",
    }


def test_seatbelt_profile_defaults_to_deny_and_scopes_access(tmp_path: Path):
    home = tmp_path / "home"
    input_root = tmp_path / "input"
    work_root = tmp_path / "work"
    runtime = tmp_path / "runtime"
    support_root = tmp_path / "support"
    for path in (home, input_root, work_root, runtime, support_root):
        path.mkdir()
    executable = runtime / "python"
    executable.write_text("runtime", encoding="utf-8")
    policy = SandboxPolicy(
        input_root=input_root,
        work_root=work_root,
        executables=(executable,),
        runtime_roots=(runtime,),
        additional_read_roots=(support_root,),
        home=home,
    )

    profile = policy.profile()
    argv = build_sandbox_argv(policy, (str(executable), "-c", "pass"))

    assert "(deny default)" in profile
    assert f'(deny file-read* (subpath "{home}"))' in profile
    assert f'(allow file-read* (subpath "{input_root}"))' in profile
    assert f'(allow file-read* (subpath "{support_root}"))' in profile
    assert f'(allow file-write* (subpath "{work_root}"))' in profile
    assert "(deny network*)" in profile
    assert "process-fork" not in profile
    assert argv[:2] == ["/usr/bin/sandbox-exec", "-p"]
    with pytest.raises(ValueError, match="allowlist"):
        build_sandbox_argv(policy, ("/bin/sh", "-c", "true"))


def test_uploaded_data_is_stream_staged_once_and_read_only(monkeypatch, tmp_path: Path):
    upload_root = tmp_path / "uploads"
    upload_root.mkdir()
    uploaded = upload_root / "dataset.csv"
    uploaded.write_bytes(b"group,value\na,1\n")
    uploaded.chmod(0o400)
    monkeypatch.setattr(code_runner_inputs.settings, "UPLOAD_DIR", str(upload_root))
    submission = SimpleNamespace(
        code_input_files=[
            SimpleNamespace(
                file_path=str(uploaded),
                original_filename="dataset.csv",
                size_bytes=uploaded.stat().st_size,
                sha256=hashlib.sha256(uploaded.read_bytes()).hexdigest(),
            )
        ]
    )
    shared = code_runner_inputs.stage_data_inputs(tmp_path, submission)
    q1 = tmp_path / "q1"
    q2 = tmp_path / "q2"
    q1.mkdir()
    q2.mkdir()
    code = [SimpleNamespace(original_filename="Q1.py", source_text="print('ok')")]

    bindings1 = code_runner_inputs.stage_code_inputs(q1, code, shared)
    bindings2 = code_runner_inputs.stage_code_inputs(q2, code, shared)
    staged1 = q1 / "assets" / "asset-1"
    staged2 = q2 / "assets" / "asset-1"

    assert bindings1 == bindings2 == [("asset-1", "dataset.csv")]
    assert staged1.read_bytes() == uploaded.read_bytes()
    assert staged1.stat().st_mode & 0o777 == 0o444
    assert os.path.samefile(staged1, staged2)
    assert uploaded.stat().st_mode & 0o777 == 0o400


def test_supervisor_request_contains_program_but_never_expected_output(monkeypatch, tmp_path: Path):
    input_root = tmp_path / "input"
    input_root.mkdir()
    (input_root / "Q1.py").write_text("print('ok')", encoding="utf-8")
    runtimes = _runtimes(tmp_path)
    requests: list[dict] = []

    class FakeProcess:
        returncode = 0

        def __init__(self):
            self.stdin = self

        def write(self, value: str) -> None:
            requests.append(json.loads(value))

        def close(self) -> None:
            return None

        def poll(self):
            return 0

        def communicate(self, timeout=None):
            del timeout
            return json.dumps(
                {
                    "reason": None,
                    "returncode": 0,
                    "stdout": "ok\n",
                    "stderr": "",
                    "elapsed_ms": 5,
                    "peak_memory_bytes": 100,
                    "peak_workdir_bytes": 20,
                }
            ), ""

    monkeypatch.setattr(code_runner_runtime, "sandbox_readiness", lambda: (True, "ready"))
    monkeypatch.setattr(code_runner_runtime, "load_runtimes", lambda: runtimes)
    monkeypatch.setattr(
        code_runner_case.subprocess, "Popen", lambda *_args, **_kwargs: FakeProcess()
    )
    result = code_runner.run_submission_tests(
        _submission("Q1.py", "print('ok')"), [_io_case()], run_id=42
    )[0]

    assert result["status"] == "passed"
    assert result["isolation"] == "seatbelt:partial"
    assert result["execution"]["runtime_version"] == "3.12.13"
    assert "expected_stdout" not in requests[0]
    assert requests[0]["commands"][-1][-1].endswith("/Q1.py")


def test_supervisor_workdir_measurement_does_not_follow_symlinks(tmp_path: Path):
    from app.services.code_execution_supervisor import _workdir_usage

    work = tmp_path / "work"
    work.mkdir()
    (work / "data.txt").write_text("data", encoding="utf-8")
    external = tmp_path / "external.txt"
    external.write_text("outside", encoding="utf-8")
    os.symlink(external, work / "link.txt")

    size, files = _workdir_usage(work)

    assert size == len("data")
    assert files == 2
