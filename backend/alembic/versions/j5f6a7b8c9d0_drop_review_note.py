"""drop review_note column from submissions

业务上 review_note 从未被读取或写入,属于历史遗留空列,从模型与数据库中移除。

Revision ID: j5f6a7b8c9d0
Revises: 86729507e0a1
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy import inspect

from alembic import op

revision: str = "j5f6a7b8c9d0"
down_revision: Union[str, None] = "86729507e0a1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    existing_columns = {col["name"] for col in inspector.get_columns("submissions")}
    if "review_note" in existing_columns:
        op.drop_column("submissions", "review_note")


def downgrade() -> None:
    op.add_column(
        "submissions", sa.Column("review_note", sa.Text(), nullable=True)
    )
