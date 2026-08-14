"""Allow same-question source groups and mark the executable entry point."""

import sqlalchemy as sa

from alembic import op

revision = "s7t8u9v1w2x3"
down_revision = "r6s7t8u9v1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("uq_submission_code_question", "submission_code_files", type_="unique")
    op.add_column(
        "submission_code_files",
        sa.Column("entrypoint", sa.Boolean(), nullable=False, server_default=sa.true()),
    )


def downgrade() -> None:
    op.drop_column("submission_code_files", "entrypoint")
    op.create_unique_constraint(
        "uq_submission_code_question", "submission_code_files", ["submission_id", "question_number"]
    )
