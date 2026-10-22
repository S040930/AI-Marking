"""批改证据定位与评分数据校验。"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from app.core.errors import ValidationError
from app.models.submission import Submission
from app.schemas.grading import GradingAssessmentRequest
from app.services.rubric import ResolvedRubric


def normalize_for_evidence(value: str) -> str:
    value = unicodedata.normalize("NFKC", value)
    return re.sub(r"\s+", " ", value).strip()


def _coerce_line_number(value: Any) -> int | None:
    """行号容忍:接受 int 或纯数字字符串;布尔与越界值返回 None。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _evidence_fuzzy(value: str) -> str:
    """OCR 容差引用键:NFKC + 空白归一 + 小写 + 去掉全部非字母数字字符。

    只用于报告引文与自由文本证据的“去噪后仍是服务端原文连续子串”回退,
    不用于代码证据(代码逐字匹配保持严格)。
    """
    return re.sub(r"[^\w]+", "", normalize_for_evidence(value).casefold())


def _report_quote_located(quote: str, ocr_text: str) -> bool:
    """报告引文定位:优先空白归一后的逐字子串;OCR 噪声时回退“去标点小写连续子串”。"""
    normalized = normalize_for_evidence(quote)
    if not normalized:
        return False
    if normalized in normalize_for_evidence(ocr_text):
        return True
    fuzzy_quote = _evidence_fuzzy(quote)
    return bool(fuzzy_quote) and fuzzy_quote in _evidence_fuzzy(ocr_text)


def format_evidence_lines(evidence_refs: list[dict]) -> list[str]:
    """把结构化证据投影成教师端可直接阅读的单行字符串。

    服务端持久化明细时调用,保证教师页(渲染自由文本 evidence 字段)
    无需感知 evidence_refs 结构即可看到证据原文。
    """
    lines: list[str] = []
    for ref in evidence_refs:
        if not isinstance(ref, dict):
            continue
        quote = str(ref.get("quote", "")).strip()
        if ref.get("type") == "source_line":
            line = ref.get("line")
            end_line = ref.get("end_line")
            span = (
                f"{line}" if not end_line or end_line == line else f"{line}-{end_line}"
            )
            lines.append(f"{ref.get('filename', '')}:{span} {quote}".strip())
        elif ref.get("type") == "report_quote":
            lines.append(quote)
        elif ref.get("type") == "test_case":
            lines.append(f"自动测试证据 {ref.get('test_case_id', '')}: {quote}".strip())
    return lines


def _report_quote_error(label: str, quote: str, ocr_text: str) -> str | None:
    if _report_quote_located(quote, ocr_text):
        return None
    return f"{label}: 报告证据无法定位"


def _source_line_error(
    label: str,
    ref: dict,
    quote: str,
    source_by_name: dict[str, list[str]],
    name_by_fold: dict[str, str],
    *,
    has_code: bool,
) -> str | None:
    if not has_code:
        return f"{label}: 无代码作业不允许 source_line 证据"

    filename = str(ref.get("filename", ""))
    key = unicodedata.normalize("NFKC", filename).casefold()
    lines = source_by_name.get(key)
    line_number = _coerce_line_number(ref.get("line"))
    end_line = _coerce_line_number(ref.get("end_line"))
    if lines is None:
        available = "、".join(sorted(name_by_fold.values()))
        return (
            f"{label}: 文件名 {filename or '<空>'} 不在作业代码中"
            f"(可用: {available})"
        )
    if not quote:
        return f"{label}: 代码证据缺少逐字引文 quote"
    if line_number is None or line_number < 1:
        return f"{label}: line 必须是正整数(收到 {ref.get('line')!r})"

    end = max(end_line or line_number, line_number)
    if end > len(lines):
        return f"{label}: 第 {end} 行超出 {name_by_fold[key]} 行数(共 {len(lines)} 行)"
    span = normalize_for_evidence("\n".join(lines[line_number - 1 : end]))
    if quote in span:
        return None

    # 引文不在引用区间:顺带给出全文件内实际行号,便于助手修正
    fuzzy_quote = _evidence_fuzzy(quote)
    found = next(
        (
            i + 1
            for i, line in enumerate(lines)
            if fuzzy_quote and fuzzy_quote in _evidence_fuzzy(line)
        ),
        None,
    )
    if found is not None and found != line_number:
        return (
            f"{label}: 引文实际位于 {name_by_fold[key]} 第 {found} 行"
            f"而非第 {line_number} 行,请修正 line 后重试"
        )
    return (
        f"{label}: 引文在 {name_by_fold[key]} 中无法定位"
        f"(第 {line_number}~{end} 行内未找到逐字子串)"
    )


def _test_case_error(
    label: str,
    ref: dict,
    quote: str,
    test_evidence: dict[str, dict] | None,
    *,
    has_code: bool,
) -> str | None:
    if not has_code:
        return f"{label}: 无代码作业不允许测试结果证据"

    test_id = str(ref.get("test_case_id") or "")
    available = (test_evidence or {}).get(test_id)
    if available is None:
        return f"{label}: 测试结果 {test_id or '<空>'} 不属于当前批改 run"
    try:
        suite_version = int(ref.get("suite_version"))
    except (TypeError, ValueError):
        suite_version = -1
    expected_quote = normalize_for_evidence(str(available.get("quote") or ""))
    if suite_version != int(available.get("suite_version") or 0):
        return f"{label}: 测试集版本与本次运行结果不匹配"
    if not quote or quote != expected_quote:
        return f"{label}: 测试证据 quote 必须与服务端结果一致"
    return None


def validate_evidence_refs(
    sub: Submission,
    payload: GradingAssessmentRequest,
    *,
    test_evidence: dict[str, dict] | None = None,
) -> None:
    """Validate structured evidence refs for every scored detail.

    放宽策略(保持“引文必须是服务端上下文的原文子串”这一核心可审计性):
    - ``source_line`` 行号接受 int 或数字字符串,支持 ``end_line`` 连续多行引用,
      文件名大小写不敏感;引文仍是该行/行区内逐字连续子串;仅限含代码作业。
    - ``report_quote`` 先做空白归一逐字匹配,OCR 噪声时回退“去标点小写连续子串”。
    - ``evidence_status="not_found"`` 只允许 0 分且不引用证据(评分句柄仅在
      context_complete 后签发,“完整上下文已读”由句柄语义保证)。
    - 得分大于 0 的明细必须提供至少一条可定位证据,含代码与纯报告作业同样强制。
    - 失败信息附带可操作线索(实际行号/行数/文件名清单),供评分助手修正后重试。
    """
    source_by_name: dict[str, list[str]] = {}
    name_by_fold: dict[str, str] = {}
    for item in sub.code_files:
        key = unicodedata.normalize("NFKC", item.original_filename).casefold()
        name_by_fold[key] = item.original_filename
        source_by_name[key] = (item.source_text or "").splitlines()
    has_code = bool(source_by_name)
    ocr_text = sub.ocr_text or ""
    invalid: list[str] = []
    for detail in payload.details:
        label = detail.rubric_item_id
        if detail.evidence_status == "not_found":
            if detail.score > 0:
                invalid.append(f"{label}: not_found 状态下得分必须为 0")
            if detail.evidence_refs:
                invalid.append(f"{label}: not_found 状态不得引用证据")
            continue
        if detail.score > 0 and not detail.evidence_refs:
            invalid.append(f"{label}: 得分大于 0 必须提供结构化证据")
            continue
        for ref in detail.evidence_refs:
            if not isinstance(ref, dict):
                invalid.append(f"{label}: evidence_refs 项格式错误")
                continue
            ref_type = ref.get("type")
            quote = normalize_for_evidence(str(ref.get("quote", "")))
            if ref_type == "report_quote":
                error = _report_quote_error(label, quote, ocr_text)
            elif ref_type == "source_line":
                error = _source_line_error(
                    label,
                    ref,
                    quote,
                    source_by_name,
                    name_by_fold,
                    has_code=has_code,
                )
            elif ref_type == "test_case":
                error = _test_case_error(
                    label, ref, quote, test_evidence, has_code=has_code
                )
            else:
                error = f"{label}: 不支持的 evidence_refs 类型 {ref_type!r}"
            if error is not None:
                invalid.append(error)
    if invalid:
        raise ValidationError(
            {
                "message": "评分证据无法在服务端上下文中定位",
                "evidence": invalid[:20],
            }
        )


def validate_assessment_payload(
    sub: Submission,
    payload: GradingAssessmentRequest,
    resolved: ResolvedRubric,
    *,
    test_evidence: dict[str, dict] | None = None,
) -> None:
    """纯预检：rubric 覆盖、单项满分、证据可定位；不写库。

    ``save_agent_assessment`` 与图的 ``verify_prepare`` 共用本函数，
    避免“预检通过、保存 422”的规则漂移。错误形态与保存路径一致
    （``ValidationError.detail`` 可直接回给模型修正）。
    """
    items_by_id = {item["rubric_item_id"]: item for item in resolved.items}
    seen: set[str] = set()
    for detail in payload.details:
        if detail.rubric_item_id not in items_by_id or detail.rubric_item_id in seen:
            raise ValidationError("评分项必须逐项引用当前 rubric 且不能重复")
        seen.add(detail.rubric_item_id)
    if seen != set(items_by_id):
        raise ValidationError("评分项未完整覆盖当前 rubric")
    for detail in payload.details:
        item = items_by_id[detail.rubric_item_id]
        if detail.score > item["max_score"] + 1e-9:
            raise ValidationError(f"{detail.rubric_item_id}: 单项得分不能超过该项满分")
    validate_evidence_refs(sub, payload, test_evidence=test_evidence)
