"""Fail-closed Docker runner for teacher-approved, question-scoped tests."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from app.core.config import settings
from app.models.submission import Submission
from app.services.code_testing import (
    MAX_CASE_OUTPUT_BYTES,
    MAX_CASE_TIMEOUT_SECONDS,
    MAX_SUBMISSION_RUNTIME_SECONDS,
)
from app.services.submission_zip import (
    MAX_CODE_INPUT_FILE_BYTES,
    MAX_CODE_INPUT_FILES,
    MAX_CODE_INPUT_TOTAL_BYTES,
)

RUNNER_IMAGES = {
    "python": "ai-marking/code-runner:python-3.12.13-v1",
    "r": "ai-marking/code-runner:r-4.4.3-v1",
    "java": "ai-marking/code-runner:java-21.0.7-v1",
    "notebook_python": "ai-marking/code-runner:notebook-python-3.12.13-v1",
    "notebook_r": "ai-marking/code-runner:notebook-r-4.4.3-v1",
}


class CodeRunnerInfrastructureError(RuntimeError):
    """Docker/runner setup failed; callers must fail the grading run."""


class CodeRunnerCancelledError(RuntimeError):
    """The grading run was cancelled while a container was active."""


def docker_readiness() -> tuple[bool, str]:
    """Return a startup-friendly Docker Engine and image readiness message."""
    if shutil.which("docker") is None:
        return False, "未找到 Docker CLI；代码批改需要运行中的 Docker Engine。"
    try:
        result = subprocess.run(
            ["docker", "info", "--format", "{{.ServerVersion}}"],
            capture_output=True,
            text=True,
            timeout=4,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"Docker Engine 不可用：{type(exc).__name__}。"
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()[:240]
        return False, f"Docker Engine 不可用：{detail or 'docker info 失败'}"
    missing: list[str] = []
    for image in RUNNER_IMAGES.values():
        try:
            image_result = subprocess.run(
                ["docker", "image", "inspect", image, "--format", "{{.Id}}"],
                capture_output=True,
                text=True,
                timeout=4,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            missing.append(image)
            continue
        if image_result.returncode:
            missing.append(image)
    if missing:
        return False, "Docker Engine 可用，但缺少运行镜像：" + ", ".join(missing)
    return True, "Docker Engine 与代码运行镜像已就绪。"


def _runner_language(entrypoint: str, source_text: str) -> str:
    suffix = Path(entrypoint).suffix.casefold()
    if suffix == ".py":
        return "python"
    if suffix == ".r":
        return "r"
    if suffix == ".ipynb":
        try:
            notebook = json.loads(source_text)
            kernel = str(
                (notebook.get("metadata") or {}).get("kernelspec", {}).get("name") or ""
            ).casefold()
        except (TypeError, ValueError, AttributeError):
            return "notebook_invalid"
        if kernel in {"python3", "python"}:
            return "notebook_python"
        if kernel in {"ir", "ir_kernel", "r"}:
            return "notebook_r"
        return "notebook_unsupported:" + (kernel or "missing")
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
    """Run the approved suite in per-test, per-question isolated containers."""
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
        language = _runner_language(
            entrypoints[0].original_filename, entrypoints[0].source_text or ""
        )
        languages[question_number] = language
    required_images = {
        languages[qnum]
        for qnum, language in languages.items()
        if any(
            int(case.get("question_number") or 0) == qnum
            and _case_matches_language(case, language)
            for case in suite_tests
        )
    }
    if required_images:
        _require_docker_and_images(required_images)

    total_deadline = time.monotonic() + MAX_SUBMISSION_RUNTIME_SECONDS
    results: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix=f"ai-marking-code-{run_id}-") as temporary:
        input_root = Path(temporary)
        shared_data = _stage_data_inputs(input_root, submission, should_cancel)
        staged: dict[int, tuple[Path, Any, str, list[tuple[str, str]]]] = {}
        for question_number, code_files in sorted(grouped.items()):
            entrypoints = [item for item in code_files if item.entrypoint]
            if len(entrypoints) != 1:
                raise CodeRunnerInfrastructureError(
                    f"第 {question_number} 题的代码入口文件数量无效"
                )
            entrypoint = entrypoints[0]
            language = languages[question_number]
            scratch = input_root / f"q{question_number}"
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
                    _result_row(question_number, index, case, "missing_code", "未提交此小题代码")
                    for index, case in question_cases
                )
                continue
            scratch, entrypoint, language, data_bindings = source
            if language == "notebook_invalid":
                results.extend(
                    _result_row(
                        question_number,
                        index,
                        case,
                        "compile_error",
                        "Notebook 文件不是有效的 JSON 或缺少有效 metadata",
                    )
                    for index, case in question_cases
                )
                continue
            matching = [
                (index, case)
                for index, case in question_cases
                if _case_matches_language(case, language)
            ]
            if not matching and language.startswith("notebook_"):
                results.extend(
                    _result_row(
                        question_number,
                        index,
                        case,
                        "kernel_mismatch",
                        f"测试集的 Notebook kernel 与提交文件 metadata 不匹配（提交 kernel: {language}）",
                    )
                    for index, case in question_cases
                )
                continue
            if not matching:
                raise CodeRunnerInfrastructureError(
                    f"第 {question_number} 题的测试集没有适用于 {language} 的测试项"
                )
            if len(matching) + len(results) > 20:
                raise CodeRunnerInfrastructureError("批改测试项总数超过 20 项上限")
            for offset, (index, case) in enumerate(matching):
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
                )
                results.append(test_result)
        if should_cancel and should_cancel():
            raise CodeRunnerCancelledError("批改运行已取消")
        return results


def _require_docker_and_images(languages: set[str]) -> None:
    if shutil.which("docker") is None:
        raise CodeRunnerInfrastructureError("Docker CLI 不可用；没有宿主机执行回退")
    try:
        probe = subprocess.run(
            ["docker", "info", "--format", "{{.ServerVersion}}"],
            capture_output=True,
            text=True,
            timeout=4,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CodeRunnerInfrastructureError(
            f"Docker Engine 不可用：{type(exc).__name__}"
        ) from exc
    if probe.returncode:
        raise CodeRunnerInfrastructureError(
            "Docker Engine 不可用：" + ((probe.stderr or probe.stdout).strip()[:400])
        )
    for language in languages:
        image = RUNNER_IMAGES.get(language)
        if image is None:
            raise CodeRunnerInfrastructureError(f"Notebook kernel 不受支持：{language}")
        try:
            result = subprocess.run(
                ["docker", "image", "inspect", image, "--format", "{{.Id}}"],
                capture_output=True,
                text=True,
                timeout=4,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise CodeRunnerInfrastructureError(
                f"检查代码运行镜像失败：{type(exc).__name__}"
            ) from exc
        if result.returncode:
            raise CodeRunnerInfrastructureError(
                f"代码运行镜像缺失：{image}；请按 docs/architecture/code-testing.md 构建镜像"
            )


def _case_matches_language(case: dict[str, Any], language: str) -> bool:
    mode = case.get("mode")
    target = case.get("language")
    if language.startswith("notebook_"):
        return mode == "notebook_assertion" and target == language
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
) -> dict[str, Any]:
    return {
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
) -> dict[str, Any]:
    image = RUNNER_IMAGES[language]
    manifest = {
        "language": language,
        "entrypoint": entrypoint,
        "test": case,
        "data_aliases": [
            {"filename": filename, "asset": asset_name}
            for asset_name, filename in data_bindings
        ],
        "max_output_bytes": MAX_CASE_OUTPUT_BYTES,
        "case_timeout_seconds": min(MAX_CASE_TIMEOUT_SECONDS, timeout_seconds),
    }
    name = f"aim-code-{run_id}-{question_number}-{test_index}-{uuid.uuid4().hex[:8]}"
    mounts = ["--mount", f"type=bind,src={input_root},dst=/input,readonly"]
    command = [
        "docker", "run", "--rm", "-i", "--name", name,
        "--network", "none",
        "--cpus", "1", "--memory", "1g", "--memory-swap", "1g",
        "--pids-limit", "64", "--ulimit", "fsize=268435456:268435456",
        "--read-only", "--user", "65534:65534",
        "--security-opt", "no-new-privileges", "--cap-drop", "ALL",
        "--tmpfs", "/tmp:rw,noexec,nosuid,size=268435456,uid=65534,gid=65534,mode=0700",
        *mounts,
        image, "python3", "/runner/case_runner.py",
    ]
    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
    except OSError as exc:
        raise CodeRunnerInfrastructureError(f"启动 Docker 运行器失败：{exc}") from exc
    assert process.stdin is not None
    try:
        process.stdin.write(json.dumps(manifest, ensure_ascii=False))
    except BrokenPipeError:
        # docker may have exited before attaching stdin; collect its infrastructure error below.
        pass
    deadline = time.monotonic() + timeout_seconds
    try:
        while True:
            if should_cancel and should_cancel():
                container_stopped = _kill_container(name)
                if process.poll() is None:
                    process.kill()
                process.communicate()
                if not container_stopped:
                    raise CodeRunnerInfrastructureError(
                        "取消批改时无法确认 Docker 容器已清理"
                    )
                raise CodeRunnerCancelledError("批改运行已取消")
            if time.monotonic() >= deadline:
                container_stopped = _kill_container(name)
                if process.poll() is None:
                    process.kill()
                out, err = process.communicate()
                if not container_stopped:
                    raise CodeRunnerInfrastructureError(
                        "单项超时后无法确认 Docker 容器已清理"
                    )
                return _result_row(
                    question_number, test_index, case, "timeout",
                    _bounded_text("容器内测试运行达到单项 10 秒上限\n" + (err or "")),
                )
            try:
                stdout, stderr = process.communicate(
                    timeout=min(0.1, max(0.01, deadline - time.monotonic()))
                )
                break
            except subprocess.TimeoutExpired:
                continue
    except CodeRunnerCancelledError:
        raise
    except BaseException:
        _kill_container(name)
        if process.poll() is None:
            process.kill()
        process.communicate()
        raise
    if process.returncode != 0:
        if process.returncode in {-9, 137}:
            return _result_row(
                question_number,
                test_index,
                case,
                "runtime_error",
                "代码运行容器因达到内存或进程资源限制而终止",
            )
        detail = (stderr or stdout or "docker run 失败").strip()
        raise CodeRunnerInfrastructureError(
            f"Docker 运行器退出码 {process.returncode}：{detail[:500]}"
        )
    try:
        result = json.loads(stdout)
    except (TypeError, ValueError) as exc:
        raise CodeRunnerInfrastructureError("容器未返回有效的测试结果") from exc
    status = str(result.get("status") or "")
    if status not in {"passed", "failed", "compile_error", "runtime_error", "timeout", "kernel_mismatch"}:
        raise CodeRunnerInfrastructureError("容器返回未知测试状态")
    return _result_row(
        question_number,
        test_index,
        case,
        status,
        _bounded_text(str(result.get("output") or "")),
    )


def _kill_container(name: str) -> bool:
    try:
        result = subprocess.run(
            ["docker", "rm", "--force", name],
            capture_output=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    if result.returncode == 0:
        return True
    detail = (result.stderr or result.stdout or "").casefold()
    return "no such container" in detail


def _bounded_text(value: str) -> str:
    return value.encode("utf-8", errors="replace")[:MAX_CASE_OUTPUT_BYTES].decode(
        "utf-8", errors="ignore"
    )

