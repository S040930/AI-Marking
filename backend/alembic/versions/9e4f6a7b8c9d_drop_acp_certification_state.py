"""Remove the obsolete application-level ACP sandbox certification state.

Revision ID: 9e4f6a7b8c9d
Revises: 8d23135ed019
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "9e4f6a7b8c9d"
down_revision: Union[str, None] = "8d23135ed019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Keep installation and connection facts, but remove certification facts."""
    for column in (
        "certified_at",
        "certification_results",
        "certification_profile_version",
        "certification_platform",
        "certification_status",
    ):
        op.drop_column("acp_agent_installations", column)


def downgrade() -> None:
    """Restore the legacy columns for rollback of this migration only."""
    op.add_column(
        "acp_agent_installations",
        sa.Column(
            "certification_status",
            sa.String(length=16),
            server_default=sa.text("'unverified'"),
            nullable=False,
        ),
    )
    op.add_column(
        "acp_agent_installations",
        sa.Column("certification_platform", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "acp_agent_installations",
        sa.Column(
            "certification_profile_version", sa.String(length=32), nullable=True
        ),
    )
    op.add_column(
        "acp_agent_installations",
        sa.Column("certification_results", sa.JSON(), nullable=True),
    )
    op.add_column(
        "acp_agent_installations",
        sa.Column("certified_at", sa.DateTime(), nullable=True),
    )
