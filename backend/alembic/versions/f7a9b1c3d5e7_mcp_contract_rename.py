"""Rename MCP workflow contracts to grading contracts (Migration B).

自研 agent 改造(Migration B,独立可回滚):

- RENAME ``mcp_workflow_handles``→``grading_handles``、
  ``mcp_assessment_receipts``→``grading_assessment_receipts``
  (序列/索引/主键/外键/唯一约束同步改名);
- ``submission_status`` 枚举值 ``awaiting_mcp``→``awaiting_grading``;
- ``submission_grading_mode`` 枚举值 ``external_agent``→``agent``。

约束名不按字面改:同一张表在不同历史库里可能有两套命名——由
``sa.UniqueConstraint('token_hash')`` 建出的库拿到 PG 自动名
``mcp_workflow_handles_token_hash_key``,而早期显式命名的库是
``uq_mcp_workflow_handles_token_hash``。因此这里按
(表, 约束类型, 列集合) 反查 ``pg_constraint`` 再改名,两种库都能升级。
``rename_table`` 不会重命名约束/索引/序列,故仍需显式处理。

迁移前必须停止 API 与 worker;SQLite 测试库走 create_all,不受本迁移影响。

Revision ID: f7a9b1c3d5e7
Revises: e5f7a9b1c3d4
"""

from typing import Sequence, Union

from sqlalchemy import text

from alembic import op

revision: str = "f7a9b1c3d5e7"
down_revision: Union[str, None] = "e5f7a9b1c3d4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# (表, 约束类型, 列集合, 规范旧名, 新名)
# 旧名只用于 downgrade 回写;upgrade 时按结构反查,不依赖旧名命中。
_CONSTRAINTS = (
    (
        "grading_handles",
        "p",
        ("id",),
        "mcp_workflow_handles_pkey",
        "grading_handles_pkey",
    ),
    (
        "grading_handles",
        "u",
        ("token_hash",),
        "mcp_workflow_handles_token_hash_key",
        "grading_handles_token_hash_key",
    ),
    (
        "grading_handles",
        "f",
        ("submission_id",),
        "mcp_workflow_handles_submission_id_fkey",
        "grading_handles_submission_id_fkey",
    ),
    (
        "grading_handles",
        "f",
        ("question_id",),
        "mcp_workflow_handles_question_id_fkey",
        "grading_handles_question_id_fkey",
    ),
    (
        "grading_assessment_receipts",
        "p",
        ("id",),
        "mcp_assessment_receipts_pkey",
        "grading_assessment_receipts_pkey",
    ),
    (
        "grading_assessment_receipts",
        "f",
        ("submission_id",),
        "mcp_assessment_receipts_submission_id_fkey",
        "grading_assessment_receipts_submission_id_fkey",
    ),
    (
        "grading_assessment_receipts",
        "u",
        ("submission_id", "request_id"),
        "uq_mcp_receipt_submission_request",
        "uq_grading_receipt_submission_request",
    ),
)

_INDEX_RENAMES = (
    (
        "ix_mcp_workflow_handles_expires_at",
        "ix_grading_handles_expires_at",
    ),
    ("ix_mcp_workflow_handles_question", "ix_grading_handles_question"),
    ("ix_mcp_workflow_handles_submission", "ix_grading_handles_submission"),
    (
        "ix_mcp_assessment_receipts_submission_id",
        "ix_grading_assessment_receipts_submission_id",
    ),
)


def _constraint_name(table: str, contype: str, columns: tuple[str, ...]) -> str | None:
    """按 (表, 类型, 列集合) 反查约束真实名;找不到返回 None。"""
    row = op.get_bind().execute(
        text(
            """
            SELECT c.conname
            FROM pg_constraint AS c
            JOIN pg_class AS t ON t.oid = c.conrelid
            WHERE t.relname = :table
              AND c.contype = :contype
              AND (
                    SELECT string_agg(a.attname, ',' ORDER BY a.attname)
                    FROM unnest(c.conkey) AS k(attnum)
                    JOIN pg_attribute AS a
                      ON a.attrelid = c.conrelid AND a.attnum = k.attnum
                  ) = :columns
            ORDER BY c.conname
            LIMIT 1
            """
        ),
        {
            "table": table,
            "contype": contype,
            "columns": ",".join(sorted(columns)),
        },
    ).scalar()
    return str(row) if row is not None else None


def _rename_constraint(table: str, contype: str, columns: tuple[str, ...], target: str) -> None:
    current = _constraint_name(table, contype, columns)
    if current is None:
        raise RuntimeError(
            f"{table} 缺少 {contype} 约束({', '.join(columns)}),无法改名为 {target}"
        )
    if current == target:
        return
    op.execute(f'ALTER TABLE "{table}" RENAME CONSTRAINT "{current}" TO "{target}"')


def upgrade() -> None:
    # 1. 表改名
    op.rename_table("mcp_workflow_handles", "grading_handles")
    op.rename_table("mcp_assessment_receipts", "grading_assessment_receipts")

    # 2. 序列改名(列默认值经 OID 引用,改名不影响 DEFAULT)
    op.execute(
        "ALTER SEQUENCE IF EXISTS mcp_workflow_handles_id_seq "
        "RENAME TO grading_handles_id_seq"
    )
    op.execute(
        "ALTER SEQUENCE IF EXISTS mcp_assessment_receipts_id_seq "
        "RENAME TO grading_assessment_receipts_id_seq"
    )

    # 3. 约束改名(按结构反查,兼容自动名与显式名两种历史库)
    for table, contype, columns, _old_name, new_name in _CONSTRAINTS:
        _rename_constraint(table, contype, columns, new_name)

    # 4. 索引改名
    for old_name, new_name in _INDEX_RENAMES:
        op.execute(f'ALTER INDEX IF EXISTS "{old_name}" RENAME TO "{new_name}"')

    # 5. 枚举值改名(PG 10+ 支持 RENAME VALUE,原地生效,无数据重写)
    op.execute(
        "ALTER TYPE submission_status RENAME VALUE 'awaiting_mcp' "
        "TO 'awaiting_grading'"
    )
    op.execute(
        "ALTER TYPE submission_grading_mode RENAME VALUE 'external_agent' "
        "TO 'agent'"
    )


def downgrade() -> None:
    """回滚:枚举值与表/约束/索引名恢复;数据本身不丢失。"""
    op.execute(
        "ALTER TYPE submission_grading_mode RENAME VALUE 'agent' "
        "TO 'external_agent'"
    )
    op.execute(
        "ALTER TYPE submission_status RENAME VALUE 'awaiting_grading' "
        "TO 'awaiting_mcp'"
    )

    for old_name, new_name in reversed(_INDEX_RENAMES):
        op.execute(f'ALTER INDEX IF EXISTS "{new_name}" RENAME TO "{old_name}"')

    for table, contype, columns, old_name, _new_name in reversed(_CONSTRAINTS):
        _rename_constraint(table, contype, columns, old_name)

    op.execute(
        "ALTER SEQUENCE IF EXISTS grading_assessment_receipts_id_seq "
        "RENAME TO mcp_assessment_receipts_id_seq"
    )
    op.execute(
        "ALTER SEQUENCE IF EXISTS grading_handles_id_seq "
        "RENAME TO mcp_workflow_handles_id_seq"
    )

    op.rename_table("grading_assessment_receipts", "mcp_assessment_receipts")
    op.rename_table("grading_handles", "mcp_workflow_handles")
