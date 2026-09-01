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


async def test_question_collection_listen_failure_releases_slot(monkeypatch):
    def fail_listen(*args, **kwargs):
        raise RuntimeError("listen failed")

    monkeypatch.setattr(events, "_listen", fail_listen)
    available_before = events._sse_slots._value
    await events.acquire_sse_slot()
    stream = events.question_collection_event_stream()
    with pytest.raises(RuntimeError, match="listen failed"):
        await anext(stream)
    assert events._sse_slots._value == available_before


def test_question_event_payload_is_fixed_shape(db_session, monkeypatch):
    executed = {}
    monkeypatch.setattr(events, "_is_postgres", lambda _db: True)

    def capture(statement, params):
        executed["statement"] = str(statement)
        executed["payload"] = params["payload"]

    monkeypatch.setattr(db_session, "execute", capture)
    events.notify_question_status(
        db_session, "essay", "ready", "processing"
    )
    assert executed["statement"] == "NOTIFY question_status, :payload"
    assert executed["payload"] == (
        '{"type": "question.changed", "question_id": "essay", '
        '"status": "ready", "replacement_status": "processing"}'
    )
