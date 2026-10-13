"""Rename ACP grading runs to grading_runs and drop chat/installation tables.

自研 agent 改造(Migration A):

- 清空非终态 ``acp_runs`` 及其事件(重试语义由新 run 承担);
- DROP ``acp_chat_sessions``/``acp_chat_events``/``acp_agent_installations``;
- 清空 ``mcp_workflow_handles``(句柄 TTL 变更后旧句柄作废;回执保留);
- RENAME ``acp_runs``→``grading_runs``、``acp_run_events``→``grading_run_events``
  (序列/索引/枚举类型同步改名);
- DROP ``agent_id``/``agent_snapshot``/``permission_mode``/``codex_config``/
  ``applied_codex_config``/``pending_codex_config``/``acp_session_id``/
  ``session_resumable``;
- ADD ``provider``(必填)/``model``/``reasoning_effort``/``applied_config``/
  ``pending_config``/``agent_state``/``usage``。

迁移前必须停止 API 与 worker;SQLite 测试库走 create_all,不受本迁移影响。

Revision ID: e5f7a9b1c3d4
Revises: c3d5e7f9a1b2
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "e5f7a9b1c3d4"
down_revision: Union[str, None] = "c3d5e7f9a1b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_GRADING_RUNS = "grading_runs"
_GRADING_RUN_EVENTS = "grading_run_events"

_LEGACY_CONFIG_COLUMNS = (
    "agent_id",
    "agent_snapshot",
    "permission_mode",
    "codex_config",
    "applied_codex_config",
    "pending_codex_config",
    "acp_session_id",
    "session_resumable",
)

_NEW_COLUMNS = (
    ("model", sa.String(128)),
    ("reasoning_effort", sa.String(64)),
    ("applied_config", sa.JSON()),
    ("pending_config", sa.JSON()),
    ("agent_state", sa.JSON()),
    ("usage", sa.JSON()),
)


def upgrade() -> None:
    # 1. 清空非终态 run 及其事件(运行中的批改不接受跨架构迁移)
    op.execute(
        "DELETE FROM acp_run_events WHERE run_id IN ("
        "SELECT id FROM acp_runs "
        "WHERE status NOT IN ('completed','failed','cancelled'))"
    )
    op.execute(
        "DELETE FROM acp_runs WHERE status NOT IN ('completed','failed','cancelled')"
    )
    # 2. 删除已移除功能的表(对话与 agent 安装)
    # IF EXISTS:downgrade 不重建这些表,回滚后再次升级需容忍缺席
    op.execute("DROP TABLE IF EXISTS acp_chat_events")
    op.execute("DROP TABLE IF EXISTS acp_chat_sessions")
    op.execute("DROP TABLE IF EXISTS acp_agent_installations")
    # 3. 旧评分句柄作废;回执(map 幂等证据)保留
    op.execute("DELETE FROM mcp_workflow_handles")

    # 4. 表与序列改名
    op.rename_table("acp_runs", _GRADING_RUNS)
    op.execute(
        "ALTER SEQUENCE IF EXISTS acp_runs_id_seq RENAME TO grading_runs_id_seq"
    )
    op.rename_table("acp_run_events", _GRADING_RUN_EVENTS)
    op.execute(
        "ALTER SEQUENCE IF EXISTS acp_run_events_id_seq "
        "RENAME TO grading_run_events_id_seq"
    )

    # 5. 索引改名
    op.execute(
        "ALTER INDEX uq_acp_runs_active_per_submission "
        "RENAME TO uq_grading_runs_active_per_submission"
    )
    op.execute(
        "ALTER INDEX ix_acp_runs_status_lease "
        "RENAME TO ix_grading_runs_status_lease"
    )
    op.execute(
        "ALTER INDEX ix_acp_runs_submission RENAME TO ix_grading_runs_submission"
    )
    op.execute(
        "ALTER INDEX ix_acp_run_events_run_seq "
        "RENAME TO ix_grading_run_events_run_seq"
    )

    # 6. 枚举类型改名
    op.execute("ALTER TYPE acp_run_status RENAME TO grading_run_status")

    # 6b. 主键/外键约束改名(rename_table 不会重命名约束)
    op.execute(
        "ALTER TABLE grading_runs RENAME CONSTRAINT "
        "acp_runs_pkey TO grading_runs_pkey"
    )
    op.execute(
        "ALTER TABLE grading_runs RENAME CONSTRAINT "
        "acp_runs_submission_id_fkey TO grading_runs_submission_id_fkey"
    )
    op.execute(
        "ALTER TABLE grading_run_events RENAME CONSTRAINT "
        "acp_run_events_pkey TO grading_run_events_pkey"
    )
    op.execute(
        "ALTER TABLE grading_run_events RENAME CONSTRAINT "
        "acp_run_events_run_id_fkey TO grading_run_events_run_id_fkey"
    )

    # 7. 删除 Codex/会话专属列
    for column in _LEGACY_CONFIG_COLUMNS:
        op.drop_column(_GRADING_RUNS, column)

    # 8. 新增 provider(必填,存量终态行回填默认通道)
    op.add_column(
        _GRADING_RUNS, sa.Column("provider", sa.String(32), nullable=True)
    )
    op.execute(
        "UPDATE grading_runs SET provider = 'openai_api' WHERE provider IS NULL"
    )
    op.alter_column(_GRADING_RUNS, "provider", nullable=False)
    for name, column_type in _NEW_COLUMNS:
        op.add_column(_GRADING_RUNS, sa.Column(name, column_type, nullable=True))


def downgrade() -> None:
    """Best-effort 回滚:恢复表名/索引/枚举与旧列。

    被清空的非终态运行、对话表与句柄数据无法恢复。
    """
    for name, _ in reversed(_NEW_COLUMNS):
        op.drop_column(_GRADING_RUNS, name)
    op.drop_column(_GRADING_RUNS, "provider")
    for column in reversed(_LEGACY_CONFIG_COLUMNS):
        if column == "session_resumable":
            op.add_column(
                _GRADING_RUNS,
                sa.Column(column, sa.Boolean(), nullable=False, server_default="false"),
            )
        elif column in ("agent_id", "permission_mode"):
            op.add_column(
                _GRADING_RUNS, sa.Column(column, sa.String(64), nullable=True)
            )
        else:
            op.add_column(_GRADING_RUNS, sa.Column(column, sa.JSON(), nullable=True))

    op.execute("ALTER TYPE grading_run_status RENAME TO acp_run_status")
    # 约束名回退(表名回退前)
    op.execute(
        "ALTER TABLE grading_runs RENAME CONSTRAINT "
        "grading_runs_pkey TO acp_runs_pkey"
    )
    op.execute(
        "ALTER TABLE grading_runs RENAME CONSTRAINT "
        "grading_runs_submission_id_fkey TO acp_runs_submission_id_fkey"
    )
    op.execute(
        "ALTER TABLE grading_run_events RENAME CONSTRAINT "
        "grading_run_events_pkey TO acp_run_events_pkey"
    )
    op.execute(
        "ALTER TABLE grading_run_events RENAME CONSTRAINT "
        "grading_run_events_run_id_fkey TO acp_run_events_run_id_fkey"
    )
    op.execute(
        "ALTER INDEX ix_grading_run_events_run_seq "
        "RENAME TO ix_acp_run_events_run_seq"
    )
    op.execute(
        "ALTER INDEX ix_grading_runs_submission RENAME TO ix_acp_runs_submission"
    )
    op.execute(
        "ALTER INDEX ix_grading_runs_status_lease RENAME TO ix_acp_runs_status_lease"
    )
    op.execute(
        "ALTER INDEX uq_grading_runs_active_per_submission "
        "RENAME TO uq_acp_runs_active_per_submission"
    )
    op.rename_table(_GRADING_RUN_EVENTS, "acp_run_events")
    op.execute(
        "ALTER SEQUENCE IF EXISTS grading_run_events_id_seq "
        "RENAME TO acp_run_events_id_seq"
    )
    op.rename_table(_GRADING_RUNS, "acp_runs")
    op.execute(
        "ALTER SEQUENCE IF EXISTS grading_runs_id_seq RENAME TO acp_runs_id_seq"
    )
