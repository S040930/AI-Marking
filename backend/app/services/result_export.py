"""Pure score and rubric statistics for finalized grade exports."""

from __future__ import annotations

import math
import re
import statistics
import unicodedata
from dataclasses import dataclass
from typing import Any, Literal

ExportLocale = Literal["zh-CN", "en-US"]


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
    # 兼容读:新数据 agent_metadata,旧数据(v12 及更早)为 mcp_metadata
    metadata = suggestion.get("agent_metadata") or suggestion.get("mcp_metadata")
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
