"""Remove unverified Codex model report.

Revision ID: n3o4p5q6r7s
Revises: m2n3o4p5q6r7
"""

import sqlalchemy as sa

from alembic import op

revision = "n3o4p5q6r7s"
down_revision = "m2n3o4p5q6r7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("submissions", "grader_model_reported")


def downgrade() -> None:
    op.add_column(
        "submissions",
        sa.Column("grader_model_reported", sa.String(length=128), nullable=True),
    )
