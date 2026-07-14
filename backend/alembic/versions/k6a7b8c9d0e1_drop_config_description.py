"""drop description column from system_config

description 仅在 upsert 时写入,从未被任何代码读取(ConfigOut 也不含该字段),
属于只写不读的死数据,从模型与数据库中移除。

Revision ID: k6a7b8c9d0e1
Revises: j5f6a7b8c9d0
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy import inspect

from alembic import op

revision: str = "k6a7b8c9d0e1"
down_revision: Union[str, None] = "j5f6a7b8c9d0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    existing_columns = {
        col["name"] for col in inspector.get_columns("system_config")
    }
    if "description" in existing_columns:
        op.drop_column("system_config", "description")


def downgrade() -> None:
    op.add_column(
        "system_config", sa.Column("description", sa.String(length=255), nullable=True)
    )
