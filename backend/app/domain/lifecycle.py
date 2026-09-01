"""Workflow states and transition policies without framework dependencies."""

from __future__ import annotations

import enum
from collections.abc import Mapping


class InvalidTransitionError(ValueError):
    """Raised when a workflow attempts an illegal state transition."""


class SubmissionStatus(str, enum.Enum):
    pending = "pending"
    ocr_processing = "ocr_processing"
    ocr_done = "ocr_done"
    awaiting_mcp = "awaiting_mcp"
    ready_for_review = "ready_for_review"
    reviewed = "reviewed"
    failed = "failed"


class SubmissionGradingMode(str, enum.Enum):
    external_agent = "external_agent"


class QuestionStatus(str, enum.Enum):
    pending = "pending"
    ocr_processing = "ocr_processing"
    ready = "ready"
    failed = "failed"


class QuestionReplacementStatus(str, enum.Enum):
    pending = "pending"
    processing = "processing"
    failed = "failed"


SUBMISSION_TRANSITIONS: Mapping[SubmissionStatus, frozenset[SubmissionStatus]] = {
    SubmissionStatus.pending: frozenset(
        {SubmissionStatus.ocr_processing, SubmissionStatus.failed}
    ),
    SubmissionStatus.ocr_processing: frozenset(
        {SubmissionStatus.ocr_processing, SubmissionStatus.ocr_done, SubmissionStatus.failed}
    ),
    SubmissionStatus.ocr_done: frozenset(
        {SubmissionStatus.ocr_processing, SubmissionStatus.awaiting_mcp, SubmissionStatus.failed}
    ),
    SubmissionStatus.awaiting_mcp: frozenset(
        {
            SubmissionStatus.ocr_processing,
            SubmissionStatus.ready_for_review,
            SubmissionStatus.failed,
        }
    ),
    SubmissionStatus.ready_for_review: frozenset(
        {SubmissionStatus.ready_for_review, SubmissionStatus.reviewed}
    ),
    SubmissionStatus.reviewed: frozenset({SubmissionStatus.reviewed}),
    SubmissionStatus.failed: frozenset({SubmissionStatus.pending}),
}

QUESTION_TRANSITIONS: Mapping[QuestionStatus, frozenset[QuestionStatus]] = {
    QuestionStatus.pending: frozenset(
        {QuestionStatus.pending, QuestionStatus.ocr_processing, QuestionStatus.failed}
    ),
    QuestionStatus.ocr_processing: frozenset(
        {QuestionStatus.ocr_processing, QuestionStatus.ready, QuestionStatus.failed}
    ),
    QuestionStatus.ready: frozenset({QuestionStatus.ready}),
    QuestionStatus.failed: frozenset({QuestionStatus.pending}),
}

QUESTION_REPLACEMENT_TRANSITIONS: Mapping[
    QuestionReplacementStatus | None,
    frozenset[QuestionReplacementStatus | None],
] = {
    None: frozenset({QuestionReplacementStatus.pending}),
    QuestionReplacementStatus.pending: frozenset(
        {
            QuestionReplacementStatus.pending,
            QuestionReplacementStatus.processing,
            QuestionReplacementStatus.failed,
        }
    ),
    QuestionReplacementStatus.processing: frozenset(
        {None, QuestionReplacementStatus.processing, QuestionReplacementStatus.failed}
    ),
    QuestionReplacementStatus.failed: frozenset(
        {QuestionReplacementStatus.pending, QuestionReplacementStatus.failed}
    ),
}


def _ensure_transition(current, target, allowed, entity: str) -> None:
    if target not in allowed[current]:
        current_value = current.value if isinstance(current, enum.Enum) else "none"
        target_value = target.value if isinstance(target, enum.Enum) else "none"
        raise InvalidTransitionError(
            f"{entity} 状态不可从 {current_value} 迁移到 {target_value}"
        )


def ensure_submission_transition(
    current: SubmissionStatus, target: SubmissionStatus
) -> None:
    _ensure_transition(current, target, SUBMISSION_TRANSITIONS, "提交")


def ensure_question_transition(current: QuestionStatus, target: QuestionStatus) -> None:
    _ensure_transition(current, target, QUESTION_TRANSITIONS, "题目")


def ensure_question_replacement_transition(
    current: QuestionReplacementStatus | None,
    target: QuestionReplacementStatus | None,
) -> None:
    _ensure_transition(
        current, target, QUESTION_REPLACEMENT_TRANSITIONS, "题目替换"
    )
