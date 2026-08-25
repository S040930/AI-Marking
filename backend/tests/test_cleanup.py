"""Bounded upload cleanup regression tests."""

import os
import time

from app.models.question import Question, QuestionStatus
from app.services import cleanup


def test_cleanup_preserves_reference_beyond_first_batch(
    db_session, tmp_path, monkeypatch
):
    monkeypatch.setattr(cleanup, "_REFERENCE_BATCH_SIZE", 2)
    cutoff_age = time.time() - 3 * 86400
    files = []
    for index in range(5):
        path = tmp_path / "documents" / f"{index}.pdf"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f"%PDF-{index}".encode())
        os.utime(path, (cutoff_age, cutoff_age))
        files.append(path)

    db_session.add(
        Question(
            name="late-batch-reference",
            original_filename="4.pdf",
            file_path=str(files[-1]),
            status=QuestionStatus.ready,
        )
    )
    db_session.commit()

    deleted = cleanup.cleanup_referenced_uploads(db_session, tmp_path, 1)

    assert deleted == 4
    assert files[-1].exists()
    assert all(not path.exists() for path in files[:-1])
