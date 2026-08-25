"""restore background job single-target constraint

Revision ID: b7c8d9e0f1a2
Revises: 858c3ddac415
Create Date: 2026-08-24

The question-id type migration necessarily dropped the table-level check that
referenced ``background_jobs.question_id``. Restore it in a follow-up revision
so databases that already applied that migration receive the constraint too.
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b7c8d9e0f1a2"
down_revision: Union[str, None] = "858c3ddac415"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_check_constraint(
        "ck_background_jobs_single_target",
        "background_jobs",
        "(question_id IS NOT NULL AND submission_id IS NULL) OR "
        "(question_id IS NULL AND submission_id IS NOT NULL)",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_background_jobs_single_target",
        "background_jobs",
        type_="check",
    )
