"""Drop the mandatory-second-pass switch (MCP API v12).

单次证据化评分成为正常路径，交叉复核是独立可选能力，"第二遍自检"开关
失去语义：删除 ``submissions.review_enabled`` 列与 ``system_config`` 中
的 ``review_enabled`` 配置行。

Revision ID: c3d5e7f9a1b2
Revises: a8b9c0d1e2f3
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "c3d5e7f9a1b2"
down_revision: Union[str, None] = "a8b9c0d1e2f3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column("submissions", "review_enabled")
    op.execute(
        "DELETE FROM system_config WHERE key = 'review_enabled'"
    )


def downgrade() -> None:
    """Restore the legacy column for rollback of this migration only."""
    op.add_column(
        "submissions",
        sa.Column("review_enabled", sa.Boolean(), nullable=True),
    )
