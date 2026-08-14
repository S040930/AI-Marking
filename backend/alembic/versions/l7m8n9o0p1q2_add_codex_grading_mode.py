"""Add Codex grading mode and awaiting state.

Revision ID: l7m8n9o0p1q2
Revises: k6a7b8c9d0e1
"""

import sqlalchemy as sa

from alembic import op

revision = "l7m8n9o0p1q2"
down_revision = "k6a7b8c9d0e1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(
            "ALTER TYPE submission_status ADD VALUE IF NOT EXISTS 'awaiting_codex'"
        )
        grading_mode = sa.Enum(
            "backend_agent",
            "codex",
            name="submission_grading_mode",
        )
        grading_mode.create(bind, checkfirst=True)
    else:
        grading_mode = sa.Enum(
            "backend_agent",
            "codex",
            name="submission_grading_mode",
        )

    op.add_column(
        "submissions",
        sa.Column(
            "grading_mode",
            grading_mode,
            nullable=False,
            server_default="backend_agent",
        ),
    )
    op.add_column(
        "submissions",
        sa.Column(
            "grading_revision",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "submissions",
        sa.Column("grader_model_reported", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "submissions",
        sa.Column("graded_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("submissions", "graded_at")
    op.drop_column("submissions", "grader_model_reported")
    op.drop_column("submissions", "grading_revision")
    op.drop_column("submissions", "grading_mode")
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP TYPE IF EXISTS submission_grading_mode")
