"""Trusted per-test supervisor, kept outside the Seatbelt student process."""

from __future__ import annotations

import json
import math
import os
import resource
import selectors
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from app.services.sandbox import (
    MAX_CASE_DISK_BYTES,
    MAX_CASE_FILES,
    MAX_CASE_MEMORY_BYTES,
    MAX_FILE_BYTES,
    MAX_OPEN_FILES,
    SandboxPolicy,
    build_sandbox_argv,
)

_ACTIVE_PROCESS: subprocess.Popen[bytes] | None = None
_MAX_OUTPUT_BYTES = 16 * 1024
_SAMPLE_INTERVAL_SECONDS = 0.1
_DISK_SAMPLE_INTERVAL_SECONDS = 0.5


def _terminate_active(_signum: int, _frame: Any) -> None:
    process = _ACTIVE_PROCESS
    if process is not None and process.poll() is None:
        try:
            process.kill()
        except OSError:
            pass
    raise SystemExit(128 + signal.SIGTERM)


def _set_child_limits(cpu_seconds: int) -> None:
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_FILE_BYTES, MAX_FILE_BYTES))
    resource.setrlimit(resource.RLIMIT_NOFILE, (MAX_OPEN_FILES, MAX_OPEN_FILES))
    if hasattr(resource, "RLIMIT_CPU"):
        resource.setrlimit(
            resource.RLIMIT_CPU, (max(1, cpu_seconds), max(2, cpu_seconds + 1))
        )


def _resident_bytes(pid: int) -> int | None:
    result = subprocess.run(
        ["/bin/ps", "-o", "rss=", "-p", str(pid)],
        capture_output=True,
        text=True,
        timeout=0.5,
        check=False,
    )
    value = result.stdout.strip()
    if result.returncode != 0 or not value:
        return None
    return int(value) * 1024


def _workdir_usage(root: Path) -> tuple[int, int]:
    total_bytes = 0
    total_files = 0

    def raise_walk_error(error: OSError) -> None:
        raise error

    for current, directories, files in os.walk(
        root, followlinks=False, onerror=raise_walk_error
    ):
        base = Path(current)
        for name in directories:
            if (base / name).is_symlink():
                total_files += 1
        for name in files:
            item = base / name
            if item.is_symlink():
                total_files += 1
            else:
                stat = item.stat(follow_symlinks=False)
                total_bytes += stat.st_size
                total_files += 1
            if total_bytes > MAX_CASE_DISK_BYTES or total_files > MAX_CASE_FILES:
                return total_bytes, total_files
    return total_bytes, total_files


def _run_command(
    command: tuple[str, ...],
    *,
    policy: SandboxPolicy,
    environment: dict[str, str],
    cwd: Path,
    input_text: str,
    deadline: float,
    output_limit: int,
) -> dict[str, Any]:
    global _ACTIVE_PROCESS
    remaining = max(0.05, deadline - time.monotonic())
    child_env = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(cwd),
        "TMPDIR": str(cwd),
        "TMP": str(cwd),
        "TEMP": str(cwd),
        "TZ": "UTC",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PYTHONHASHSEED": "0",
        "PYTHONNOUSERSITE": "1",
        "OMP_NUM_THREADS": "1",
        "OPENBLAS_NUM_THREADS": "1",
        "MKL_NUM_THREADS": "1",
        "R_LIBS_USER": str(cwd / "empty-r-library"),
        **environment,
    }
    started = time.monotonic()
    try:
        process = subprocess.Popen(
            build_sandbox_argv(policy, command),
            cwd=cwd,
            env=child_env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=False,
            preexec_fn=lambda: _set_child_limits(math.ceil(remaining)),
        )
    except (OSError, ValueError) as exc:
        return {"reason": "launch_error", "detail": f"{type(exc).__name__}: {exc}"}
    _ACTIVE_PROCESS = process
    assert process.stdin is not None
    try:
        try:
            process.stdin.write(input_text.encode("utf-8"))
            process.stdin.close()
        except (BrokenPipeError, OSError):
            pass

        assert process.stdout is not None and process.stderr is not None
        selector = selectors.DefaultSelector()
        for stream, label in ((process.stdout, "stdout"), (process.stderr, "stderr")):
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ, label)
        output = {"stdout": bytearray(), "stderr": bytearray()}
        total_output = 0
        peak_memory = 0
        peak_disk = 0
        last_memory_sample = 0.0
        last_disk_sample = 0.0
        reason: str | None = None
        try:
            while selector.get_map() or process.poll() is None:
                now = time.monotonic()
                if now >= deadline:
                    reason = "timeout"
                    process.kill()
                if process.poll() is None and now - last_memory_sample >= _SAMPLE_INTERVAL_SECONDS:
                    try:
                        sampled_memory = _resident_bytes(process.pid)
                        if sampled_memory is None and process.poll() is None:
                            reason = "monitor_error"
                        elif sampled_memory is not None:
                            peak_memory = max(peak_memory, sampled_memory)
                    except (OSError, ValueError, subprocess.SubprocessError):
                        if process.poll() is None:
                            reason = "monitor_error"
                    last_memory_sample = now
                    if peak_memory > MAX_CASE_MEMORY_BYTES:
                        reason = "memory_limit"
                        process.kill()
                if process.poll() is None and now - last_disk_sample >= _DISK_SAMPLE_INTERVAL_SECONDS:
                    try:
                        disk_bytes, file_count = _workdir_usage(policy.work_root)
                        peak_disk = max(peak_disk, disk_bytes)
                        if disk_bytes > MAX_CASE_DISK_BYTES or file_count > MAX_CASE_FILES:
                            reason = "disk_limit"
                    except OSError:
                        reason = "monitor_error"
                    last_disk_sample = now
                for key, _ in selector.select(min(0.05, max(0.0, deadline - now))):
                    try:
                        chunk = os.read(key.fileobj.fileno(), 4096)
                    except BlockingIOError:
                        continue
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    total_output += len(chunk)
                    kept = max(0, output_limit - len(output[key.data]))
                    if kept:
                        output[key.data].extend(chunk[:kept])
                    if total_output > output_limit:
                        reason = "output_limit"
                        if process.poll() is None:
                            process.kill()
                if reason is not None and process.poll() is None:
                    process.kill()
                if process.poll() is not None and not selector.get_map():
                    break
            process.wait()
        finally:
            selector.close()
        try:
            final_disk, final_files = _workdir_usage(policy.work_root)
            peak_disk = max(peak_disk, final_disk)
            if final_disk > MAX_CASE_DISK_BYTES or final_files > MAX_CASE_FILES:
                reason = "disk_limit"
        except OSError:
            reason = "monitor_error"
        return {
            "reason": reason,
            "stdout": bytes(output["stdout"]).decode("utf-8", errors="replace"),
            "stderr": bytes(output["stderr"]).decode("utf-8", errors="replace"),
            "returncode": process.returncode,
            "peak_memory_bytes": peak_memory,
            "peak_workdir_bytes": peak_disk,
            "elapsed_ms": round((time.monotonic() - started) * 1000),
        }
    finally:
        _ACTIVE_PROCESS = None
        if process.poll() is None:
            process.kill()
            process.wait()


def execute(request: dict[str, Any]) -> dict[str, Any]:
    input_root = Path(request["input_root"]).resolve(strict=True)
    work_root = Path(request["work_root"]).resolve(strict=True)
    commands = [tuple(str(value) for value in command) for command in request["commands"]]
    executables = tuple(Path(value) for value in request["executables"])
    runtime_roots = tuple(Path(value) for value in request["runtime_roots"])
    additional_read_roots = tuple(
        Path(value) for value in request.get("additional_read_roots", [])
    )
    policy = SandboxPolicy(
        input_root=input_root,
        work_root=work_root,
        executables=executables,
        runtime_roots=runtime_roots,
        additional_read_roots=additional_read_roots,
    )
    deadline = time.monotonic() + float(request["timeout_seconds"])
    input_text = str(request.get("input") or "")
    max_output = int(request.get("max_output_bytes", _MAX_OUTPUT_BYTES))
    stdout: list[str] = []
    stderr: list[str] = []
    measurements = {"peak_memory_bytes": 0, "peak_workdir_bytes": 0, "elapsed_ms": 0}
    for index, command in enumerate(commands):
        result = _run_command(
            command,
            policy=policy,
            environment={str(k): str(v) for k, v in request.get("environment", {}).items()},
            cwd=work_root,
            input_text=input_text if index == len(commands) - 1 else "",
            deadline=deadline,
            output_limit=max_output - sum(len(part.encode("utf-8")) for part in stdout + stderr),
        )
        stdout.append(result.get("stdout", ""))
        stderr.append(result.get("stderr", ""))
        for key in measurements:
            value = int(result.get(key) or 0)
            measurements[key] = (
                measurements[key] + value
                if key == "elapsed_ms"
                else max(measurements[key], value)
            )
        if result.get("reason"):
            return {**measurements, **result, "failed_stage": index, "stdout": "".join(stdout), "stderr": "".join(stderr)}
        if result.get("returncode") != 0:
            return {**measurements, **result, "failed_stage": index, "stdout": "".join(stdout), "stderr": "".join(stderr)}
    return {
        **measurements,
        "reason": None,
        "returncode": 0,
        "stdout": "".join(stdout),
        "stderr": "".join(stderr),
    }


def main() -> int:
    signal.signal(signal.SIGTERM, _terminate_active)
    signal.signal(signal.SIGINT, _terminate_active)
    request = json.load(sys.stdin)
    try:
        result = execute(request)
        sys.stdout.write(json.dumps(result, ensure_ascii=False))
        return 0
    except BaseException as exc:  # supervisor failures must fail closed
        sys.stderr.write(f"sandbox supervisor error: {type(exc).__name__}: {exc}\n")
        return 70


if __name__ == "__main__":
    raise SystemExit(main())
