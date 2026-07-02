"""add question pdf fields

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "d4e5f6a7b8c9"
down_revision: Union[str, None] = "c3d4e5f6a7b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "submissions",
        sa.Column("question_original_filename", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "submissions",
        sa.Column("question_file_path", sa.String(length=512), nullable=True),
    )
    op.add_column(
        "submissions", sa.Column("question_ocr_text", sa.Text(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("submissions", "question_ocr_text")
    op.drop_column("submissions", "question_file_path")
    op.drop_column("submissions", "question_original_filename")
