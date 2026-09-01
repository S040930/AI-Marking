"""Exercise the latest migration against duplicate default-profile data."""

from __future__ import annotations

from uuid import uuid4

from alembic.config import Config
from sqlalchemy import select
from sqlalchemy.engine import make_url

from alembic import command
from app.core.config import settings
from app.db.session import SessionLocal
from app.models.config_profile import ConfigProfile

PREVIOUS_REVISION = "b7c8d9e0f1a2"


def _require_test_database() -> None:
    database = make_url(settings.DATABASE_URL).database or ""
    if not database.endswith("_test"):
        raise SystemExit(
            "refusing migration check: DATABASE_URL database must end with _test"
        )


def main() -> None:
    _require_test_database()
    config = Config("alembic.ini")
    command.downgrade(config, PREVIOUS_REVISION)

    suffix = uuid4().hex
    with SessionLocal() as db:
        existing_ids = list(
            db.scalars(
                select(ConfigProfile.id).where(ConfigProfile.is_default.is_(True))
            )
        )
        added = [
            ConfigProfile(name=f"migration-default-a-{suffix}", is_default=True),
            ConfigProfile(name=f"migration-default-b-{suffix}", is_default=True),
        ]
        db.add_all(added)
        db.commit()
        expected_default_id = min([*existing_ids, *(item.id for item in added)])

    command.upgrade(config, "head")
    with SessionLocal() as db:
        default_ids = list(
            db.scalars(
                select(ConfigProfile.id)
                .where(ConfigProfile.is_default.is_(True))
                .order_by(ConfigProfile.id)
            )
        )
    if default_ids != [expected_default_id]:
        raise SystemExit(
            f"default-profile normalization failed: expected {expected_default_id}, "
            f"got {default_ids}"
        )

    # Prove that the latest revision remains reversible after normalizing data.
    command.downgrade(config, PREVIOUS_REVISION)
    command.upgrade(config, "head")
    print(f"runtime migration check passed; retained default id={expected_default_id}")


if __name__ == "__main__":
    main()
