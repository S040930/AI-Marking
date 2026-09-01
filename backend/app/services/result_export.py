"""Generate auditable Excel grade reports from finalized submission results."""

from __future__ import annotations

import math
import re
import statistics
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import xlsxwriter

ExportLocale = Literal["zh-CN", "en-US"]

XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@dataclass
class CriterionStats:
    key: str
    name: str
    max_score: float
    count: int = 0
    total_rate: float = 0.0
    highest_rate: float = -math.inf
    lowest_rate: float = math.inf

    def add(self, rate: float) -> None:
        self.count += 1
        self.total_rate += rate
        self.highest_rate = max(self.highest_rate, rate)
        self.lowest_rate = min(self.lowest_rate, rate)

    @property
    def average_rate(self) -> float:
        return self.total_rate / self.count if self.count else 0.0


@dataclass(frozen=True)
class ExportSummary:
    reviewed_count: int
    valid_score_count: int
    criterion_count: int


_TEXT = {
    "zh-CN": {
        "analysis_sheet": "结果分析",
        "summary_sheet": "成绩总表",
        "detail_sheet": "评分项明细",
        "title": "成绩结果分析",
        "question": "题目",
        "pass_threshold": "本次及格线",
        "generated_at": "导出时间",
        "reviewed_count": "已审阅份数",
        "valid_count": "有效总分份数",
        "average_rate": "平均得分率",
        "median_rate": "中位得分率",
        "highest_rate": "最高得分率",
        "lowest_rate": "最低得分率",
        "pass_rate": "及格率",
        "analysis": "结果说明",
        "distribution": "得分率分布",
        "band": "分数段",
        "students": "人数",
        "criterion_analysis": "评分项分析",
        "criterion": "评分项",
        "max_score": "满分",
        "count": "样本数",
        "criterion_average": "平均得分率",
        "criterion_highest": "最高得分率",
        "criterion_lowest": "最低得分率",
        "distribution_chart": "得分率分布（人数）",
        "criterion_chart": "各评分项平均得分率",
        "chart_limit_note": "图表与完整评分项统计均基于下表数据。",
        "submission_id": "提交 ID",
        "filename": "学生文件名",
        "score": "最终得分",
        "score_rate": "得分率",
        "passed": "达到及格线",
        "uploaded_at": "上传时间",
        "reviewed_at": "审核时间",
        "reviewer": "审核教师",
        "feedback": "总体反馈",
        "rubric_snapshot": "Rubric 快照 ID",
        "rubric_item_id": "评分项 ID",
        "comment": "评分项评语",
        "evidence": "评分证据",
        "yes": "是",
        "no": "否",
        "missing": "缺少有效总分",
        "internal_key": "统计键",
        "bands": ["90–100%", "80–<90%", "70–<80%", "60–<70%", "<60%"],
    },
    "en-US": {
        "analysis_sheet": "Results Analysis",
        "summary_sheet": "Grade Summary",
        "detail_sheet": "Criterion Details",
        "title": "Grade Results Analysis",
        "question": "Question",
        "pass_threshold": "Pass threshold",
        "generated_at": "Exported at",
        "reviewed_count": "Reviewed submissions",
        "valid_count": "Valid total scores",
        "average_rate": "Average rate",
        "median_rate": "Median rate",
        "highest_rate": "Highest rate",
        "lowest_rate": "Lowest rate",
        "pass_rate": "Pass rate",
        "analysis": "Results summary",
        "distribution": "Score-rate distribution",
        "band": "Band",
        "students": "Students",
        "criterion_analysis": "Criterion analysis",
        "criterion": "Criterion",
        "max_score": "Maximum",
        "count": "Samples",
        "criterion_average": "Average rate",
        "criterion_highest": "Highest rate",
        "criterion_lowest": "Lowest rate",
        "distribution_chart": "Score-rate distribution (students)",
        "criterion_chart": "Average rate by criterion",
        "chart_limit_note": "The chart and full criterion statistics use the table below.",
        "submission_id": "Submission ID",
        "filename": "Student filename",
        "score": "Final score",
        "score_rate": "Score rate",
        "passed": "Meets threshold",
        "uploaded_at": "Uploaded at",
        "reviewed_at": "Reviewed at",
        "reviewer": "Reviewer",
        "feedback": "Overall feedback",
        "rubric_snapshot": "Rubric snapshot ID",
        "rubric_item_id": "Criterion ID",
        "comment": "Criterion comment",
        "evidence": "Evidence",
        "yes": "Yes",
        "no": "No",
        "missing": "Missing valid total score",
        "internal_key": "Grouping key",
        "bands": ["90–100%", "80–<90%", "70–<80%", "60–<70%", "<60%"],
    },
}


def _normalize_criterion(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).strip().casefold()
    return re.sub(r"\s+", " ", normalized)


def _as_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _criterion_key(item: dict[str, Any], name: str, max_score: float) -> str:
    item_id = item.get("rubric_item_id")
    identity = str(item_id).strip() if item_id else _normalize_criterion(name)
    return f"{identity}\x1f{max_score:.10g}"


def _rubric_snapshot_id(suggestion: object) -> str:
    if not isinstance(suggestion, dict):
        return ""
    metadata = suggestion.get("mcp_metadata")
    if not isinstance(metadata, dict):
        return ""
    value = metadata.get("rubric_snapshot_id")
    return str(value) if value is not None else ""


def _evidence_text(item: dict[str, Any]) -> str:
    evidence = item.get("evidence")
    if isinstance(evidence, list):
        return "；".join(str(value) for value in evidence if value is not None)
    if evidence is None:
        return ""
    return str(evidence)


def _score_band_index(rate: float) -> int:
    if rate >= 0.9:
        return 0
    if rate >= 0.8:
        return 1
    if rate >= 0.7:
        return 2
    if rate >= 0.6:
        return 3
    return 4


def _join_names(names: list[str], locale: ExportLocale) -> str:
    separator = "、" if locale == "zh-CN" else ", "
    return separator.join(names)


def _analysis_text(
    *,
    locale: ExportLocale,
    reviewed_count: int,
    valid_rates: list[float],
    pass_threshold: float,
    band_counts: list[int],
    criteria: list[CriterionStats],
    band_labels: list[str],
) -> str:
    if not valid_rates:
        if locale == "zh-CN":
            return f"本次共导出 {reviewed_count} 份已审阅作业，但没有可计算得分率的有效总分。"
        return (
            f"This export contains {reviewed_count} reviewed submissions, "
            "but none has a valid total score for rate analysis."
        )

    mean_rate = statistics.fmean(valid_rates)
    median_rate = statistics.median(valid_rates)
    passed = sum(rate * 100 >= pass_threshold for rate in valid_rates)
    below = len(valid_rates) - passed
    largest = max(band_counts)
    dominant_bands = [
        label
        for label, count in zip(band_labels, band_counts, strict=True)
        if count == largest
    ]

    criterion_sentence = ""
    if criteria:
        highest = max(item.average_rate for item in criteria)
        lowest = min(item.average_rate for item in criteria)
        strongest = [
            item.name for item in criteria if math.isclose(item.average_rate, highest)
        ]
        weakest = [
            item.name for item in criteria if math.isclose(item.average_rate, lowest)
        ]
        if locale == "zh-CN":
            criterion_sentence = (
                f"评分项平均得分率最高的是{_join_names(strongest, locale)}"
                f"（{highest:.1%}），最低的是{_join_names(weakest, locale)}"
                f"（{lowest:.1%}）。"
            )
        else:
            criterion_sentence = (
                f"The highest average criterion rate is {_join_names(strongest, locale)} "
                f"({highest:.1%}); the lowest is {_join_names(weakest, locale)} "
                f"({lowest:.1%})."
            )

    if locale == "zh-CN":
        return (
            f"本次共导出 {reviewed_count} 份已审阅作业，其中 {len(valid_rates)} 份具有有效总分。"
            f"平均得分率为 {mean_rate:.1%}，中位得分率为 {median_rate:.1%}。"
            f"人数最多的分数段为{_join_names(dominant_bands, locale)}，共 {largest} 人。"
            f"按 {pass_threshold:g}% 及格线，{passed} 人达到及格线，{below} 人未达到。"
            f"{criterion_sentence}"
        )
    return (
        f"This export contains {reviewed_count} reviewed submissions; {len(valid_rates)} have "
        f"valid total scores. The average rate is {mean_rate:.1%} and the median is "
        f"{median_rate:.1%}. The largest score band is {_join_names(dominant_bands, locale)} "
        f"with {largest} student(s). At a {pass_threshold:g}% threshold, {passed} pass and "
        f"{below} do not. {criterion_sentence}"
    )


def _write_datetime(worksheet, row: int, col: int, value: object, cell_format) -> None:
    if isinstance(value, datetime):
        worksheet.write_datetime(row, col, value, cell_format)
    else:
        worksheet.write(row, col, "" if value is None else str(value), cell_format)


def build_results_workbook(
    output_path: Path,
    *,
    question_name: str,
    rows: Iterable[object],
    pass_threshold: float,
    locale: ExportLocale,
    generated_at: datetime,
) -> ExportSummary:
    """Write one report workbook while retaining only compact statistics in memory."""
    text = _TEXT[locale]
    workbook = xlsxwriter.Workbook(
        str(output_path),
        {
            "constant_memory": True,
            "remove_timezone": True,
            "strings_to_urls": False,
            "strings_to_formulas": False,
        },
    )
    workbook.set_properties(
        {
            "title": f"{text['title']} - {question_name}",
            "subject": "Finalized grade export",
            "author": "AI-Marking",
            "company": "AI-Marking",
            "comments": "Generated from teacher-reviewed results only.",
        }
    )
    workbook.set_calc_mode("auto")

    analysis = workbook.add_worksheet(text["analysis_sheet"])
    summary = workbook.add_worksheet(text["summary_sheet"])
    detail = workbook.add_worksheet(text["detail_sheet"])
    for sheet in (analysis, summary, detail):
        sheet.hide_gridlines(2)

    navy = "#172554"
    indigo = "#4F46E5"
    pale_indigo = "#EEF2FF"
    muted = "#64748B"
    green = "#15803D"
    pale_green = "#DCFCE7"
    red = "#B91C1C"
    pale_red = "#FEE2E2"
    body_font = "PingFang SC"

    title_format = workbook.add_format(
        {
            "bold": True,
            "font_name": body_font,
            "font_size": 20,
            "font_color": "#FFFFFF",
            "bg_color": navy,
            "align": "left",
            "valign": "vcenter",
        }
    )
    section_format = workbook.add_format(
        {
            "bold": True,
            "font_name": body_font,
            "font_size": 12,
            "font_color": navy,
            "bg_color": pale_indigo,
            "bottom": 1,
            "bottom_color": indigo,
        }
    )
    header_format = workbook.add_format(
        {
            "bold": True,
            "font_name": body_font,
            "font_color": "#FFFFFF",
            "bg_color": indigo,
            "align": "center",
            "valign": "vcenter",
            "text_wrap": True,
            "bottom": 1,
            "bottom_color": navy,
        }
    )
    label_format = workbook.add_format(
        {"bold": True, "font_name": body_font, "font_color": navy}
    )
    text_format = workbook.add_format(
        {"font_name": body_font, "font_color": "#0F172A", "valign": "top"}
    )
    wrap_format = workbook.add_format(
        {
            "font_name": body_font,
            "font_color": "#0F172A",
            "valign": "top",
            "text_wrap": True,
        }
    )
    integer_format = workbook.add_format(
        {"font_name": body_font, "num_format": "#,##0", "align": "right"}
    )
    number_format = workbook.add_format(
        {"font_name": body_font, "num_format": "0.00", "align": "right"}
    )
    percent_format = workbook.add_format(
        {"font_name": body_font, "num_format": "0.0%", "align": "right"}
    )
    date_format = workbook.add_format(
        {"font_name": body_font, "num_format": "yyyy-mm-dd hh:mm:ss"}
    )
    note_format = workbook.add_format(
        {
            "font_name": body_font,
            "font_color": muted,
            "italic": True,
            "text_wrap": True,
            "valign": "top",
        }
    )
    pass_format = workbook.add_format(
        {
            "font_name": body_font,
            "font_color": green,
            "bg_color": pale_green,
            "align": "center",
        }
    )
    fail_format = workbook.add_format(
        {
            "font_name": body_font,
            "font_color": red,
            "bg_color": pale_red,
            "align": "center",
        }
    )

    summary_headers = [
        text["submission_id"],
        text["filename"],
        text["score"],
        text["max_score"],
        text["score_rate"],
        text["passed"],
        text["uploaded_at"],
        text["reviewed_at"],
        text["reviewer"],
        text["feedback"],
        text["rubric_snapshot"],
    ]
    summary.write_row(0, 0, summary_headers, header_format)
    summary.set_row(0, 34)
    summary.freeze_panes(1, 2)
    summary.set_column("A:A", 12)
    summary.set_column("B:B", 34)
    summary.set_column("C:D", 12)
    summary.set_column("E:E", 13)
    summary.set_column("F:F", 15)
    summary.set_column("G:H", 20)
    summary.set_column("I:I", 18)
    summary.set_column("J:J", 52)
    summary.set_column("K:K", 38)

    detail_headers = [
        text["submission_id"],
        text["filename"],
        text["rubric_item_id"],
        text["criterion"],
        text["score"],
        text["max_score"],
        text["score_rate"],
        text["comment"],
        text["evidence"],
        text["internal_key"],
    ]
    detail.write_row(0, 0, detail_headers, header_format)
    detail.set_row(0, 34)
    detail.freeze_panes(1, 2)
    detail.set_column("A:A", 12)
    detail.set_column("B:B", 34)
    detail.set_column("C:C", 24)
    detail.set_column("D:D", 30)
    detail.set_column("E:F", 12)
    detail.set_column("G:G", 13)
    detail.set_column("H:H", 52)
    detail.set_column("I:I", 48)
    detail.set_column("J:J", None, None, {"hidden": True})

    valid_rates: list[float] = []
    band_counts = [0, 0, 0, 0, 0]
    criterion_stats: dict[str, CriterionStats] = {}
    reviewed_count = 0
    detail_row = 1

    for row in rows:
        mapping = row._mapping if hasattr(row, "_mapping") else row
        reviewed_count += 1
        score = _as_number(mapping.score)
        max_score = _as_number(mapping.max_score)
        rate = (
            score / max_score
            if score is not None and max_score and max_score > 0
            else None
        )
        if rate is not None:
            valid_rates.append(rate)
            band_counts[_score_band_index(rate)] += 1

        summary_row = reviewed_count
        summary.write_number(summary_row, 0, mapping.id, integer_format)
        summary.write(summary_row, 1, mapping.original_filename, text_format)
        if score is None:
            summary.write_blank(summary_row, 2, None, number_format)
        else:
            summary.write_number(summary_row, 2, score, number_format)
        if max_score is None:
            summary.write_blank(summary_row, 3, None, number_format)
        else:
            summary.write_number(summary_row, 3, max_score, number_format)
        excel_row = summary_row + 1
        if rate is None:
            summary.write_blank(summary_row, 4, None, percent_format)
            summary.write(summary_row, 5, text["missing"], note_format)
        else:
            summary.write_formula(
                summary_row,
                4,
                f"=IFERROR(C{excel_row}/D{excel_row},0)",
                percent_format,
                rate,
            )
            passed = rate * 100 >= pass_threshold
            yes_no = text["yes"] if passed else text["no"]
            summary.write_formula(
                summary_row,
                5,
                (
                    f'=IF(E{excel_row}>=\'{text["analysis_sheet"]}\'!$B$4,'
                    f'"{text["yes"]}","{text["no"]}")'
                ),
                pass_format if passed else fail_format,
                yes_no,
            )
        _write_datetime(summary, summary_row, 6, mapping.uploaded_at, date_format)
        _write_datetime(summary, summary_row, 7, mapping.reviewed_at, date_format)
        summary.write(summary_row, 8, mapping.reviewed_by or "", text_format)
        summary.write(summary_row, 9, mapping.feedback or "", wrap_format)
        summary.write(
            summary_row,
            10,
            _rubric_snapshot_id(mapping.assessment_suggestion),
            text_format,
        )
        summary.set_row(summary_row, 36)

        details = mapping.details if isinstance(mapping.details, list) else []
        for item in details:
            if not isinstance(item, dict):
                continue
            name = str(item.get("criterion") or "").strip()
            item_score = _as_number(item.get("score"))
            item_max = _as_number(item.get("max_score"))
            item_rate = (
                item_score / item_max
                if item_score is not None and item_max and item_max > 0
                else None
            )
            key = (
                _criterion_key(item, name, item_max)
                if item_max and item_max > 0
                else ""
            )
            if item_rate is not None and key:
                aggregate = criterion_stats.setdefault(
                    key,
                    CriterionStats(
                        key=key,
                        name=name or str(item.get("rubric_item_id") or key),
                        max_score=item_max,
                    ),
                )
                aggregate.add(item_rate)

            detail.write_number(detail_row, 0, mapping.id, integer_format)
            detail.write(detail_row, 1, mapping.original_filename, text_format)
            detail.write(
                detail_row, 2, str(item.get("rubric_item_id") or ""), text_format
            )
            detail.write(detail_row, 3, name, text_format)
            if item_score is None:
                detail.write_blank(detail_row, 4, None, number_format)
            else:
                detail.write_number(detail_row, 4, item_score, number_format)
            if item_max is None:
                detail.write_blank(detail_row, 5, None, number_format)
            else:
                detail.write_number(detail_row, 5, item_max, number_format)
            if item_rate is None:
                detail.write_blank(detail_row, 6, None, percent_format)
            else:
                detail_excel_row = detail_row + 1
                detail.write_formula(
                    detail_row,
                    6,
                    f"=IFERROR(E{detail_excel_row}/F{detail_excel_row},0)",
                    percent_format,
                    item_rate,
                )
            detail.write(detail_row, 7, str(item.get("comment") or ""), wrap_format)
            detail.write(detail_row, 8, _evidence_text(item), wrap_format)
            detail.write(detail_row, 9, key, text_format)
            detail.set_row(detail_row, 36)
            detail_row += 1

    summary_last_row = max(1, reviewed_count)
    detail_last_row = max(1, detail_row - 1)
    summary.autofilter(0, 0, summary_last_row, len(summary_headers) - 1)
    detail.autofilter(0, 0, detail_last_row, len(detail_headers) - 1)
    if reviewed_count:
        summary.conditional_format(
            1,
            4,
            reviewed_count,
            4,
            {
                "type": "3_color_scale",
                "min_color": pale_red,
                "mid_color": "#FEF3C7",
                "max_color": pale_green,
            },
        )
    if detail_row > 1:
        detail.conditional_format(
            1,
            6,
            detail_row - 1,
            6,
            {"type": "data_bar", "bar_color": indigo, "bar_solid": True},
        )

    criteria = sorted(
        criterion_stats.values(),
        key=lambda item: (
            item.average_rate,
            _normalize_criterion(item.name),
            item.max_score,
        ),
    )
    criterion_name_counts: dict[str, int] = {}
    for item in criteria:
        normalized_name = _normalize_criterion(item.name)
        criterion_name_counts[normalized_name] = (
            criterion_name_counts.get(normalized_name, 0) + 1
        )
    for item in criteria:
        if criterion_name_counts[_normalize_criterion(item.name)] > 1:
            item.name = f"{item.name} ({text['max_score']} {item.max_score:g})"
    band_labels = list(text["bands"])
    narrative = _analysis_text(
        locale=locale,
        reviewed_count=reviewed_count,
        valid_rates=valid_rates,
        pass_threshold=pass_threshold,
        band_counts=band_counts,
        criteria=criteria,
        band_labels=band_labels,
    )

    analysis.set_column("A:A", 32)
    analysis.set_column("B:B", 18)
    analysis.set_column("C:C", 3)
    analysis.set_column("D:D", 32)
    analysis.set_column("E:E", 12)
    analysis.set_column("F:F", 12)
    analysis.set_column("G:I", 16)
    analysis.set_column("J:J", None, None, {"hidden": True})
    analysis.set_row(0, 32)
    analysis.set_row(1, 10)
    analysis.write(0, 0, text["title"], title_format)
    analysis.write_row(0, 1, [""] * 8, title_format)
    analysis.write(2, 0, text["question"], label_format)
    analysis.write(2, 1, question_name, text_format)
    analysis.write(3, 0, text["pass_threshold"], label_format)
    analysis.write_number(3, 1, pass_threshold / 100, percent_format)
    analysis.write(4, 0, text["generated_at"], label_format)
    analysis.write_datetime(4, 1, generated_at, date_format)

    analysis.write(6, 0, text["reviewed_count"], label_format)
    analysis.write_number(6, 1, reviewed_count, integer_format)
    analysis.write(7, 0, text["valid_count"], label_format)
    analysis.write_number(7, 1, len(valid_rates), integer_format)
    mean_rate = statistics.fmean(valid_rates) if valid_rates else 0.0
    median_rate = statistics.median(valid_rates) if valid_rates else 0.0
    highest_rate = max(valid_rates) if valid_rates else 0.0
    lowest_rate = min(valid_rates) if valid_rates else 0.0
    pass_rate = (
        sum(rate * 100 >= pass_threshold for rate in valid_rates) / len(valid_rates)
        if valid_rates
        else 0.0
    )
    rate_range = f"'{text['summary_sheet']}'!$E$2:$E${reviewed_count + 1}"
    metric_specs = [
        (text["average_rate"], f"=IFERROR(AVERAGE({rate_range}),0)", mean_rate),
        (text["median_rate"], f"=IFERROR(MEDIAN({rate_range}),0)", median_rate),
        (text["highest_rate"], f"=IFERROR(MAX({rate_range}),0)", highest_rate),
        (text["lowest_rate"], f"=IFERROR(MIN({rate_range}),0)", lowest_rate),
        (
            text["pass_rate"],
            (f'=IFERROR(COUNTIF({rate_range},">="&$B$4)/COUNT({rate_range}),0)'),
            pass_rate,
        ),
    ]
    for offset, (label, formula, cached) in enumerate(metric_specs, start=8):
        analysis.write(offset, 0, label, label_format)
        analysis.write_formula(offset, 1, formula, percent_format, cached)

    analysis.write(13, 0, text["analysis"], section_format)
    analysis.write_row(13, 1, [""] * 8, section_format)
    analysis.write(14, 0, narrative, wrap_format)
    analysis.set_row(14, 140)

    analysis.write(18, 0, text["distribution"], section_format)
    analysis.write(18, 1, "", section_format)
    analysis.write(18, 3, text["criterion_analysis"], section_format)
    analysis.write_row(18, 4, [""] * 5, section_format)
    analysis.write_row(
        19,
        0,
        [
            text["band"],
            text["students"],
            "",
            text["criterion"],
            text["max_score"],
            text["count"],
            text["criterion_average"],
            text["criterion_highest"],
            text["criterion_lowest"],
            text["internal_key"],
        ],
        header_format,
    )
    band_formulas = [
        f'=COUNTIF({rate_range},">=90%")',
        f'=COUNTIFS({rate_range},">=80%",{rate_range},"<90%")',
        f'=COUNTIFS({rate_range},">=70%",{rate_range},"<80%")',
        f'=COUNTIFS({rate_range},">=60%",{rate_range},"<70%")',
        f'=COUNTIF({rate_range},"<60%")',
    ]
    detail_key_range = f"'{text['detail_sheet']}'!$J$2:$J${detail_row}"
    detail_rate_range = f"'{text['detail_sheet']}'!$G$2:$G${detail_row}"
    table_rows = max(len(band_labels), len(criteria))
    for offset in range(table_rows):
        index = 20 + offset
        if offset < len(band_labels):
            analysis.write(index, 0, band_labels[offset], text_format)
            analysis.write_formula(
                index,
                1,
                band_formulas[offset],
                integer_format,
                band_counts[offset],
            )
        if offset >= len(criteria):
            continue
        item = criteria[offset]
        excel_row = index + 1
        analysis.write(index, 3, item.name, text_format)
        analysis.write_number(index, 4, item.max_score, number_format)
        analysis.write_formula(
            index,
            5,
            f"=COUNTIF({detail_key_range},$J{excel_row})",
            integer_format,
            item.count,
        )
        analysis.write_formula(
            index,
            6,
            f"=IFERROR(AVERAGEIF({detail_key_range},$J{excel_row},{detail_rate_range}),0)",
            percent_format,
            item.average_rate,
        )
        analysis.write_formula(
            index,
            7,
            f"=IFERROR(MAXIFS({detail_rate_range},{detail_key_range},$J{excel_row}),0)",
            percent_format,
            item.highest_rate,
        )
        analysis.write_formula(
            index,
            8,
            f"=IFERROR(MINIFS({detail_rate_range},{detail_key_range},$J{excel_row}),0)",
            percent_format,
            item.lowest_rate,
        )
        analysis.write(index, 9, item.key, text_format)

    note_row = 20 + table_rows
    analysis.write(note_row, 0, text["chart_limit_note"], note_format)
    distribution_chart = workbook.add_chart({"type": "column"})
    distribution_chart.add_series(
        {
            "name": text["students"],
            "categories": [text["analysis_sheet"], 20, 0, 24, 0],
            "values": [text["analysis_sheet"], 20, 1, 24, 1],
            "fill": {"color": indigo},
            "border": {"none": True},
            "data_labels": {"value": True},
        }
    )
    distribution_chart.set_title(
        {"name": text["distribution_chart"], "name_font": {"name": body_font}}
    )
    distribution_chart.set_legend({"none": True})
    distribution_chart.set_x_axis({"num_font": {"name": body_font}})
    distribution_chart.set_y_axis(
        {
            "major_unit": 1,
            "min": 0,
            "num_format": "0",
            "num_font": {"name": body_font},
        }
    )
    distribution_chart.set_style(10)
    analysis.insert_chart(2, 3, distribution_chart, {"x_scale": 1.2, "y_scale": 1.05})

    if criteria:
        criterion_chart = workbook.add_chart({"type": "bar"})
        criterion_chart.add_series(
            {
                "name": text["criterion_average"],
                "categories": [text["analysis_sheet"], 20, 3, 19 + len(criteria), 3],
                "values": [text["analysis_sheet"], 20, 6, 19 + len(criteria), 6],
                "fill": {"color": "#0EA5E9"},
                "border": {"none": True},
            }
        )
        criterion_chart.set_title(
            {"name": text["criterion_chart"], "name_font": {"name": body_font}}
        )
        criterion_chart.set_legend({"none": True})
        criterion_chart.set_x_axis(
            {
                "min": 0,
                "max": 1,
                "num_format": "0%",
                "num_font": {"name": body_font},
            }
        )
        criterion_chart.set_y_axis({"num_font": {"name": body_font}})
        criterion_chart.set_style(10)
        chart_row = note_row + 2
        analysis.insert_chart(
            chart_row,
            0,
            criterion_chart,
            {"x_scale": 1.35, "y_scale": max(0.8, min(1.8, len(criteria) / 6))},
        )

    analysis.freeze_panes(6, 0)
    analysis.set_landscape()
    analysis.fit_to_pages(1, 1)
    analysis.set_margins(0.35, 0.35, 0.5, 0.5)
    summary.set_landscape()
    summary.fit_to_pages(1, 0)
    detail.set_landscape()
    detail.fit_to_pages(1, 0)

    workbook.close()
    return ExportSummary(
        reviewed_count=reviewed_count,
        valid_score_count=len(valid_rates),
        criterion_count=len(criteria),
    )


def safe_export_filename(question_id: str, generated_at: datetime) -> str:
    safe_id = re.sub(r"[^A-Za-z0-9._-]+", "_", question_id).strip("._-") or "question"
    return f"grade_analysis_{safe_id}_{generated_at:%Y%m%d_%H%M}.xlsx"


def delete_export_file(path: str) -> None:
    try:
        Path(path).unlink(missing_ok=True)
    except OSError:
        # Response cleanup must never replace a successful download with an error.
        pass
