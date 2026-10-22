"""Helpers for recognizing rubric structures without rewriting their source text."""

from __future__ import annotations

import re


def has_grade_band_scale(value: str) -> bool:
    """Whether an OCR excerpt explicitly contains at least two letter-grade bands."""
    plain_text = re.sub(r"<[^>]*>", " ", value)
    bands = re.findall(
        r"\b([A-F])\s+(\d+\s*(?:\+|[-–−]\s*\d+))",
        plain_text,
        flags=re.IGNORECASE,
    )
    return len({grade.upper() for grade, _ in bands}) >= 2
