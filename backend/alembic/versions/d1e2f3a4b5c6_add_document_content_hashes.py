"""Track content hashes for permanently retained PDF blobs.

Revision ID: d1e2f3a4b5c6
Revises: c0ff3e1a2b3c4d5e6f7a8b9c0d1e2f3a
"""

from alembic import op
import sqlalchemy as sa

revision = "d1e2f3a4b5c6"
down_revision = "c0ff3e1a2b3c4d5e6f7a8b9c0d1e2f3a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "submissions",
        sa.Column("file_sha256", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "questions",
        sa.Column("file_sha256", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "questions",
        sa.Column("replacement_file_sha256", sa.String(length=64), nullable=True),
    )
    op.create_index("ix_submissions_file_sha256", "submissions", ["file_sha256"])
    op.create_index("ix_questions_file_sha256", "questions", ["file_sha256"])
    op.create_index(
        "ix_questions_replacement_file_sha256",
        "questions",
        ["replacement_file_sha256"],
    )


def downgrade() -> None:
    op.drop_index("ix_questions_replacement_file_sha256", table_name="questions")
    op.drop_index("ix_questions_file_sha256", table_name="questions")
    op.drop_index("ix_submissions_file_sha256", table_name="submissions")
    op.drop_column("questions", "replacement_file_sha256")
    op.drop_column("questions", "file_sha256")
    op.drop_column("submissions", "file_sha256")
