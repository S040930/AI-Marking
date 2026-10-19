#!/usr/bin/env python
"""P0 spike：验证 ChatGPT 订阅通道能否两路并发、各自独立 turn-state。

只读使用 ``system_config.chatgpt_tokens``，不打印令牌。默认跑 3 回合文本
对话；每路一个独立 ``ChatGptSubProvider``（独立 session_id 与 turn_state），
并发执行后汇总耗时、错误类别与 turn-state 是否持续存在。

用法（backend/ 下）::

    .venv/bin/python scripts/spike_sub_concurrency.py --turns 3

退出码：0 成功；2 未登录/无令牌；1 有分支失败。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import uuid
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent.models import TurnRequest, user_message  # noqa: E402
from app.agent.provider import (  # noqa: E402
    AgentProviderError,
    ProviderTextDelta,
    ProviderTurnCompleted,
)
from app.agent.providers.chatgpt_sub import ChatGptSubProvider  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.services.provider_config import (  # noqa: E402
    CHATGPT_DEFAULT_MODEL_KEY,
    get_provider_config,
    load_chatgpt_tokens,
    save_chatgpt_tokens,
)

INSTRUCTIONS = "You are a terse connectivity test agent. Follow the user exactly."


def _load_config() -> tuple[dict[str, Any] | None, str | None]:
    with SessionLocal() as db:
        tokens = load_chatgpt_tokens(db)
        config = get_provider_config(db)
    model = config.get(CHATGPT_DEFAULT_MODEL_KEY) or None
    return tokens, model


def _save_tokens(updated: dict[str, Any]) -> None:
    with SessionLocal() as db:
        save_chatgpt_tokens(db, updated)


def _reload_tokens() -> dict[str, Any] | None:
    with SessionLocal() as db:
        return load_chatgpt_tokens(db)


async def _run_chain(
    label: str,
    provider: ChatGptSubProvider,
    model: str | None,
    turns: int,
) -> dict[str, Any]:
    items: list[dict[str, Any]] = [
        user_message("Reply with the single word OK, nothing else.")
    ]
    state: dict[str, Any] | None = None
    texts: list[str] = []
    started = time.monotonic()
    for index in range(1, turns + 1):
        request = TurnRequest(
            instructions=INSTRUCTIONS,
            items=list(items),
            tools=[],
            model=model,
            provider_state=state,
        )
        completed: ProviderTurnCompleted | None = None
        async for event in provider.create_turn(request):
            if isinstance(event, ProviderTextDelta):
                texts.append(event.text)
            elif isinstance(event, ProviderTurnCompleted):
                completed = event
        if completed is None:
            raise RuntimeError(f"{label} 第 {index} 回合没有完成事件")
        state = completed.provider_state or state
        items.extend(completed.response_items)
        items.append(user_message("Reply OK again."))
        print(
            json.dumps(
                {
                    "branch": label,
                    "turn": index,
                    "chars": len("".join(texts)),
                    "has_turn_state": bool(state),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
    await provider.aclose()
    return {
        "branch": label,
        "ok": True,
        "elapsed_s": round(time.monotonic() - started, 2),
        "chars": len("".join(texts)),
        "has_turn_state": bool(state),
    }


async def main_async(turns: int, model_override: str | None) -> int:
    tokens, default_model = _load_config()
    if not tokens or not tokens.get("access_token"):
        print("未找到 ChatGPT 订阅令牌；请先在设置页登录。", file=sys.stderr)
        return 2
    model = model_override or default_model

    def make_provider(branch: str) -> ChatGptSubProvider:
        return ChatGptSubProvider(
            tokens=tokens,
            default_model=model,
            session_id=f"spike-{branch}-{uuid.uuid4().hex[:8]}",
            on_tokens_refreshed=_save_tokens,
            load_tokens=_reload_tokens,
        )

    # 先用一次探测把过期令牌换掉并落库，避免两路同时消费同一个 refresh_token。
    probe = make_provider("probe")
    try:
        await probe._ensure_token()  # noqa: SLF001 - spike 需要显式预热
    except AgentProviderError as exc:
        print(
            f"订阅令牌预热失败：{exc.kind}: {exc.message}",
            file=sys.stderr,
        )
        return 1
    finally:
        await probe.aclose()
    refreshed_tokens = _reload_tokens() or tokens

    def make_branch(branch: str) -> ChatGptSubProvider:
        return ChatGptSubProvider(
            tokens=refreshed_tokens,
            default_model=model,
            session_id=f"spike-{branch}-{uuid.uuid4().hex[:8]}",
            on_tokens_refreshed=_save_tokens,
            load_tokens=_reload_tokens,
        )

    branches = {
        "A": make_branch("A"),
        "B": make_branch("B"),
    }
    results = await asyncio.gather(
        *(
            _run_chain(label, provider, model, turns)
            for label, provider in branches.items()
        ),
        return_exceptions=True,
    )

    failed = False
    summary: list[dict[str, Any]] = []
    for label, result in zip(branches, results):
        if isinstance(result, BaseException):
            failed = True
            if isinstance(result, AgentProviderError):
                detail = {
                    "branch": label,
                    "ok": False,
                    "kind": result.kind,
                    "status_code": result.status_code,
                    "message": result.message[:300],
                }
            else:
                detail = {
                    "branch": label,
                    "ok": False,
                    "kind": type(result).__name__,
                    "message": str(result)[:300],
                }
            summary.append(detail)
        else:
            summary.append(result)
    print("\n=== summary ===")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 1 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--turns", type=int, default=3, help="每路回合数")
    parser.add_argument("--model", default=None, help="覆盖默认订阅模型")
    args = parser.parse_args()
    if args.turns < 1:
        parser.error("--turns 必须 >= 1")
    return asyncio.run(main_async(args.turns, args.model))


if __name__ == "__main__":
    raise SystemExit(main())
