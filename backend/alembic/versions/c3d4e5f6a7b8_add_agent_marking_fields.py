"""add agent marking fields

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "c3d4e5f6a7b8"
down_revision: Union[str, None] = "b2c3d4e5f6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TYPE submission_status ADD VALUE IF NOT EXISTS 'agent_grading'")
    op.execute("ALTER TYPE submission_status ADD VALUE IF NOT EXISTS 'agent_reviewing'")
    op.execute("ALTER TYPE submission_status ADD VALUE IF NOT EXISTS 'agent_revising'")
    op.execute("ALTER TYPE submission_status ADD VALUE IF NOT EXISTS 'review_required'")
    op.add_column("submissions", sa.Column("max_score", sa.Float(), nullable=True))
    op.add_column("submissions", sa.Column("confidence", sa.Float(), nullable=True))
    op.add_column("submissions", sa.Column("ai_result", sa.JSON(), nullable=True))
    op.add_column("submissions", sa.Column("agent_trace", sa.JSON(), nullable=True))
    op.add_column("submissions", sa.Column("review_reason", sa.Text(), nullable=True))
    op.add_column(
        "submissions", sa.Column("reviewed_by", sa.String(length=100), nullable=True)
    )
    op.add_column("submissions", sa.Column("review_note", sa.Text(), nullable=True))
    op.add_column("submissions", sa.Column("reviewed_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("submissions", "reviewed_at")
    op.drop_column("submissions", "review_note")
    op.drop_column("submissions", "reviewed_by")
    op.drop_column("submissions", "review_reason")
    op.drop_column("submissions", "agent_trace")
    op.drop_column("submissions", "ai_result")
    op.drop_column("submissions", "confidence")
    op.drop_column("submissions", "max_score")
