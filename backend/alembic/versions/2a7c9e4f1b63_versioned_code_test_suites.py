"""Add question-scoped code test suites and generalized checkpoint answers.

Revision ID: 2a7c9e4f1b63
Revises: c177625dbb36
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "2a7c9e4f1b63"
down_revision: Union[str, None] = "c177625dbb36"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "grading_runs",
        sa.Column("regenerate_code_tests", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("grading_runs", sa.Column("checkpoint_answer", sa.JSON(), nullable=True))
    op.add_column(
        "grading_runs", sa.Column("checkpoint_answered_at", sa.DateTime(), nullable=True)
    )
    op.create_table(
        "code_test_suites",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("question_id", sa.String(length=100), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("source_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("tests", sa.JSON(), nullable=False),
        sa.Column("generated_by_run_id", sa.Integer(), nullable=True),
        sa.Column("approved_at", sa.DateTime(), nullable=True),
        sa.Column("invalidated_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["question_id"], ["questions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["generated_by_run_id"], ["grading_runs.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("question_id", "version", name="uq_code_test_suites_question_version"),
    )
    op.create_index(
        "ix_code_test_suites_question_status", "code_test_suites", ["question_id", "status"]
    )


def downgrade() -> None:
    op.drop_index("ix_code_test_suites_question_status", table_name="code_test_suites")
    op.drop_table("code_test_suites")
    op.drop_column("grading_runs", "checkpoint_answered_at")
    op.drop_column("grading_runs", "checkpoint_answer")
    op.drop_column("grading_runs", "regenerate_code_tests")
