"""跨进程刷新租约的语义测试。

SQLite 下 `enforce=None` 直接放行(单进程/测试保持原行为);用 `enforce=True`
强制走算法验证占用、过期接管与释放语义。
"""

from __future__ import annotations

import json
import time

from sqlalchemy import select, update

from app.models.system_config import SystemConfig
from app.services.chatgpt_refresh_lease import (
    CHATGPT_REFRESH_LEASE_KEY,
    acquire_refresh_lease,
    release_refresh_lease,
)


def _row(db_session):
    return db_session.execute(
        select(SystemConfig).where(
            SystemConfig.key == CHATGPT_REFRESH_LEASE_KEY
        )
    ).scalar_one_or_none()


def test_sqlite_degrades_without_touching_db(db_session):
    assert acquire_refresh_lease(db_session, owner="a") is True
    assert _row(db_session) is None


def test_acquire_writes_payload(db_session):
    assert acquire_refresh_lease(db_session, owner="a", enforce=True) is True

    row = _row(db_session)
    assert row is not None
    payload = json.loads(row.value)
    assert payload["owner"] == "a"
    assert payload["acquired_at"] <= payload["expires_at"]
    assert payload["expires_at"] > time.time()


def test_live_lease_blocks_second_owner(db_session):
    assert acquire_refresh_lease(db_session, owner="a", enforce=True) is True
    assert acquire_refresh_lease(db_session, owner="b", enforce=True) is False

    payload = json.loads(_row(db_session).value)
    assert payload["owner"] == "a"


def test_expired_lease_can_be_taken_over(db_session):
    db_session.add(
        SystemConfig(
            key=CHATGPT_REFRESH_LEASE_KEY,
            value=json.dumps({"owner": "a", "expires_at": 0, "acquired_at": 0}),
        )
    )
    db_session.commit()

    assert acquire_refresh_lease(db_session, owner="b", enforce=True) is True
    assert json.loads(_row(db_session).value)["owner"] == "b"


def test_corrupt_lease_value_can_be_taken_over(db_session):
    db_session.add(SystemConfig(key=CHATGPT_REFRESH_LEASE_KEY, value="not-json"))
    db_session.commit()

    assert acquire_refresh_lease(db_session, owner="b", enforce=True) is True
    assert json.loads(_row(db_session).value)["owner"] == "b"


def test_release_only_by_owner(db_session):
    assert acquire_refresh_lease(db_session, owner="a", enforce=True) is True

    assert release_refresh_lease(db_session, owner="b", enforce=True) is False
    assert json.loads(_row(db_session).value)["owner"] == "a"

    assert release_refresh_lease(db_session, owner="a", enforce=True) is True
    assert _row(db_session).value == ""


def test_release_allows_next_owner(db_session):
    assert acquire_refresh_lease(db_session, owner="a", enforce=True) is True
    assert release_refresh_lease(db_session, owner="a", enforce=True) is True
    assert acquire_refresh_lease(db_session, owner="b", enforce=True) is True
    assert json.loads(_row(db_session).value)["owner"] == "b"


def test_same_owner_can_reacquire(db_session):
    assert acquire_refresh_lease(db_session, owner="a", enforce=True) is True
    db_session.execute(
        update(SystemConfig)
        .where(SystemConfig.key == CHATGPT_REFRESH_LEASE_KEY)
        .values(value=json.dumps({"owner": "a", "expires_at": time.time() + 60}))
    )
    db_session.commit()

    assert acquire_refresh_lease(db_session, owner="a", enforce=True) is True
