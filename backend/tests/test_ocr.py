"""OCR 配置校验测试。"""

import json

import httpx
import pytest

from app.core.errors import BusinessError
from app.services import ocr
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


async def test_queue_full_400_is_retried_then_succeeds(monkeypatch):
    """PaddleOCR 队列满(HTTP 400 + code=10010)是暂时性拒绝,应重试而非终态失败。"""
    attempts = 0

    async def no_sleep(_seconds):
        return None

    async def request():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return _response(
                "https://x.com/api/v2/ocr/jobs",
                status=400,
                payload={"code": 10010, "msg": "任务提交队列已满，请稍后重试"},
            )
        return _response("https://x.com/api/v2/ocr/jobs", payload={"code": 0})

    monkeypatch.setattr(ocr.asyncio, "sleep", no_sleep)
    response = await ocr._request_with_retry(request, "提交任务")
    assert response.json()["code"] == 0
    assert attempts == 2


async def test_queue_full_400_exhaustion_raises_ocr_error(monkeypatch):
    async def no_sleep(_seconds):
        return None

    async def request():
        return _response(
            "https://x.com/api/v2/ocr/jobs",
            status=400,
            payload={"code": 10010, "msg": "任务提交队列已满，请稍后重试"},
        )

    monkeypatch.setattr(ocr.asyncio, "sleep", no_sleep)
    with pytest.raises(OCRError, match="重试 3 次"):
        await ocr._request_with_retry(request, "提交任务")


async def test_other_400_business_error_includes_server_msg():
    """非队列满的 400 直接终态失败,错误信息带服务端 msg 便于定位。"""

    async def request():
        return _response(
            "https://x.com/api/v2/ocr/jobs",
            status=400,
            payload={"code": 10001, "msg": "文件格式不支持"},
        )

    with pytest.raises(BusinessError, match="文件格式不支持"):
        await ocr._request_with_retry(request, "提交任务")


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


async def test_circuit_breaker_resets_failure_count_when_window_expires(monkeypatch):
    """熔断窗口过期后必须清零失败计数，否则一次失败即重新开断、永久熔断。"""
    monkeypatch.setattr(ocr, "_consecutive_failures", ocr._CIRCUIT_OPEN_THRESHOLD)
    monkeypatch.setattr(
        ocr, "_circuit_open_until", 0.0
    )  # 窗口已过期
    monkeypatch.setattr(ocr.time, "time", lambda: 10_000.0)
    monkeypatch.setattr(ocr, "_CIRCUIT_OPEN_SECONDS", 300.0)

    assert await ocr._check_circuit_open() is False
    assert ocr._consecutive_failures == 0
    assert ocr._circuit_open_until == 0.0


async def test_circuit_breaker_reopens_after_single_failure_when_window_not_reset(
    monkeypatch,
):
    """回归：若窗口过期时未清零计数，单次失败会把计数推到阈值、立刻再开断。"""
    monkeypatch.setattr(ocr, "_consecutive_failures", ocr._CIRCUIT_OPEN_THRESHOLD)
    monkeypatch.setattr(ocr, "_circuit_open_until", 0.0)
    monkeypatch.setattr(ocr.time, "time", lambda: 10_000.0)
    monkeypatch.setattr(ocr, "_CIRCUIT_OPEN_SECONDS", 300.0)

    # 窗口已过期但未清零 -> 一次失败
    await ocr._record_failure()
    assert ocr._consecutive_failures == ocr._CIRCUIT_OPEN_THRESHOLD + 1
    assert await ocr._check_circuit_open() is True


class _MalformedJsonClient:
    def __init__(self):
        self.post_calls = 0

    async def post(self, url, **kwargs):
        self.post_calls += 1
        return _response(
            "https://x.com/layout-parsing", status=200, text="<html>error</html>"
        )


async def test_ocr_with_retry_malformed_json_is_business_error(monkeypatch):
    """HTTP 200 但响应体非 JSON 应归为业务失败，不重试也不进熔断计数。"""
    client = _MalformedJsonClient()

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(ocr.asyncio, "sleep", no_sleep)
    with pytest.raises(BusinessError, match="返回内容无法解析"):
        await ocr._ocr_with_retry(client, "https://x.com/layout-parsing", {}, {})
    assert client.post_calls == 1
