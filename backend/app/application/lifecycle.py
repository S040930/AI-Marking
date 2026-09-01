"""Single mutation gateway for workflow state fields."""

from __future__ import annotations

from typing import Protocol

from app.domain.lifecycle import (
    QuestionReplacementStatus,
    QuestionStatus,
    SubmissionStatus,
    ensure_question_replacement_transition,
    ensure_question_transition,
    ensure_submission_transition,
)


class _SubmissionState(Protocol):
    status: SubmissionStatus


class _QuestionState(Protocol):
    status: QuestionStatus
    replacement_status: QuestionReplacementStatus | None


def transition_submission(entity: _SubmissionState, target: SubmissionStatus) -> None:
    ensure_submission_transition(entity.status, target)
    entity.status = target


def transition_question(entity: _QuestionState, target: QuestionStatus) -> None:
    ensure_question_transition(entity.status, target)
    entity.status = target


def transition_question_replacement(
    entity: _QuestionState, target: QuestionReplacementStatus | None
) -> None:
    ensure_question_replacement_transition(entity.replacement_status, target)
    entity.replacement_status = target

