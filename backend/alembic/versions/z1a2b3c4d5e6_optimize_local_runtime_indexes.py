"""Optimize local runtime indexes and enforce one default profile.

Revision ID: z1a2b3c4d5e6
Revises: b7c8d9e0f1a2
"""

import sqlalchemy as sa

from alembic import op

revision = "z1a2b3c4d5e6"
down_revision = "b7c8d9e0f1a2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            UPDATE config_profiles
            SET is_default = false
            WHERE is_default = true
              AND id <> (
                  SELECT min(id) FROM config_profiles WHERE is_default = true
              )
            """
        )
    )
    op.create_index(
        "uq_config_profiles_single_default",
        "config_profiles",
        ["is_default"],
        unique=True,
        postgresql_where=sa.text("is_default"),
    )
    op.drop_index("ix_background_jobs_claim", table_name="background_jobs")
    op.create_index(
        "ix_background_jobs_queued",
        "background_jobs",
        ["available_at", "created_at"],
        postgresql_where=sa.text("status = 'queued'"),
    )
    op.create_index(
        "ix_background_jobs_running_lease",
        "background_jobs",
        ["lease_expires_at", "available_at", "created_at"],
        postgresql_where=sa.text("status = 'running'"),
    )
    op.create_index(
        "ix_background_jobs_dead_updated",
        "background_jobs",
        [sa.text("updated_at DESC")],
        postgresql_where=sa.text("status = 'dead'"),
    )


def downgrade() -> None:
    op.drop_index("ix_background_jobs_dead_updated", table_name="background_jobs")
    op.drop_index("ix_background_jobs_running_lease", table_name="background_jobs")
    op.drop_index("ix_background_jobs_queued", table_name="background_jobs")
    op.create_index(
        "ix_background_jobs_claim",
        "background_jobs",
        ["status", "available_at", "lease_expires_at", "created_at"],
    )
    op.drop_index(
        "uq_config_profiles_single_default", table_name="config_profiles"
    )
