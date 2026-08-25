"""OCR 配置校验测试。"""

import json

import httpx
import pytest

from app.services import ocr
from app.services.errors import BusinessError
from app.services.ocr import OCRError, _normalize_api_url


def test_normalize_async_jobs_url_passthrough():
    url = "https://x.com/api/v2/ocr/jobs"
    assert _normalize_api_url(url) == "https://x.com/api/v2/ocr/jobs"


def test_normalize_sync_layout_parsing_passthrough():
    url = "https://x.com/layout-parsing"
    assert _normalize_api_url(url) == "https://x.com/layout-parsing"


def test_normalize_rejects_mixed_suffix():
    """误拼为 .../api/v2/ocr/jobs/layout-parsing 必须直接报错,不发起请求。"""
    with pytest.raises(BusinessError):
        _normalize_api_url("https://x.com/api/v2/ocr/jobs/layout-parsing")


def test_normalize_rejects_bare_url():
    """缺少明确入口后缀的裸地址应被拒绝。"""
    with pytest.raises(BusinessError):
        _normalize_api_url("https://x.com/ocr")


def _response(
    url: str,
    *,
    status: int = 200,
    payload: dict | None = None,
    text: str | None = None,
) -> httpx.Response:
    request = httpx.Request("GET", url)
    if payload is not None:
        return httpx.Response(status, request=request, json=payload)
    return httpx.Response(status, request=request, text=text or "")


async def test_retryable_http_failure_exhaustion_raises_ocr_error(monkeypatch):
    attempts = 0

    async def no_sleep(_seconds):
        return None

    async def request():
        nonlocal attempts
        attempts += 1
        raise httpx.ConnectError(
            "temporarily unavailable",
            request=httpx.Request("POST", "https://x.com/api/v2/ocr/jobs"),
        )

    monkeypatch.setattr(ocr.asyncio, "sleep", no_sleep)
    with pytest.raises(OCRError, match="重试 3 次"):
        await ocr._request_with_retry(request, "提交任务")
    assert attempts == 3


async def test_non_retryable_http_failure_is_business_error():
    attempts = 0

    async def request():
        nonlocal attempts
        attempts += 1
        return _response("https://x.com/api/v2/ocr/jobs", status=401)

    with pytest.raises(BusinessError, match="提交任务失败"):
        await ocr._request_with_retry(request, "提交任务")
    assert attempts == 1


class _AsyncJobsClient:
    def __init__(self, *, fail_first_submit: bool = False, job_state: str = "done"):
        self.fail_first_submit = fail_first_submit
        self.job_state = job_state
        self.post_calls = 0

    async def post(self, url, **kwargs):
        self.post_calls += 1
        if self.fail_first_submit and self.post_calls == 1:
            raise httpx.ConnectError(
                "temporary submit failure", request=httpx.Request("POST", url)
            )
        return _response(
            url,
            payload={"code": 0, "data": {"jobId": "job-1"}},
        )

    async def get(self, url, **kwargs):
        if url.endswith("/job-1"):
            return _response(
                url,
                payload={
                    "code": 0,
                    "data": {
                        "state": self.job_state,
                        "errorMsg": "invalid document",
                        "resultUrl": {"jsonUrl": "https://objects/result.json"},
                    },
                },
            )
        result = {
            "result": {
                "layoutParsingResults": [
                    {"markdown": {"text": "recognized text"}}
                ]
            }
        }
        return _response(url, text=json.dumps(result))


async def test_async_job_submit_retries_then_completes(monkeypatch):
    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(ocr.asyncio, "sleep", no_sleep)
    client = _AsyncJobsClient(fail_first_submit=True)

    text = await ocr._ocr_via_async_jobs(
        client,
        "https://x.com/api/v2/ocr/jobs",
        b"%PDF-1.4",
        "token",
    )

    assert text == "recognized text"
    assert client.post_calls == 2


async def test_async_job_explicit_parse_failure_is_business_error():
    client = _AsyncJobsClient(job_state="failed")

    with pytest.raises(BusinessError, match="解析失败"):
        await ocr._ocr_via_async_jobs(
            client,
            "https://x.com/api/v2/ocr/jobs",
            b"%PDF-1.4",
            "token",
        )
