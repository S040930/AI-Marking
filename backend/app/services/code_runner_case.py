"""Run one approved test case through the trusted Seatbelt supervisor."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from app.services import code_runner_results as result_helpers
from app.services import code_runner_runtime as runtime_helpers
from app.services.code_runner_errors import (
    CodeRunnerCancelledError,
    CodeRunnerInfrastructureError,
)
from app.services.code_testing import MAX_CASE_OUTPUT_BYTES, MAX_CASE_TIMEOUT_SECONDS


def run_case(
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
        return result_helpers.build_result_row(
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
        r_profile.write_text(runtime_helpers.R_PROFILE_SOURCE, encoding="utf-8")
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
                        "Seatbelt 监督器未返回有效结果：" + result_helpers.bounded_text(stderr or stdout or "空结果")
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
            "Seatbelt 执行失败：" + result_helpers.bounded_text(str(result.get("detail") or result.get("stderr") or "未知错误"))
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
        detail = result_helpers.bounded_text((stdout_text + stderr_text).strip() or "程序退出状态非零。")
        return finish(status, detail, measurements=diagnostics)
    expected = str(case.get("expected_stdout") or "").replace("\r\n", "\n").strip()
    actual = stdout_text.replace("\r\n", "\n").strip()
    if actual != expected:
        detail = f"预期输出：{expected[:4000]}\n实际输出：{actual[:4000]}"
        if stderr_text:
            detail += "\n" + stderr_text
        return finish("failed", result_helpers.bounded_text(detail), measurements=diagnostics)
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
