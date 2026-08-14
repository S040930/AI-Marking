"""MCP 到本机 FastAPI 的共享 HTTP client。"""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import urlparse

import httpx

from app.core.config import AI_MARKING_SERVICE, MCP_API_VERSION
from app.mcp.errors import McpApiError

API_BASE_URL = os.environ.get("AI_MARKING_API_URL", "http://127.0.0.1:8000").rstrip("/")
WEB_BASE_URL = os.environ.get("AI_MARKING_WEB_URL", "http://localhost:5173").rstrip("/")


def _require_loopback(name: str, value: str) -> None:
    if urlparse(value).hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise RuntimeError(f"{name} 必须指向本机 loopback 地址")


_require_loopback("AI_MARKING_API_URL", API_BASE_URL)
_require_loopback("AI_MARKING_WEB_URL", WEB_BASE_URL)


def review_url(submission_id: int) -> str:
    return f"{WEB_BASE_URL}/result/{submission_id}"


class ApiClient:
    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "ApiClient":
        self._client = httpx.AsyncClient(
            base_url=API_BASE_URL,
            timeout=httpx.Timeout(60.0, connect=5.0),
            headers={},
        )
        return self

    async def __aexit__(self, *_exc: object) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def request(self, method: str, path: str, **kwargs: object) -> dict | list:
        if self._client is None:
            raise RuntimeError("MCP HTTP client 未初始化")
        try:
            response = await self._client.request(method, path, **kwargs)
        except httpx.ConnectError as exc:
            raise McpApiError(
                "无法连接 AI-Marking FastAPI。请先在项目目录运行 ./start.sh；"
                "若 8000 端口已被占用，请运行 ./scripts/setup-codex-mcp --doctor。"
            ) from exc
        except httpx.TimeoutException as exc:
            raise McpApiError(
                "AI-Marking FastAPI 响应超时。请检查后端、数据库和 worker 日志。"
            ) from exc
        except httpx.HTTPError as exc:
            raise McpApiError(f"AI-Marking HTTP 请求失败: {exc}") from exc
        if response.is_error:
            raise McpApiError(await self._diagnose_error(response))
        payload: Any = response.json()
        if path == "/api/mcp/health":
            if (
                not isinstance(payload, dict)
                or payload.get("service") != AI_MARKING_SERVICE
            ):
                raise McpApiError(
                    "8000 端口响应的不是当前 AI-Marking 服务。请停止占用端口的旧进程后运行 ./start.sh。"
                )
            version = payload.get("mcp_api_version")
            if version != MCP_API_VERSION:
                raise McpApiError(
                    f"AI-Marking MCP API 版本不兼容（需要 {MCP_API_VERSION}，当前 {version or '未知'}）。"
                    "请重启 ./start.sh 和 Codex。"
                )
        return payload

    async def request_bytes(self, method: str, path: str, **kwargs: object) -> tuple[bytes, str]:
        if self._client is None:
            raise RuntimeError("MCP HTTP client 未初始化")
        try:
            response = await self._client.request(method, path, **kwargs)
        except httpx.ConnectError as exc:
            raise McpApiError(
                "无法连接 AI-Marking FastAPI。请先在项目目录运行 ./start.sh。"
            ) from exc
        except httpx.TimeoutException as exc:
            raise McpApiError("AI-Marking 图片资产读取超时。请检查后端状态。") from exc
        except httpx.HTTPError as exc:
            raise McpApiError(f"AI-Marking HTTP 请求失败: {exc}") from exc
        if response.is_error:
            raise McpApiError(await self._diagnose_error(response))
        return response.content, response.headers.get("content-type", "application/octet-stream")

    async def _diagnose_error(self, response: httpx.Response) -> str:
        detail: Any
        try:
            payload = response.json()
            detail = (
                payload.get("detail", payload) if isinstance(payload, dict) else payload
            )
        except ValueError:
            detail = response.text

        if response.status_code == 503:
            detail_text = str(detail)
            if "数据库" in detail_text:
                return (
                    "AI-Marking 数据库不可用。请检查 DATABASE_URL 和 PostgreSQL 状态。"
                )
        if response.status_code == 404:
            identity = await self._read_public_identity()
            if identity is None or identity.get("service") != AI_MARKING_SERVICE:
                return (
                    "8000 端口不是当前 AI-Marking 服务，或服务尚未启动。"
                    "请停止错误进程后在项目目录运行 ./start.sh。"
                )
            return (
                "当前 AI-Marking 后端缺少所需 MCP 接口，可能仍是旧进程。"
                "请重启 ./start.sh 和 Codex。"
            )
        return f"AI-Marking API 返回 HTTP {response.status_code}: {detail}"

    async def _read_public_identity(self) -> dict | None:
        if self._client is None:
            return None
        try:
            response = await self._client.get("/api/health")
            payload = response.json()
        except (httpx.HTTPError, ValueError):
            return None
        return payload if response.is_success and isinstance(payload, dict) else None


__all__ = [
    "API_BASE_URL",
    "WEB_BASE_URL",
    "ApiClient",
    "McpApiError",
    "review_url",
]
