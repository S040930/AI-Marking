"""只读检查工具：让子 agent 按需读取评分包工件，而不是把 200k 全文塞进上下文。

所有工具都不触碰数据库、不写任何状态；写权限由 ``tools/grading.py`` 的
写工具与图节点白名单共同控制。
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import Any

from app.agent.models import ToolResult, ToolSpec
from app.agent.tools import ToolRegistry
from app.services.code_testing import evidence_quote

MAX_SLICE_CHARS = 8_000
MAX_SEARCH_RESULTS = 20
MAX_CODE_LINES = 200


@dataclass(frozen=True)
class InspectionContext:
    """单个 run 的只读视图；``teacher`` 为检查点答复镜像。"""

    artifact: dict[str, Any]
    teacher: dict[str, Any] | None = None


def _section(ctx: InspectionContext, name: str) -> dict[str, Any] | None:
    for section in ctx.artifact.get("sections", []):
        if isinstance(section, dict) and section.get("name") == name:
            return section
    return None


def _section_names(ctx: InspectionContext) -> list[str]:
    return [
        str(section["name"])
        for section in ctx.artifact.get("sections", [])
        if isinstance(section, dict) and section.get("name")
    ]


def _read_slice(ctx: InspectionContext, args: dict[str, Any]) -> ToolResult:
    name = str(args.get("section") or "")
    section = _section(ctx, name)
    if section is None:
        return ToolResult(
            ok=False,
            error=f"未知 section {name!r}；可用: {', '.join(_section_names(ctx))}",
        )
    text = str(section.get("text") or "")
    try:
        offset = int(args.get("offset") or 0)
        length = int(args.get("length") or MAX_SLICE_CHARS)
    except (TypeError, ValueError):
        return ToolResult(ok=False, error="offset/length 必须是整数")
    offset = max(0, offset)
    length = min(max(1, length), MAX_SLICE_CHARS)
    end = min(len(text), offset + length)
    chunk = text[offset:end]
    complete = end >= len(text)
    return ToolResult(
        ok=True,
        output={
            "section": name,
            "offset": offset,
            "end": end,
            "total_chars": len(text),
            "complete": complete,
            "next_offset": None if complete else end,
            "text": chunk,
        },
    )


def _search(ctx: InspectionContext, args: dict[str, Any]) -> ToolResult:
    query = str(args.get("query") or "").strip()
    if not query:
        return ToolResult(ok=False, error="query 不能为空")
    try:
        limit = int(args.get("max_results") or 10)
    except (TypeError, ValueError):
        return ToolResult(ok=False, error="max_results 必须是整数")
    limit = min(max(1, limit), MAX_SEARCH_RESULTS)
    needle = query.casefold()
    matches: list[dict[str, Any]] = []
    for section in ctx.artifact.get("sections", []):
        if not isinstance(section, dict) or not section.get("name"):
            continue
        text = str(section.get("text") or "")
        folded = text.casefold()
        start = 0
        while len(matches) < limit:
            index = folded.find(needle, start)
            if index < 0:
                break
            line = text.count("\n", 0, index) + 1
            excerpt_start = max(0, index - 80)
            excerpt_end = min(len(text), index + len(query) + 120)
            matches.append(
                {
                    "section": section["name"],
                    "offset": index,
                    "line": line,
                    "excerpt": text[excerpt_start:excerpt_end],
                }
            )
            start = index + max(1, len(needle))
    return ToolResult(ok=True, output={"query": query, "matches": matches})


def _code_file(ctx: InspectionContext, filename: str) -> dict[str, Any] | None:
    key = unicodedata.normalize("NFKC", filename).casefold()
    for item in ctx.artifact.get("code_files", []):
        if not isinstance(item, dict):
            continue
        candidate = unicodedata.normalize(
            "NFKC", str(item.get("filename") or "")
        ).casefold()
        if candidate == key:
            return item
    return None


def _read_code_lines(ctx: InspectionContext, args: dict[str, Any]) -> ToolResult:
    filename = str(args.get("filename") or "")
    item = _code_file(ctx, filename)
    if item is None:
        available = "、".join(
            str(entry.get("filename"))
            for entry in ctx.artifact.get("code_files", [])
            if isinstance(entry, dict)
        )
        return ToolResult(
            ok=False, error=f"文件 {filename!r} 不在作业代码中；可用: {available}"
        )
    lines = str(item.get("source_text") or "").splitlines()
    try:
        start = int(args.get("start_line") or 1)
        end = int(args.get("end_line") or min(len(lines), start + 79))
    except (TypeError, ValueError):
        return ToolResult(ok=False, error="start_line/end_line 必须是整数")
    if start < 1:
        return ToolResult(ok=False, error="start_line 从 1 开始")
    if end < start:
        return ToolResult(ok=False, error="end_line 不能小于 start_line")
    if end - start + 1 > MAX_CODE_LINES:
        return ToolResult(
            ok=False, error=f"单次最多读取 {MAX_CODE_LINES} 行，请分段读取"
        )
    end = min(end, len(lines))
    if start > len(lines):
        return ToolResult(
            ok=False, error=f"start_line 超出文件行数（共 {len(lines)} 行）"
        )
    payload = [
        {"line": number, "text": lines[number - 1]}
        for number in range(start, end + 1)
    ]
    return ToolResult(
        ok=True,
        output={
            "filename": item.get("filename"),
            "total_lines": len(lines),
            "start_line": start,
            "end_line": end,
            "lines": payload,
        },
    )


def _read_rubric_item(ctx: InspectionContext, args: dict[str, Any]) -> ToolResult:
    item_id = str(args.get("rubric_item_id") or "")
    rubric = ctx.artifact.get("rubric") or {}
    for item in rubric.get("items", []):
        if isinstance(item, dict) and item.get("rubric_item_id") == item_id:
            return ToolResult(ok=True, output=item)
    ids = "、".join(
        str(item.get("rubric_item_id"))
        for item in rubric.get("items", [])
        if isinstance(item, dict)
    )
    return ToolResult(
        ok=False, error=f"未知 rubric_item_id {item_id!r}；可用: {ids}"
    )


def _read_question_ocr(ctx: InspectionContext, args: dict[str, Any]) -> ToolResult:
    section = _section(ctx, "question")
    if section is None:
        return ToolResult(ok=False, error="评分包没有 question 段")
    text = str(section.get("text") or "")
    try:
        offset = max(0, int(args.get("offset") or 0))
        length = min(max(1, int(args.get("length") or MAX_SLICE_CHARS)), MAX_SLICE_CHARS)
    except (TypeError, ValueError):
        return ToolResult(ok=False, error="offset/length 必须是整数")
    end = min(len(text), offset + length)
    return ToolResult(
        ok=True,
        output={
            "offset": offset,
            "end": end,
            "total_chars": len(text),
            "complete": end >= len(text),
            "next_offset": None if end >= len(text) else end,
            "text": text[offset:end],
        },
    )


def _read_teacher_answer(ctx: InspectionContext, args: dict[str, Any]) -> ToolResult:
    if not ctx.teacher:
        return ToolResult(ok=True, output={"available": False})
    return ToolResult(
        ok=True,
        output={
            "available": True,
            "verdict": ctx.teacher.get("verdict"),
            "note": ctx.teacher.get("note"),
        },
    )


def _read_code_test_result(ctx: InspectionContext, args: dict[str, Any]) -> ToolResult:
    payload = ctx.artifact.get("test_results") or {}
    test_id = str(args.get("test_case_id") or "")
    for result in payload.get("results", []):
        if not isinstance(result, dict) or result.get("id") != test_id:
            continue
        return ToolResult(
            ok=True,
            output={
                "test_case_id": test_id,
                "suite_version": payload.get("version"),
                "question_number": result.get("question_number"),
                "title": result.get("title"),
                "status": result.get("status"),
                "output": result.get("output"),
                "isolation": result.get("isolation"),
                "quote": evidence_quote(result),
            },
        )
    available = [
        {"test_case_id": result.get("id"), "title": result.get("title"), "status": result.get("status")}
        for result in payload.get("results", [])
        if isinstance(result, dict)
    ]
    return ToolResult(
        ok=False,
        error=f"测试结果 {test_id!r} 不属于当前 run；可用结果: {available}",
    )


def build_inspection_tools(ctx: InspectionContext) -> ToolRegistry:
    """构建只读工具集；每个 run 一份，handler 闭包持有 artifact。"""
    registry = ToolRegistry()
    registry.register(_LIST_SECTIONS_SPEC, lambda args: _list_sections(ctx))
    registry.register(_READ_SLICE_SPEC, lambda args: _read_slice(ctx, args))
    registry.register(_SEARCH_SPEC, lambda args: _search(ctx, args))
    registry.register(_READ_CODE_SPEC, lambda args: _read_code_lines(ctx, args))
    registry.register(_READ_RUBRIC_SPEC, lambda args: _read_rubric_item(ctx, args))
    registry.register(_READ_QUESTION_SPEC, lambda args: _read_question_ocr(ctx, args))
    registry.register(_READ_TEACHER_SPEC, lambda args: _read_teacher_answer(ctx, args))
    registry.register(_READ_CODE_TEST_RESULT_SPEC, lambda args: _read_code_test_result(ctx, args))
    return registry


def _list_sections(ctx: InspectionContext) -> ToolResult:
    return ToolResult(
        ok=True,
        output=[
            {
                "name": section.get("name"),
                "chars": section.get("chars", len(str(section.get("text") or ""))),
            }
            for section in ctx.artifact.get("sections", [])
            if isinstance(section, dict)
        ],
    )


_LIST_SECTIONS_SPEC = ToolSpec(
    name="list_sections",
    description="列出评分包的段（question/submission/source）及字符数。",
    parameters={"type": "object", "properties": {}, "additionalProperties": False},
)

_READ_SLICE_SPEC = ToolSpec(
    name="read_package_slice",
    description=(
        "按段读取评分包正文，单次最多 8000 字；返回 next_offset 时继续读取。"
        "用于定位报告原文或代码段，不要一次性读取全文。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "section": {"type": "string"},
            "offset": {"type": "integer", "minimum": 0},
            "length": {"type": "integer", "minimum": 1, "maximum": 8000},
        },
        "required": ["section"],
        "additionalProperties": False,
    },
)

_SEARCH_SPEC = ToolSpec(
    name="search_package",
    description=(
        "在评分包各段做字面量、大小写不敏感搜索，返回段名、偏移、行号与摘录；"
        "最多 20 条。用于快速定位证据原文。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "max_results": {"type": "integer", "minimum": 1, "maximum": 20},
        },
        "required": ["query"],
        "additionalProperties": False,
    },
)

_READ_CODE_SPEC = ToolSpec(
    name="read_code_lines",
    description=(
        "按文件名与真实行号读取代码（1 起，单次最多 200 行）。"
        "证据 source_line 的 line 必须使用本工具返回的行号。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "filename": {"type": "string"},
            "start_line": {"type": "integer", "minimum": 1},
            "end_line": {"type": "integer", "minimum": 1},
        },
        "required": ["filename"],
        "additionalProperties": False,
    },
)

_READ_RUBRIC_SPEC = ToolSpec(
    name="read_rubric_item",
    description="读取单个 rubric 项的 criterion/max_score/details/source_quote。",
    parameters={
        "type": "object",
        "properties": {"rubric_item_id": {"type": "string"}},
        "required": ["rubric_item_id"],
        "additionalProperties": False,
    },
)

_READ_QUESTION_SPEC = ToolSpec(
    name="read_question_ocr",
    description="读取题目 OCR 文本（用于 rubric 提取），单次最多 8000 字。",
    parameters={
        "type": "object",
        "properties": {
            "offset": {"type": "integer", "minimum": 0},
            "length": {"type": "integer", "minimum": 1, "maximum": 8000},
        },
        "additionalProperties": False,
    },
)

_READ_TEACHER_SPEC = ToolSpec(
    name="read_teacher_answer",
    description="读取教师检查点答复（verdict/note）；没有答复时 available=false。",
    parameters={"type": "object", "properties": {}, "additionalProperties": False},
)

_READ_CODE_TEST_RESULT_SPEC = ToolSpec(
    name="read_code_test_result",
    description=(
        "读取一个由服务端保存的真实测试结果。请用返回的 test_case_id、suite_version 与 quote "
        "构造 test_case 证据引用；不得改写 quote 或引用其他 run 的测试。"
    ),
    parameters={
        "type": "object",
        "properties": {"test_case_id": {"type": "string"}},
        "required": ["test_case_id"],
        "additionalProperties": False,
    },
)
