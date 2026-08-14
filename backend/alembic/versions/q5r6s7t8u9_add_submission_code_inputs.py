"""Add question-declared CSV inputs for Codex code execution."""

import sqlalchemy as sa

from alembic import op

revision = "q5r6s7t8u9"
down_revision = "p4q5r6s7t8u9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "submission_code_input_files",
        sa.UniqueConstraint(
            "submission_id",
            "original_filename",
            name="uq_submission_code_input_filename",
        ),
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "submission_id",
            sa.Integer(),
            sa.ForeignKey("submissions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("file_path", sa.String(length=512), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index(
        "ix_submission_code_input_files_submission_id",
        "submission_code_input_files",
        ["submission_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_submission_code_input_files_submission_id",
        table_name="submission_code_input_files",
    )
    op.drop_table("submission_code_input_files")
