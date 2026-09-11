"""baseline

Revision ID: 8d23135ed019
Revises:
Create Date: 2026-09-09 15:53:47.425967

全量建表 baseline(由"仅 drop config_profiles"的旧版重写):

- 历史版本只包含"删除配置项目功能"的 schema 变更,新建库执行
  ``alembic upgrade head`` 后不会创建任何业务表;
- 现在本迁移按顺序做两件事:
  1. 幂等清理遗留的 config_profiles 及其外键列(只有存量旧库会命中,
     新建库/已迁移库直接跳过);
  2. 逐张创建全部业务表;每张表先经 ``sa.inspect`` 判断,已存在则整组
     (表 + 索引)跳过,已有库不受影响。
- ``acp_agent_installations`` 按 baseline 时刻的旧态建表(含 certification
  列),由后续 9e4f6a7b8c9d 统一移除,保证整条迁移链在新建库上可重放。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '8d23135ed019'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# baseline 时刻存在、后续迁移(9e4f6a7b8c9d)移除的 certification 列。
_LEGACY_CERTIFICATION_COLUMNS = (
    sa.Column(
        'certification_status',
        sa.String(length=16),
        server_default=sa.text("'unverified'"),
        nullable=False,
    ),
    sa.Column('certification_platform', sa.String(length=64), nullable=True),
    sa.Column('certification_profile_version', sa.String(length=32), nullable=True),
    sa.Column('certification_results', sa.JSON(), nullable=True),
    sa.Column('certified_at', sa.DateTime(), nullable=True),
)


def _inspector() -> sa.Inspector:
    return sa.inspect(op.get_bind())


def _column_names(inspector: sa.Inspector, table: str) -> set[str]:
    return {column["name"] for column in inspector.get_columns(table)}


def _index_names(inspector: sa.Inspector, table: str) -> set[str]:
    return {index["name"] for index in inspector.get_indexes(table)}


def _foreign_key_names(inspector: sa.Inspector, table: str) -> set[str]:
    return {fk["name"] for fk in inspector.get_foreign_keys(table) if fk["name"]}


def _unique_constraint_names(inspector: sa.Inspector, table: str) -> set[str]:
    return {
        constraint["name"]
        for constraint in inspector.get_unique_constraints(table)
        if constraint["name"]
    }


def _drop_config_profiles_legacy(inspector: sa.Inspector) -> None:
    """幂等清理 config_profiles 及其残留外键列(仅存量旧库需要)。"""
    if not inspector.has_table("config_profiles"):
        return
    if inspector.has_table("questions"):
        fks = _foreign_key_names(inspector, "questions")
        if op.f("fk_questions_config_profile_id") in fks:
            op.drop_constraint(
                op.f("fk_questions_config_profile_id"),
                "questions",
                type_="foreignkey",
            )
        indexes = _index_names(inspector, "questions")
        if "config_profile_id" in _column_names(inspector, "questions"):
            if op.f("ix_questions_config_profile_id") in indexes:
                op.drop_index(
                    op.f("ix_questions_config_profile_id"), table_name="questions"
                )
            op.drop_column("questions", "config_profile_id")
    if inspector.has_table("system_config"):
        fks = _foreign_key_names(inspector, "system_config")
        if op.f("fk_system_config_profile_id") in fks:
            op.drop_constraint(
                op.f("fk_system_config_profile_id"),
                "system_config",
                type_="foreignkey",
            )
        columns = _column_names(inspector, "system_config")
        if "profile_id" in columns:
            indexes = _index_names(inspector, "system_config")
            if op.f("ix_system_config_profile_id") in indexes:
                op.drop_index(
                    op.f("ix_system_config_profile_id"), table_name="system_config"
                )
            constraints = _unique_constraint_names(inspector, "system_config")
            if op.f("uq_system_config_profile_key") in constraints:
                op.drop_constraint(
                    op.f("uq_system_config_profile_key"),
                    "system_config",
                    type_="unique",
                )
            op.drop_column("system_config", "profile_id")
        if "uq_system_config_key" not in _unique_constraint_names(
            inspector, "system_config"
        ):
            op.create_unique_constraint(
                "uq_system_config_key", "system_config", ["key"]
            )
    if (
        op.f("uq_config_profiles_single_default")
        in _index_names(inspector, "config_profiles")
    ):
        op.drop_index(
            op.f("uq_config_profiles_single_default"),
            table_name="config_profiles",
            postgresql_where="is_default",
        )
    op.drop_table("config_profiles")


def upgrade() -> None:
    inspector = _inspector()
    _drop_config_profiles_legacy(inspector)
    _create_all_tables(inspector)


def _create_all_tables(inspector: sa.Inspector) -> None:
    # 每张表建表前先 inspect:已有库逐表跳过,新建库完整建出全部业务表。
    # acp_agent_installations 刻意保留 certification 旧列,供 9e4f6a7b8c9d
    # 在新建库上统一 drop(见模块 docstring)。
    if not inspector.has_table("questions"):
        op.create_table('questions',
        sa.Column('id', sa.String(length=100), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('original_filename', sa.String(length=255), nullable=False),
        sa.Column('file_path', sa.String(length=512), nullable=False),
        sa.Column('file_sha256', sa.String(length=64), nullable=True),
        sa.Column('ocr_text', sa.Text(), nullable=True),
        sa.Column('extracted_rubric', sa.Text(), nullable=True),
        sa.Column('extracted_rubric_items', sa.JSON(), nullable=True),
        sa.Column('extracted_rubric_ocr_hash', sa.String(length=71), nullable=True),
        sa.Column('extracted_rubric_version', sa.String(length=64), nullable=True),
        sa.Column('extracted_rubric_at', sa.DateTime(), nullable=True),
        sa.Column('status', sa.Enum('pending', 'ocr_processing', 'ready', 'failed', name='question_status'), server_default='pending', nullable=False),
        sa.Column('error_message', sa.String(length=1024), nullable=True),
        sa.Column('replacement_status', sa.Enum('pending', 'processing', 'failed', name='question_replacement_status'), nullable=True),
        sa.Column('replacement_file_path', sa.String(length=512), nullable=True),
        sa.Column('replacement_file_sha256', sa.String(length=64), nullable=True),
        sa.Column('replacement_original_filename', sa.String(length=255), nullable=True),
        sa.Column('replacement_error_message', sa.String(length=1024), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.Column('last_used_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id')
        )
        op.create_index(op.f('ix_questions_file_sha256'), 'questions', ['file_sha256'], unique=False)
        op.create_index('ix_questions_last_used_at', 'questions', ['last_used_at'], unique=False)
        op.create_index('ix_questions_name', 'questions', ['name'], unique=False)
        op.create_index(op.f('ix_questions_replacement_file_sha256'), 'questions', ['replacement_file_sha256'], unique=False)
    if not inspector.has_table("system_config"):
        op.create_table('system_config',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('key', sa.String(length=64), nullable=False),
        sa.Column('value', sa.Text(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('key', name='uq_system_config_key')
        )
    if not inspector.has_table("submissions"):
        op.create_table('submissions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('original_filename', sa.String(length=255), nullable=False),
        sa.Column('file_path', sa.String(length=512), nullable=False),
        sa.Column('file_sha256', sa.String(length=64), nullable=True),
        sa.Column('question_id', sa.String(length=100), nullable=False),
        sa.Column('status', sa.Enum('pending', 'ocr_processing', 'ocr_done', 'awaiting_mcp', 'ready_for_review', 'reviewed', 'failed', name='submission_status'), server_default='pending', nullable=False),
        sa.Column('grading_mode', sa.Enum('external_agent', name='submission_grading_mode'), server_default='external_agent', nullable=False),
        sa.Column('review_enabled', sa.Boolean(), nullable=True),
        sa.Column('grading_revision', sa.Integer(), server_default='0', nullable=False),
        sa.Column('graded_at', sa.DateTime(), nullable=True),
        sa.Column('ocr_text', sa.Text(), nullable=True),
        sa.Column('score', sa.Float(), nullable=True),
        sa.Column('max_score', sa.Float(), nullable=True),
        sa.Column('confidence', sa.Float(), nullable=True),
        sa.Column('feedback', sa.Text(), nullable=True),
        sa.Column('details', sa.JSON(), nullable=True),
        sa.Column('assessment_suggestion', sa.JSON(), nullable=True),
        sa.Column('assessment_review', sa.JSON(), nullable=True),
        sa.Column('reviewed_by', sa.String(length=100), nullable=True),
        sa.Column('reviewed_at', sa.DateTime(), nullable=True),
        sa.Column('error_message', sa.String(length=1024), nullable=True),
        sa.Column('uploaded_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.Column('completed_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['question_id'], ['questions.id'], ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('id')
        )
        op.create_index(op.f('ix_submissions_file_sha256'), 'submissions', ['file_sha256'], unique=False)
        op.create_index(op.f('ix_submissions_question_id'), 'submissions', ['question_id'], unique=False)
        op.create_index('ix_submissions_uploaded_at', 'submissions', ['uploaded_at'], unique=False)
    if not inspector.has_table("acp_chat_sessions"):
        op.create_table('acp_chat_sessions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('submission_id', sa.Integer(), nullable=False),
        sa.Column('agent_id', sa.String(length=64), nullable=False),
        sa.Column('permission_mode', sa.String(length=16), server_default='ask', nullable=False),
        sa.Column('codex_config', sa.JSON(), server_default='{}', nullable=False),
        sa.Column('applied_codex_config', sa.JSON(), nullable=True),
        sa.Column('pending_codex_config', sa.JSON(), nullable=True),
        sa.Column('status', sa.Enum('idle', 'running', 'waiting_permission', 'closed', 'error', name='acp_chat_status'), server_default='idle', nullable=False),
        sa.Column('acp_session_id', sa.String(length=128), nullable=True),
        sa.Column('session_resumable', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('host_pid', sa.Integer(), nullable=True),
        sa.Column('agent_pid', sa.Integer(), nullable=True),
        sa.Column('last_error', sa.String(length=1024), nullable=True),
        sa.Column('workspace_path', sa.String(length=512), nullable=True),
        sa.Column('next_event_seq', sa.Integer(), server_default='0', nullable=False),
        sa.Column('transcript_bytes', sa.Integer(), server_default='0', nullable=False),
        sa.Column('transcript_truncated', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.Column('closed_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['submission_id'], ['submissions.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
        )
        op.create_index('ix_acp_chat_sessions_status', 'acp_chat_sessions', ['status'], unique=False)
        op.create_index('ix_acp_chat_sessions_submission', 'acp_chat_sessions', ['submission_id'], unique=False)
    if not inspector.has_table("acp_runs"):
        op.create_table('acp_runs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('submission_id', sa.Integer(), nullable=False),
        sa.Column('agent_id', sa.String(length=64), nullable=False),
        sa.Column('agent_snapshot', sa.JSON(), nullable=False),
        sa.Column('permission_mode', sa.String(length=16), server_default='ask', nullable=False),
        sa.Column('codex_config', sa.JSON(), server_default='{}', nullable=False),
        sa.Column('applied_codex_config', sa.JSON(), nullable=True),
        sa.Column('pending_codex_config', sa.JSON(), nullable=True),
        sa.Column('status', sa.Enum('queued', 'starting', 'running', 'waiting_for_teacher', 'cancelling', 'completed', 'failed', 'cancelled', name='acp_run_status'), server_default='queued', nullable=False),
        sa.Column('worker_id', sa.String(length=128), nullable=True),
        sa.Column('claim_token', sa.String(length=64), nullable=True),
        sa.Column('lease_expires_at', sa.DateTime(), nullable=True),
        sa.Column('heartbeat_at', sa.DateTime(), nullable=True),
        sa.Column('acp_session_id', sa.String(length=128), nullable=True),
        sa.Column('session_resumable', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('checkpoint', sa.JSON(), nullable=True),
        sa.Column('checkpoint_asked_at', sa.DateTime(), nullable=True),
        sa.Column('checkpoint_expires_at', sa.DateTime(), nullable=True),
        sa.Column('teacher_verdict', sa.String(length=16), nullable=True),
        sa.Column('teacher_note', sa.Text(), nullable=True),
        sa.Column('teacher_answered_at', sa.DateTime(), nullable=True),
        sa.Column('error_message', sa.String(length=1024), nullable=True),
        sa.Column('attempts', sa.Integer(), server_default='0', nullable=False),
        sa.Column('transcript_bytes', sa.Integer(), server_default='0', nullable=False),
        sa.Column('next_event_seq', sa.Integer(), server_default='0', nullable=False),
        sa.Column('transcript_truncated', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('confirmation_revision', sa.Integer(), nullable=True),
        sa.Column('confirmation_context_hash', sa.String(length=71), nullable=True),
        sa.Column('cancel_requested_at', sa.DateTime(), nullable=True),
        sa.Column('workspace_path', sa.String(length=512), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.Column('finished_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['submission_id'], ['submissions.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
        )
        op.create_index('ix_acp_runs_status_lease', 'acp_runs', ['status', 'lease_expires_at'], unique=False)
        op.create_index('ix_acp_runs_submission', 'acp_runs', ['submission_id'], unique=False)
        op.create_index('uq_acp_runs_active_per_submission', 'acp_runs', ['submission_id'], unique=True, postgresql_where=sa.text("status NOT IN ('completed','failed','cancelled')"), sqlite_where=sa.text("status NOT IN ('completed','failed','cancelled')"))
    if not inspector.has_table("background_jobs"):
        op.create_table('background_jobs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('job_type', sa.Enum('question_ocr', 'question_replace', 'submission_ocr', name='background_job_type'), nullable=False),
        sa.Column('question_id', sa.String(length=100), nullable=True),
        sa.Column('submission_id', sa.Integer(), nullable=True),
        sa.Column('status', sa.Enum('queued', 'running', 'dead', name='background_job_status'), server_default='queued', nullable=False),
        sa.Column('attempts', sa.Integer(), server_default='0', nullable=False),
        sa.Column('available_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.Column('lease_expires_at', sa.DateTime(), nullable=True),
        sa.Column('worker_id', sa.String(length=128), nullable=True),
        sa.Column('claim_token', sa.String(length=36), nullable=True),
        sa.Column('last_error', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint('(question_id IS NOT NULL AND submission_id IS NULL) OR (question_id IS NULL AND submission_id IS NOT NULL)', name='ck_background_jobs_single_target'),
        sa.ForeignKeyConstraint(['question_id'], ['questions.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['submission_id'], ['submissions.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('question_id'),
        sa.UniqueConstraint('submission_id')
        )
        op.create_index('ix_background_jobs_dead_updated', 'background_jobs', [sa.literal_column('updated_at DESC')], unique=False, postgresql_where=sa.text("status = 'dead'"), sqlite_where=sa.text("status = 'dead'"))
        op.create_index('ix_background_jobs_queued', 'background_jobs', ['available_at', 'created_at'], unique=False, postgresql_where=sa.text("status = 'queued'"), sqlite_where=sa.text("status = 'queued'"))
        op.create_index('ix_background_jobs_running_lease', 'background_jobs', ['lease_expires_at', 'available_at', 'created_at'], unique=False, postgresql_where=sa.text("status = 'running'"), sqlite_where=sa.text("status = 'running'"))
    if not inspector.has_table("mcp_assessment_receipts"):
        op.create_table('mcp_assessment_receipts',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('submission_id', sa.Integer(), nullable=False),
        sa.Column('request_id', sa.String(length=36), nullable=False),
        sa.Column('handle_hash', sa.String(length=64), nullable=False),
        sa.Column('payload_hash', sa.String(length=64), nullable=False),
        sa.Column('response', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['submission_id'], ['submissions.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('submission_id', 'request_id', name='uq_mcp_receipt_submission_request')
        )
        op.create_index(op.f('ix_mcp_assessment_receipts_submission_id'), 'mcp_assessment_receipts', ['submission_id'], unique=False)
    if not inspector.has_table("mcp_workflow_handles"):
        op.create_table('mcp_workflow_handles',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('token_hash', sa.String(length=64), nullable=False),
        sa.Column('kind', sa.String(length=32), nullable=False),
        sa.Column('submission_id', sa.Integer(), nullable=False),
        sa.Column('question_id', sa.String(length=100), nullable=True),
        sa.Column('ocr_hash', sa.String(length=71), nullable=True),
        sa.Column('context_hash', sa.String(length=71), nullable=False),
        sa.Column('grading_revision', sa.Integer(), nullable=False),
        sa.Column('offset', sa.Integer(), nullable=True),
        sa.Column('context_complete', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('visual_confirmation', sa.String(length=32), nullable=True),
        sa.Column('visual_confirmation_note', sa.Text(), nullable=True),
        sa.Column('visual_confirmed_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['question_id'], ['questions.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['submission_id'], ['submissions.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('token_hash')
        )
        op.create_index('ix_mcp_workflow_handles_expires_at', 'mcp_workflow_handles', ['expires_at'], unique=False)
        op.create_index('ix_mcp_workflow_handles_question', 'mcp_workflow_handles', ['question_id'], unique=False)
        op.create_index('ix_mcp_workflow_handles_submission', 'mcp_workflow_handles', ['submission_id'], unique=False)
    if not inspector.has_table("submission_code_files"):
        op.create_table('submission_code_files',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('submission_id', sa.Integer(), nullable=False),
        sa.Column('question_number', sa.Integer(), nullable=False),
        sa.Column('entrypoint', sa.Boolean(), server_default='1', nullable=False),
        sa.Column('original_filename', sa.String(length=255), nullable=False),
        sa.Column('file_path', sa.String(length=512), nullable=False),
        sa.Column('file_kind', sa.String(length=16), nullable=False),
        sa.Column('source_sha256', sa.String(length=64), nullable=False),
        sa.Column('source_text', sa.Text(), nullable=True),
        sa.Column('execution_status', sa.Enum('pending', 'running', 'completed', 'failed', name='code_execution_status'), server_default='pending', nullable=False),
        sa.Column('execution_result', sa.JSON(), nullable=True),
        sa.Column('artifacts', sa.JSON(), nullable=True),
        sa.Column('visual_reviews', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['submission_id'], ['submissions.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('submission_id', 'original_filename', name='uq_submission_code_filename')
        )
        op.create_index(op.f('ix_submission_code_files_submission_id'), 'submission_code_files', ['submission_id'], unique=False)
    if not inspector.has_table("submission_code_input_files"):
        op.create_table('submission_code_input_files',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('submission_id', sa.Integer(), nullable=False),
        sa.Column('original_filename', sa.String(length=255), nullable=False),
        sa.Column('file_path', sa.String(length=512), nullable=False),
        sa.Column('size_bytes', sa.Integer(), nullable=False),
        sa.Column('sha256', sa.String(length=64), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['submission_id'], ['submissions.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('submission_id', 'original_filename', name='uq_submission_code_input_filename')
        )
        op.create_index(op.f('ix_submission_code_input_files_submission_id'), 'submission_code_input_files', ['submission_id'], unique=False)
    if not inspector.has_table("acp_chat_events"):
        op.create_table('acp_chat_events',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('session_id', sa.Integer(), nullable=False),
        sa.Column('seq', sa.Integer(), nullable=False),
        sa.Column('kind', sa.String(length=32), nullable=False),
        sa.Column('payload', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['session_id'], ['acp_chat_sessions.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
        )
        op.create_index('ix_acp_chat_events_session_seq', 'acp_chat_events', ['session_id', 'seq'], unique=True)
    if not inspector.has_table("acp_run_events"):
        op.create_table('acp_run_events',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('run_id', sa.Integer(), nullable=False),
        sa.Column('seq', sa.Integer(), nullable=False),
        sa.Column('kind', sa.String(length=32), nullable=False),
        sa.Column('payload', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['run_id'], ['acp_runs.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
        )
        op.create_index('ix_acp_run_events_run_seq', 'acp_run_events', ['run_id', 'seq'], unique=True)
    if not inspector.has_table("acp_agent_installations"):
        op.create_table('acp_agent_installations',
        sa.Column('agent_id', sa.String(length=64), nullable=False),
        sa.Column('version', sa.String(length=64), nullable=False),
        sa.Column('distribution', sa.String(length=16), nullable=False),
        sa.Column('launch_snapshot', sa.JSON(), nullable=False),
        sa.Column('is_default', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('connection_status', sa.String(length=16), server_default='unknown', nullable=False),
        sa.Column('connection_detail', sa.String(length=1024), nullable=True),
        *_LEGACY_CERTIFICATION_COLUMNS,
        sa.Column('tested_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('agent_id')
        )


def downgrade() -> None:
    # 先在 questions/system_config 仍在时恢复 config_profiles(原版逻辑,
    # 调整为先建被引用的 config_profiles 再建外键,原顺序无法执行);
    # 之后逆序 drop 全部业务表。枚举类型随之下清理,保证
    # downgrade → upgrade 可以重放(config_profiles 的再次清理由
    # upgrade 的幂等 legacy 段负责)。
    op.create_table('config_profiles',
    sa.Column('id', sa.INTEGER(), autoincrement=True, nullable=False),
    sa.Column('name', sa.VARCHAR(length=100), autoincrement=False, nullable=False),
    sa.Column('is_default', sa.BOOLEAN(), server_default=sa.text('false'), autoincrement=False, nullable=False),
    sa.Column('created_at', postgresql.TIMESTAMP(), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.Column('updated_at', postgresql.TIMESTAMP(), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('config_profiles_pkey')),
    sa.UniqueConstraint('name', name=op.f('config_profiles_name_key'), postgresql_include=[], postgresql_nulls_not_distinct=False)
    )
    op.create_index(op.f('uq_config_profiles_single_default'), 'config_profiles', ['is_default'], unique=True, postgresql_where='is_default')
    op.add_column('system_config', sa.Column('profile_id', sa.INTEGER(), autoincrement=False, nullable=False))
    op.create_foreign_key(op.f('fk_system_config_profile_id'), 'system_config', 'config_profiles', ['profile_id'], ['id'], ondelete='CASCADE')
    op.drop_constraint('uq_system_config_key', 'system_config', type_='unique')
    op.create_unique_constraint(op.f('uq_system_config_profile_key'), 'system_config', ['profile_id', 'key'], postgresql_nulls_not_distinct=False)
    op.create_index(op.f('ix_system_config_profile_id'), 'system_config', ['profile_id'], unique=False)
    op.add_column('questions', sa.Column('config_profile_id', sa.INTEGER(), autoincrement=False, nullable=False))
    op.create_foreign_key(op.f('fk_questions_config_profile_id'), 'questions', 'config_profiles', ['config_profile_id'], ['id'], ondelete='RESTRICT')
    op.create_index(op.f('ix_questions_config_profile_id'), 'questions', ['config_profile_id'], unique=False)
    # 与 upgrade 的建表一一对应逆序 drop。
    op.drop_index('ix_acp_run_events_run_seq', table_name='acp_run_events')
    op.drop_table('acp_run_events')
    op.drop_index('ix_acp_chat_events_session_seq', table_name='acp_chat_events')
    op.drop_table('acp_chat_events')
    op.drop_index(op.f('ix_submission_code_files_submission_id'), table_name='submission_code_files')
    op.drop_table('submission_code_files')
    # submission_code_input_files 的 drop 由 a8b9c0d1e2f3.downgrade 负责
    # (downgrade 链先于本迁移执行,此处再删会报"表不存在")。
    op.drop_index('ix_mcp_workflow_handles_submission', table_name='mcp_workflow_handles')
    op.drop_index('ix_mcp_workflow_handles_question', table_name='mcp_workflow_handles')
    op.drop_index('ix_mcp_workflow_handles_expires_at', table_name='mcp_workflow_handles')
    op.drop_table('mcp_workflow_handles')
    op.drop_index(op.f('ix_mcp_assessment_receipts_submission_id'), table_name='mcp_assessment_receipts')
    op.drop_table('mcp_assessment_receipts')
    op.drop_index('ix_background_jobs_running_lease', table_name='background_jobs', postgresql_where=sa.text("status = 'running'"), sqlite_where=sa.text("status = 'running'"))
    op.drop_index('ix_background_jobs_queued', table_name='background_jobs', postgresql_where=sa.text("status = 'queued'"), sqlite_where=sa.text("status = 'queued'"))
    op.drop_index('ix_background_jobs_dead_updated', table_name='background_jobs', postgresql_where=sa.text("status = 'dead'"), sqlite_where=sa.text("status = 'dead'"))
    op.drop_table('background_jobs')
    op.drop_index('uq_acp_runs_active_per_submission', table_name='acp_runs', postgresql_where=sa.text("status NOT IN ('completed','failed','cancelled')"), sqlite_where=sa.text("status NOT IN ('completed','failed','cancelled')"))
    op.drop_index('ix_acp_runs_submission', table_name='acp_runs')
    op.drop_index('ix_acp_runs_status_lease', table_name='acp_runs')
    op.drop_table('acp_runs')
    op.drop_index('ix_acp_chat_sessions_submission', table_name='acp_chat_sessions')
    op.drop_index('ix_acp_chat_sessions_status', table_name='acp_chat_sessions')
    op.drop_table('acp_chat_sessions')
    op.drop_index('ix_submissions_uploaded_at', table_name='submissions')
    op.drop_index(op.f('ix_submissions_question_id'), table_name='submissions')
    op.drop_index(op.f('ix_submissions_file_sha256'), table_name='submissions')
    op.drop_table('submissions')
    op.drop_table('system_config')
    op.drop_index(op.f('ix_questions_replacement_file_sha256'), table_name='questions')
    op.drop_index('ix_questions_name', table_name='questions')
    op.drop_index('ix_questions_last_used_at', table_name='questions')
    op.drop_index(op.f('ix_questions_file_sha256'), table_name='questions')
    op.drop_table('questions')
    op.drop_table('acp_agent_installations')
    for enum_name in (
        "acp_chat_status",
        "acp_run_status",
        "background_job_status",
        "background_job_type",
        "code_execution_status",
        "submission_grading_mode",
        "submission_status",
        "question_replacement_status",
        "question_status",
    ):
        op.execute(f"DROP TYPE IF EXISTS {enum_name}")
