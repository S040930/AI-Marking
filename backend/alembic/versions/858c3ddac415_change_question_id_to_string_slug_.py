"""change question_id to string slug primary key

Revision ID: 858c3ddac415
Revises: e2f3a4b5c6d7
Create Date: 2026-08-22 14:37:10.440008

将 questions 主键与 3 张子表外键从自增整数改为字符串 slug(基于创建时
original_filename 去扩展名生成)。存量数据按 created_at 顺序逐行用与运行时
相同的 build_question_id 生成 slug;重名自动追加 -2/-3 后缀保证唯一(运行时
新创建由 API 层拒绝重名,此处仅处理历史数据)。

迁移顺序:
1. questions 增加 id_new(String) 并回填 slug(旧 id 暂保留)。
2. 3 张子表各增加 question_id_new(String),按旧 question_id 逐行回填新 slug。
3. 删除子表旧外键与 questions 旧主键,删除旧 id 列,重命名 id_new/id。
4. 重建主键、外键、唯一约束与索引。
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op
from app.services.question_identity import MAX_QUESTION_ID_LEN, build_question_id

# revision identifiers, used by Alembic.
revision: str = '858c3ddac415'
down_revision: Union[str, None] = 'e2f3a4b5c6d7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_QUESTION_ID_TYPE = sa.String(MAX_QUESTION_ID_LEN)

# (表名, 旧外键名, 旧索引名, 旧 unique 约束名, 外键 ondelete, 新列是否 NOT NULL)
# 注意:只有 submissions.question_id 原为 NOT NULL;另两表原为 nullable。
_SUB_TABLES = [
    ("submissions", "fk_submissions_question_id_questions",
     "ix_submissions_question_id", None, "RESTRICT", True),
    ("background_jobs", "background_jobs_question_id_fkey",
     None, "uq_background_jobs_question_id", "CASCADE", False),
    ("mcp_workflow_handles", "mcp_workflow_handles_question_id_fkey",
     "ix_mcp_workflow_handles_question", None, "CASCADE", False),
]


def _slug_map() -> dict[int, str]:
    """按 created_at 顺序为每条题目生成唯一 slug(重名加 -2/-3 后缀)。"""
    bind = op.get_bind()
    rows = bind.exec_driver_sql(
        "SELECT id, original_filename FROM questions ORDER BY created_at, id"
    ).fetchall()
    used: set[str] = set()
    mapping: dict[int, str] = {}
    for row_id, original_filename in rows:
        base = build_question_id(original_filename or "question") or "question"
        slug = _unique_slug(base, used)
        used.add(slug)
        mapping[row_id] = slug
    return mapping


def _unique_slug(base: str, used: set[str]) -> str:
    """Return a unique slug without ever exceeding the model column length."""
    slug = base[:MAX_QUESTION_ID_LEN]
    suffix = 2
    while slug in used:
        suffix_text = f"-{suffix}"
        prefix = base[: MAX_QUESTION_ID_LEN - len(suffix_text)].rstrip("-")
        slug = f"{prefix}{suffix_text}"
        suffix += 1
    return slug


def _backfill_sub_table(table: str, mapping: dict[int, str]) -> None:
    """按旧 question_id 逐行回填 question_id_new(WHERE 一律用 id,值已 sanitize)。"""
    bind = op.get_bind()
    rows = bind.exec_driver_sql(
        f"SELECT id, question_id FROM {table} WHERE question_id IS NOT NULL"
    ).fetchall()
    for row_id, old_question_id in rows:
        slug = mapping.get(old_question_id)
        if slug is None:
            continue
        op.execute(
            f"UPDATE {table} SET question_id_new = '{slug}' WHERE id = {row_id}"
        )


def _restore_sub_table(table: str, mapping: dict[str, int]) -> None:
    """反向回填:按字符串 question_id 逐行换回整数(WHERE 一律用 id)。"""
    bind = op.get_bind()
    rows = bind.exec_driver_sql(
        f"SELECT id, question_id FROM {table} WHERE question_id IS NOT NULL"
    ).fetchall()
    for row_id, slug in rows:
        new_id = mapping.get(slug)
        if new_id is None:
            continue
        op.execute(
            f"UPDATE {table} SET question_id_new = {new_id} WHERE id = {row_id}"
        )


def upgrade() -> None:
    mapping = _slug_map()

    # 1) questions:加临时 id_new 列并回填 slug(旧 id 保留到子表回填完成)。
    op.add_column("questions", sa.Column("id_new", _QUESTION_ID_TYPE, nullable=True))
    for old_id, slug in mapping.items():
        op.execute(
            f"UPDATE questions SET id_new = '{slug}' WHERE id = {old_id}"
        )
    op.alter_column("questions", "id_new", nullable=False)

    # 2) 子表:加 question_id_new 列,按旧 question_id 回填新 slug。
    for table, _fk, _idx, _uq, _ondelete, _not_null in _SUB_TABLES:
        op.add_column(
            table,
            sa.Column("question_id_new", _QUESTION_ID_TYPE, nullable=True),
        )
        _backfill_sub_table(table, mapping)

    # 3) 删除旧外键与旧主键(顺序:先子表 FK,再 questions PK)。
    for table, fk_name, _idx, _uq, _ondelete, _not_null in _SUB_TABLES:
        op.drop_constraint(fk_name, table, type_="foreignkey")
    op.drop_constraint("questions_pkey", "questions", type_="primary")

    # 4) questions:删旧 id 列(连同其默认序列),重命名 id_new -> id,重建主键。
    op.drop_column("questions", "id")
    op.alter_column("questions", "id_new", new_column_name="id")
    op.create_primary_key("questions_pkey", "questions", ["id"])

    # 5) 子表:删旧 question_id 列,重命名 question_id_new -> question_id,
    #    重建外键、唯一约束与索引。
    for table, fk_name, index_name, unique_name, ondelete, not_null in _SUB_TABLES:
        op.drop_column(table, "question_id")
        op.alter_column(table, "question_id_new", new_column_name="question_id")
        if not_null:
            op.alter_column(table, "question_id", nullable=False)
        op.create_foreign_key(
            fk_name, table, "questions", ["question_id"], ["id"],
            ondelete=ondelete,
        )
        if unique_name is not None:
            op.create_unique_constraint(unique_name, table, ["question_id"])
        if index_name is not None:
            op.create_index(index_name, table, ["question_id"])


def downgrade() -> None:
    bind = op.get_bind()
    rows = bind.exec_driver_sql(
        "SELECT id FROM questions ORDER BY created_at, id"
    ).fetchall()
    int_map: dict[str, int] = {}
    for idx, (slug,) in enumerate(rows, start=1):
        int_map[slug] = idx

    op.add_column("questions", sa.Column("id_new", sa.Integer(), nullable=True))
    for slug, new_id in int_map.items():
        op.execute(
            f"UPDATE questions SET id_new = {new_id} WHERE id = '{slug}'"
        )
    op.alter_column("questions", "id_new", nullable=False)

    for table, _fk, _idx, _uq, _ondelete, _not_null in _SUB_TABLES:
        op.add_column(
            table, sa.Column("question_id_new", sa.Integer(), nullable=True)
        )
        _restore_sub_table(table, int_map)

    for table, fk_name, _idx, _uq, _ondelete, _not_null in _SUB_TABLES:
        op.drop_constraint(fk_name, table, type_="foreignkey")
    op.drop_constraint("questions_pkey", "questions", type_="primary")

    op.drop_column("questions", "id")
    op.alter_column("questions", "id_new", new_column_name="id")
    op.create_primary_key("questions_pkey", "questions", ["id"])
    # 反向:字符串 id 已按 created_at 重排为 1..n,序列从 1 开始即可续用。
    op.execute("CREATE SEQUENCE IF NOT EXISTS questions_id_seq OWNED BY questions.id")
    op.alter_column(
        "questions",
        "id",
        server_default=sa.text("nextval('questions_id_seq')"),
    )
    op.execute(
        """
        SELECT setval(
            'questions_id_seq',
            COALESCE((SELECT MAX(id) FROM questions), 1),
            EXISTS (SELECT 1 FROM questions)
        )
        """
    )

    for table, fk_name, index_name, unique_name, ondelete, not_null in _SUB_TABLES:
        op.drop_column(table, "question_id")
        op.alter_column(table, "question_id_new", new_column_name="question_id")
        if not_null:
            op.alter_column(table, "question_id", nullable=False)
        op.create_foreign_key(
            fk_name, table, "questions", ["question_id"], ["id"],
            ondelete=ondelete,
        )
        if unique_name is not None:
            op.create_unique_constraint(unique_name, table, ["question_id"])
        if index_name is not None:
            op.create_index(index_name, table, ["question_id"])
    op.create_check_constraint(
        "ck_background_jobs_single_target",
        "background_jobs",
        "(question_id IS NOT NULL AND submission_id IS NULL) OR "
        "(question_id IS NULL AND submission_id IS NOT NULL)",
    )
