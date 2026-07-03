"""PaddleOCR 两种 API 形态的兼容性测试。"""

import httpx
import pytest

from app.services.ocr import _normalize_api_url, _ocr_via_async_jobs


def test_async_jobs_url_is_not_modified():
    url = "https://paddleocr.aistudio-app.com/api/v2/ocr/jobs"
    assert _normalize_api_url(url) == url


def test_deployment_url_gets_layout_parsing_suffix():
    assert (
        _normalize_api_url("https://example.com/ocr")
        == "https://example.com/ocr/layout-parsing"
    )


@pytest.mark.asyncio
async def test_async_jobs_submission_polling_and_jsonl_parsing():
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if request.method == "POST":
            assert request.headers["authorization"].startswith("bearer ")
            assert b'name="model"' in request.content
            return httpx.Response(
                200,
                json={"code": 0, "data": {"jobId": "ocrjob-1"}},
            )
        if request.url.path.endswith("/ocrjob-1"):
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "state": "done",
                        "resultUrl": {"jsonUrl": "https://result.test/out.jsonl"},
                    },
                },
            )
        return httpx.Response(
            200,
            text=(
                '{"result":{"layoutParsingResults":'
                '[{"markdown":{"text":"第一页"}}]}}\n'
                '{"result":{"layoutParsingResults":'
                '[{"markdown":{"text":"第二页"}}]}}'
            ),
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    ) as client:
        result = await _ocr_via_async_jobs(
            client,
            "https://paddleocr.aistudio-app.com/api/v2/ocr/jobs",
            b"%PDF-test",
            "token",
        )

    assert result == "第一页\n\n第二页"
    assert calls == 3
