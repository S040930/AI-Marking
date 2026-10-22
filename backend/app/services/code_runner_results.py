"""Build stable, size-bounded test result records."""

from __future__ import annotations

from typing import Any

from app.services.code_testing import MAX_CASE_OUTPUT_BYTES
from app.services.sandbox import SANDBOX_POLICY_VERSION, SEATBELT_PARTIAL


def build_result_row(
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


def bounded_text(value: str) -> str:
    return value.encode("utf-8", errors="replace")[:MAX_CASE_OUTPUT_BYTES].decode(
        "utf-8", errors="ignore"
    )
