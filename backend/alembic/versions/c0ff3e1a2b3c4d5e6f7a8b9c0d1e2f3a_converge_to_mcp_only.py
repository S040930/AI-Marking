"""Converge to MCP-only grading: drop backend Agent records and fields.

Revision ID: c0ff3e1a2b3c4d5e6f7a8b9c0d1e2f3a
Revises: y9z0a1b2c3d4

变更内容:
- 分批删除所有 ``grading_mode='backend_agent'`` 的 submission(100 条/批),
  依赖外键级联删除 conversations / code files / code input files / MCP 关联;
  删除成功后清理磁盘 PDF(校验路径位于 UPLOAD_DIR 内),文件删除失败记录路径并失败。
- 重建 ``submission_status`` 枚举:移除 ``agent_grading/agent_reviewing/agent_revising``,
  ``awaiting_external_agent`` 改名为 ``awaiting_mcp``。
- 重建 ``submission_grading_mode`` 枚举:移除 ``backend_agent``(仅剩 external_agent)。
- 重建 ``background_job_type`` 枚举:``submission_marking`` 改名为 ``submission_ocr``。
- ``ai_suggestion`` 重命名为 ``assessment_suggestion``。
- 删除废弃列:``ai_result/agent_trace/review_reason/code_runtime/code_visual_assets``。
- 删除 ``conversations`` 表。
- ``mcp_workflow_handles`` 新增 ``question_id/ocr_hash`` 列(rubric 提取句柄)。
"""

import os
import shutil
from pathlib import Path

import sqlalchemy as sa

from alembic import op

revision = "c0ff3e1a2b3c4d5e6f7a8b9c0d1e2f3a"
down_revision = "y9z0a1b2c3d4"
branch_labels = None
depends_on = None

_STATUSES = (
    "pending",
    "ocr_processing",
    "ocr_done",
    "awaiting_mcp",
    "ready_for_review",
    "reviewed",
    "failed",
)

_BATCH_SIZE = 100

# 状态枚举值来自模块级常量，SQL 在加载时确定，不随运行时输入变化。
_STATUS_ENUM_SQL = (
    "CREATE TYPE submission_status AS ENUM ("
    + ", ".join(f"'{value}'" for value in _STATUSES)
    + ")"
)


def _upload_root() -> Path:
    """上传目录(与 app.core.config 一致,相对 CWD 解析)。"""
    configured = os.environ.get("UPLOAD_DIR", "./uploads")
    return Path(configured).resolve()


def _delete_backend_agent_submissions() -> None:
    """分批删除 backend_agent 记录并清理磁盘文件。

    使用独立 engine/session(不占用 alembic 迁移事务),每批删除 DB 行并提交
    后,再校验路径位于 UPLOAD_DIR 内并删除磁盘文件。任何路径不在上传目录内
    直接抛错,绝不删除目录之外的内容;文件删除失败记录明确路径并抛错,
    允许针对残留文件重试迁移。
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from app.core.config import settings

    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    upload_root = _upload_root()
    engine = create_engine(settings.DATABASE_URL)
    try:
        while True:
            with Session(engine) as db:
                rows = (
                    db.execute(
                        sa.text(
                            "SELECT id, file_path FROM submissions "
                            "WHERE grading_mode = 'backend_agent' "
                            "ORDER BY id LIMIT :batch"
                        ),
                        {"batch": _BATCH_SIZE},
                    )
                    .mappings()
                    .all()
                )
                if not rows:
                    return
                ids = [r["id"] for r in rows]
                paths: list[str] = [r["file_path"] for r in rows if r["file_path"]]
                for sid in ids:
                    code_paths = (
                        db.execute(
                            sa.text(
                                "SELECT file_path FROM submission_code_files "
                                "WHERE submission_id = :sid"
                            ),
                            {"sid": sid},
                        )
                        .scalars()
                        .all()
                    )
                    input_paths = (
                        db.execute(
                            sa.text(
                                "SELECT file_path FROM submission_code_input_files "
                                "WHERE submission_id = :sid"
                            ),
                            {"sid": sid},
                        )
                        .scalars()
                        .all()
                    )
                    paths.extend(code_paths)
                    paths.extend(input_paths)
                db.execute(
                    sa.text("DELETE FROM submissions WHERE id = ANY(:ids)"),
                    {"ids": ids},
                )
                db.commit()
                # DB 删除是权威来源;提交成功后再删文件,避免"记录仍在但 PDF 丢失"。
                for raw in paths:
                    path = Path(raw).resolve()
                    if upload_root != path and not str(path).startswith(
                        str(upload_root)
                    ):
                        raise RuntimeError(
                            f"拒绝删除上传目录之外的路径: {path} "
                            f"(UPLOAD_DIR={upload_root})"
                        )
                    try:
                        path.unlink(missing_ok=True)
                    except OSError as exc:
                        raise RuntimeError(
                            f"删除文件失败: {path}: {exc}"
                        ) from exc
                for sid in ids:
                    artifact_root = upload_root / "code-artifacts" / str(sid)
                    shutil.rmtree(artifact_root, ignore_errors=True)
    finally:
        engine.dispose()


def upgrade() -> None:
    bind = op.get_bind()

    # 1. 分批删除历史 backend_agent 记录(含磁盘清理)
    _delete_backend_agent_submissions()

    if bind.dialect.name == "postgresql":
        # 2. 重建 submission_status 枚举
        op.execute("ALTER TABLE submissions ALTER COLUMN status DROP DEFAULT")
        # 先转 text 再改名,避免旧枚举类型不识别新值
        op.execute(
            "ALTER TABLE submissions ALTER COLUMN status TYPE text USING status::text"
        )
        op.execute(
            "UPDATE submissions SET status = 'awaiting_mcp' "
            "WHERE status = 'awaiting_external_agent'"
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

        # 3. 重建 submission_grading_mode 枚举(仅剩 external_agent)
        op.execute("ALTER TABLE submissions ALTER COLUMN grading_mode DROP DEFAULT")
        op.execute(
            "ALTER TABLE submissions ALTER COLUMN grading_mode TYPE text "
            "USING grading_mode::text"
        )
        op.execute("DROP TYPE submission_grading_mode")
        op.execute("CREATE TYPE submission_grading_mode AS ENUM ('external_agent')")
        op.execute(
            "ALTER TABLE submissions ALTER COLUMN grading_mode "
            "TYPE submission_grading_mode USING grading_mode::submission_grading_mode"
        )
        op.execute(
            "ALTER TABLE submissions ALTER COLUMN grading_mode SET DEFAULT 'external_agent'"
        )

        # 4. 重建 background_job_type 枚举
        # 先转 text 再改名,避免旧枚举类型不识别新值
        op.execute(
            "ALTER TABLE background_jobs ALTER COLUMN job_type TYPE text "
            "USING job_type::text"
        )
        op.execute(
            "UPDATE background_jobs SET job_type = 'submission_ocr' "
            "WHERE job_type = 'submission_marking'"
        )
        op.execute("DROP TYPE background_job_type")
        op.execute(
            "CREATE TYPE background_job_type AS ENUM "
            "('question_ocr', 'question_replace', 'submission_ocr')"
        )
        op.execute(
            "ALTER TABLE background_jobs ALTER COLUMN job_type "
            "TYPE background_job_type USING job_type::background_job_type"
        )

    # 5. 字段重命名与删除(对 PG 与 SQLite 通用)
    op.alter_column(
        "submissions", "ai_suggestion", new_column_name="assessment_suggestion"
    )
    for column in (
        "ai_result",
        "agent_trace",
        "review_reason",
        "code_runtime",
        "code_visual_assets",
    ):
        op.drop_column("submissions", column)

    # 6. 删除 conversations 表
    op.drop_table("conversations")

    # 7. mcp_workflow_handles 新增 rubric 提取句柄字段
    op.add_column(
        "mcp_workflow_handles",
        sa.Column(
            "question_id",
            sa.Integer(),
            sa.ForeignKey("questions.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    op.add_column(
        "mcp_workflow_handles",
        sa.Column("ocr_hash", sa.String(length=71), nullable=True),
    )
    op.create_index(
        "ix_mcp_workflow_handles_question", "mcp_workflow_handles", ["question_id"]
    )


def downgrade() -> None:
    bind = op.get_bind()
    op.drop_index("ix_mcp_workflow_handles_question", table_name="mcp_workflow_handles")
    op.drop_column("mcp_workflow_handles", "ocr_hash")
    op.drop_column("mcp_workflow_handles", "question_id")

    op.create_table(
        "conversations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "submission_id",
            sa.Integer(),
            sa.ForeignKey("submissions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("suggestion", sa.JSON(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_conversations_submission_id", "conversations", ["submission_id"]
    )
    op.add_column(
        "submissions", sa.Column("code_visual_assets", sa.JSON(), nullable=True)
    )
    op.add_column("submissions", sa.Column("code_runtime", sa.JSON(), nullable=True))
    op.add_column("submissions", sa.Column("review_reason", sa.Text(), nullable=True))
    op.add_column("submissions", sa.Column("agent_trace", sa.JSON(), nullable=True))
    op.add_column("submissions", sa.Column("ai_result", sa.JSON(), nullable=True))
    op.alter_column(
        "submissions", "assessment_suggestion", new_column_name="ai_suggestion"
    )
    if bind.dialect.name == "postgresql":
        # 枚举重建为完整旧形态(含 backend_agent / agent_* / awaiting_external_agent)
        op.execute(
            "ALTER TABLE background_jobs ALTER COLUMN job_type TYPE text USING job_type::text"
        )
        op.execute("DROP TYPE background_job_type")
        op.execute(
            "CREATE TYPE background_job_type AS ENUM "
            "('question_ocr', 'question_replace', 'submission_marking')"
        )
        op.execute(
            "ALTER TABLE background_jobs ALTER COLUMN job_type "
            "TYPE background_job_type USING job_type::background_job_type"
        )
        op.execute(
            "ALTER TABLE submissions ALTER COLUMN grading_mode TYPE text USING grading_mode::text"
        )
        op.execute("DROP TYPE submission_grading_mode")
        op.execute(
            "CREATE TYPE submission_grading_mode AS ENUM ('backend_agent', 'external_agent')"
        )
        op.execute(
            "ALTER TABLE submissions ALTER COLUMN grading_mode TYPE submission_grading_mode "
            "USING grading_mode::submission_grading_mode"
        )
        op.execute(
            "ALTER TABLE submissions ALTER COLUMN grading_mode SET DEFAULT 'backend_agent'"
        )
        op.execute(
            "ALTER TABLE submissions ALTER COLUMN status TYPE text USING status::text"
        )
        op.execute("DROP TYPE submission_status")
        old_statuses = (
            "pending",
            "ocr_processing",
            "ocr_done",
            "awaiting_external_agent",
            "agent_grading",
            "agent_reviewing",
            "agent_revising",
            "ready_for_review",
            "reviewed",
            "failed",
        )
        op.execute(
            "CREATE TYPE submission_status AS ENUM "
            + "(" + ", ".join(f"'{v}'" for v in old_statuses) + ")"
        )
        op.execute(
            "ALTER TABLE submissions ALTER COLUMN status TYPE submission_status "
            "USING status::submission_status"
        )
        op.execute(
            "ALTER TABLE submissions ALTER COLUMN status SET DEFAULT 'pending'"
        )
