"""Measure peak RSS for two concurrent maximum-size synchronous OCR requests."""

from __future__ import annotations

import asyncio
import json
import resource
import sys
import tempfile
from pathlib import Path

import httpx

from app.services import ocr as ocr_service

FILE_SIZE_BYTES = 50 * 1024 * 1024
TASK_COUNT = 2
RSS_LIMIT_BYTES = 1024 * 1024 * 1024


def _peak_rss_bytes() -> int:
    raw = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # macOS reports bytes; Linux reports KiB.
    return raw if sys.platform == "darwin" else raw * 1024


def _mock_ocr_response(request: httpx.Request) -> httpx.Response:
    # Reading the request content makes the check include JSON/base64 serialization,
    # which is the dominant memory cost of the synchronous PaddleOCR endpoint.
    if not request.content:
        raise RuntimeError("OCR request body is empty")
    return httpx.Response(
        200,
        json={
            "result": {
                "layoutParsingResults": [{"markdown": {"text": "ok"}}]
            }
        },
    )


async def _run() -> int:
    with tempfile.TemporaryDirectory(prefix="ai-marking-ocr-memory-") as tmp:
        paths = [Path(tmp) / f"input-{index}.pdf" for index in range(TASK_COUNT)]
        for path in paths:
            with path.open("wb") as file:
                file.truncate(FILE_SIZE_BYTES)

        client = httpx.AsyncClient(transport=httpx.MockTransport(_mock_ocr_response))
        previous_client = ocr_service._client
        ocr_service._client = client
        try:
            results = await asyncio.gather(
                *(
                    ocr_service.ocr_pdf(
                        str(path), "https://ocr.test/layout-parsing", "test-token"
                    )
                    for path in paths
                )
            )
        finally:
            await client.aclose()
            ocr_service._client = previous_client

    if results != ["ok"] * TASK_COUNT:
        raise RuntimeError(f"unexpected OCR results: {results!r}")
    return _peak_rss_bytes()


def main() -> None:
    peak = asyncio.run(_run())
    print(
        json.dumps(
            {
                "tasks": TASK_COUNT,
                "file_size_mb": FILE_SIZE_BYTES // (1024 * 1024),
                "peak_rss_mb": round(peak / (1024 * 1024), 1),
                "limit_mb": RSS_LIMIT_BYTES // (1024 * 1024),
            },
            sort_keys=True,
        )
    )
    if peak >= RSS_LIMIT_BYTES:
        raise SystemExit("OCR memory check exceeded 1 GiB")


if __name__ == "__main__":
    main()
