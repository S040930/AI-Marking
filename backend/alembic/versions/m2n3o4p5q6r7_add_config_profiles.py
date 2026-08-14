"""add config profiles

引入「配置项目」概念:
- 新建 ``config_profiles`` 表(项目元数据,name 唯一,is_default 标记默认项目)
- ``system_config`` 增加 ``profile_id`` 归属列,唯一约束由 (key) 改为 (profile_id, key)
- ``questions`` 增加 ``config_profile_id`` 绑定项目
- 现有配置行与题目全部回填到新建的「默认配置」项目,数据不丢失

Revision ID: m2n3o4p5q6r7
Revises: l7m8n9o0p1q2
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "m2n3o4p5q6r7"
down_revision: Union[str, None] = "l7m8n9o0p1q2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# SQLite 下未命名约束的自动命名约定(与 SQLAlchemy 默认一致)
_NC = {
    "uq": "uq_%(table_name)s_%(column_0_name)s",
}


def upgrade() -> None:
    bind = op.get_bind()

    op.create_table(
        "config_profiles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column(
            "is_default",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )

    # 插入默认配置项目
    default_profile_id = bind.execute(
        sa.text(
            "INSERT INTO config_profiles (name, is_default) "
            "VALUES ('默认配置', true) RETURNING id"
        )
    ).scalar()
    if default_profile_id is None:
        # SQLite 不支持 RETURNING
        op.execute(
            "INSERT INTO config_profiles (name, is_default) VALUES ('默认配置', 1)"
        )
        default_profile_id = bind.execute(
            sa.text("SELECT id FROM config_profiles WHERE name = '默认配置'")
        ).scalar_one()

    # --- system_config: profile_id + 唯一约束改 (profile_id, key) ---
    op.add_column(
        "system_config",
        sa.Column("profile_id", sa.Integer(), nullable=True),
    )
    bind.execute(
        sa.text("UPDATE system_config SET profile_id = :pid"),
        {"pid": default_profile_id},
    )

    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("system_config", naming_convention=_NC) as batch_op:
            batch_op.alter_column(
                "profile_id", existing_type=sa.Integer(), nullable=False
            )
            batch_op.drop_constraint("uq_system_config_key", type_="unique")
            batch_op.create_unique_constraint(
                "uq_system_config_profile_key", ["profile_id", "key"]
            )
            batch_op.create_foreign_key(
                "fk_system_config_profile_id",
                "config_profiles",
                ["profile_id"],
                ["id"],
                ondelete="CASCADE",
            )
        op.create_index("ix_system_config_profile_id", "system_config", ["profile_id"])
    else:
        op.alter_column("system_config", "profile_id", nullable=False)
        op.drop_constraint("system_config_key_key", "system_config", type_="unique")
        op.create_unique_constraint(
            "uq_system_config_profile_key",
            "system_config",
            ["profile_id", "key"],
        )
        op.create_foreign_key(
            "fk_system_config_profile_id",
            "system_config",
            "config_profiles",
            ["profile_id"],
            ["id"],
            ondelete="CASCADE",
        )
        op.create_index("ix_system_config_profile_id", "system_config", ["profile_id"])

    # --- questions: config_profile_id 绑定项目 ---
    op.add_column(
        "questions",
        sa.Column("config_profile_id", sa.Integer(), nullable=True),
    )
    bind.execute(
        sa.text("UPDATE questions SET config_profile_id = :pid"),
        {"pid": default_profile_id},
    )
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("questions") as batch_op:
            batch_op.alter_column(
                "config_profile_id", existing_type=sa.Integer(), nullable=False
            )
            batch_op.create_foreign_key(
                "fk_questions_config_profile_id",
                "config_profiles",
                ["config_profile_id"],
                ["id"],
                ondelete="RESTRICT",
            )
        op.create_index(
            "ix_questions_config_profile_id", "questions", ["config_profile_id"]
        )
    else:
        op.alter_column("questions", "config_profile_id", nullable=False)
        op.create_foreign_key(
            "fk_questions_config_profile_id",
            "questions",
            "config_profiles",
            ["config_profile_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        op.create_index(
            "ix_questions_config_profile_id", "questions", ["config_profile_id"]
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("questions", naming_convention=_NC) as batch_op:
            batch_op.drop_constraint(
                "fk_questions_config_profile_id", type_="foreignkey"
            )
            batch_op.drop_column("config_profile_id")
        op.drop_index("ix_questions_config_profile_id", table_name="questions")
        with op.batch_alter_table("system_config", naming_convention=_NC) as batch_op:
            batch_op.drop_constraint("fk_system_config_profile_id", type_="foreignkey")
            batch_op.drop_constraint("uq_system_config_profile_key", type_="unique")
            batch_op.create_unique_constraint("uq_system_config_key", ["key"])
            batch_op.drop_column("profile_id")
        op.drop_index("ix_system_config_profile_id", table_name="system_config")
    else:
        op.drop_index("ix_questions_config_profile_id", table_name="questions")
        op.drop_constraint(
            "fk_questions_config_profile_id", "questions", type_="foreignkey"
        )
        op.drop_column("questions", "config_profile_id")
        op.drop_index("ix_system_config_profile_id", table_name="system_config")
        op.drop_constraint(
            "fk_system_config_profile_id", "system_config", type_="foreignkey"
        )
        op.drop_constraint(
            "uq_system_config_profile_key", "system_config", type_="unique"
        )
        op.create_unique_constraint("uq_system_config_key", "system_config", ["key"])
        op.drop_column("system_config", "profile_id")
    op.drop_table("config_profiles")
