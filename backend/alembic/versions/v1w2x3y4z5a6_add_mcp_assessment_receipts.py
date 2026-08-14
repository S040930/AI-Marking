"""Add durable MCP assessment idempotency receipts."""

import sqlalchemy as sa

from alembic import op

revision = "v1w2x3y4z5a6"
down_revision = "u9v1w2x3y4z5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mcp_assessment_receipts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("submission_id", sa.Integer(), nullable=False),
        sa.Column("request_id", sa.String(length=36), nullable=False),
        sa.Column("handle_hash", sa.String(length=64), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("response", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["submission_id"], ["submissions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("submission_id", "request_id", name="uq_mcp_receipt_submission_request"),
    )
    op.create_index("ix_mcp_assessment_receipts_submission_id", "mcp_assessment_receipts", ["submission_id"])


def downgrade() -> None:
    op.drop_index("ix_mcp_assessment_receipts_submission_id", table_name="mcp_assessment_receipts")
    op.drop_table("mcp_assessment_receipts")
