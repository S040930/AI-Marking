"""Add submissions.assessment_review for independent MCP review results."""

import sqlalchemy as sa

from alembic import op

revision = "e2f3a4b5c6d7"
down_revision = "d1e2f3a4b5c6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "submissions",
        sa.Column("assessment_review", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("submissions", "assessment_review")
