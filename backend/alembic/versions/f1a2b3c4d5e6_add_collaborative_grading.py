"""add collaborative grading schema

Revision ID: f1a2b3c4d5e6
Revises: e5f6a7b8c9d0

变更内容:
- 新增 submissions.ai_suggestion 字段(JSON,Agent 完成时的建议分快照)
- 调整 submission_status enum:新增 ready_for_review/reviewed
  (不删除旧值 llm_processing/review_required/done,PostgreSQL 不支持直接删除 enum 值;
  旧值在代码层已不再使用,数据库保留不影响功能)
- 新增 conversations 表(教师-AI 对话历史)
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "f1a2b3c4d5e6"
down_revision: Union[str, None] = "e5f6a7b8c9d0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. submissions 表新增 ai_suggestion 字段
    op.add_column(
        "submissions",
        sa.Column("ai_suggestion", sa.JSON(), nullable=True),
    )

    # 2. 调整 submission_status enum:新增 ready_for_review/reviewed
    #    PostgreSQL 支持 ADD VALUE IF NOT EXISTS,旧值保留不删
    op.execute(
        "ALTER TYPE submission_status ADD VALUE IF NOT EXISTS 'ready_for_review'"
    )
    op.execute("ALTER TYPE submission_status ADD VALUE IF NOT EXISTS 'reviewed'")

    # 3. 新增 conversations 表
    op.create_table(
        "conversations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "submission_id",
            sa.Integer(),
            sa.ForeignKey("submissions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_conversations_submission_id",
        "conversations",
        ["submission_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_conversations_submission_id", table_name="conversations"
    )
    op.drop_table("conversations")
    op.drop_column("submissions", "ai_suggestion")
    # enum 新增值无法回滚(PostgreSQL 不支持 DROP VALUE),保持现状
