"""langgraph grading artifacts

Revision ID: c177625dbb36
Revises: f7a9b1c3d5e7
Create Date: 2026-10-17 22:58:48.696539
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'c177625dbb36'
down_revision: Union[str, None] = 'f7a9b1c3d5e7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "grading_run_artifacts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "run_id",
            sa.Integer(),
            sa.ForeignKey("grading_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("meta", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "run_id",
            "kind",
            "sha256",
            name="uq_grading_run_artifacts_run_kind_sha",
        ),
    )
    op.create_index(
        "ix_grading_run_artifacts_run",
        "grading_run_artifacts",
        ["run_id"],
    )
    op.add_column(
        "grading_runs",
        sa.Column(
            "graph_version",
            sa.Integer(),
            server_default="1",
            nullable=False,
        ),
    )
    op.add_column(
        "grading_runs",
        sa.Column("checkpoint_request_id", sa.String(length=64), nullable=True),
    )
    # 一次性切换：旧架构的进行中 run 不能由新 worker 续跑，直接终止；
    # 历史终态 run 保留，便于教师查看。
    op.execute(
        sa.text(
            "UPDATE grading_runs SET "
            "status='failed', "
            "error_message='架构升级：进行中的批改已终止，请重新发起', "
            "finished_at=now(), "
            "claim_token=NULL, worker_id=NULL, lease_expires_at=NULL, "
            "checkpoint=NULL, checkpoint_request_id=NULL "
            "WHERE status NOT IN ('completed','failed','cancelled')"
        )
    )


def downgrade() -> None:
    op.drop_column("grading_runs", "checkpoint_request_id")
    op.drop_column("grading_runs", "graph_version")
    op.drop_index(
        "ix_grading_run_artifacts_run", table_name="grading_run_artifacts"
    )
    op.drop_table("grading_run_artifacts")
