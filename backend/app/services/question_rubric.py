"""Rubric 提取结果的服务端确定性校验与持久化。

rubric 由 MCP 客户端在首次评分时从题目 OCR 中提取，服务端只做
确定性校验（引用必须是 OCR 子串、引用含满分、分项=总分、条目不重复），
校验通过后落题目级权威快照，后续评分直接复用。
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata

from pydantic import BaseModel, Field, model_validator
from sqlalchemy.orm import Session

from app.core.time import utc_now_naive
from app.models.question import Question

EXTRACTOR_VERSION = "question-rubric-v1"


class RubricExtractionItem(BaseModel):
    criterion: str = Field(min_length=1, max_length=200)
    max_score: float = Field(gt=0)
    details: str = Field(min_length=1, max_length=2_000)
    source_quote: str = Field(min_length=1, max_length=4_000)


class RubricExtraction(BaseModel):
    status: str
    items: list[RubricExtractionItem] = Field(default_factory=list, max_length=100)
    total_max_score: float | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def validate_status(self):
        if self.status not in {"complete", "absent_or_ambiguous"}:
            raise ValueError("invalid rubric extraction status")
        if self.status == "complete" and (not self.items or self.total_max_score is None):
            raise ValueError("complete rubric must contain items and total")
        if self.status != "complete" and self.items:
            raise ValueError("ambiguous rubric cannot contain items")
        return self


def _normalized(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).split()).casefold()


def _item_id(criterion: str, max_score: float, details: str) -> str:
    raw = json.dumps(
        {"criterion": criterion.strip(), "max_score": max_score, "details": details.strip()},
        ensure_ascii=False,
        sort_keys=True,
    )
    return "rubric_" + hashlib.sha256(raw.encode()).hexdigest()[:24]


def validate_extraction(result: RubricExtraction, ocr_text: str) -> list[dict] | None:
    """确定性校验客户端提交的 rubric 提取结果。

    ``complete`` 时要求：逐项 source_quote 是 OCR 原文子串、引用包含该项满分、
    分项满分之和等于 total_max_score、条目不重复。任一不满足返回 None。
    ``absent_or_ambiguous`` 返回 None（由调用方持久化识别结果）。
    """
    if result.status != "complete":
        return None
    normalized_ocr = _normalized(ocr_text)
    criteria: set[str] = set()
    total = 0.0
    items: list[dict] = []
    for item in result.items:
        criterion = item.criterion.strip()
        if _normalized(criterion) in criteria:
            return None
        criteria.add(_normalized(criterion))
        quote = _normalized(item.source_quote)
        if not quote or quote not in normalized_ocr:
            return None
        score_text = format(float(item.max_score), "g")
        if not re.search(rf"(?<![\d.]){re.escape(score_text)}(?:\.0+)?(?![\d.])", quote):
            return None
        total += item.max_score
        items.append(
            {
                "rubric_item_id": _item_id(criterion, item.max_score, item.details),
                "criterion": criterion,
                "max_score": item.max_score,
                "details": item.details.strip(),
                "source_quote": item.source_quote.strip(),
            }
        )
    if result.total_max_score is None or abs(total - result.total_max_score) > 0.01:
        return None
    canonical_total = result.total_max_score
    canonical_text = "\n".join(
        f"{item['criterion']} ({item['max_score']}分): {item['details']}"
        for item in items
    ) + f"\n总分:{canonical_total}分"
    return [{"items": items, "total_max_score": canonical_total, "text": canonical_text}]


def persist_question_rubric(
    db: Session,
    question: Question,
    *,
    status: str,
    validated: list[dict] | None,
    ocr_text: str,
) -> dict:
    """持久化题目级 rubric 提取结果快照。

    Args:
        status: ``complete`` 或 ``absent_or_ambiguous``
        validated: ``complete`` 时 ``validate_extraction`` 返回的校验通过项
        ocr_text: 当前题目 OCR 文本（用于绑定 ocr_hash，防 OCR 变化后复用）

    Returns:
        写入后的快照字段 dict。
    """
    ocr_hash = "sha256:" + hashlib.sha256(ocr_text.encode()).hexdigest()
    if status == "complete" and validated:
        snapshot = validated[0]
        question.extracted_rubric = snapshot.get("text")
        question.extracted_rubric_items = snapshot.get("items")
        question.extracted_rubric_ocr_hash = ocr_hash
        question.extracted_rubric_version = EXTRACTOR_VERSION
        question.extracted_rubric_at = utc_now_naive()
        return {
            "status": "complete",
            "text": snapshot.get("text"),
            "items": snapshot.get("items"),
        }
    # absent_or_ambiguous：持久化识别结果，后续走配置 rubric 或内置默认，
    # 避免对同一题目重复询问客户端。
    question.extracted_rubric = None
    question.extracted_rubric_items = None
    question.extracted_rubric_ocr_hash = ocr_hash
    question.extracted_rubric_version = EXTRACTOR_VERSION
    question.extracted_rubric_at = utc_now_naive()
    return {"status": "absent_or_ambiguous", "text": None, "items": []}
