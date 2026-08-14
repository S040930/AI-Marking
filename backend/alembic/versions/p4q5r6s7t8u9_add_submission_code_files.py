"""Add multi-file Codex code execution audit records.

Revision ID: p4q5r6s7t8u9
Revises: n3o4p5q6r7s
"""

import sqlalchemy as sa

from alembic import op

revision = "p4q5r6s7t8u9"
down_revision = "n3o4p5q6r7s"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # PostgreSQL enum values cannot be represented by SQLAlchemy's create_all
    # path alone; add the state before adding columns that may use it.
    op.execute(
        "ALTER TYPE submission_status ADD VALUE IF NOT EXISTS 'code_processing'"
    )
    op.create_table(
        "submission_code_files",
        sa.UniqueConstraint(
            "submission_id", "question_number", name="uq_submission_code_question"
        ),
        sa.UniqueConstraint(
            "submission_id", "original_filename", name="uq_submission_code_filename"
        ),
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "submission_id",
            sa.Integer(),
            sa.ForeignKey("submissions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("question_number", sa.Integer(), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("file_path", sa.String(length=512), nullable=False),
        sa.Column("file_kind", sa.String(length=16), nullable=False),
        sa.Column("source_sha256", sa.String(length=64), nullable=False),
        sa.Column("source_text", sa.Text(), nullable=True),
        sa.Column(
            "execution_status",
            sa.Enum(
                "pending",
                "running",
                "completed",
                "failed",
                name="code_execution_status",
            ),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("execution_result", sa.JSON(), nullable=True),
        sa.Column("artifacts", sa.JSON(), nullable=True),
        sa.Column("visual_reviews", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index(
        "ix_submission_code_files_submission_id",
        "submission_code_files",
        ["submission_id"],
    )
    op.add_column("submissions", sa.Column("code_runtime", sa.JSON(), nullable=True))
    op.add_column("submissions", sa.Column("code_visual_assets", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("submissions", "code_visual_assets")
    op.drop_column("submissions", "code_runtime")
    op.drop_index(
        "ix_submission_code_files_submission_id", table_name="submission_code_files"
    )
    op.drop_table("submission_code_files")
    # PostgreSQL does not support removing an enum value safely. Existing rows
    # are migrated back to ocr_done before a downgrade in supported deployments.
