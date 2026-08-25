"""Question string-id migration helper regressions."""

import importlib.util
from pathlib import Path

from app.services.question_identity import MAX_QUESTION_ID_LEN


def _migration_module():
    path = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "858c3ddac415_change_question_id_to_string_slug_.py"
    )
    spec = importlib.util.spec_from_file_location("question_id_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_duplicate_max_length_slug_stays_within_column_limit():
    migration = _migration_module()
    base = "q" * MAX_QUESTION_ID_LEN

    unique = migration._unique_slug(base, {base})

    assert unique.endswith("-2")
    assert len(unique) == MAX_QUESTION_ID_LEN
    assert unique != base


def test_downgrade_resets_sequence_and_restores_background_job_check(monkeypatch):
    migration = _migration_module()

    class RecordingOp:
        def __init__(self):
            self.executed: list[str] = []
            self.checks: list[str] = []

        def execute(self, statement):
            self.executed.append(str(statement))

        def create_check_constraint(self, name, *args, **kwargs):
            self.checks.append(name)

        def get_bind(self):
            class EmptyBind:
                @staticmethod
                def exec_driver_sql(_statement):
                    class EmptyResult:
                        @staticmethod
                        def fetchall():
                            return []

                    return EmptyResult()

            return EmptyBind()

        def __getattr__(self, _name):
            return lambda *args, **kwargs: None

    recording = RecordingOp()
    monkeypatch.setattr(migration, "op", recording)

    migration.downgrade()

    assert any("setval" in statement for statement in recording.executed)
    assert "ck_background_jobs_single_target" in recording.checks
