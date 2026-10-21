"""macOS Seatbelt runner for teacher-approved, question-scoped tests."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable

from app.core.config import settings
from app.models.submission import Submission
from app.services.code_testing import (
    MAX_CASE_OUTPUT_BYTES,
    MAX_CASE_TIMEOUT_SECONDS,
    MAX_SUBMISSION_RUNTIME_SECONDS,
)
from app.services.sandbox import (
    SANDBOX_EXEC,
    SANDBOX_POLICY_VERSION,
    SEATBELT_PARTIAL,
    SandboxPolicy,
    build_sandbox_argv,
    python_executable,
    runtime_roots,
)
from app.services.submission_zip import (
    MAX_CODE_INPUT_FILE_BYTES,
    MAX_CODE_INPUT_FILES,
    MAX_CODE_INPUT_TOTAL_BYTES,
)

EXPECTED_PYTHON_VERSION = "3.12.13"
EXPECTED_R_VERSION = "4.5.2"
EXPECTED_JAVA_VERSION = "11.0.28"
JAVA_HOME_TOOL = Path("/usr/libexec/java_home")
RSCRIPT_TOOL = Path("/usr/local/bin/Rscript")
_R_PROFILE_SOURCE = "\n".join(
    (
        "base_environment <- baseenv()",
        "unlockBinding('Sys.which', base_environment)",
        "assign('Sys.which', function(names) structure(rep('', length(names)), names = names), envir = base_environment)",
        "lockBinding('Sys.which', base_environment)",
    )
)


class CodeRunnerInfrastructureError(RuntimeError):
    """The native sandbox or its trusted supervisor failed; fail closed."""


class CodeRunnerCancelledError(RuntimeError):
    """The grading run was cancelled while student code was active."""


@lru_cache(maxsize=1)
def _runtime_set() -> dict[str, Any]:
    if sys.platform != "darwin":
        raise CodeRunnerInfrastructureError("Seatbelt 代码运行仅支持 macOS")
    python = python_executable()
    python_result = subprocess.run(
        [str(python), "--version"], capture_output=True, text=True, timeout=4, check=True
    )
    python_version = (python_result.stdout or python_result.stderr).strip().removeprefix("Python ")
    if python_version != EXPECTED_PYTHON_VERSION:
        raise CodeRunnerInfrastructureError(
            f"Python 运行时版本必须为 {EXPECTED_PYTHON_VERSION}，当前为 {python_version}"
        )

    if not RSCRIPT_TOOL.is_file():
        raise CodeRunnerInfrastructureError(f"未找到固定 R 运行时：{RSCRIPT_TOOL}")
    rscript = Path(os.path.realpath(RSCRIPT_TOOL))
    r_version_result = subprocess.run(
        [str(rscript), "--version"], capture_output=True, text=True, timeout=30, check=True
    )
    r_match = re.search(
        r"version\s+([0-9]+\.[0-9]+\.[0-9]+)",
        r_version_result.stderr + r_version_result.stdout,
    )
    if r_match is None or r_match.group(1) != EXPECTED_R_VERSION:
        actual = r_match.group(1) if r_match else "unknown"
        raise CodeRunnerInfrastructureError(
            f"R 运行时版本必须为 {EXPECTED_R_VERSION}，当前为 {actual}"
        )
    r_home_result = subprocess.run(
        [str(rscript), "--vanilla", "-e", "cat(R.home())"],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
        env={"PATH": "/usr/bin:/bin", "HOME": tempfile.gettempdir(), "TZ": "UTC"},
    )
    r_home = Path(r_home_result.stdout.strip()).resolve(strict=True)
    r_main = r_home / "bin" / "exec" / "R"
    if not r_main.is_file():
        raise CodeRunnerInfrastructureError("R 安装缺少固定主程序")

    java_home_result = subprocess.run(
        [str(JAVA_HOME_TOOL)],
        capture_output=True,
        text=True,
        timeout=4,
        check=True,
    )
    java_home = Path(java_home_result.stdout.strip()).resolve(strict=True)
    java = java_home / "bin" / "java"
    javac = java_home / "bin" / "javac"
    java_version_result = subprocess.run(
        [str(java), "-version"], capture_output=True, text=True, timeout=8, check=True
    )
    java_match = re.search(r'version "([0-9]+\.[0-9]+\.[0-9]+)', java_version_result.stderr)
    if java_match is None or java_match.group(1) != EXPECTED_JAVA_VERSION:
        actual = java_match.group(1) if java_match else "unknown"
        raise CodeRunnerInfrastructureError(
            f"Java 运行时版本必须为 {EXPECTED_JAVA_VERSION}，当前为 {actual}"
        )
    if not java.is_file() or not javac.is_file():
        raise CodeRunnerInfrastructureError("固定 Java 安装缺少 java 或 javac")

    executables = (python, r_main, java, javac)
    roots = tuple(sorted(set(runtime_roots(executables)) | {r_home, java_home}, key=str))
    return {
        "python": python,
        "rscript": rscript,
        "r_home": r_home,
        "r_main": r_main,
        "java": java,
        "javac": javac,
        "executables": executables,
        "runtime_roots": roots,
        "versions": {
            "python": python_version,
            "r": EXPECTED_R_VERSION,
            "java": EXPECTED_JAVA_VERSION,
        },
    }


@lru_cache(maxsize=1)
def sandbox_readiness() -> tuple[bool, str]:
    """Fail closed unless the native Seatbelt boundary and fixed runtimes work."""
    if sys.platform != "darwin" or not Path(SANDBOX_EXEC).is_file():
        return False, f"未找到 macOS Seatbelt 执行器：{SANDBOX_EXEC}"
    try:
        permissive_probe = subprocess.run(
            [SANDBOX_EXEC, "-p", "(version 1)(allow default)", "/usr/bin/true"],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"Seatbelt 自检失败：{type(exc).__name__}"
    if permissive_probe.returncode:
        detail = (permissive_probe.stderr or permissive_probe.stdout).strip()[:240]
        return False, f"Seatbelt 自检未通过：{detail or 'sandbox-exec 失败'}"
    try:
        runtimes = _runtime_set()
    except (CodeRunnerInfrastructureError, OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)
    try:
        _probe_restrictive_profile(runtimes)
    except (OSError, subprocess.SubprocessError, CodeRunnerInfrastructureError) as exc:
        return False, f"Seatbelt 限制策略自检失败：{exc}"
    versions = runtimes["versions"]
    return True, (
        "macOS Seatbelt 与固定运行时已就绪："
        f"Python {versions['python']} / R {versions['r']} / Java {versions['java']}。"
    )


def _probe_restrictive_profile(runtimes: dict[str, Any]) -> None:
    """Apply the production policy to a harmless read/write smoke check."""
    with tempfile.TemporaryDirectory(prefix="ai-marking-seatbelt-check-") as temporary:
        root = Path(temporary)
        input_root = root / "input"
        work_root = root / "work"
        support_root = root / "support"
        input_root.mkdir(mode=0o700)
        work_root.mkdir(mode=0o700)
        support_root.mkdir(mode=0o700)
        input_file = input_root / "probe.txt"
        output_file = work_root / "probe.txt"
        input_file.write_text("seatbelt-input", encoding="utf-8")
        input_file.chmod(0o444)
        r_profile = support_root / "r-init.R"
        r_profile.write_text(_R_PROFILE_SOURCE, encoding="utf-8")
        r_profile.chmod(0o444)
        policy = SandboxPolicy(
            input_root=input_root,
            work_root=work_root,
            executables=runtimes["executables"],
            runtime_roots=runtimes["runtime_roots"],
            additional_read_roots=(support_root,),
        )
        script = "\n".join(
            (
                "import pathlib, socket, subprocess, sys",
                "assert pathlib.Path(sys.argv[1]).read_text() == 'seatbelt-input'",
                "pathlib.Path(sys.argv[2]).write_text('seatbelt-output')",
                "try:",
                "    pathlib.Path(sys.argv[1]).parent.joinpath('write-denied').write_text('bad')",
                "except OSError:",
                "    pass",
                "else:",
                "    raise SystemExit('input directory is writable')",
                "for denied in (sys.argv[3], '/private/var/folders'):",
                "    try:",
                "        list(pathlib.Path(denied).iterdir())",
                "    except OSError:",
                "        pass",
                "    else:",
                "        raise SystemExit('protected directory is readable')",
                "try:",
                "    subprocess.run(['/bin/true'], timeout=2, check=False)",
                "except (OSError, subprocess.SubprocessError):",
                "    pass",
                "else:",
                "    raise SystemExit('student child process is allowed')",
                "try:",
                "    with socket.socket() as sock: sock.bind(('127.0.0.1', 0))",
                "except OSError:",
                "    pass",
                "else:",
                "    raise SystemExit('network socket is allowed')",
                "print('SEATBELT_PROFILE_READY')",
            )
        )
        environment = {
            "PATH": "/usr/bin:/bin",
            "HOME": str(work_root),
            "TMPDIR": str(work_root),
            "TMP": str(work_root),
            "TEMP": str(work_root),
            "PYTHONNOUSERSITE": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        try:
            result = subprocess.run(
                build_sandbox_argv(
                    policy,
                    (
                        str(runtimes["python"]),
                        "-s",
                        "-S",
                        "-B",
                        "-c",
                        script,
                        str(input_file),
                        str(output_file),
                        str(Path.home().resolve(strict=True)),
                    ),
                ),
                cwd=work_root,
                env=environment,
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise CodeRunnerInfrastructureError("Python Seatbelt 探针超时") from exc
        if result.returncode or "SEATBELT_PROFILE_READY" not in result.stdout:
            detail = (result.stderr or result.stdout or "无输出").strip()[:300]
            raise CodeRunnerInfrastructureError(detail)
        if not output_file.is_file() or output_file.read_text(encoding="utf-8") != "seatbelt-output":
            raise CodeRunnerInfrastructureError("Seatbelt 工作目录写入未通过")

        runtime_checks = (
            (
                runtimes["r_main"],
                (
                    "--slave",
                    "--no-site-file",
                    "--no-restore",
                    "--no-save",
                    "-e",
                    "stopifnot(requireNamespace('utils', quietly=TRUE), requireNamespace('stats', quietly=TRUE))",
                ),
                {
                    "R_HOME": str(runtimes["r_home"]),
                    "R_USER": str(work_root),
                    "R_LIBS_USER": str(work_root / "empty-r-library"),
                    "R_PROFILE_USER": str(r_profile),
                    "R_PROFILE_SITE": "/dev/null",
                    "R_ENVIRON": "/dev/null",
                    "R_ENVIRON_USER": "/dev/null",
                },
            ),
            (
                runtimes["java"],
                ("-Duser.home=" + str(work_root), "-version"),
                {"JAVA_HOME": str(runtimes["java"].parent.parent)},
            ),
            (
                runtimes["javac"],
                ("-version",),
                {"JAVA_HOME": str(runtimes["java"].parent.parent)},
            ),
        )
        for executable, arguments, extra_environment in runtime_checks:
            try:
                runtime_result = subprocess.run(
                    build_sandbox_argv(policy, (str(executable), *arguments)),
                    cwd=work_root,
                    env={**environment, **extra_environment},
                    capture_output=True,
                    text=True,
                    timeout=20,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                raise CodeRunnerInfrastructureError(
                    f"{Path(executable).name} Seatbelt 探针超时"
                ) from exc
            if runtime_result.returncode:
                detail = (runtime_result.stderr or runtime_result.stdout or "无输出").strip()[:300]
                raise CodeRunnerInfrastructureError(
                    f"{Path(executable).name} 无法在 Seatbelt 下启动：{detail}"
                )


def _runner_language(entrypoint: str) -> str:
    suffix = Path(entrypoint).suffix.casefold()
    if suffix == ".py":
        return "python"
    if suffix == ".r":
        return "r"
    if suffix == ".ipynb":
        return "notebook"
    if suffix == ".java":
        return "java"
    raise CodeRunnerInfrastructureError(
        f"代码文件类型不受首版运行器支持：{Path(entrypoint).suffix or '无扩展名'}"
    )


def run_submission_tests(
    submission: Submission,
    suite_tests: list[dict[str, Any]],
    *,
    run_id: int,
    should_cancel: Callable[[], bool] | None = None,
) -> list[dict[str, Any]]:
    """Run approved program I/O cases under a per-test Seatbelt profile."""
    if should_cancel and should_cancel():
        raise CodeRunnerCancelledError("批改运行已取消")
    if len(suite_tests) > 20:
        raise CodeRunnerInfrastructureError("已批准测试集超过 20 项上限")
    grouped: dict[int, list[Any]] = {}
    for code_file in submission.code_files:
        grouped.setdefault(int(code_file.question_number), []).append(code_file)
    if not grouped:
        return [
            _result_row(
                int(case.get("question_number") or 0),
                index,
                case,
                "missing_code",
                "未提交此小题代码",
                isolation=None,
            )
            for index, case in enumerate(suite_tests, start=1)
        ]

    languages: dict[int, str] = {}
    for question_number, code_files in grouped.items():
        entrypoints = [item for item in code_files if item.entrypoint]
        if len(entrypoints) != 1:
            raise CodeRunnerInfrastructureError(
                f"第 {question_number} 题的代码入口文件数量无效"
            )
        language = _runner_language(entrypoints[0].original_filename)
        languages[question_number] = language
    has_executable_tests = any(
        str(case.get("mode") or "program_io") == "program_io"
        and languages.get(int(case.get("question_number") or 0)) not in (None, "notebook")
        for case in suite_tests
    )
    runtimes = None
    if has_executable_tests:
        ready, detail = sandbox_readiness()
        if not ready:
            raise CodeRunnerInfrastructureError(detail)
        runtimes = _runtime_set()

    total_deadline = time.monotonic() + MAX_SUBMISSION_RUNTIME_SECONDS
    results: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix=f"ai-marking-code-{run_id}-") as temporary:
        submission_root = Path(temporary)
        shared_data = _stage_data_inputs(submission_root, submission, should_cancel)
        staged: dict[int, tuple[Path, Any, str, list[tuple[str, str]]]] = {}
        for question_number, code_files in sorted(grouped.items()):
            entrypoints = [item for item in code_files if item.entrypoint]
            if len(entrypoints) != 1:
                raise CodeRunnerInfrastructureError(
                    f"第 {question_number} 题的代码入口文件数量无效"
                )
            entrypoint = entrypoints[0]
            language = languages[question_number]
            scratch = submission_root / f"q{question_number}"
            scratch.mkdir()
            data_bindings = _stage_inputs(scratch, code_files, shared_data, should_cancel)
            staged[question_number] = (scratch, entrypoint, language, data_bindings)

        cases_by_question: dict[int, list[tuple[int, dict[str, Any]]]] = {}
        for index, case in enumerate(suite_tests, start=1):
            cases_by_question.setdefault(int(case.get("question_number") or 0), []).append((index, case))

        for question_number, question_cases in sorted(cases_by_question.items()):
            source = staged.get(question_number)
            if source is None:
                results.extend(
                    _result_row(
                        question_number,
                        index,
                        case,
                        "missing_code",
                        "未提交此小题代码",
                        isolation=None,
                    )
                    for index, case in question_cases
                )
                continue
            scratch, entrypoint, language, data_bindings = source
            if language == "notebook":
                results.extend(
                    _result_row(
                        question_number,
                        index,
                        case,
                        "not_run",
                        "Notebook 暂不自动执行；由 AI 与教师检查提交内容。",
                        isolation=None,
                    )
                    for index, case in question_cases
                )
                continue
            notebook_cases = [
                (index, case)
                for index, case in question_cases
                if case.get("mode") == "notebook_assertion"
            ]
            results.extend(
                _result_row(
                    question_number,
                    index,
                    case,
                    "not_run",
                    "Notebook 暂不自动执行；由 AI 与教师检查提交内容。",
                    isolation=None,
                )
                for index, case in notebook_cases
            )
            matching = [
                (index, case)
                for index, case in question_cases
                if case.get("mode") != "notebook_assertion"
                and _case_matches_language(case, language)
            ]
            if not matching:
                if notebook_cases and len(notebook_cases) == len(question_cases):
                    continue
                raise CodeRunnerInfrastructureError(
                    f"第 {question_number} 题的测试集没有适用于 {language} 的测试项"
                )
            if len(matching) + len(results) > 20:
                raise CodeRunnerInfrastructureError("批改测试项总数超过 20 项上限")
            for index, case in matching:
                if should_cancel and should_cancel():
                    raise CodeRunnerCancelledError("批改运行已取消")
                remaining = total_deadline - time.monotonic()
                if remaining <= 0:
                    completed_ids = {result["id"] for result in results}
                    results.extend(
                        _result_row(
                            int(test.get("question_number") or 0),
                            idx,
                            test,
                            "timeout",
                            "单份作业运行达到 180 秒上限",
                        )
                        for idx, test in enumerate(suite_tests, start=1)
                        if str(
                            test.get("id")
                            or f"q{test.get('question_number')}-t{idx}"
                        )
                        not in completed_ids
                    )
                    return results
                test_result = _run_case(
                    scratch,
                    case,
                    question_number=question_number,
                    test_index=index,
                    entrypoint=entrypoint.original_filename,
                    language=language,
                    run_id=run_id,
                    timeout_seconds=min(MAX_CASE_TIMEOUT_SECONDS, remaining),
                    should_cancel=should_cancel,
                    data_bindings=data_bindings,
                    runtimes=runtimes,
                )
                results.append(test_result)
        if should_cancel and should_cancel():
            raise CodeRunnerCancelledError("批改运行已取消")
        return results


def _case_matches_language(case: dict[str, Any], language: str) -> bool:
    mode = case.get("mode")
    target = case.get("language")
    return mode == "program_io" and target in ("any", language)


def _stage_inputs(
    root: Path,
    code_files: list[Any],
    shared_data: list[tuple[Path, str, str]],
    should_cancel: Callable[[], bool] | None = None,
) -> list[tuple[str, str]]:
    staged_names: set[str] = set()
    for item in code_files:
        if should_cancel and should_cancel():
            raise CodeRunnerCancelledError("批改运行已取消")
        name = Path(item.original_filename).name
        if not name or name in {".", ".."}:
            raise CodeRunnerInfrastructureError("代码文件名不安全")
        if name in staged_names:
            raise CodeRunnerInfrastructureError(f"小题内代码文件重名：{name}")
        staged_names.add(name)
        target = root / name
        target.write_text(item.source_text or "", encoding="utf-8")
        target.chmod(0o444)
    bindings: list[tuple[str, str]] = []
    if shared_data:
        data_dir = root / "assets"
        data_dir.mkdir(mode=0o755)
        for source, asset_name, filename in shared_data:
            if should_cancel and should_cancel():
                raise CodeRunnerCancelledError("批改运行已取消")
            try:
                os.link(source, data_dir / asset_name)
            except OSError as exc:
                raise CodeRunnerInfrastructureError("作业数据集无法隔离暂存") from exc
            bindings.append((asset_name, filename))
    return bindings


def _stage_data_inputs(
    root: Path,
    submission: Submission,
    should_cancel: Callable[[], bool] | None = None,
) -> list[tuple[Path, str, str]]:
    bindings: list[tuple[Path, str, str]] = []
    question_inputs = list(submission.code_input_files)
    if not question_inputs:
        return bindings
    if len(question_inputs) > MAX_CODE_INPUT_FILES:
        raise CodeRunnerInfrastructureError("作业数据集文件数超过暂存限制")
    data_dir = root / "shared-assets"
    data_dir.mkdir(mode=0o700)
    data_names: set[str] = set()
    try:
        upload_root = Path(settings.UPLOAD_DIR).resolve(strict=True)
    except OSError as exc:
        raise CodeRunnerInfrastructureError("上传目录不可读取") from exc
    total_size = 0
    for index, item in enumerate(question_inputs, start=1):
        if should_cancel and should_cancel():
            raise CodeRunnerCancelledError("批改运行已取消")
        source = Path(item.file_path)
        try:
            absolute_source = source.absolute()
            resolved_source = source.resolve(strict=True)
            resolved_source.relative_to(upload_root)
        except (OSError, ValueError) as exc:
            raise CodeRunnerInfrastructureError(
                "作业数据集文件不在受控上传目录内"
            ) from exc
        if (
            not resolved_source.is_file()
            or absolute_source != resolved_source
            or source.is_symlink()
        ):
            raise CodeRunnerInfrastructureError("作业数据集文件不可读取")
        declared_size = int(item.size_bytes)
        if declared_size > MAX_CODE_INPUT_FILE_BYTES or total_size + declared_size > MAX_CODE_INPUT_TOTAL_BYTES:
            raise CodeRunnerInfrastructureError("作业数据集超过暂存大小限制")
        filename = Path(item.original_filename).name
        if not filename or filename in {".", ".."} or filename in data_names:
            raise CodeRunnerInfrastructureError("作业数据集文件名无效或重复")
        data_names.add(filename)
        asset_name = f"asset-{index}"
        target = data_dir / asset_name
        digest = hashlib.sha256()
        copied_size = 0
        try:
            with resolved_source.open("rb") as source_file, target.open("xb") as target_file:
                while chunk := source_file.read(1024 * 1024):
                    if should_cancel and should_cancel():
                        raise CodeRunnerCancelledError("批改运行已取消")
                    copied_size += len(chunk)
                    if copied_size > declared_size or total_size + copied_size > MAX_CODE_INPUT_TOTAL_BYTES:
                        raise CodeRunnerInfrastructureError("作业数据集超过暂存大小限制")
                    digest.update(chunk)
                    target_file.write(chunk)
            target.chmod(0o444)
        except OSError as exc:
            raise CodeRunnerInfrastructureError("作业数据集无法暂存") from exc
        if copied_size != declared_size or digest.hexdigest() != item.sha256:
            raise CodeRunnerInfrastructureError("作业数据集内容与上传记录不匹配")
        total_size += copied_size
        bindings.append((target, asset_name, filename))
    return bindings


def _result_row(
    question_number: int,
    index: int,
    case: dict[str, Any],
    status: str,
    output: str,
    isolation: str | None = SEATBELT_PARTIAL,
    measurements: dict[str, Any] | None = None,
) -> dict[str, Any]:
    row = {
        "id": str(case.get("id") or f"q{question_number}-t{index}"),
        "question_number": question_number,
        "title": str(case.get("title") or "测试项"),
        "mode": str(case.get("mode") or "program_io"),
        "language": str(case.get("language") or "any"),
        "status": status,
        "output": output.encode("utf-8", errors="replace")[:MAX_CASE_OUTPUT_BYTES].decode(
            "utf-8", errors="ignore"
        ),
    }
    if isolation is not None:
        row["isolation"] = isolation
    if measurements:
        row["execution"] = {**measurements, "policy_version": SANDBOX_POLICY_VERSION}
    return row


def _run_case(
    input_root: Path,
    case: dict[str, Any],
    *,
    question_number: int,
    test_index: int,
    entrypoint: str,
    language: str,
    run_id: int,
    timeout_seconds: float,
    should_cancel: Callable[[], bool] | None,
    data_bindings: list[tuple[str, str]],
    runtimes: dict[str, Any],
) -> dict[str, Any]:
    source_path = (input_root / Path(entrypoint).name).resolve(strict=True)
    try:
        source_path.relative_to(input_root.resolve(strict=True))
    except ValueError as exc:
        raise CodeRunnerInfrastructureError("入口文件离开受控输入目录") from exc
    case_root = input_root.parent / f"case-{run_id}-{question_number}-{test_index}-{uuid.uuid4().hex[:8]}"
    work_root = case_root / "work"
    work_root.mkdir(parents=True, mode=0o700)
    additional_read_roots: tuple[Path, ...] = ()
    r_profile: Path | None = None

    def finish(status: str, output: str, *, measurements: dict[str, Any] | None = None) -> dict[str, Any]:
        _remove_case_root(case_root)
        return _result_row(
            question_number, test_index, case, status, output, measurements=measurements
        )

    for asset_name, filename in data_bindings:
        target = work_root / Path(filename).name
        if target.exists() or target.is_symlink():
            continue
        os.symlink(input_root / "assets" / asset_name, target)

    if language == "python":
        commands = (
            (str(runtimes["python"]), "-s", "-S", "-B", "-m", "py_compile", str(source_path)),
            (str(runtimes["python"]), "-s", "-S", "-B", str(source_path)),
        )
        environment = {"PYTHONPYCACHEPREFIX": str(work_root / "pycache")}
    elif language == "r":
        support_root = case_root / "support"
        support_root.mkdir(mode=0o700)
        r_profile = support_root / "r-init.R"
        r_profile.write_text(_R_PROFILE_SOURCE, encoding="utf-8")
        r_profile.chmod(0o444)
        additional_read_roots = (support_root,)
        r_flags = ("--slave", "--no-site-file", "--no-restore", "--no-save")
        commands = (
            (
                str(runtimes["r_main"]),
                *r_flags,
                "-e",
                "invisible(parse(file=commandArgs(TRUE)[1]))",
                "--args",
                str(source_path),
            ),
            (
                str(runtimes["r_main"]),
                *r_flags,
                "-e",
                "source(commandArgs(TRUE)[1])",
                "--args",
                str(source_path),
            ),
        )
        environment = {
            "R_HOME": str(runtimes["r_home"]),
            "R_USER": str(work_root),
            "R_LIBS_USER": str(work_root / "empty-r-library"),
            "R_PROFILE_USER": str(r_profile),
            "R_PROFILE_SITE": "/dev/null",
            "R_ENVIRON": "/dev/null",
            "R_ENVIRON_USER": "/dev/null",
        }
    elif language == "java":
        java_files = tuple(
            sorted(
                str(path)
                for path in input_root.iterdir()
                if path.is_file() and path.suffix.casefold() == ".java"
            )
        )
        if not java_files:
            return finish("compile_error", "没有可编译的 Java 源文件")
        classes = work_root / "classes"
        classes.mkdir(mode=0o700)
        source_text = source_path.read_text(encoding="utf-8", errors="replace")
        package_match = re.search(r"^\s*package\s+([\w.]+)\s*;", source_text, re.MULTILINE)
        class_match = re.search(r"\bpublic\s+class\s+([A-Za-z_$][\w$]*)", source_text)
        package_class_match = re.search(r"\bclass\s+([A-Za-z_$][\w$]*)", source_text)
        main_class = (
            class_match.group(1)
            if class_match is not None
            else package_class_match.group(1)
            if package_class_match is not None
            else source_path.stem
        )
        if package_match:
            main_class = package_match.group(1) + "." + main_class
        commands = (
            (str(runtimes["javac"]), "-J-Duser.home=" + str(work_root), "-proc:none", "-d", str(classes), *java_files),
            (str(runtimes["java"]), "-XX:ActiveProcessorCount=1", "-Duser.home=" + str(work_root), "-Djava.io.tmpdir=" + str(work_root), "-cp", str(classes), main_class),
        )
        environment = {"JAVA_HOME": str(runtimes["java"].parent.parent)}
    else:
        raise CodeRunnerInfrastructureError(f"不支持的本机执行语言：{language}")

    request = {
        "input_root": str(input_root),
        "work_root": str(work_root),
        "executables": [str(path) for path in runtimes["executables"]],
        "runtime_roots": [str(path) for path in runtimes["runtime_roots"]],
        "additional_read_roots": [str(path) for path in additional_read_roots],
        "commands": commands,
        "timeout_seconds": min(MAX_CASE_TIMEOUT_SECONDS, timeout_seconds),
        "max_output_bytes": MAX_CASE_OUTPUT_BYTES,
        "input": str(case.get("input") or ""),
        "environment": environment,
    }
    supervisor_env = {
        "PATH": "/usr/bin:/bin",
        "PYTHONUNBUFFERED": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPATH": str(Path(__file__).resolve().parents[2]),
    }
    try:
        process = subprocess.Popen(
            [sys.executable, "-m", "app.services.code_execution_supervisor"],
            cwd=Path(__file__).resolve().parents[2],
            env=supervisor_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
    except OSError as exc:
        _remove_case_root(case_root)
        raise CodeRunnerInfrastructureError(f"启动 Seatbelt 监督器失败：{exc}") from exc
    assert process.stdin is not None
    try:
        process.stdin.write(json.dumps(request, ensure_ascii=False))
    except (BrokenPipeError, OSError):
        pass
    finally:
        try:
            process.stdin.close()
        except OSError:
            pass
        process.stdin = None
    deadline = time.monotonic() + timeout_seconds
    try:
        while True:
            if should_cancel and should_cancel():
                if process.poll() is None:
                    process.terminate()
                try:
                    process.communicate(timeout=2)
                except subprocess.TimeoutExpired:
                    _kill_process_group(process)
                    process.communicate()
                _remove_case_root(case_root)
                raise CodeRunnerCancelledError("批改运行已取消")
            if time.monotonic() >= deadline:
                if process.poll() is None:
                    process.terminate()
                try:
                    stdout, stderr = process.communicate(timeout=2)
                except subprocess.TimeoutExpired:
                    _kill_process_group(process)
                    stdout, stderr = process.communicate()
                try:
                    result = json.loads(stdout or "{}")
                except ValueError:
                    result = {}
                result.setdefault("reason", "timeout")
                break
            try:
                stdout, stderr = process.communicate(
                    timeout=min(0.1, max(0.01, deadline - time.monotonic()))
                )
                try:
                    result = json.loads(stdout)
                except (TypeError, ValueError) as exc:
                    raise CodeRunnerInfrastructureError(
                        "Seatbelt 监督器未返回有效结果：" + _bounded_text(stderr or stdout or "空结果")
                    ) from exc
                break
            except subprocess.TimeoutExpired:
                continue
    except CodeRunnerCancelledError:
        raise
    except BaseException:
        if process.poll() is None:
            process.terminate()
            try:
                process.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                _kill_process_group(process)
                process.communicate()
        _remove_case_root(case_root)
        raise
    timed_out_by_caller = result.get("reason") == "timeout"
    if process.returncode not in {0, None} and not timed_out_by_caller:
        detail = (stderr or "Seatbelt 监督器异常退出").strip()
        _remove_case_root(case_root)
        raise CodeRunnerInfrastructureError(
            f"Seatbelt 监督器退出码 {process.returncode}：{detail[:500]}"
        )
    reason = result.get("reason")
    if reason in {"launch_error", "sandbox_error", "monitor_error"} or "sandbox-exec:" in str(result.get("stderr") or ""):
        _remove_case_root(case_root)
        raise CodeRunnerInfrastructureError(
            "Seatbelt 执行失败：" + _bounded_text(str(result.get("detail") or result.get("stderr") or "未知错误"))
        )
    diagnostics = {
        "elapsed_ms": result.get("elapsed_ms", 0),
        "peak_memory_bytes": result.get("peak_memory_bytes", 0),
        "peak_workdir_bytes": result.get("peak_workdir_bytes", 0),
        "runtime_version": runtimes["versions"][language],
    }
    if reason == "timeout":
        output = "程序运行达到单项时间上限。"
        return finish("timeout", output, measurements=diagnostics)
    if reason in {"memory_limit", "disk_limit", "output_limit"}:
        labels = {
            "memory_limit": "内存使用超过监测阈值。",
            "disk_limit": "工作目录超过大小或文件数阈值。",
            "output_limit": "标准输出与错误输出合计超过上限。",
        }
        return finish("runtime_error", labels[reason], measurements=diagnostics)

    stdout_text = str(result.get("stdout") or "")
    stderr_text = str(result.get("stderr") or "")
    if result.get("returncode") != 0:
        status = "compile_error" if result.get("failed_stage") == 0 else "runtime_error"
        detail = _bounded_text((stdout_text + stderr_text).strip() or "程序退出状态非零。")
        return finish(status, detail, measurements=diagnostics)
    expected = str(case.get("expected_stdout") or "").replace("\r\n", "\n").strip()
    actual = stdout_text.replace("\r\n", "\n").strip()
    if actual != expected:
        detail = f"预期输出：{expected[:4000]}\n实际输出：{actual[:4000]}"
        if stderr_text:
            detail += "\n" + stderr_text
        return finish("failed", _bounded_text(detail), measurements=diagnostics)
    return finish(
        "passed",
        "标准输出与预期一致" + ("；程序向 stderr 输出了内容。" if stderr_text else "。"),
        measurements=diagnostics,
    )


def _kill_process_group(process: subprocess.Popen[str]) -> None:
    try:
        os.killpg(process.pid, 9)
    except (ProcessLookupError, PermissionError):
        if process.poll() is None:
            process.kill()


def _remove_case_root(root: Path) -> None:
    """Restore directory access before deleting a tree modified by student code."""
    if root.is_symlink():
        root.unlink(missing_ok=True)
        return
    if not root.exists():
        return

    def restore_directory(path: Path) -> None:
        path.chmod(0o700)
        with os.scandir(path) as entries:
            children = [Path(entry.path) for entry in entries if entry.is_dir(follow_symlinks=False)]
        for child in children:
            restore_directory(child)

    restore_directory(root)
    shutil.rmtree(root)


def _bounded_text(value: str) -> str:
    return value.encode("utf-8", errors="replace")[:MAX_CASE_OUTPUT_BYTES].decode(
        "utf-8", errors="ignore"
    )

