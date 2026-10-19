#!/usr/bin/env python3
"""ChatGPT 订阅通道 spike（阶段 6，离线脚本）。

目的：在**不动生产代码**的前提下，实测 ChatGPT 订阅额度能否承载批改 agent 的
全部请求形态，为「实装 / 放弃」提供证据。规范见
``docs/architecture/agent-refactor.md`` 的「订阅 spike」一节。

分段：
- A 段（默认）：用本机 ``~/.codex/auth.json`` 现成 token 探测
  A1 最小必填头/体集合、A2 自定义 tool function-call 往返（核心）、
  A3 ``originator`` 自报接受度、A4 usage/耗时/配额痕迹、A5 错误路径、
  A6 实测可用 model/effort 清单。
- B 段（``--section b``，需人工登录）：OAuth PKCE 全流程（:1455 回调、
  token 交换与刷新轮换）+ 连续多次调用风控观察。

用法：
    cd backend
    .venv/bin/python scripts/spike_chatgpt_sub.py --dry-run
    .venv/bin/python scripts/spike_chatgpt_sub.py --section a --md-out ../docs/architecture/chatgpt-sub-spike.md
    .venv/bin/python scripts/spike_chatgpt_sub.py --section b

安全约定：
- 只读本机 auth.json，绝不打印任何 token 原文；所有输出经 ``redact``；
- token 过期时才刷新；刷新会轮换 refresh_token，默认**回写** auth.json
  （先备份 ``auth.json.spike-bak-<ts>``），可用 ``--no-write-back`` 关闭；
- 报告只落 ``backend/.spike/``（已 gitignore），md 报告不含密钥。
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import http.server
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
import uuid
import webbrowser
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

# ---------------------------------------------------------------------------
# 常量（来源：codex-cli 0.162.0 二进制常量表，见报告「端点与常量」节）
# ---------------------------------------------------------------------------

CODEX_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
AUTHORIZE_URL = "https://auth.openai.com/oauth/authorize"
TOKEN_URL = "https://auth.openai.com/oauth/token"
CHATGPT_BASE = "https://chatgpt.com"
RESPONSES_PATHS = (
    "/backend-api/codex/responses",
    "/backend-api/codex/v1/responses",
)
MODELS_PATH = "/backend-api/codex/models"
REDIRECT_URI = "http://localhost:1455/auth/callback"
OAUTH_SCOPE = "openid profile email offline_access"

SELF_ORIGINATOR = "ai_marking_spike"
REFERENCE_ORIGINATOR = "codex_cli_rs"
SELF_UA = "ai-marking-spike/0.1"

# 仅作模型目录端点不可用时的兜底；真实清单以目录端点为唯一来源
DEFAULT_MODEL = "gpt-6.1-sol"
MODEL_CANDIDATES = (
    "gpt-6.1-sol",
    "gpt-6-astra",
    "gpt-6-sol",
    "gpt-6-luna",
    "gpt-reserve",
    "gpt-5.6-sol",
    "gpt-5.6-luna",
)
EFFORT_CANDIDATES = (
    "none",
    "minimal",
    "low",
    "medium",
    "high",
    "xhigh",
    "max",
    "ultra",
)

# 可选字段：baseline 400 时按错误信息自适应剔除，再做消融
OPTIONAL_BODY_FIELDS = (
    "client_metadata",
    "text",
    "prompt_cache_key",
    "parallel_tool_calls",
    "tool_choice",
    "include",
    "reasoning",
    "store",
)
ABLATABLE_HEADERS = (
    "chatgpt-account-id",
    "originator",
    "session_id",
    "OpenAI-Beta",
    "User-Agent",
)

PROBE_TOOL = {
    "type": "function",
    "name": "probe_echo",
    "description": "Echo the given value back. Always call this tool when asked.",
    "parameters": {
        "type": "object",
        "properties": {"value": {"type": "string"}},
        "required": ["value"],
        "additionalProperties": False,
    },
}

_SECRET_PATTERNS = (
    (
        re.compile(r"eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{4,}"),
        "<jwt>",
    ),
    (re.compile(r"rt\.[A-Za-z0-9_\-.]{8,}"), "<refresh-token>"),
    (re.compile(r"sk-[A-Za-z0-9_\-]{8,}"), "<api-key>"),
    (re.compile(r"(?i)bearer\s+[A-Za-z0-9_\-.~+/]{8,}"), "Bearer <token>"),
)
_ACCOUNT_RE = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b"
)


def redact(text: Any) -> str:
    """把疑似凭证替换为占位符；账号 UUID 只留后四位。"""
    out = str(text)
    for pattern, replacement in _SECRET_PATTERNS:
        out = pattern.sub(replacement, out)

    def _mask(match: re.Match[str]) -> str:
        value = match.group(0)
        return f"<uuid:…{value[-4:]}>"

    return _ACCOUNT_RE.sub(_mask, out)


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------


@dataclass
class Auth:
    access_token: str
    refresh_token: str
    account_id: str
    plan: str
    expires_at: int
    auth_mode: str
    source: Path
    refreshed: bool = False

    @property
    def expires_in_min(self) -> int:
        return int((self.expires_at - time.time()) / 60)


@dataclass
class Result:
    label: str
    status: int | None
    latency_ms: float
    ttfb_ms: float | None
    events: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    completed: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    request_id: str | None = None
    response_headers: dict[str, str] = field(default_factory=dict)
    body_bytes: int = 0
    transport_error: str | None = None

    @property
    def ok(self) -> bool:
        return (
            self.status is not None
            and 200 <= self.status < 300
            and self.transport_error is None
            and self.completed is not None
        )

    @property
    def output_items(self) -> list[dict[str, Any]]:
        response = self.completed or {}
        items = response.get("output")
        return (
            [item for item in items if isinstance(item, dict)]
            if isinstance(items, list)
            else []
        )

    @property
    def function_calls(self) -> list[dict[str, Any]]:
        return [
            item for item in self.output_items if item.get("type") == "function_call"
        ]

    @property
    def text(self) -> str:
        chunks: list[str] = []
        for item in self.output_items:
            if item.get("type") != "message":
                continue
            for part in item.get("content") or []:
                if isinstance(part, dict) and part.get("type") in (
                    "output_text",
                    "text",
                ):
                    chunks.append(str(part.get("text") or ""))
        return "".join(chunks)

    @property
    def usage(self) -> dict[str, Any] | None:
        response = self.completed or {}
        usage = response.get("usage")
        return usage if isinstance(usage, dict) else None

    def error_summary(self) -> dict[str, Any] | None:
        if self.error is None:
            return None
        return {
            "type": self.error.get("type"),
            "code": self.error.get("code"),
            "message": redact(self.error.get("message") or "")[:400],
        }


@dataclass
class CallLog:
    label: str
    section: str
    status: int | None
    latency_ms: float
    ttfb_ms: float | None
    ok: bool
    request_id: str | None = None
    error: dict[str, Any] | None = None
    usage: dict[str, Any] | None = None
    note: str = ""
    rate_headers: dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "section": self.section,
            "status": self.status,
            "ok": self.ok,
            "latency_ms": round(self.latency_ms, 1),
            "ttfb_ms": round(self.ttfb_ms, 1) if self.ttfb_ms is not None else None,
            "request_id": self.request_id,
            "error": self.error,
            "usage": self.usage,
            "rate_headers": self.rate_headers,
            "note": self.note,
        }


# ---------------------------------------------------------------------------
# 认证
# ---------------------------------------------------------------------------


def _jwt_claims(token: str) -> dict[str, Any]:
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload))
    except Exception:  # noqa: BLE001 - 结构异常按空声明处理
        return {}


def load_auth(path: Path) -> Auth:
    raw = json.loads(path.read_text())
    tokens = raw.get("tokens") or {}
    access = str(tokens.get("access_token") or "")
    if not access:
        raise SystemExit(
            f"{path} 没有 ChatGPT 订阅 token（auth_mode={raw.get('auth_mode')!r}）。"
            " 先执行 `codex login`。"
        )
    claims = _jwt_claims(access)
    id_claims = _jwt_claims(str(tokens.get("id_token") or ""))
    account = id_claims.get("https://api.openai.com/auth") or {}
    return Auth(
        access_token=access,
        refresh_token=str(tokens.get("refresh_token") or ""),
        account_id=str(
            tokens.get("account_id") or account.get("chatgpt_account_id") or ""
        ),
        plan=str(account.get("chatgpt_plan_type") or ""),
        expires_at=int(claims.get("exp") or 0),
        auth_mode=str(raw.get("auth_mode") or ""),
        source=path,
    )


def refresh_auth(auth: Auth, client: httpx.Client, *, write_back: bool) -> Auth:
    """access_token 过期时才刷新；默认回写 auth.json（先备份）。"""
    if auth.expires_at - time.time() > 120:
        return auth
    if not auth.refresh_token:
        raise SystemExit(
            "access_token 已过期且无 refresh_token，请执行 `codex login`。"
        )
    response = client.post(
        TOKEN_URL,
        data={
            "grant_type": "refresh_token",
            "refresh_token": auth.refresh_token,
            "client_id": CODEX_CLIENT_ID,
            "scope": OAUTH_SCOPE,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    if response.status_code >= 400:
        raise SystemExit(
            f"token 刷新失败 {response.status_code}: {redact(response.text)[:300]}"
            "；请执行 `codex login` 后重试。"
        )
    payload = response.json()
    access = str(payload.get("access_token") or "")
    id_token = str(payload.get("id_token") or "")
    claims = _jwt_claims(access)
    account = _jwt_claims(id_token).get("https://api.openai.com/auth") or {}
    rotated = Auth(
        access_token=access,
        refresh_token=str(payload.get("refresh_token") or auth.refresh_token),
        account_id=str(account.get("chatgpt_account_id") or auth.account_id),
        plan=str(account.get("chatgpt_plan_type") or auth.plan),
        expires_at=int(claims.get("exp") or 0),
        auth_mode="chatgpt",
        source=auth.source,
        refreshed=True,
    )
    if write_back:
        _write_back(auth.source, payload)
    return rotated


def _write_back(path: Path, payload: dict[str, Any]) -> None:
    backup = path.with_name(f"{path.name}.spike-bak-{int(time.time())}")
    shutil.copy2(path, backup)
    raw = json.loads(path.read_text())
    tokens = raw.setdefault("tokens", {})
    for key in ("access_token", "refresh_token", "id_token"):
        if payload.get(key):
            tokens[key] = payload[key]
    raw["last_refresh"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    path.write_text(json.dumps(raw, ensure_ascii=False, indent=2))
    path.chmod(0o600)
    print(f"[auth] 已回写 {path}（备份 {backup.name}）")


# ---------------------------------------------------------------------------
# HTTP / SSE
# ---------------------------------------------------------------------------


def detect_proxy(explicit: str | None) -> str | None:
    """优先显式参数/环境变量，其次读 macOS 系统代理。"""
    if explicit:
        return explicit
    env = os.environ.get("SPIKE_PROXY")
    if env:
        return env
    try:
        out = subprocess.run(
            ["scutil", "--proxy"], capture_output=True, text=True, timeout=3
        ).stdout
    except Exception:  # noqa: BLE001 - 非 macOS 或不可用时按无代理
        return None
    host = re.search(r"HTTPSProxy\s*:\s*(\S+)", out)
    port = re.search(r"HTTPSPort\s*:\s*(\d+)", out)
    enabled = re.search(r"HTTPSEnable\s*:\s*(\d)", out)
    if host and port and (enabled is None or enabled.group(1) == "1"):
        return f"http://{host.group(1)}:{port.group(1)}"
    return None


def _rate_headers(headers: dict[str, str]) -> dict[str, str]:
    """限流/配额相关响应头；turn-state 只留长度，避免把不透明会话状态落盘。"""
    picked: dict[str, str] = {}
    for key, value in headers.items():
        if key.startswith("x-ratelimit") or key.startswith("x-codex"):
            picked[key] = (
                f"<{len(value)} chars>" if key == "x-codex-turn-state" else value
            )
        elif key in ("retry-after", "openai-processing-ms"):
            picked[key] = value
    return picked


class Spike:
    """执行一次 spike 并累积证据。"""

    def __init__(
        self,
        auth: Auth,
        client: httpx.Client,
        *,
        model: str,
        effort: str,
        max_calls: int,
        verbose: bool,
        client_version: str,
    ) -> None:
        self.auth = auth
        self.client = client
        self.model = model
        self.effort = effort
        self.max_calls = max_calls
        self.verbose = verbose
        self.client_version = client_version
        self.logs: list[CallLog] = []
        self.calls = 0
        self.responses_path: str | None = None
        self.notes: list[str] = []

    # -- 请求构造 ------------------------------------------------------

    def headers(
        self,
        *,
        originator: str | None = SELF_ORIGINATOR,
        session_id: str | None = None,
        include_beta: bool = True,
        user_agent: str = SELF_UA,
        token: str | None = None,
        account_id: str | None = None,
    ) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {token or self.auth.access_token}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "User-Agent": user_agent,
        }
        if account_id is None:
            account_id = self.auth.account_id
        if account_id:
            headers["chatgpt-account-id"] = account_id
        if originator:
            headers["originator"] = originator
        if session_id:
            headers["session_id"] = session_id
        if include_beta:
            headers["OpenAI-Beta"] = "responses=experimental"
        return headers

    def body(
        self,
        *,
        model: str | None = None,
        instructions: str = "You are a protocol probe. Follow the user instruction exactly.",
        items: list[dict[str, Any]] | None = None,
        tools: list[dict[str, Any]] | None = None,
        effort: str | None = None,
        max_output_tokens: int = 64,
        include_cache_key: bool = True,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model or self.model,
            "instructions": instructions,
            "input": items
            if items is not None
            else [_user_text("Reply with exactly: OK")],
            "tools": tools or [],
            "tool_choice": "auto",
            "parallel_tool_calls": False,
            "store": False,
            "stream": True,
            "include": ["reasoning.encrypted_content"],
        }
        if include_cache_key:
            payload["prompt_cache_key"] = str(uuid.uuid4())
        effort_value = effort if effort is not None else self.effort
        if effort_value:
            payload["reasoning"] = {"effort": effort_value, "summary": "auto"}
        if max_output_tokens:
            payload["max_output_tokens"] = max_output_tokens
        return payload

    # -- 发送 ----------------------------------------------------------

    def _url(self) -> str:
        if self.responses_path:
            return f"{CHATGPT_BASE}{self.responses_path}"
        return f"{CHATGPT_BASE}{RESPONSES_PATHS[0]}"

    def post(
        self,
        label: str,
        section: str,
        headers: dict[str, str],
        body: dict[str, Any],
        *,
        note: str = "",
        allow_path_fallback: bool = False,
    ) -> Result:
        if self.calls >= self.max_calls:
            raise SystemExit(
                f"已达调用上限 {self.max_calls} 次，停止以免消耗配额；"
                "确认后可用 --max-calls 提高。"
            )
        self.calls += 1
        paths = RESPONSES_PATHS if allow_path_fallback else (self._url_path(),)
        last: Result | None = None
        for path in paths:
            url = f"{CHATGPT_BASE}{path}"
            result = self._post_once(label, headers, body, url)
            if result.status == 404 and allow_path_fallback:
                last = result
                self._log(label, section, result, note=f"path {path} 404")
                continue
            self.responses_path = path
            self._log(label, section, result, note=note)
            return result
        assert last is not None
        self._log(label, section, last, note=note)
        return last

    def _url_path(self) -> str:
        return self.responses_path or RESPONSES_PATHS[0]

    def _post_once(
        self,
        label: str,
        headers: dict[str, str],
        body: dict[str, Any],
        url: str,
    ) -> Result:
        started = time.perf_counter()
        ttfb: float | None = None
        events: list[tuple[str, dict[str, Any]]] = []
        completed: dict[str, Any] | None = None
        error: dict[str, Any] | None = None
        status: int | None = None
        response_headers: dict[str, str] = {}
        request_id: str | None = None
        size = 0
        transport_error: str | None = None
        try:
            with self.client.stream(
                "POST", url, headers=headers, json=body
            ) as response:
                status = response.status_code
                response_headers = {
                    key.lower(): value for key, value in response.headers.items()
                }
                request_id = response_headers.get("x-request-id")
                if status >= 400:
                    raw = response.read().decode("utf-8", errors="replace")
                    size = len(raw)
                    error = _parse_error(raw)
                else:
                    buffer: list[str] = []
                    event_name: str | None = None
                    for line in response.iter_lines():
                        if ttfb is None:
                            ttfb = (time.perf_counter() - started) * 1000
                        size += len(line)
                        if line == "":
                            if buffer:
                                payload = "\n".join(buffer)
                                buffer = []
                                data = _parse_json(payload)
                                name = event_name or "message"
                                if data is not None and event_name is None:
                                    # 部分实现只发 data 行，用 payload.type 兜底
                                    inline = data.get("type")
                                    if isinstance(inline, str):
                                        name = inline
                                if data is not None:
                                    events.append((name, data))
                                    if name == "response.completed":
                                        response_obj = data.get("response")
                                        if isinstance(response_obj, dict):
                                            completed = response_obj
                                    elif name == "response.incomplete":
                                        completed = data.get("response") or {}
                                    elif name in ("error",):
                                        err = data.get("error")
                                        error = (
                                            err
                                            if isinstance(err, dict)
                                            else {"message": data.get("message")}
                                        )
                                    elif name == "response.failed":
                                        response_obj = data.get("response") or {}
                                        error = response_obj.get("error") or {
                                            "message": "response.failed"
                                        }
                                event_name = None
                            continue
                        if line.startswith("event:"):
                            event_name = line.split(":", 1)[1].strip()
                        elif line.startswith("data:"):
                            buffer.append(line.split(":", 1)[1].lstrip())
        except httpx.HTTPError as exc:
            transport_error = f"{type(exc).__name__}: {exc}"
        latency = (time.perf_counter() - started) * 1000
        result = Result(
            label=label,
            status=status,
            latency_ms=latency,
            ttfb_ms=ttfb,
            events=events,
            completed=completed,
            error=error,
            request_id=request_id,
            response_headers=response_headers,
            body_bytes=size,
            transport_error=transport_error,
        )
        if self.verbose:
            state = f"{status}" if status else (transport_error or "?")
            print(f"  · {label:34s} {state:>6s} {latency / 1000:6.2f}s")
        return result

    def get_json(self, path: str) -> tuple[int, Any]:
        self.calls += 1
        url = f"{CHATGPT_BASE}{path}"
        try:
            response = self.client.get(url, headers=self.headers(include_beta=False))
        except httpx.HTTPError as exc:
            return 0, {"transport_error": f"{type(exc).__name__}: {exc}"}
        try:
            payload: Any = response.json()
        except ValueError:
            payload = {"text": redact(response.text)[:300]}
        return response.status_code, payload

    # -- 记录 ----------------------------------------------------------

    def _log(self, label: str, section: str, result: Result, *, note: str) -> None:
        rate_headers = _rate_headers(result.response_headers or {})
        self.logs.append(
            CallLog(
                label=label,
                section=section,
                status=result.status,
                latency_ms=result.latency_ms,
                ttfb_ms=result.ttfb_ms,
                ok=result.ok,
                request_id=result.request_id,
                error=result.error_summary(),
                usage=result.usage,
                note=note or (result.transport_error or ""),
                rate_headers=rate_headers,
            )
        )


def _user_text(text: str) -> dict[str, Any]:
    return {"role": "user", "content": [{"type": "input_text", "text": text}]}


def _parse_json(raw: str) -> dict[str, Any] | None:
    try:
        value = json.loads(raw)
    except ValueError:
        return None
    return value if isinstance(value, dict) else {"value": value}


def _parse_error(raw: str) -> dict[str, Any]:
    payload = _parse_json(raw)
    if payload is None:
        return {"message": redact(raw)[:400]}
    error = payload.get("error")
    if isinstance(error, dict):
        return error
    detail = payload.get("detail")
    if detail is not None:
        return {"message": str(detail)}
    return payload


# ---------------------------------------------------------------------------
# A 段
# ---------------------------------------------------------------------------


def _model_entry(item: dict[str, Any]) -> dict[str, Any]:
    """从目录条目里抽出模型 id 与受支持的推理档位。"""
    slug = item.get("slug") or item.get("id") or item.get("model")
    levels = item.get("supported_reasoning_levels") or []
    efforts = [
        str(level.get("effort"))
        for level in levels
        if isinstance(level, dict) and level.get("effort")
    ]
    return {
        "id": str(slug) if slug else "",
        "display_name": item.get("display_name") or "",
        "description": item.get("description") or "",
        "default_effort": item.get("default_reasoning_level") or "",
        "efforts": efforts,
    }


def resolve_model(spike: Spike, preferred: str | None) -> dict[str, Any]:
    """模型目录端点（必须带 client_version）；失败则回退静态候选。"""
    query = urllib.parse.urlencode({"client_version": spike.client_version})
    status, payload = spike.get_json(f"{MODELS_PATH}?{query}")
    info: dict[str, Any] = {
        "status": status,
        "source": MODELS_PATH,
        "client_version": spike.client_version,
        "ids": [],
        "models": [],
    }
    if status == 200 and isinstance(payload, dict):
        items = payload.get("data") or payload.get("models") or payload.get("items")
        if isinstance(items, list):
            entries = [_model_entry(item) for item in items if isinstance(item, dict)]
            entries = [entry for entry in entries if entry["id"]]
            info["models"] = entries
            info["ids"] = [entry["id"] for entry in entries]
            info["raw_keys"] = sorted(payload.keys())
    if preferred:
        info["chosen"] = preferred
    elif info["ids"]:
        info["chosen"] = info["ids"][0]
    else:
        info["chosen"] = DEFAULT_MODEL
    chosen_entry = next(
        (entry for entry in info["models"] if entry["id"] == info["chosen"]), None
    )
    info["chosen_efforts"] = (chosen_entry or {}).get("efforts") or []
    return info


def run_a1(spike: Spike, model: str) -> dict[str, Any]:
    print("[A1] 最小必填头/体集合")
    headers = spike.headers(session_id=str(uuid.uuid4()))
    body = spike.body(model=model, max_output_tokens=32)
    result = spike.post("a1-baseline", "A1", headers, body, allow_path_fallback=True)
    stripped: list[str] = []
    if result.status in (400, 422) and result.error:
        message = str(result.error.get("message") or "")
        for name in OPTIONAL_BODY_FIELDS:
            if name in message and name in body:
                body.pop(name, None)
                stripped.append(name)
                result = spike.post(f"a1-baseline-strip-{name}", "A1", headers, body)
                if result.ok:
                    break
    if not result.ok:
        return {
            "ok": False,
            "stripped_fields": stripped,
            "status": result.status,
            "error": result.error_summary(),
            "note": "baseline 未通，头/体消融无意义",
        }

    header_probe = []
    for name in ABLATABLE_HEADERS:
        candidate = dict(headers)
        candidate.pop(name, None)
        probe = spike.post(f"a1-no-{name}", "A1", candidate, body)
        header_probe.append(
            {
                "header": name,
                "removed_status": probe.status,
                "removed_ok": probe.ok,
                "required": not probe.ok,
                "error": probe.error_summary(),
            }
        )

    body_probe = []
    for name in OPTIONAL_BODY_FIELDS:
        if name not in body:
            continue
        candidate = dict(body)
        candidate.pop(name, None)
        probe = spike.post(f"a1-no-body-{name}", "A1", headers, candidate)
        body_probe.append(
            {
                "field": name,
                "removed_status": probe.status,
                "removed_ok": probe.ok,
                "required": not probe.ok,
                "error": probe.error_summary(),
            }
        )

    stable = spike.post("a1-baseline-repeat", "A1", headers, body)
    minimal_headers = {
        "Authorization": headers["Authorization"],
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }
    minimal_header_result = spike.post(
        "a1-minimal-headers", "A1", minimal_headers, body
    )
    minimal_body = {
        key: body[key] for key in ("model", "input", "store", "stream") if key in body
    }
    minimal_body_result = spike.post("a1-minimal-body", "A1", headers, minimal_body)
    return {
        "ok": True,
        "status": result.status,
        "stripped_fields": stripped,
        "baseline_headers": sorted(headers.keys()),
        "baseline_body_fields": sorted(body.keys()),
        "headers": header_probe,
        "body": body_probe,
        "required_headers": [row["header"] for row in header_probe if row["required"]],
        "required_body_fields": [row["field"] for row in body_probe if row["required"]],
        "stable_repeat_ok": stable.ok,
        "minimal_headers_ok": minimal_header_result.ok,
        "minimal_headers_status": minimal_header_result.status,
        "minimal_body_fields": sorted(minimal_body),
        "minimal_body_ok": minimal_body_result.ok,
        "minimal_body_status": minimal_body_result.status,
        "usage": result.usage,
        "ttfb_ms": round(result.ttfb_ms, 1) if result.ttfb_ms else None,
        "latency_ms": round(result.latency_ms, 1),
    }


def run_a2(spike: Spike, model: str) -> dict[str, Any]:
    print("[A2] 自定义 tool function-call 往返（核心）")
    instructions = (
        "You are a protocol probe. When the user asks you to call a tool, "
        "call it exactly once with the requested arguments and do not answer "
        "in text before calling."
    )
    body = spike.body(
        model=model,
        instructions=instructions,
        items=[_user_text("Call probe_echo with value PING.")],
        tools=[PROBE_TOOL],
        max_output_tokens=128,
    )
    headers = spike.headers(session_id=str(uuid.uuid4()))
    first = spike.post("a2-turn1-tool-call", "A2", headers, body)
    if not first.ok:
        return {"ok": False, "stage": "turn1", "error": first.error_summary()}

    calls = first.function_calls
    call = calls[0] if calls else None
    arguments_raw = str(call.get("arguments") or "") if call else ""
    try:
        arguments = json.loads(arguments_raw) if arguments_raw else {}
    except ValueError:
        arguments = {}

    reasoning_items = [
        item for item in first.output_items if item.get("type") == "reasoning"
    ]
    encrypted = any(item.get("encrypted_content") for item in reasoning_items)

    replay = [
        item
        for item in first.output_items
        if item.get("type") in ("reasoning", "message", "function_call")
    ]
    turn2_ok = False
    turn2_text = ""
    turn2_error = None
    turn2_usage = None
    if call:
        call_id = str(call.get("call_id") or call.get("id") or "")
        second_body = spike.body(
            model=model,
            instructions=instructions,
            items=[
                *replay,
                {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": "PONG",
                },
            ],
            tools=[PROBE_TOOL],
            max_output_tokens=64,
        )
        second = spike.post("a2-turn2-tool-result", "A2", headers, second_body)
        turn2_ok = second.ok
        turn2_text = second.text.strip()
        turn2_error = second.error_summary()
        turn2_usage = second.usage

    # 追加：高 effort 下是否回传 reasoning（含 encrypted_content），以及
    # x-codex-turn-state 是否可作为下一回合的续会话凭据。
    reasoning_probe: dict[str, Any] = {"ok": False}
    high_body = spike.body(
        model=model,
        instructions=instructions,
        items=[_user_text("Call probe_echo with value PING.")],
        tools=[PROBE_TOOL],
        effort="high",
        max_output_tokens=256,
    )
    high = spike.post("a2b-reasoning-high", "A2", headers, high_body)
    if high.ok:
        high_reasoning = [
            item for item in high.output_items if item.get("type") == "reasoning"
        ]
        turn_state = high.response_headers.get("x-codex-turn-state") or ""
        high_call = next(iter(high.function_calls), None)
        continuation_ok = False
        continuation_status = None
        if high_call:
            high_replay = [
                item
                for item in high.output_items
                if item.get("type") in ("reasoning", "message", "function_call")
            ]
            continuation_headers = dict(headers)
            if turn_state:
                continuation_headers["x-codex-turn-state"] = turn_state
            continuation_body = spike.body(
                model=model,
                instructions=instructions,
                items=[
                    *high_replay,
                    {
                        "type": "function_call_output",
                        "call_id": str(high_call.get("call_id") or ""),
                        "output": "PONG",
                    },
                ],
                tools=[PROBE_TOOL],
                effort="high",
                max_output_tokens=128,
            )
            continuation = spike.post(
                "a2b-turn-state-continuation",
                "A2",
                continuation_headers,
                continuation_body,
            )
            continuation_ok = continuation.ok
            continuation_status = continuation.status
        reasoning_probe = {
            "ok": bool(high_reasoning),
            "reasoning_items": len(high_reasoning),
            "encrypted_reasoning_present": any(
                item.get("encrypted_content") for item in high_reasoning
            ),
            "turn_state_present": bool(turn_state),
            "turn_state_len": len(turn_state),
            "continuation_ok": continuation_ok,
            "continuation_status": continuation_status,
        }
    return {
        "ok": bool(call) and turn2_ok,
        "stage": "done" if turn2_ok else "turn2",
        "function_call_received": bool(call),
        "function_name": call.get("name") if call else None,
        "arguments": arguments,
        "arguments_parse_ok": bool(arguments),
        "reasoning_items": len(reasoning_items),
        "encrypted_reasoning_present": encrypted,
        "reasoning_replayed": any(item.get("type") == "reasoning" for item in replay),
        "turn2_ok": turn2_ok,
        "turn2_text": redact(turn2_text)[:200],
        "turn2_error": turn2_error,
        "turn2_usage": turn2_usage,
        "turn1_usage": first.usage,
        "reasoning_probe": reasoning_probe,
    }


def run_a3(spike: Spike, model: str) -> dict[str, Any]:
    print("[A3] originator 自报接受度")
    body = spike.body(model=model, max_output_tokens=24)
    rows = []
    for label, originator, ua in (
        ("self-reported", SELF_ORIGINATOR, SELF_UA),
        ("reference", REFERENCE_ORIGINATOR, SELF_UA),
        ("absent", None, SELF_UA),
    ):
        headers = spike.headers(
            originator=originator, session_id=str(uuid.uuid4()), user_agent=ua
        )
        result = spike.post(f"a3-{label}", "A3", headers, body)
        rows.append(
            {
                "variant": label,
                "originator": originator,
                "user_agent": ua,
                "status": result.status,
                "ok": result.ok,
                "error": result.error_summary(),
            }
        )
    self_ok = next(row["ok"] for row in rows if row["variant"] == "self-reported")
    reference_ok = next(row["ok"] for row in rows if row["variant"] == "reference")
    accepted = bool(self_ok)
    return {
        "ok": accepted,
        "self_reported_accepted": self_ok,
        "reference_accepted": reference_ok,
        "per_variant": rows,
        "decision": "可用（自报身份被接受）"
        if accepted
        else "按默认决策 4：不做伪装，订阅轨放弃",
    }


def run_a4(spike: Spike, model: str) -> dict[str, Any]:
    print("[A4] usage / 耗时 / 配额痕迹")
    body = spike.body(model=model, max_output_tokens=48)
    result = spike.post(
        "a4-metrics", "A4", spike.headers(session_id=str(uuid.uuid4())), body
    )
    rate_headers = _rate_headers(result.response_headers)
    return {
        "ok": result.ok,
        "usage": result.usage,
        "ttfb_ms": round(result.ttfb_ms, 1) if result.ttfb_ms else None,
        "latency_ms": round(result.latency_ms, 1),
        "rate_limit_headers": rate_headers,
        "request_id": result.request_id,
        "quota_hint": sorted(rate_headers.keys()),
    }


def run_a5(spike: Spike, model: str) -> dict[str, Any]:
    print("[A5] 错误路径可用线索")
    body = spike.body(model=model, max_output_tokens=16)
    probes = []

    bad_token_headers = spike.headers(
        session_id=str(uuid.uuid4()), token="spike-invalid-token"
    )
    probes.append(("bad-token", "A5-bad-token", bad_token_headers, body))

    bad_model_body = dict(body)
    bad_model_body["model"] = "gpt-spike-nonexistent"
    probes.append(
        (
            "bad-model",
            "A5-bad-model",
            spike.headers(session_id=str(uuid.uuid4())),
            bad_model_body,
        )
    )

    bad_body = dict(body)
    bad_body["reasoning"] = {"effort": "ultra", "summary": "auto"}
    probes.append(
        (
            "bad-effort",
            "A5-bad-effort",
            spike.headers(session_id=str(uuid.uuid4())),
            bad_body,
        )
    )

    rows = []
    for label, call_label, headers, payload in probes:
        result = spike.post(call_label, "A5", headers, payload)
        rows.append(
            {
                "probe": label,
                "status": result.status,
                "request_id": result.request_id,
                "error": result.error_summary(),
                "structured": result.error is not None
                and bool(result.error.get("message")),
            }
        )
    structured = sum(1 for row in rows if row["structured"])
    return {
        "ok": structured >= 2,
        "probes": rows,
        "structured_count": structured,
    }


def run_a6(
    spike: Spike, model_info: dict[str, Any], *, max_probes: int
) -> dict[str, Any]:
    print("[A6] 可用 model / effort 清单")
    ids = list(model_info.get("ids") or [])
    candidates = ids[:max_probes] if ids else list(MODEL_CANDIDATES[:max_probes])
    if model_info.get("chosen") and model_info["chosen"] not in candidates:
        candidates.insert(0, model_info["chosen"])
    rows = []
    working: list[str] = []
    for model_id in candidates[:max_probes]:
        body = spike.body(model=model_id, max_output_tokens=16, effort=spike.effort)
        result = spike.post(f"a6-model-{model_id}", "A6", spike.headers(), body)
        rows.append(
            {
                "model": model_id,
                "status": result.status,
                "ok": result.ok,
                "error": result.error_summary(),
            }
        )
        if result.ok:
            working.append(model_id)

    effort_rows = []
    effort_target = working[0] if working else (model_info.get("chosen") or spike.model)
    catalog_efforts = next(
        (
            entry.get("efforts") or []
            for entry in model_info.get("models") or []
            if entry.get("id") == effort_target
        ),
        [],
    )
    # 目录声明与实测不一致（如 ultra），取并集逐个实测，不采信声明
    effort_candidates = list(dict.fromkeys([*catalog_efforts, *EFFORT_CANDIDATES]))
    for effort in effort_candidates:
        body = spike.body(model=effort_target, max_output_tokens=16, effort=effort)
        result = spike.post(f"a6-effort-{effort}", "A6", spike.headers(), body)
        effort_rows.append(
            {
                "model": effort_target,
                "effort": effort,
                "status": result.status,
                "ok": result.ok,
                "error": result.error_summary(),
            }
        )
    accepted_efforts = [row["effort"] for row in effort_rows if row["ok"]]
    return {
        "ok": bool(working),
        "catalog_status": model_info.get("status"),
        "client_version": model_info.get("client_version"),
        "catalog_ids": ids,
        "catalog_models": model_info.get("models") or [],
        "probed_models": rows,
        "working_models": working,
        "effort_model": effort_target,
        "effort_probes": effort_rows,
        "accepted_efforts": accepted_efforts,
    }


# ---------------------------------------------------------------------------
# B 段：OAuth PKCE
# ---------------------------------------------------------------------------


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    server_version = "spike"

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 接口
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/auth/callback":
            self.send_response(404)
            self.end_headers()
            return
        query = urllib.parse.parse_qs(parsed.query)
        state = (query.get("state") or [""])[0]
        code = (query.get("code") or [""])[0]
        error = (query.get("error") or [""])[0]
        if state != self.server.expected_state:  # type: ignore[attr-defined]
            self.send_response(400)
            self.end_headers()
            self.wfile.write("state mismatch".encode())
            return
        self.server.received_code = code  # type: ignore[attr-defined]
        self.server.received_error = error  # type: ignore[attr-defined]
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        body = "登录完成，可以关闭此页回到终端。" if code else f"登录失败：{error}"
        self.wfile.write(f"<html><body><h3>{body}</h3></body></html>".encode())

    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
        return


def _pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


def _wait_for_code(state: str, timeout: float = 300.0) -> str:
    try:
        server = http.server.HTTPServer(("127.0.0.1", 1455), _CallbackHandler)
    except OSError as exc:
        raise SystemExit(
            f"无法监听 127.0.0.1:1455（{exc}）；请关闭占用该端口的 codex 登录流程。"
        ) from exc
    server.expected_state = state  # type: ignore[attr-defined]
    server.received_code = ""  # type: ignore[attr-defined]
    server.received_error = ""  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    deadline = time.time() + timeout
    try:
        while time.time() < deadline:
            if server.received_code or server.received_error:  # type: ignore[attr-defined]
                break
            time.sleep(0.2)
    finally:
        server.shutdown()
        server.server_close()
    code = server.received_code  # type: ignore[attr-defined]
    error = server.received_error  # type: ignore[attr-defined]
    if not code:
        raise SystemExit(f"未拿到授权码（{error or '超时'}）")
    return str(code)


def _token_request(client: httpx.Client, data: dict[str, str]) -> dict[str, Any]:
    response = client.post(
        TOKEN_URL,
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    if response.status_code >= 400:
        raise SystemExit(
            f"token 端点 {response.status_code}: {redact(response.text)[:300]}"
        )
    return response.json()


def run_b(spike: Spike) -> dict[str, Any]:
    print("[B] OAuth PKCE 全流程（会打开浏览器）")
    verifier, challenge = _pkce_pair()
    state = secrets.token_urlsafe(16)
    session_id = str(uuid.uuid4())
    query = urllib.parse.urlencode(
        {
            "response_type": "code",
            "client_id": CODEX_CLIENT_ID,
            "redirect_uri": REDIRECT_URI,
            "scope": OAUTH_SCOPE,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": state,
            "id_token_add_organizations": "true",
            "codex_cli_simplified_flow": "true",
        }
    )
    url = f"{AUTHORIZE_URL}?{query}"
    print("  授权地址（若不自动打开请手动访问）：")
    print(f"  {url}")
    try:
        webbrowser.open(url)
    except Exception:  # noqa: BLE001 - 无 GUI 环境忽略
        pass
    code = _wait_for_code(state)
    tokens = _token_request(
        spike.client,
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "client_id": CODEX_CLIENT_ID,
            "code_verifier": verifier,
        },
    )
    access_claims = _jwt_claims(str(tokens.get("access_token") or ""))
    account = (
        _jwt_claims(str(tokens.get("id_token") or "")).get(
            "https://api.openai.com/auth"
        )
        or {}
    )
    exchange = {
        "ok": bool(tokens.get("access_token")),
        "has_refresh_token": bool(tokens.get("refresh_token")),
        "expires_in": tokens.get("expires_in"),
        "scope": tokens.get("scope"),
        "plan": account.get("chatgpt_plan_type"),
        "exp_utc": datetime.fromtimestamp(
            int(access_claims.get("exp") or 0), tz=timezone.utc
        ).isoformat(),
    }

    rotated = _token_request(
        spike.client,
        {
            "grant_type": "refresh_token",
            "refresh_token": str(tokens.get("refresh_token") or ""),
            "client_id": CODEX_CLIENT_ID,
            "scope": OAUTH_SCOPE,
        },
    )
    refresh = {
        "ok": bool(rotated.get("access_token")),
        "refresh_token_rotated": bool(rotated.get("refresh_token"))
        and rotated.get("refresh_token") != tokens.get("refresh_token"),
    }

    # 用新 token 连续调用，观察风控/限流
    spike.auth = Auth(
        access_token=str(rotated.get("access_token") or tokens.get("access_token")),
        refresh_token=str(rotated.get("refresh_token") or ""),
        account_id=str(account.get("chatgpt_account_id") or spike.auth.account_id),
        plan=str(account.get("chatgpt_plan_type") or spike.auth.plan),
        expires_at=int(access_claims.get("exp") or 0),
        auth_mode="chatgpt",
        source=spike.auth.source,
    )
    calls = []
    for index in range(1, 4):
        result = spike.post(
            f"b-call-{index}",
            "B",
            spike.headers(session_id=session_id),
            spike.body(max_output_tokens=24),
        )
        calls.append(
            {
                "index": index,
                "status": result.status,
                "ok": result.ok,
                "request_id": result.request_id,
                "rate_headers": {
                    key: value
                    for key, value in result.response_headers.items()
                    if key.startswith("x-ratelimit") or key.startswith("x-codex")
                },
            }
        )
        time.sleep(2.0)
    return {
        "ok": exchange["ok"] and refresh["ok"] and all(row["ok"] for row in calls),
        "exchange": exchange,
        "refresh": refresh,
        "sequential_calls": calls,
    }


# ---------------------------------------------------------------------------
# 汇总与报告
# ---------------------------------------------------------------------------


def build_conclusion(sections: dict[str, Any], ran_b: bool) -> dict[str, Any]:
    criteria = {
        "A1 最小头/体集合": bool(sections.get("A1", {}).get("ok")),
        "A2 工具往返": bool(sections.get("A2", {}).get("ok")),
        "A3 自报 originator 被接受": bool(
            sections.get("A3", {}).get("self_reported_accepted")
        ),
        "A4 usage 可见": bool(sections.get("A4", {}).get("ok")),
        "A5 错误可诊断": bool(sections.get("A5", {}).get("ok")),
        "A6 至少一个 model 可用": bool(sections.get("A6", {}).get("ok")),
    }
    if ran_b:
        criteria["B OAuth + 刷新 + 连续调用"] = bool(sections.get("B", {}).get("ok"))
    failed = [name for name, passed in criteria.items() if not passed]
    if not failed:
        verdict = "implement"
    elif "A3 自报 originator 被接受" in failed or "A2 工具往返" in failed:
        verdict = "abandon"
    else:
        verdict = "partial"
    note = {
        "implement": "全部通过，可进阶段 7 实装。",
        "abandon": "核心项未过，按默认决策 4/8 保留「未开放」占位。",
        "partial": "非核心项未过，需人工判断是否降级实装。",
    }[verdict]
    if not ran_b:
        note += "（B 段 OAuth 全流程未执行）"
    return {
        "verdict": verdict,
        "criteria": criteria,
        "failed": failed,
        "scope": "A+B" if ran_b else "A",
        "note": note,
    }


def render_markdown(report: dict[str, Any]) -> str:
    sections = report.get("sections", {})
    conclusion = report.get("conclusion", {})
    lines: list[str] = []
    add = lines.append
    add("# ChatGPT 订阅通道 spike 报告（阶段 6）")
    add("")
    add(
        f"> 生成时间：{report['generated_at']}　脚本：`backend/scripts/spike_chatgpt_sub.py`"
    )
    add(f"> 结论：**{conclusion.get('verdict')}** — {conclusion.get('note', '')}")
    add("")
    add("## 环境与认证")
    add("")
    env = report.get("environment", {})
    auth = report.get("auth", {})
    add("| 项 | 值 |")
    add("|---|---|")
    for key in ("python", "httpx", "platform", "proxy", "endpoint", "client_version"):
        add(f"| {key} | `{env.get(key)}` |")
    for key in (
        "source",
        "auth_mode",
        "plan",
        "account",
        "expires_in_min",
        "refreshed",
    ):
        add(f"| auth.{key} | `{auth.get(key)}` |")
    add("")
    add("## 验收结论")
    add("")
    add("| 验收项 | 结果 |")
    add("|---|---|")
    for name, passed in (conclusion.get("criteria") or {}).items():
        add(f"| {name} | {'✅ 通过' if passed else '❌ 未过'} |")
    add("")

    a1 = sections.get("A1") or {}
    add("## A1 最小必填头/体集合")
    add("")
    if a1.get("ok"):
        add(
            f"基线状态 `{a1.get('status')}`，重复调用稳定：`{a1.get('stable_repeat_ok')}`。"
        )
        add("")
        add(f"- 基线请求头：`{', '.join(a1.get('baseline_headers') or [])}`")
        add(f"- 基线请求体字段：`{', '.join(a1.get('baseline_body_fields') or [])}`")
        add(
            f"- 逐个移除后失效（**必需**）：`{', '.join(a1.get('required_headers') or []) or '无'}`"
            f" / `{', '.join(a1.get('required_body_fields') or []) or '无'}`"
        )
        if a1.get("stripped_fields"):
            add(f"- 首次 400 后自适应剔除：`{', '.join(a1['stripped_fields'])}`")
        add(
            f"- 极简头集合（仅 Authorization/Content-Type/Accept）："
            f"`{a1.get('minimal_headers_ok')}`（状态 {a1.get('minimal_headers_status')}）"
        )
        add(
            f"- 极简体（仅 {', '.join(a1.get('minimal_body_fields') or [])}）："
            f"`{a1.get('minimal_body_ok')}`（状态 {a1.get('minimal_body_status')}）"
        )
        add("")
        add("| 移除项 | 类别 | 移除后状态 | 结论 |")
        add("|---|---|---|---|")
        for row in a1.get("headers") or []:
            add(
                f"| `{row['header']}` | header | {row['removed_status']} | "
                f"{'必需' if row['required'] else '可选'} |"
            )
        for row in a1.get("body") or []:
            add(
                f"| `{row['field']}` | body | {row['removed_status']} | "
                f"{'必需' if row['required'] else '可选'} |"
            )
    else:
        add(f"未通过：`{a1.get('error')}`")
    add("")

    a2 = sections.get("A2") or {}
    add("## A2 自定义 tool function-call 往返（核心）")
    add("")
    add(
        f"- 收到 function_call：`{a2.get('function_call_received')}`"
        f"（name=`{a2.get('function_name')}`，参数=`{a2.get('arguments')}`）"
    )
    add(
        f"- reasoning item 数量：`{a2.get('reasoning_items')}`，"
        f"含 encrypted_content：`{a2.get('encrypted_reasoning_present')}`"
    )
    add(
        f"- 回传 function_call_output 后成功：`{a2.get('turn2_ok')}`，"
        f"末轮文本：`{(a2.get('turn2_text') or '')[:80]}`"
    )
    add(f"- turn1 usage：`{a2.get('turn1_usage')}`")
    if a2.get("turn2_error"):
        add(f"- turn2 错误：`{a2.get('turn2_error')}`")
    probe = a2.get("reasoning_probe") or {}
    if probe:
        add(
            f"- 高 effort 追加探测：reasoning items `{probe.get('reasoning_items')}`，"
            f"encrypted_content `{probe.get('encrypted_reasoning_present')}`，"
            f"`x-codex-turn-state` 存在 `{probe.get('turn_state_present')}`"
            f"（{probe.get('turn_state_len')} 字符）"
        )
        add(
            f"- 回传 turn-state 续回合：`{probe.get('continuation_ok')}`"
            f"（状态 {probe.get('continuation_status')}）"
        )
    add("")

    a3 = sections.get("A3") or {}
    add("## A3 originator 接受度")
    add("")
    add("| 变体 | originator | 状态 | 结论 |")
    add("|---|---|---|---|")
    for row in a3.get("per_variant") or []:
        add(
            f"| {row['variant']} | `{row['originator']}` | {row['status']} | "
            f"{'接受' if row['ok'] else '拒绝'} |"
        )
    add("")
    add(f"判定：{a3.get('decision', '')}")
    add("")

    a4 = sections.get("A4") or {}
    add("## A4 usage / 耗时 / 配额痕迹")
    add("")
    add(f"- usage：`{a4.get('usage')}`")
    add(f"- TTFB `{a4.get('ttfb_ms')} ms`，总耗时 `{a4.get('latency_ms')} ms`")
    add(f"- 限流相关响应头：`{a4.get('rate_limit_headers')}`")
    add(f"- `x-request-id`：`{a4.get('request_id')}`")
    add("")

    a5 = sections.get("A5") or {}
    add("## A5 错误路径")
    add("")
    add("| 探针 | 状态 | 结构化错误 | 错误摘要 |")
    add("|---|---|---|---|")
    for row in a5.get("probes") or []:
        error = row.get("error") or {}
        add(
            f"| {row['probe']} | {row['status']} | "
            f"{'是' if row['structured'] else '否'} | "
            f"`{error.get('code') or error.get('type') or ''}` "
            f"{(error.get('message') or '')[:120]} |"
        )
    add("")

    a6 = sections.get("A6") or {}
    add("## A6 可用 model / effort")
    add("")
    add(
        f"- 模型目录端点状态：`{a6.get('catalog_status')}`（client_version="
        f"`{a6.get('client_version')}`）"
    )
    add(f"- 目录返回：`{a6.get('catalog_ids')}`")
    add(f"- 实测可用：`{a6.get('working_models')}`")
    add(f"- `{a6.get('effort_model')}` 接受的 effort：`{a6.get('accepted_efforts')}`")
    add("")
    catalog = a6.get("catalog_models") or []
    if catalog:
        add("| 目录模型 | 显示名 | 默认档位 | 支持档位 |")
        add("|---|---|---|---|")
        for entry in catalog:
            add(
                f"| `{entry.get('id')}` | {entry.get('display_name')} | "
                f"`{entry.get('default_effort')}` | "
                f"`{', '.join(entry.get('efforts') or [])}` |"
            )
        add("")
    add("| 实测 model | 状态 | 结果 |")
    add("|---|---|---|")
    for row in a6.get("probed_models") or []:
        add(
            f"| `{row['model']}` | {row['status']} | {'可用' if row['ok'] else '不可用'} |"
        )
    add("")

    if sections.get("B"):
        b = sections["B"]
        add("## B OAuth PKCE 全流程")
        add("")
        add(f"- 授权码交换：`{b.get('exchange')}`")
        add(f"- refresh 轮换：`{b.get('refresh')}`")
        add(f"- 连续调用：`{b.get('sequential_calls')}`")
        add("")

    add("## 调用明细（脱敏）")
    add("")
    add("| # | label | 状态 | TTFB(ms) | 耗时(ms) | 备注 |")
    add("|---|---|---|---|---|---|")
    for index, call in enumerate(report.get("calls") or [], start=1):
        add(
            f"| {index} | `{call['label']}` | {call['status']} | "
            f"{call['ttfb_ms']} | {call['latency_ms']} | {call.get('note') or ''} |"
        )
    add("")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="ChatGPT 订阅通道 spike（阶段 6）",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--section",
        choices=("a", "b", "all"),
        default="a",
        help="运行哪一段：a=离线探测，b=OAuth 全流程（需人工登录）",
    )
    parser.add_argument("--model", default=None, help="指定模型；默认取模型目录首项")
    parser.add_argument("--effort", default="low", help="推理档位")
    parser.add_argument(
        "--client-version",
        default=None,
        help="模型目录所需 client_version；默认读 `codex --version`",
    )
    parser.add_argument("--auth-file", default="~/.codex/auth.json")
    parser.add_argument("--proxy", default=None, help="显式代理；默认读系统代理")
    parser.add_argument("--max-calls", type=int, default=60, help="调用次数上限")
    parser.add_argument(
        "--max-model-probes", type=int, default=4, help="A6 实测模型数量上限"
    )
    parser.add_argument(
        "--no-write-back",
        action="store_true",
        help="刷新 token 后不回写 auth.json（可能使 codex CLI 会话失效）",
    )
    parser.add_argument("--dry-run", action="store_true", help="只打印计划，不发请求")
    parser.add_argument("--verbose", action="store_true", help="逐次打印请求结果")
    parser.add_argument(
        "--out",
        default=None,
        help="JSON 报告路径；默认 backend/.spike/spike-<ts>.json",
    )
    parser.add_argument("--md-out", default=None, help="额外输出 Markdown 报告路径")
    return parser.parse_args(argv)


def detect_client_version(explicit: str | None) -> str:
    """模型目录端点强制要求 client_version；默认沿用本机 codex CLI 版本。"""
    if explicit:
        return explicit
    try:
        out = subprocess.run(
            ["codex", "--version"], capture_output=True, text=True, timeout=5
        ).stdout
    except Exception:  # noqa: BLE001 - 未安装 codex 时用兜底版本
        return "0.162.0"
    match = re.search(r"(\d+\.\d+\.\d+)", out)
    return match.group(1) if match else "0.162.0"


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    auth_path = Path(args.auth_file).expanduser()
    if not auth_path.exists():
        print(f"找不到 {auth_path}，先执行 `codex login`。", file=sys.stderr)
        return 2
    auth = load_auth(auth_path)
    proxy = detect_proxy(args.proxy)
    client_version = detect_client_version(args.client_version)
    base_url = CHATGPT_BASE + RESPONSES_PATHS[0]

    if args.dry_run:
        print("== dry run ==")
        print(
            f"auth      : {auth_path} (mode={auth.auth_mode}, plan={auth.plan}, "
            f"account=…{auth.account_id[-4:]}, 剩余 {auth.expires_in_min} 分钟)"
        )
        print(f"proxy     : {proxy}")
        print(f"endpoint  : {base_url}")
        print(f"catalog   : {MODELS_PATH}?client_version={client_version}")
        print(f"model     : {args.model or '(目录首项)'} / effort={args.effort}")
        print(f"sections  : {args.section}（B 段需人工登录）")
        print(
            "headers   : Authorization, chatgpt-account-id, originator, "
            "session_id, OpenAI-Beta, User-Agent, Content-Type, Accept"
        )
        print(
            "body      : model, instructions, input, tools, tool_choice, "
            "parallel_tool_calls, store, stream, include, prompt_cache_key, "
            "reasoning, max_output_tokens"
        )
        print(f"call cap  : {args.max_calls}")
        return 0

    client = httpx.Client(
        proxy=proxy,
        timeout=httpx.Timeout(connect=15.0, read=180.0, write=30.0, pool=15.0),
        follow_redirects=False,
        trust_env=False,
    )
    with client:
        auth = refresh_auth(auth, client, write_back=not args.no_write_back)
        spike = Spike(
            auth,
            client,
            model=args.model or DEFAULT_MODEL,
            effort=args.effort,
            max_calls=args.max_calls,
            verbose=args.verbose,
            client_version=client_version,
        )
        sections: dict[str, Any] = {}
        model_info: dict[str, Any] = {"chosen": spike.model, "ids": []}
        ran_b = False

        if args.section in ("a", "all"):
            model_info = resolve_model(spike, args.model)
            spike.model = str(model_info.get("chosen") or spike.model)
            print(f"[model] 目录状态 {model_info.get('status')} → 使用 {spike.model}")
            sections["A1"] = run_a1(spike, spike.model)
            sections["A2"] = run_a2(spike, spike.model)
            sections["A3"] = run_a3(spike, spike.model)
            sections["A4"] = run_a4(spike, spike.model)
            sections["A5"] = run_a5(spike, spike.model)
            sections["A6"] = run_a6(spike, model_info, max_probes=args.max_model_probes)
        if args.section in ("b", "all"):
            ran_b = True
            sections["B"] = run_b(spike)

        report = {
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "environment": {
                "python": sys.version.split()[0],
                "httpx": httpx.__version__,
                "platform": sys.platform,
                "proxy": proxy or "none",
                "endpoint": base_url,
                "client_version": client_version,
            },
            "auth": {
                "source": str(auth_path),
                "auth_mode": auth.auth_mode,
                "plan": auth.plan,
                "account": f"…{auth.account_id[-4:]}",
                "expires_in_min": auth.expires_in_min,
                "refreshed": auth.refreshed,
            },
            "sections": sections,
            "conclusion": build_conclusion(sections, ran_b),
            "calls": [log.as_dict() for log in spike.logs],
        }

    out_path = (
        Path(args.out).expanduser()
        if args.out
        else Path(__file__).resolve().parent.parent
        / ".spike"
        / f"spike-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"\n[report] {out_path}")

    if args.md_out:
        md_path = Path(args.md_out).expanduser()
        md_path.write_text(render_markdown(report))
        print(f"[report] {md_path}")

    conclusion = report["conclusion"]
    print(f"\n== 结论：{conclusion['verdict']} ==")
    for name, passed in conclusion["criteria"].items():
        print(f"  {'PASS' if passed else 'FAIL'}  {name}")
    failed = conclusion["failed"]
    if failed:
        print(f"  未过项：{', '.join(failed)}")
    return 0 if conclusion["verdict"] == "implement" else 1


if __name__ == "__main__":
    raise SystemExit(main())
