"""Table-driven coverage for pure workflow transition policies."""

import pytest

from app.domain.lifecycle import (
    QUESTION_REPLACEMENT_TRANSITIONS,
    QUESTION_TRANSITIONS,
    SUBMISSION_TRANSITIONS,
    InvalidTransitionError,
    QuestionReplacementStatus,
    QuestionStatus,
    SubmissionStatus,
    ensure_question_replacement_transition,
    ensure_question_transition,
    ensure_submission_transition,
)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (current, target)
        for current, targets in SUBMISSION_TRANSITIONS.items()
        for target in targets
    ],
)
def test_all_legal_submission_transitions(current, target):
    ensure_submission_transition(current, target)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (current, target)
        for current in SubmissionStatus
        for target in SubmissionStatus
        if target not in SUBMISSION_TRANSITIONS[current]
    ],
)
def test_all_illegal_submission_transitions(current, target):
    with pytest.raises(InvalidTransitionError):
        ensure_submission_transition(current, target)


def test_reviewed_submission_cannot_move_backwards():
    with pytest.raises(InvalidTransitionError):
        ensure_submission_transition(
            SubmissionStatus.reviewed, SubmissionStatus.ready_for_review
        )


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (current, target)
        for current, targets in QUESTION_TRANSITIONS.items()
        for target in targets
    ],
)
def test_all_legal_question_transitions(current, target):
    ensure_question_transition(current, target)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (current, target)
        for current, targets in QUESTION_REPLACEMENT_TRANSITIONS.items()
        for target in targets
    ],
)
def test_all_legal_question_replacement_transitions(current, target):
    ensure_question_replacement_transition(current, target)


def test_question_and_replacement_invalid_transitions_fail():
    with pytest.raises(InvalidTransitionError):
        ensure_question_transition(QuestionStatus.ready, QuestionStatus.pending)
    with pytest.raises(InvalidTransitionError):
        ensure_question_replacement_transition(
            QuestionReplacementStatus.processing,
            QuestionReplacementStatus.pending,
        )
