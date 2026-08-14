"""Add question extracted-rubric cache and submission review toggle.

Revision ID: r6s7t8u9v1
Revises: q5r6s7t8u9
"""

import sqlalchemy as sa

from alembic import op

revision = "r6s7t8u9v1"
down_revision = "q5r6s7t8u9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "questions", sa.Column("extracted_rubric", sa.Text(), nullable=True)
    )
    op.add_column(
        "submissions", sa.Column("review_enabled", sa.Boolean(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("submissions", "review_enabled")
    op.drop_column("questions", "extracted_rubric")
