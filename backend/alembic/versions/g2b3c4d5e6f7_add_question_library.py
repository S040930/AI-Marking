"""add reusable question library

Revision ID: g2b3c4d5e6f7
Revises: f1a2b3c4d5e6
"""

from typing import Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "g2b3c4d5e6f7"
down_revision: Union[str, None] = "f1a2b3c4d5e6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    question_status = postgresql.ENUM(
        "pending",
        "ocr_processing",
        "ready",
        "failed",
        name="question_status",
        create_type=False,
    )
    question_status.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "questions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("original_filename", sa.String(255), nullable=False),
        sa.Column("file_path", sa.String(512), nullable=False),
        sa.Column("ocr_text", sa.Text(), nullable=True),
        sa.Column("status", question_status, server_default="pending", nullable=False),
        sa.Column("error_message", sa.String(1024), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_questions_name", "questions", ["name"])
    op.create_index("ix_questions_last_used_at", "questions", ["last_used_at"])
    op.add_column("submissions", sa.Column("question_id", sa.Integer(), nullable=True))

    # 每条旧记录独立迁移，刻意不按文件名合并，避免不同题目被误判为同题。
    op.execute(
        sa.text(
            """
            INSERT INTO questions
                (name, original_filename, file_path, ocr_text, status,
                 error_message, created_at, updated_at, last_used_at)
            SELECT
                COALESCE(question_original_filename, '历史题目'),
                COALESCE(question_original_filename, 'question.pdf'),
                COALESCE(question_file_path, ''),
                question_ocr_text,
                CASE WHEN question_ocr_text IS NOT NULL THEN 'ready'::question_status
                     ELSE 'failed'::question_status END,
                CASE WHEN question_ocr_text IS NULL THEN '历史题目缺少 OCR 内容' ELSE NULL END,
                uploaded_at, uploaded_at, uploaded_at
            FROM submissions
            ORDER BY id
            """
        )
    )
    op.execute(
        sa.text(
            """
            WITH ranked_submissions AS (
                SELECT id, row_number() OVER (ORDER BY id) AS rn FROM submissions
            ),
            ranked_questions AS (
                SELECT id, row_number() OVER (ORDER BY id) AS rn FROM questions
            )
            UPDATE submissions s
            SET question_id = q.id
            FROM ranked_submissions rs
            JOIN ranked_questions q ON q.rn = rs.rn
            WHERE s.id = rs.id
            """
        )
    )
    op.alter_column("submissions", "question_id", nullable=False)
    op.create_index("ix_submissions_question_id", "submissions", ["question_id"])
    op.create_foreign_key(
        "fk_submissions_question_id_questions",
        "submissions",
        "questions",
        ["question_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.drop_column("submissions", "question_ocr_text")
    op.drop_column("submissions", "question_file_path")
    op.drop_column("submissions", "question_original_filename")


def downgrade() -> None:
    op.add_column(
        "submissions", sa.Column("question_original_filename", sa.String(255))
    )
    op.add_column("submissions", sa.Column("question_file_path", sa.String(512)))
    op.add_column("submissions", sa.Column("question_ocr_text", sa.Text()))
    op.execute(
        sa.text(
            """
            UPDATE submissions s SET
                question_original_filename = q.original_filename,
                question_file_path = q.file_path,
                question_ocr_text = q.ocr_text
            FROM questions q WHERE q.id = s.question_id
            """
        )
    )
    op.drop_constraint(
        "fk_submissions_question_id_questions", "submissions", type_="foreignkey"
    )
    op.drop_index("ix_submissions_question_id", table_name="submissions")
    op.drop_column("submissions", "question_id")
    op.drop_index("ix_questions_last_used_at", table_name="questions")
    op.drop_index("ix_questions_name", table_name="questions")
    op.drop_table("questions")
    sa.Enum(name="question_status").drop(op.get_bind(), checkfirst=True)
