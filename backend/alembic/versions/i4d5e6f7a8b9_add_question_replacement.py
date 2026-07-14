"""add asynchronous question replacement

Revision ID: i4d5e6f7a8b9
Revises: h3c4d5e6f7a8
"""

from typing import Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "i4d5e6f7a8b9"
down_revision: Union[str, None] = "h3c4d5e6f7a8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TYPE background_job_type ADD VALUE IF NOT EXISTS 'question_replace'"
    )
    replacement_status = postgresql.ENUM(
        "pending",
        "processing",
        "failed",
        name="question_replacement_status",
        create_type=False,
    )
    replacement_status.create(op.get_bind(), checkfirst=True)
    op.add_column(
        "questions",
        sa.Column("replacement_status", replacement_status, nullable=True),
    )
    op.add_column(
        "questions",
        sa.Column("replacement_file_path", sa.String(512), nullable=True),
    )
    op.add_column(
        "questions",
        sa.Column("replacement_original_filename", sa.String(255), nullable=True),
    )
    op.add_column(
        "questions",
        sa.Column("replacement_error_message", sa.String(1024), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("questions", "replacement_error_message")
    op.drop_column("questions", "replacement_original_filename")
    op.drop_column("questions", "replacement_file_path")
    op.drop_column("questions", "replacement_status")
    sa.Enum(name="question_replacement_status").drop(
        op.get_bind(), checkfirst=True
    )
