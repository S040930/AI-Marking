#!/usr/bin/env python3
"""Container-side test harness. Student code runs only as a child process/kernel."""

from __future__ import annotations

import json
import os
import re
import selectors
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

MAX_OUTPUT = 16 * 1024


def bounded(value: bytes) -> str:
    return value[:MAX_OUTPUT].decode("utf-8", errors="replace")


def run_process(command: list[str], input_text: str, deadline: float) -> tuple[str, str, int | None]:
    """Read output incrementally; stop runaway output and process trees."""
    process = subprocess.Popen(
        command,
        cwd="/tmp/work",
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    assert process.stdin is not None
    try:
        process.stdin.write(input_text.encode("utf-8"))
    except BrokenPipeError:
        # Exiting before reading stdin is a student runtime outcome.
        pass
    finally:
        try:
            process.stdin.close()
        except BrokenPipeError:
            pass
    selector = selectors.DefaultSelector()
    assert process.stdout is not None and process.stderr is not None
    os.set_blocking(process.stdout.fileno(), False)
    os.set_blocking(process.stderr.fileno(), False)
    selector.register(process.stdout, selectors.EVENT_READ, "stdout")
    selector.register(process.stderr, selectors.EVENT_READ, "stderr")
    outputs = {"stdout": bytearray(), "stderr": bytearray()}
    total = 0
    exceeded = False
    try:
        while selector.get_map() or process.poll() is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
                return "", "", None
            for key, _ in selector.select(min(0.05, remaining)):
                try:
                    chunk = os.read(key.fileobj.fileno(), 4096)
                except BlockingIOError:
                    continue
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                total += len(chunk)
                keep = max(0, MAX_OUTPUT - len(outputs[key.data]))
                if keep:
                    outputs[key.data].extend(chunk[:keep])
                if total > MAX_OUTPUT:
                    exceeded = True
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    process.wait()
                    break
            if exceeded:
                break
        if process.poll() is None:
            process.wait()
        stdout = bytes(outputs["stdout"])
        stderr = bytes(outputs["stderr"])
        if exceeded:
            return bounded(stdout), bounded(stderr) + "\n输出超过 16 KiB，进程已终止", -2
        return bounded(stdout), bounded(stderr), process.returncode
    finally:
        selector.close()


def prepare_work(
    manifest: dict,
    *,
    input_root: Path = Path("/input"),
    work_root: Path = Path("/tmp/work"),
) -> Path:
    work = work_root
    if work.exists():
        shutil.rmtree(work)
    work.mkdir()
    for alias in manifest.get("data_aliases", []):
        filename = Path(str(alias.get("filename") or "")).name
        asset = input_root / "assets" / Path(str(alias.get("asset") or "")).name
        if filename and asset.is_file():
            link = work / filename
            if not link.exists():
                link.symlink_to(asset)
    return work


def io_case(manifest: dict, work: Path) -> dict:
    language = manifest["language"]
    entrypoint = str(Path(manifest["entrypoint"]).name)
    testcase = manifest["test"]
    deadline = time.monotonic() + float(manifest["case_timeout_seconds"])
    script = str(Path("/input") / entrypoint)
    if language == "python":
        try:
            import ast

            ast.parse(Path(script).read_text(encoding="utf-8"), filename=entrypoint)
        except (SyntaxError, UnicodeDecodeError) as exc:
            return {"status": "compile_error", "output": f"{type(exc).__name__}: {exc}"}
        command = [sys.executable, script]
    elif language == "r":
        parse_stdout, parse_stderr, parse_status = run_process(
            ["Rscript", "-e", "parse(file=commandArgs(TRUE)[1])", script], "", deadline
        )
        if parse_status is None:
            return {"status": "timeout", "output": "R 语法检查超过单项时限"}
        if parse_status != 0:
            return {"status": "compile_error", "output": (parse_stdout + parse_stderr).strip()}
        command = ["Rscript", script]
    elif language == "java":
        java_files = sorted(str(path) for path in Path("/input").glob("*.java"))
        if not java_files:
            return {"status": "compile_error", "output": "没有可编译的 Java 源文件"}
        classes = Path("/tmp/classes")
        classes.mkdir(exist_ok=True)
        compile_stdout, compile_stderr, compile_status = run_process(
            ["javac", "-d", str(classes), *java_files], "", deadline
        )
        if compile_status is None:
            return {"status": "timeout", "output": "Java 编译超过单项时限"}
        if compile_status != 0:
            return {"status": "compile_error", "output": (compile_stdout + compile_stderr).strip()}
        source = Path(script).read_text(encoding="utf-8", errors="replace")
        package_match = re.search(r"^\s*package\s+([\w.]+)\s*;", source, re.MULTILINE)
        class_match = re.search(r"\bpublic\s+class\s+([A-Za-z_$][\w$]*)", source)
        package_class_match = re.search(r"\bclass\s+([A-Za-z_$][\w$]*)", source)
        main_class = (
            class_match.group(1)
            if class_match is not None
            else package_class_match.group(1)
            if package_class_match is not None
            else Path(entrypoint).stem
        )
        if package_match:
            main_class = package_match.group(1) + "." + main_class
        command = ["java", "-cp", str(classes), main_class]
    else:
        return {"status": "kernel_mismatch", "output": "不支持的运行语言"}
    stdout, stderr, returncode = run_process(
        command, str(testcase.get("input") or ""), deadline
    )
    if returncode is None:
        return {"status": "timeout", "output": "程序运行超过单项时限"}
    if returncode == -2:
        return {"status": "failed", "output": (stdout + stderr).strip()}
    if returncode != 0:
        return {"status": "runtime_error", "output": (stdout + stderr).strip()}
    expected = str(testcase.get("expected_stdout") or "").replace("\r\n", "\n").strip()
    actual = stdout.replace("\r\n", "\n").strip()
    if actual != expected:
        return {
            "status": "failed",
            "output": f"预期输出：{expected[:4000]}\n实际输出：{actual[:4000]}\n{stderr}",
        }
    return {"status": "passed", "output": "标准输出与预期一致"}


def notebook_case(manifest: dict, work: Path) -> dict:
    testcase = manifest["test"]
    notebook_path = Path("/input") / Path(manifest["entrypoint"]).name
    import nbformat
    from nbclient import NotebookClient
    from nbclient.exceptions import CellExecutionError

    try:
        notebook = nbformat.read(notebook_path, as_version=4)
    except Exception as exc:  # noqa: BLE001 - malformed student Notebook is evidence
        return {"status": "compile_error", "output": f"{type(exc).__name__}: {exc}"}
    failed_cell_index = None

    def capture_cell_error(*, cell_index: int, **_kwargs) -> None:
        nonlocal failed_cell_index
        failed_cell_index = cell_index

    try:
        kernelspec = notebook.metadata.get("kernelspec", {})
        actual = str(kernelspec.get("name") or "").casefold()
        expected = manifest["language"]
        kernel_name = notebook_kernel_name(actual)
        actual_language = (
            "notebook_python" if actual in {"python", "python3"}
            else "notebook_r" if actual in {"ir", "ir_kernel", "r"}
            else "unsupported"
        )
        if actual_language != expected:
            return {
                "status": "kernel_mismatch",
                "output": f"测试要求 {expected}，Notebook metadata.kernel={actual or '缺失'}",
            }
        notebook.cells.append(nbformat.v4.new_code_cell(str(testcase["assertion"])))
        timeout = max(1, int(float(manifest["case_timeout_seconds"])))
        NotebookClient(
            notebook,
            timeout=timeout,
            kernel_name=kernel_name,
            resources={"metadata": {"path": "/tmp/work"}},
            allow_errors=False,
            record_timing=False,
            on_cell_error=capture_cell_error,
        ).execute()
    except CellExecutionError as exc:
        ename = str(getattr(exc, "ename", ""))
        evalue = str(getattr(exc, "evalue", ""))
        assertion_failed = failed_cell_index == len(notebook.cells) - 1
        status = notebook_error_status(ename, evalue, assertion_failed=assertion_failed)
        return {"status": status, "output": f"{ename or type(exc).__name__}: {exc}"}
    except Exception as exc:  # noqa: BLE001 - student kernel failures are evidence
        status = "timeout" if "timeout" in type(exc).__name__.casefold() else "runtime_error"
        output = f"{type(exc).__name__}: {exc}"
        return {"status": status, "output": output}
    return {"status": "passed", "output": "Notebook 执行及断言通过"}


def notebook_error_status(
    ename: str, evalue: str = "", *, assertion_failed: bool = False
) -> str:
    """Map Notebook cell errors to student-facing test evidence categories."""
    if ename == "SyntaxError" or (
        ename == "Error"
        and any(marker in evalue.casefold() for marker in ("unexpected", "parse error"))
    ):
        return "compile_error"
    if ename == "AssertionError" or assertion_failed:
        return "failed"
    if ename in {"TimeoutError", "CellTimeoutError"}:
        return "timeout"
    return "runtime_error"


def notebook_kernel_name(metadata_name: str) -> str:
    """Resolve supported metadata aliases to the installed kernel spec names."""
    if metadata_name in {"python", "python3"}:
        return "python3"
    if metadata_name in {"ir", "ir_kernel", "r"}:
        return "ir"
    return metadata_name


def main() -> int:
    try:
        manifest = json.load(sys.stdin)
        work = prepare_work(manifest)
        if manifest["test"]["mode"] == "notebook_assertion":
            result = notebook_case(manifest, work)
        else:
            result = io_case(manifest, work)
        output = str(result.get("output") or "").encode("utf-8", errors="replace")[:MAX_OUTPUT]
        sys.stdout.write(json.dumps({"status": result["status"], "output": output.decode("utf-8", errors="ignore")}, ensure_ascii=False))
        return 0
    except Exception as exc:  # noqa: BLE001 - malformed harness setup is infrastructure
        sys.stderr.write(f"runner error: {type(exc).__name__}: {exc}\n")
        return 70


if __name__ == "__main__":
    raise SystemExit(main())
