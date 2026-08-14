"""Persist MCP workflow handles across API workers."""

import sqlalchemy as sa

from alembic import op

revision = "t8u9v1w2x3y4"
down_revision = "s7t8u9v1w2x3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mcp_workflow_handles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("submission_id", sa.Integer(), nullable=False),
        sa.Column("context_hash", sa.String(length=71), nullable=False),
        sa.Column("grading_revision", sa.Integer(), nullable=False),
        sa.Column("offset", sa.Integer(), nullable=True),
        sa.Column("context_complete", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("visual_confirmation", sa.String(length=32), nullable=True),
        sa.Column("visual_confirmation_note", sa.Text(), nullable=True),
        sa.Column("visual_confirmed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["submission_id"], ["submissions.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("token_hash", name="uq_mcp_workflow_handles_token_hash"),
    )
    op.create_index(
        "ix_mcp_workflow_handles_expires_at",
        "mcp_workflow_handles",
        ["expires_at"],
    )
    op.create_index(
        "ix_mcp_workflow_handles_submission",
        "mcp_workflow_handles",
        ["submission_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_mcp_workflow_handles_submission", table_name="mcp_workflow_handles")
    op.drop_index("ix_mcp_workflow_handles_expires_at", table_name="mcp_workflow_handles")
    op.drop_table("mcp_workflow_handles")
