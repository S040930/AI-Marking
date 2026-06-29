"""rename doubao_* config keys to llm_*

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-06-26 20:30:00.000000

Making the LLM config generic (OpenAI compatible) instead of Doubao-specific.
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b2c3d4e5f6a7"
down_revision: Union[str, None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_RENAME_MAP = {
    "doubao_api_key": "llm_api_key",
    "doubao_base_url": "llm_base_url",
    "doubao_model": "llm_model",
}


def upgrade() -> None:
    for old_key, new_key in _RENAME_MAP.items():
        op.execute(
            f"UPDATE system_config SET key = '{new_key}', "
            f"description = description WHERE key = '{old_key}'"
        )


def downgrade() -> None:
    for old_key, new_key in _RENAME_MAP.items():
        op.execute(
            f"UPDATE system_config SET key = '{old_key}' WHERE key = '{new_key}'"
        )
