"""AgentEngine:进程内 tool-calling 循环。

职责(与 runner 的分工):
- engine:驱动单个 run 的模型回合——流式消费 provider、合并文本、
  提取工具调用、执行工具、把结果回注、循环直到终态;
- runner:持久化(事件落库/状态机/租约)与恢复,不在本模块。

生命周期保证:
- 取消:每 ``cancel_poll_seconds`` 轮询一次 ``should_cancel()``,
  命中即以 ``cancelled`` 结局返回(pump 任务被取消,HTTP 流关闭);
- 单回合超时 ``turn_timeout_seconds``(默认 900s);
- 仅传输类错误重试(默认 2 次,退避 + retry_after);
- ``max_rounds``(默认 40)防止工具调用死循环。
"""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import suppress
from typing import Any, AsyncIterator, Callable

from app.agent.events import (
    TextDeltaBuffer,
    tool_output_summary,
)
from app.agent.models import (
    ChatItem,
    EngineEvent,
    EngineOutcome,
    EngineStatus,
    TurnRequest,
    function_call_output,
)
from app.agent.provider import (
    AgentProvider,
    AgentProviderError,
    ProviderEvent,
    ProviderReasoningDelta,
    ProviderTextDelta,
    ProviderToolCallStarted,
    ProviderTurnCompleted,
)

logger = logging.getLogger(__name__)

DEFAULT_MAX_ROUNDS = 40
DEFAULT_TURN_TIMEOUT_SECONDS = 900.0
DEFAULT_CANCEL_POLL_SECONDS = 2.0
DEFAULT_MAX_TRANSPORT_RETRIES = 2
_SENTINEL = object()


class TurnTimeoutError(Exception):
    """单回合超时。"""


class _EngineCancelledError(Exception):
    """内部取消信号。"""


EventSink = Callable[[EngineEvent], Any]


class AgentEngine:
    """执行一次批量批改的完整 tool-calling 循环。"""

    def __init__(
        self,
        provider: AgentProvider,
        tools,
        *,
        instructions: str,
        max_rounds: int = DEFAULT_MAX_ROUNDS,
        turn_timeout_seconds: float = DEFAULT_TURN_TIMEOUT_SECONDS,
        cancel_poll_seconds: float = DEFAULT_CANCEL_POLL_SECONDS,
        max_transport_retries: int = DEFAULT_MAX_TRANSPORT_RETRIES,
        should_cancel: Callable[[], bool] | None = None,
        model: str | None = None,
        reasoning_effort: str | None = None,
        max_output_tokens: int | None = None,
        sleep: Callable[[float], Any] = asyncio.sleep,
    ) -> None:
        self._provider = provider
        self._tools = tools
        self._instructions = instructions
        self._max_rounds = max_rounds
        self._turn_timeout = turn_timeout_seconds
        self._cancel_poll = max(0.05, cancel_poll_seconds)
        self._max_retries = max(0, max_transport_retries)
        self._should_cancel = should_cancel
        self._model = model
        self._reasoning_effort = reasoning_effort
        self._max_output_tokens = max_output_tokens
        self._sleep = sleep

    async def run(self, items: list[ChatItem], emit: EventSink) -> EngineOutcome:
        """从 ``items`` 出发执行循环;``emit`` 可为同步或异步回调。"""
        self._buffer = TextDeltaBuffer()
        total_usage: dict[str, Any] = {}
        history: list[ChatItem] = list(items)
        rounds = 0

        async def _emit(kind: str, payload: dict[str, Any]) -> None:
            event = EngineEvent(kind=kind, payload=payload)  # type: ignore[arg-type]
            result = emit(event)
            if asyncio.iscoroutine(result):
                await result

        async def _flush_text() -> None:
            merged = self._buffer.flush()
            if merged:
                await _emit("message_delta", {"text": merged})

        outcome: EngineOutcome | None = None
        try:
            for round_index in range(1, self._max_rounds + 1):
                rounds = round_index
                if self._should_cancel and self._should_cancel():
                    outcome = EngineOutcome(EngineStatus.cancelled)
                    break
                turn = await self._run_turn(history, _emit, _flush_text)
                await _flush_text()
                usage = turn.usage or {}
                if usage:
                    _merge_usage(total_usage, usage)
                    await _emit("usage", {"round": round_index, **usage})
                response_items = [
                    item for item in turn.response_items if isinstance(item, dict)
                ]
                history.extend(response_items)
                tool_calls = [
                    item
                    for item in response_items
                    if item.get("type") == "function_call"
                    and item.get("call_id")
                ]
                if not tool_calls:
                    await _emit(
                        "turn_completed",
                        {"stop_reason": turn.stop_reason, "round": round_index},
                    )
                    outcome = EngineOutcome(
                        EngineStatus.incomplete,
                        items=history,
                        usage=total_usage,
                        rounds=rounds,
                    )
                    break
                terminal = await self._execute_tool_calls(
                    tool_calls, history, _emit, _flush_text
                )
                if isinstance(terminal, EngineOutcome):
                    terminal.items = history
                    terminal.usage = total_usage
                    terminal.rounds = rounds
                    outcome = terminal
                    break
                if terminal == "saved":
                    await _emit(
                        "turn_completed",
                        {"stop_reason": "tool_terminal", "round": round_index},
                    )
                    outcome = EngineOutcome(
                        EngineStatus.completed,
                        items=history,
                        usage=total_usage,
                        rounds=rounds,
                    )
                    break
                # 无终态工具:把结果回注后进入下一回合
            if outcome is None:
                await _emit(
                    "error", {"message": f"达到最大回合数 {self._max_rounds},批改未完成"}
                )
                outcome = EngineOutcome(
                    EngineStatus.failed,
                    error=f"达到最大回合数 {self._max_rounds},批改未完成",
                    items=history,
                    usage=total_usage,
                    rounds=rounds,
                )
            return outcome
        except _EngineCancelledError:
            await _flush_text()
            return EngineOutcome(
                EngineStatus.cancelled, items=history, usage=total_usage, rounds=rounds
            )
        except TurnTimeoutError as exc:
            await self._emit("error", {"message": str(exc)})
            return EngineOutcome(
                EngineStatus.failed,
                error=str(exc),
                items=history,
                usage=total_usage,
                rounds=rounds,
            )
        except AgentProviderError as exc:
            await _emit("error", {"message": str(exc), "kind": exc.kind})
            return EngineOutcome(
                EngineStatus.failed,
                error=str(exc),
                items=history,
                usage=total_usage,
                rounds=rounds,
            )
        except Exception as exc:  # noqa: BLE001 - 引擎兜底,转 failed 结局
            logger.exception("agent engine 未预期失败")
            await _emit("error", {"message": f"{type(exc).__name__}: {exc}"})
            return EngineOutcome(
                EngineStatus.failed,
                error=f"{type(exc).__name__}: {exc}",
                items=history,
                usage=total_usage,
                rounds=rounds,
            )

    # ------------------------------------------------------------------
    # 单个回合
    # ------------------------------------------------------------------

    async def _run_turn(self, history, emit, flush_text) -> ProviderTurnCompleted:
        """执行一次回合,含传输错误重试。"""
        attempt = 0
        while True:
            turn_result: ProviderTurnCompleted | None = None
            started = False
            try:
                async for event in self._consume_turn(history):
                    if isinstance(event, ProviderTextDelta):
                        if self._buffer.add(event.text):
                            await flush_text()
                    elif isinstance(event, ProviderReasoningDelta):
                        await flush_text()
                        await emit("reasoning", {"text": event.text})
                    elif isinstance(event, ProviderToolCallStarted):
                        started = True
                        await flush_text()
                        await emit(
                            "tool_started",
                            {"call_id": event.call_id, "name": event.name},
                        )
                    elif isinstance(event, ProviderTurnCompleted):
                        turn_result = event
                if turn_result is None:
                    raise AgentProviderError(
                        kind="retryable",
                        message="模型流在没有完成事件的情况下结束",
                        retryable=True,
                    )
                return turn_result
            except AgentProviderError as exc:
                if not exc.retryable or attempt >= self._max_retries:
                    raise
                attempt += 1
                delay = exc.retry_after if exc.retry_after is not None else float(attempt)
                await emit(
                    "notice",
                    {
                        "text": f"模型通道传输错误({exc.message}),第 {attempt} 次重试",
                    },
                )
                await self._sleep(min(delay, 30.0))
                if started:
                    # 已向 UI 产出过工具事件:重试整回合,标注可能出现重复文本
                    await emit("notice", {"text": "该回合将完整重放"})

    async def _consume_turn(
        self, history: list[ChatItem]
    ) -> AsyncIterator[ProviderEvent]:
        """把 provider 的流包装为可控队列,支持取消轮询与回合超时。"""
        request = TurnRequest(
            instructions=self._instructions,
            items=list(history),
            tools=self._tools.specs(),
            model=self._model,
            reasoning_effort=self._reasoning_effort,
            max_output_tokens=self._max_output_tokens,
        )
        queue: asyncio.Queue[Any] = asyncio.Queue()

        async def _pump() -> None:
            try:
                async for event in self._provider.create_turn(request):
                    await queue.put(event)
            except asyncio.CancelledError:
                raise
            except AgentProviderError as exc:
                await queue.put(exc)
            except Exception as exc:  # noqa: BLE001 - 统一转统一错误
                await queue.put(exc)
            finally:
                await queue.put(_SENTINEL)

        pump = asyncio.create_task(_pump())
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._turn_timeout
        try:
            while True:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    raise TurnTimeoutError(
                        f"模型回合超过 {int(self._turn_timeout)}s 未完成"
                    )
                wait_for = min(self._cancel_poll, remaining)
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=wait_for)
                except asyncio.TimeoutError:
                    if self._should_cancel and self._should_cancel():
                        raise _EngineCancelledError() from None
                    # tick:窗口满则冲刷文本
                    if self._buffer.should_flush():
                        merged = self._buffer.flush()
                        if merged:
                            yield ProviderTextDelta(merged)
                    continue
                if item is _SENTINEL:
                    return
                if isinstance(item, AgentProviderError):
                    raise item
                if isinstance(item, BaseException):
                    raise item
                yield item
        finally:
            if not pump.done():
                pump.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await pump

    # ------------------------------------------------------------------
    # 工具执行
    # ------------------------------------------------------------------

    async def _execute_tool_calls(
        self, tool_calls: list[ChatItem], history: list[ChatItem], emit, flush_text
    ):
        """执行本回合全部工具调用。

        返回值:
        - ``EngineOutcome``:终态(检查点/取消);
        - ``"saved"``:save_assessment 成功,引擎应结束为 completed;
        - ``None``:继续下一回合。
        """
        from app.agent.tools import CheckpointRequested

        saved = False
        for call in tool_calls:
            name = str(call.get("name") or "")
            call_id = str(call.get("call_id") or "")
            raw_arguments = call.get("arguments")
            if isinstance(raw_arguments, dict):
                arguments: dict[str, Any] = raw_arguments
                arguments_text = json.dumps(raw_arguments, ensure_ascii=False)
            else:
                arguments_text = str(raw_arguments or "")
                try:
                    parsed = json.loads(arguments_text) if arguments_text else {}
                except json.JSONDecodeError:
                    parsed = None
                if not isinstance(parsed, dict):
                    message = "工具参数不是合法 JSON 对象,请修正后重新调用"
                    await emit(
                        "tool_finished",
                        {"call_id": call_id, "name": name, "ok": False,
                         "summary": message},
                    )
                    history.append(function_call_output(call_id, {"error": message}))
                    continue
                arguments = parsed
            try:
                result = self._tools.execute(name, arguments)
            except CheckpointRequested as exc:
                await emit(
                    "checkpoint_requested",
                    {"call_id": call_id, "name": name, "message": exc.message},
                )
                return EngineOutcome(
                    EngineStatus.checkpoint, checkpoint_message=exc.message
                )
            except _EngineCancelledError:
                raise
            if not result.ok:
                summary = (result.error or "工具执行失败")[:400]
                await emit(
                    "tool_finished",
                    {"call_id": call_id, "name": name, "ok": False, "summary": summary},
                )
                history.append(
                    function_call_output(call_id, {"ok": False, "error": result.error})
                )
                continue
            summary = tool_output_summary(result.output)
            await emit(
                "tool_finished",
                {"call_id": call_id, "name": name, "ok": True, "summary": summary},
            )
            history.append(function_call_output(call_id, result.output))
            if name == "save_assessment":
                saved = True
        if saved:
            return "saved"
        return None


def _merge_usage(total: dict[str, Any], usage: dict[str, Any]) -> None:
    """跨回合累计 usage 的数值字段。"""
    for key, value in usage.items():
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            total[key] = total.get(key, 0) + value
        else:
            total.setdefault(key, value)
