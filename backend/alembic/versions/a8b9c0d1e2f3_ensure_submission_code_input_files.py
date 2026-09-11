"""ensure submission_code_input_files table

Revision ID: a8b9c0d1e2f3
Revises: 9e4f6a7b8c9d
Create Date: 2026-09-10

``submission_code_input_files`` 的读侧链路(模型/关系/ACP 工作区物化/清理)
早已存在,但建表迁移随历史版本被清理,基线迁移(8d23135ed019)并不创建该表。
ZIP 上传恢复数据集写入后,此迁移为新建库补齐表;已有表的老库幂等跳过。
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a8b9c0d1e2f3"
down_revision: Union[str, None] = "9e4f6a7b8c9d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("submission_code_input_files"):
        return
    op.create_table(
        "submission_code_input_files",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("submission_id", sa.Integer(), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("file_path", sa.String(length=512), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["submission_id"],
            ["submissions.id"],
            name=op.f("fk_submission_code_input_files_submission_id_submissions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_submission_code_input_files")),
        sa.UniqueConstraint(
            "submission_id",
            "original_filename",
            name="uq_submission_code_input_filename",
        ),
    )
    op.create_index(
        op.f("ix_submission_code_input_files_submission_id"),
        "submission_code_input_files",
        ["submission_id"],
    )


def downgrade() -> None:
    # 仅供 downgrade 测试;正常不会执行。已有库的表可能早于本迁移存在,
    # 不主动删除以免破坏历史 schema。
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("submission_code_input_files"):
        op.drop_index(
            op.f("ix_submission_code_input_files_submission_id"),
            table_name="submission_code_input_files",
        )
        op.drop_table("submission_code_input_files")
