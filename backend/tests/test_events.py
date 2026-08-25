"""SSE resource lifecycle regression tests."""

import pytest
from sqlalchemy.orm import sessionmaker

from app.services import events


async def test_listen_failure_releases_acquired_sse_slot(db_session, monkeypatch):
    factory = sessionmaker(bind=db_session.bind, expire_on_commit=False)
    monkeypatch.setattr(events, "SessionLocal", factory)

    def fail_listen(*args, **kwargs):
        raise RuntimeError("listen failed")

    monkeypatch.setattr(events, "_listen", fail_listen)
    available_before = events._sse_slots._value
    await events.acquire_sse_slot()

    stream = events.submission_event_stream(999999)
    with pytest.raises(RuntimeError, match="listen failed"):
        await anext(stream)

    assert events._sse_slots._value == available_before
