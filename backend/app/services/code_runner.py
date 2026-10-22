"""Coordinate safe execution of approved submission test cases."""

from __future__ import annotations

import tempfile
import time
from pathlib import Path
from typing import Any, Callable

from app.models.submission import Submission
from app.services import (
    code_runner_case as case_helpers,
)
from app.services import (
    code_runner_inputs as input_helpers,
)
from app.services import (
    code_runner_results as result_helpers,
)
from app.services import (
    code_runner_runtime as runtime_helpers,
)
from app.services.code_runner_errors import (
    CodeRunnerCancelledError,
    CodeRunnerInfrastructureError,
)
from app.services.code_testing import (
    MAX_CASE_TIMEOUT_SECONDS,
    MAX_SUBMISSION_RUNTIME_SECONDS,
)


def runner_language(entrypoint: str) -> str:
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
            result_helpers.build_result_row(
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
    entrypoints: dict[int, Any] = {}
    for question_number, code_files in grouped.items():
        candidates = [item for item in code_files if item.entrypoint]
        if len(candidates) != 1:
            raise CodeRunnerInfrastructureError(
                f"第 {question_number} 题的代码入口文件数量无效"
            )
        entrypoints[question_number] = candidates[0]
        languages[question_number] = runner_language(candidates[0].original_filename)
    has_executable_tests = any(
        str(case.get("mode") or "program_io") == "program_io"
        and languages.get(int(case.get("question_number") or 0)) not in (None, "notebook")
        for case in suite_tests
    )
    runtimes = None
    if has_executable_tests:
        ready, detail = runtime_helpers.sandbox_readiness()
        if not ready:
            raise CodeRunnerInfrastructureError(detail)
        runtimes = runtime_helpers.load_runtimes()

    total_deadline = time.monotonic() + MAX_SUBMISSION_RUNTIME_SECONDS
    results: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix=f"ai-marking-code-{run_id}-") as temporary:
        submission_root = Path(temporary)
        shared_data = input_helpers.stage_data_inputs(submission_root, submission, should_cancel)
        staged: dict[int, tuple[Path, Any, str, list[tuple[str, str]]]] = {}
        for question_number, code_files in sorted(grouped.items()):
            entrypoint = entrypoints[question_number]
            language = languages[question_number]
            scratch = submission_root / f"q{question_number}"
            scratch.mkdir()
            data_bindings = input_helpers.stage_code_inputs(scratch, code_files, shared_data, should_cancel)
            staged[question_number] = (scratch, entrypoint, language, data_bindings)

        cases_by_question: dict[int, list[tuple[int, dict[str, Any]]]] = {}
        for index, case in enumerate(suite_tests, start=1):
            cases_by_question.setdefault(int(case.get("question_number") or 0), []).append((index, case))

        for question_number, question_cases in sorted(cases_by_question.items()):
            source = staged.get(question_number)
            if source is None:
                results.extend(
                    result_helpers.build_result_row(
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
                    result_helpers.build_result_row(
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
                result_helpers.build_result_row(
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
                and case_matches_language(case, language)
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
                        result_helpers.build_result_row(
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
                test_result = case_helpers.run_case(
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


def case_matches_language(case: dict[str, Any], language: str) -> bool:
    mode = case.get("mode")
    target = case.get("language")
    return mode == "program_io" and target in ("any", language)
