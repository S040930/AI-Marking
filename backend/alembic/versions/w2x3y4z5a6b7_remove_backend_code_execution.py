"""Remove the server-side code execution state from the submission workflow."""

from alembic import op

revision = "w2x3y4z5a6b7"
down_revision = "v1w2x3y4z5a6"
branch_labels = None
depends_on = None

_STATUSES = (
    "pending",
    "ocr_processing",
    "ocr_done",
    "awaiting_codex",
    "agent_grading",
    "agent_reviewing",
    "agent_revising",
    "ready_for_review",
    "reviewed",
    "failed",
)

# 状态枚举值来自模块级常量，SQL 在加载时确定，不随运行时输入变化。
_STATUS_ENUM_SQL = (
    "CREATE TYPE submission_status AS ENUM ("
    + ", ".join(f"'{value}'" for value in _STATUSES)
    + ")"
)


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    op.execute(
        "UPDATE submissions SET status = 'awaiting_codex' "
        "WHERE status = 'code_processing'"
    )
    op.execute("ALTER TABLE submissions ALTER COLUMN status DROP DEFAULT")
    op.execute(
        "ALTER TABLE submissions ALTER COLUMN status TYPE text "
        "USING status::text"
    )
    op.execute("DROP TYPE submission_status")
    op.execute(_STATUS_ENUM_SQL)
    op.execute(
        "ALTER TABLE submissions ALTER COLUMN status TYPE submission_status "
        "USING status::submission_status"
    )
    op.execute(
        "ALTER TABLE submissions ALTER COLUMN status SET DEFAULT 'pending'"
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(
            "ALTER TYPE submission_status ADD VALUE IF NOT EXISTS 'code_processing'"
        )
