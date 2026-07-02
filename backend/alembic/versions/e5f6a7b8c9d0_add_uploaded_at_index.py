"""add uploaded_at index

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
"""

from typing import Sequence, Union

from alembic import op

revision: str = "e5f6a7b8c9d0"
down_revision: Union[str, None] = "d4e5f6a7b8c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "ix_submissions_uploaded_at",
        "submissions",
        ["uploaded_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_submissions_uploaded_at", table_name="submissions")
