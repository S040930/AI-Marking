"""add durable background jobs

Revision ID: h3c4d5e6f7a8
Revises: g2b3c4d5e6f7
"""

from typing import Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "h3c4d5e6f7a8"
down_revision: Union[str, None] = "g2b3c4d5e6f7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    job_type = postgresql.ENUM(
        "question_ocr",
        "submission_marking",
        name="background_job_type",
        create_type=False,
    )
    job_status = postgresql.ENUM(
        "queued",
        "running",
        "dead",
        name="background_job_status",
        create_type=False,
    )
    job_type.create(op.get_bind(), checkfirst=True)
    job_status.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "background_jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("job_type", job_type, nullable=False),
        sa.Column(
            "question_id",
            sa.Integer(),
            sa.ForeignKey("questions.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "submission_id",
            sa.Integer(),
            sa.ForeignKey("submissions.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "status", job_status, server_default="queued", nullable=False
        ),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "available_at", sa.DateTime(), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("lease_expires_at", sa.DateTime(), nullable=True),
        sa.Column("worker_id", sa.String(128), nullable=True),
        sa.Column("claim_token", sa.String(36), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "(question_id IS NOT NULL AND submission_id IS NULL) OR "
            "(question_id IS NULL AND submission_id IS NOT NULL)",
            name="ck_background_jobs_single_target",
        ),
        sa.UniqueConstraint("question_id", name="uq_background_jobs_question_id"),
        sa.UniqueConstraint(
            "submission_id", name="uq_background_jobs_submission_id"
        ),
    )
    op.create_index(
        "ix_background_jobs_claim",
        "background_jobs",
        ["status", "available_at", "lease_expires_at", "created_at"],
    )
    # 升级时恢复旧进程内任务留下的非终态记录，避免部署后永久卡住。
    op.execute(
        sa.text(
            """
            INSERT INTO background_jobs
                (job_type, question_id, status, attempts, available_at,
                 created_at, updated_at)
            SELECT
                'question_ocr'::background_job_type,
                id,
                'queued'::background_job_status,
                0,
                now(),
                now(),
                now()
            FROM questions
            WHERE status IN ('pending', 'ocr_processing')
            """
        )
    )
    op.execute(
        sa.text(
            """
            INSERT INTO background_jobs
                (job_type, submission_id, status, attempts, available_at,
                 created_at, updated_at)
            SELECT
                'submission_marking'::background_job_type,
                id,
                'queued'::background_job_status,
                0,
                now(),
                now(),
                now()
            FROM submissions
            WHERE status IN (
                'pending', 'ocr_processing', 'ocr_done', 'agent_grading',
                'agent_reviewing', 'agent_revising'
            )
            """
        )
    )


def downgrade() -> None:
    op.drop_index("ix_background_jobs_claim", table_name="background_jobs")
    op.drop_table("background_jobs")
    sa.Enum(name="background_job_status").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="background_job_type").drop(op.get_bind(), checkfirst=True)
