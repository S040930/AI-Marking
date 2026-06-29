"""remove legacy baidu_ocr_* config keys

Revision ID: a1b2c3d4e5f6
Revises: 872a4bdf9609
Create Date: 2026-06-26 20:00:00.000000

Switching from 百度云 OCR (accurate_basic) to PaddleOCR-VL (aistudio.baidu.com).
The old baidu_ocr_api_key / baidu_ocr_secret_key rows are no longer read by
marking pipeline; drop them to keep the table clean.
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "872a4bdf9609"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "DELETE FROM system_config WHERE key IN "
        "('baidu_ocr_api_key', 'baidu_ocr_secret_key')"
    )


def downgrade() -> None:
    # 数据已删除,downgrade 仅保留占位;若需回滚,需从备份恢复
    pass
