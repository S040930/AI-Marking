"""Shared locking primitives for the modular monolith write paths."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.question import Question
from app.models.submission import Submission


def lock_submission_after_question(
    db: Session, submission_id: int
) -> Submission | None:
    """Lock one submission using the global Question -> Submission order."""
    question_id = db.scalar(
        select(Submission.question_id).where(Submission.id == submission_id)
    )
    if question_id is None:
        return None
    db.execute(
        select(Question.id)
        .where(Question.id == question_id)
        .with_for_update()
    ).scalar_one_or_none()
    return db.get(Submission, submission_id, with_for_update=True)
