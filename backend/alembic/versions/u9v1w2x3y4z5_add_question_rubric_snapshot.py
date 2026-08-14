"""Store validated question rubric snapshots."""

import sqlalchemy as sa

from alembic import op

revision = "u9v1w2x3y4z5"
down_revision = "t8u9v1w2x3y4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("questions", sa.Column("extracted_rubric_items", sa.JSON(), nullable=True))
    op.add_column(
        "questions",
        sa.Column("extracted_rubric_ocr_hash", sa.String(length=71), nullable=True),
    )
    op.add_column(
        "questions",
        sa.Column("extracted_rubric_version", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "questions",
        sa.Column("extracted_rubric_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("questions", "extracted_rubric_at")
    op.drop_column("questions", "extracted_rubric_version")
    op.drop_column("questions", "extracted_rubric_ocr_hash")
    op.drop_column("questions", "extracted_rubric_items")
