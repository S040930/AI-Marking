"""Generalize codex grading names to external_agent.

Revision ID: y9z0a1b2c3d4
Revises: w2x3y4z5a6b7
"""

from alembic import op

revision = "y9z0a1b2c3d4"
down_revision = "w2x3y4z5a6b7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(
            "ALTER TYPE submission_status RENAME VALUE 'awaiting_codex'"
            " TO 'awaiting_external_agent'"
        )
        op.execute(
            "ALTER TYPE submission_grading_mode RENAME VALUE 'codex'"
            " TO 'external_agent'"
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(
            "ALTER TYPE submission_status RENAME VALUE 'awaiting_external_agent'"
            " TO 'awaiting_codex'"
        )
        op.execute(
            "ALTER TYPE submission_grading_mode RENAME VALUE 'external_agent'"
            " TO 'codex'"
        )
