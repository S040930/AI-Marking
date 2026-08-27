"""PaddleOCR-VL 文档解析服务模块。

通过 PaddleOCR-VL ``layout-parsing`` 接口,将 PDF 转成结构化 Markdown,
支持表格、公式、阅读顺序的还原,适合作业批改场景。

API URL / Access Token 由调用方(批改流水线)从数据库读取后传入,
不再依赖 ``settings`` 中的环境变量。

性能/稳定性:
- 共享模块级 ``httpx.AsyncClient`` 复用连接,避免每次新建 TCP/TLS 握手
- 仅对网络错误/超时/429/5xx 进行指数退避重试(最多 3 次,1s/2s/4s)
- 简单熔断:连续失败 N 次后开熔断 M 秒,期间直接 raise 不重试,避免
  信号量被重试 sleep 长期占用导致整个批改服务"假死"
- 配置错误/认证错误/PDF 内容错误立即抛出,不重试
- 提供 ``close_client`` 用于 FastAPI lifespan shutdown
"""

from __future__ import annotations

import asyncio
import base64
import json
import time
from collections.abc import Awaitable, Callable

import aiofiles
import httpx

from app.services.errors import BusinessError
from app.services.metrics import ocr_calls

# PaddleOCR-VL layout-parsing 接口超时(秒)
# 大 PDF + 复杂版面可能耗时较长,这里给到 120s
_TIMEOUT = httpx.Timeout(120.0)
_JOB_POLL_INTERVAL_SECONDS = 2.0
_JOB_MAX_WAIT_SECONDS = 600.0

# 重试参数
_MAX_ATTEMPTS = 3
_BACKOFF_BASE_SECONDS = 1.0  # 1s/2s/4s 指数退避(原 2s/4s/8s,缩短最坏阻塞)
_RETRYABLE_STATUSES = {408, 409, 425, 429, 500, 502, 503, 504}

# 简单熔断参数(模块级、进程内)
# 连续失败 _CIRCUIT_OPEN_THRESHOLD 次后,熔断打开 _CIRCUIT_OPEN_SECONDS 秒,
# 期间所有 OCR 调用直接 raise OCRError,不发起请求也不重试。
# 成功一次立即重置失败计数并关闭熔断。
_CIRCUIT_OPEN_THRESHOLD = 5
_CIRCUIT_OPEN_SECONDS = 300.0  # 5 分钟

# 共享客户端(lazy 初始化、模块级单例)
_client: httpx.AsyncClient | None = None
_client_lock = asyncio.Lock()

# 熔断状态(模块级,所有 OCR 调用共享)
_consecutive_failures: int = 0
_circuit_open_until: float = 0.0
_circuit_lock = asyncio.Lock()


class OCRError(Exception):
    """Transient OCR infrastructure failure eligible for queue retry."""


def _normalize_api_url(api_url: str) -> str:
    """规范化 PaddleOCR API URL。

    仅接受两类明确入口:
    - 异步任务入口 ``.../api/v2/ocr/jobs``(原样返回,走 ``_ocr_via_async_jobs``)
    - 同步 layout-parsing 入口 ``.../layout-parsing``(原样返回)

    其余输入(包括误拼为 ``.../api/v2/ocr/jobs/layout-parsing`` 这类把异步
    与同步后缀混用的地址)一律拒绝,避免把配置错误静默吞掉后向错误的路径
    发起请求,也避免向同步接口误用异步地址。
    """
    base = api_url.rstrip("/")
    # 先拒绝把异步入口与同步后缀混用的误拼地址
    if base.endswith("/api/v2/ocr/jobs/layout-parsing"):
        raise BusinessError(
            f"PaddleOCR 地址配置错误: {api_url!r}。"
            "请填写为 .../api/v2/ocr/jobs(异步任务) 或 .../layout-parsing(同步),"
            "不要将两者后缀混用。"
        )
    if _is_async_jobs_url(base):
        return base
    if base.endswith("/layout-parsing"):
        return base
    raise BusinessError(
        f"PaddleOCR 地址配置错误: {api_url!r}。"
        "请填写为 .../api/v2/ocr/jobs(异步任务) 或 .../layout-parsing(同步),"
        "不要将两者后缀混用。"
    )


def _is_async_jobs_url(url: str) -> bool:
    """判断是否为 AI Studio 异步 OCR Jobs 接口。"""
    return url.rstrip("/").endswith("/api/v2/ocr/jobs")


def _http_status_for_retry(exc: httpx.HTTPError) -> int | None:
    """从 HTTP 异常中提取状态码,用于判断是否可重试。"""
    response = getattr(exc, "response", None)
    return response.status_code if response is not None else None


async def _get_client() -> httpx.AsyncClient:
    """懒加载并复用模块级 ``httpx.AsyncClient``。

    首次调用时初始化,后续直接复用,避免每次都新建 TCP/TLS 连接。
    """
    global _client
    if _client is not None:
        return _client
    async with _client_lock:
        if _client is not None:
            return _client
        limits = httpx.Limits(
            max_keepalive_connections=20,
            max_connections=50,
        )
        _client = httpx.AsyncClient(timeout=_TIMEOUT, limits=limits)
    return _client


async def close_client() -> None:
    """关闭共享 HTTP 客户端,由 FastAPI lifespan shutdown 调用。"""
    global _client
    client, _client = _client, None
    if client is not None:
        await client.aclose()


async def _check_circuit_open() -> bool:
    """检查熔断器是否打开。打开则返回 True(调用方应直接 raise)。

    熔断窗口过期时顺势清零失败计数：否则窗口过期后下一次单次失败
    会因计数仍 ≥ 阈值立刻重新开断，健康服务在偶发抖动下被永久熔断。
    """
    global _consecutive_failures, _circuit_open_until
    async with _circuit_lock:
        if time.time() < _circuit_open_until:
            return True
        if _circuit_open_until != 0.0 or _consecutive_failures > 0:
            _consecutive_failures = 0
            _circuit_open_until = 0.0
        return False


async def _record_failure() -> None:
    """记录一次失败,达到阈值时打开熔断。"""
    global _consecutive_failures, _circuit_open_until
    async with _circuit_lock:
        _consecutive_failures += 1
        if _consecutive_failures >= _CIRCUIT_OPEN_THRESHOLD:
            _circuit_open_until = time.time() + _CIRCUIT_OPEN_SECONDS


async def _record_success() -> None:
    """记录一次成功,重置失败计数与熔断状态。"""
    global _consecutive_failures, _circuit_open_until
    async with _circuit_lock:
        _consecutive_failures = 0
        _circuit_open_until = 0.0


def reset_circuit_breaker() -> None:
    """重置熔断状态(供测试隔离使用)。"""
    global _consecutive_failures, _circuit_open_until
    _consecutive_failures = 0
    _circuit_open_until = 0.0


def _is_retryable(exc: Exception) -> bool:
    """仅对瞬时错误重试:网络异常、连接错误、超时、429、5xx。"""
    if isinstance(exc, (httpx.TransportError, httpx.TimeoutException, httpx.NetworkError)):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        return status in _RETRYABLE_STATUSES
    if isinstance(exc, httpx.HTTPError):
        return _http_status_for_retry(exc) in _RETRYABLE_STATUSES
    return False


async def _request_with_retry(
    request: Callable[[], Awaitable[httpx.Response]],
    operation: str,
) -> httpx.Response:
    """Run one OCR HTTP operation with the shared retry classification."""
    if await _check_circuit_open():
        ocr_calls.labels(result="circuit_open").inc()
        raise OCRError("PaddleOCR-VL 熔断中(连续失败过多),请稍后再试")

    last_exc: httpx.HTTPError | None = None
    for attempt in range(_MAX_ATTEMPTS):
        try:
            response = await request()
            response.raise_for_status()
            await _record_success()
            ocr_calls.labels(result="success").inc()
            return response
        except httpx.HTTPError as exc:
            if not _is_retryable(exc):
                ocr_calls.labels(result="failure").inc()
                raise BusinessError(f"PaddleOCR-VL {operation}失败: {exc}") from exc
            last_exc = exc
            if attempt + 1 < _MAX_ATTEMPTS:
                await asyncio.sleep(_BACKOFF_BASE_SECONDS * (2**attempt))

    await _record_failure()
    ocr_calls.labels(result="failure").inc()
    status_code = _http_status_for_retry(last_exc) if last_exc else None
    detail = f"HTTP {status_code}" if status_code else type(last_exc).__name__
    raise OCRError(
        f"PaddleOCR-VL {operation}重试 {_MAX_ATTEMPTS} 次仍失败: "
        f"{detail}: {last_exc}"
    ) from last_exc


async def _ocr_with_retry(
    client: httpx.AsyncClient, url: str, payload: dict, headers: dict
) -> dict:
    """调用 PaddleOCR-VL,带指数退避的重试与熔断。"""
    # 熔断打开时直接 raise,避免无谓重试占用信号量
    if await _check_circuit_open():
        ocr_calls.labels(result="circuit_open").inc()
        raise OCRError(
            "PaddleOCR-VL 熔断中(连续失败过多),请稍后再试"
        )

    last_exc: Exception | None = None
    for attempt in range(_MAX_ATTEMPTS):
        try:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            await _record_success()
            ocr_calls.labels(result="success").inc()
            return data
        except httpx.HTTPError as exc:
            last_exc = exc
            if not _is_retryable(exc):
                # 不可重试错误(如 4xx 配置错误):业务失败,不记入熔断计数,
                # 直接抛出 BusinessError 由 worker 标记终态且不重试。
                ocr_calls.labels(result="failure").inc()
                raise BusinessError(f"PaddleOCR-VL 调用失败: {exc}") from exc
            if attempt + 1 >= _MAX_ATTEMPTS:
                break
            backoff = _BACKOFF_BASE_SECONDS * (2**attempt)
            await asyncio.sleep(backoff)
        except (ValueError, json.JSONDecodeError) as exc:
            # HTTP 200 但响应体不是合法 JSON(如代理返回 HTML 错误页):属于
            # 确定性的响应格式问题,与异步路径分类一致归为业务失败,不重试。
            ocr_calls.labels(result="failure").inc()
            raise BusinessError(f"PaddleOCR-VL 返回内容无法解析: {exc}") from exc

    # 重试耗尽:记一次熔断失败(整个 _ocr_with_retry 算一次失败)
    await _record_failure()
    ocr_calls.labels(result="failure").inc()
    status_code = _http_status_for_retry(last_exc) if last_exc else None
    detail = f"HTTP {status_code}" if status_code else type(last_exc).__name__
    raise OCRError(
        f"PaddleOCR-VL 重试 {_MAX_ATTEMPTS} 次仍失败: {detail}: {last_exc}"
    ) from last_exc


async def ocr_pdf(file_path: str, api_url: str, token: str) -> str:
    """对 PDF 文件执行 PaddleOCR-VL 文档解析,返回 Markdown 文本。

    Args:
        file_path: PDF 文件的本地路径
        api_url: PaddleOCR-VL API 入口(如 ``https://xxx/layout-parsing`` 或根地址)
        token: AI Studio 个人访问令牌(``Authorization: token <token>``)

    Returns:
        解析后的 Markdown 文本,保留表格、公式、阅读顺序。

    Raises:
        OCRError: 短暂网络异常、限流、服务端错误或超时。
        BusinessError: 配置、文件或返回内容存在确定性错误。
    """
    if not api_url or not token:
        raise BusinessError("PaddleOCR-VL API URL / Token 未配置,请在设置页填写")

    try:
        async with aiofiles.open(file_path, "rb") as f:
            pdf_bytes = await f.read()
    except OSError as exc:
        # 文件读取失败属于配置/环境问题,直接抛出(不再重试)
        raise BusinessError(f"读取 PDF 文件失败: {exc}") from exc

    url = _normalize_api_url(api_url)
    client = await _get_client()
    if _is_async_jobs_url(url):
        return await _ocr_via_async_jobs(client, url, pdf_bytes, token)

    pdf_b64 = base64.b64encode(pdf_bytes).decode("ascii")
    payload = {
        "file": pdf_b64,
        "fileType": 0,  # 0=PDF, 1=图片
    }
    headers = {
        "Authorization": f"token {token}",
        "Content-Type": "application/json",
    }

    data = await _ocr_with_retry(client, url, payload, headers)

    # 此后属于响应内容解析,不属于需要重试的瞬时错误
    try:
        results = data["result"]["layoutParsingResults"]
    except (KeyError, TypeError) as exc:
        raise BusinessError(f"PaddleOCR-VL 返回结构异常: {data}") from exc

    if not results:
        raise BusinessError("PaddleOCR-VL 返回空文本,可能 PDF 无有效内容")

    # 遍历所有页面,用换行符拼接
    texts: list[str] = []
    for page in results:
        try:
            texts.append(page["markdown"]["text"])
        except (KeyError, TypeError):
            # 某页结构异常时跳过,不影响其他页
            continue

    combined = "\n\n".join(texts)

    if not combined or not combined.strip():
        raise BusinessError("PaddleOCR-VL 返回空文本,可能 PDF 无有效内容")

    return combined.strip()


async def _ocr_via_async_jobs(
    client: httpx.AsyncClient, job_url: str, pdf_bytes: bytes, token: str
) -> str:
    """调用 AI Studio `/api/v2/ocr/jobs` 异步接口并轮询结果。"""
    headers = {"Authorization": f"bearer {token}"}
    try:
        response = await _request_with_retry(
            lambda: client.post(
                job_url,
                headers=headers,
                data={
                    "model": "PaddleOCR-VL",
                    "optionalPayload": json.dumps(
                        {
                            "useDocOrientationClassify": False,
                            "useDocUnwarping": False,
                            "useChartRecognition": False,
                        }
                    ),
                },
                files={"file": ("document.pdf", pdf_bytes, "application/pdf")},
            ),
            "提交任务",
        )
        submitted = response.json()
        if submitted.get("code") != 0:
            raise BusinessError(
                f"PaddleOCR-VL 提交任务失败: {submitted.get('msg', submitted)}"
            )
        job_id = submitted["data"]["jobId"]
    except (BusinessError, OCRError):
        raise
    except (KeyError, TypeError, ValueError) as exc:
        raise BusinessError(f"PaddleOCR-VL 提交任务失败: {exc}") from exc

    deadline = time.monotonic() + _JOB_MAX_WAIT_SECONDS
    result_url = ""
    while time.monotonic() < deadline:
        try:
            response = await _request_with_retry(
                lambda: client.get(f"{job_url}/{job_id}", headers=headers),
                "查询任务",
            )
            status_data = response.json()
            if status_data.get("code") != 0:
                raise BusinessError(
                    f"PaddleOCR-VL 查询任务失败: "
                    f"{status_data.get('msg', status_data)}"
                )
            job = status_data["data"]
            state = job["state"]
        except (BusinessError, OCRError):
            raise
        except (KeyError, TypeError, ValueError) as exc:
            raise BusinessError(f"PaddleOCR-VL 查询任务失败: {exc}") from exc

        if state == "done":
            result_url = job.get("resultUrl", {}).get("jsonUrl", "")
            break
        if state == "failed":
            raise BusinessError(
                f"PaddleOCR-VL 解析失败: {job.get('errorMsg', '未知错误')}"
            )
        if state not in {"pending", "running"}:
            raise BusinessError(f"PaddleOCR-VL 返回未知任务状态: {state}")
        await asyncio.sleep(_JOB_POLL_INTERVAL_SECONDS)
    else:
        raise OCRError("PaddleOCR-VL 解析超时，请稍后重试")

    if not result_url:
        raise OCRError("PaddleOCR-VL 完成任务但未返回结果地址")

    response = await _request_with_retry(
        lambda: client.get(result_url),
        "下载结果",
    )
    raw = response.text.strip()
    try:
        if raw.startswith(("{", "[")):
            try:
                parsed = json.loads(raw)
                records = parsed if isinstance(parsed, list) else [parsed]
            except json.JSONDecodeError:
                records = [
                    json.loads(line) for line in raw.splitlines() if line.strip()
                ]
        else:
            records = [
                json.loads(line) for line in raw.splitlines() if line.strip()
            ]
        texts = []
        for record in records:
            result = record.get("result", record)
            for page in result.get("layoutParsingResults", []):
                text = page.get("markdown", {}).get("text", "")
                if text:
                    texts.append(text)
    except (json.JSONDecodeError, AttributeError, TypeError, ValueError) as exc:
        raise BusinessError(f"PaddleOCR-VL 解析结果失败: {exc}") from exc

    if not texts:
        raise BusinessError("PaddleOCR-VL 返回空文本，可能 PDF 无有效内容")

    combined = "\n\n".join(texts).strip()
    if not combined:
        raise BusinessError("PaddleOCR-VL 返回空文本，可能 PDF 无有效内容")
    return combined
