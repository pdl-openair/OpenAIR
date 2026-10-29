"""Shared Claude Agent SDK turn helper (U-03).

Consolidates the verbatim-duplicated pieces between the inline runtime
(``claude_code_runner``) and the container worker (``worker/runner``):

- ``HEADLESS_ALLOWED_TOOLS`` (was duplicated as a module constant + an inline list)
- ``build_prompt`` (was ``_build_prompt`` — identical in both)
- ``extract_text_from_message`` (inline had it; worker inlined the same logic)
- ``run_turn``: the non-stream query loop + text/usage extraction

Options construction (``_build_options`` / ``_build_worker_options``) stays
per-site — that is the natural split line (inline cwd/permission/sources vs
container cwd/permission/user-skills). This module only owns the query
iteration and the pure helpers around it.

Streaming (``run_turn_stream``) is **not** unified here yet: the inline runtime
emits richer events (tool_use / tool_result, staged timeout, idle heartbeats)
while the worker captures producer errors. Merging those needs a flags design
and is deferred to a focused follow-up; both stream paths keep working as-is.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Any

from llm_core.token_usage import normalize_claude_sdk_usage

# acceptEdits / default / dontAsk modes pre-approve these tools. Single source
# for both inline and worker option builders.
HEADLESS_ALLOWED_TOOLS = [
    "Read",
    "Write",
    "Edit",
    "Glob",
    "Grep",
    "Bash",
    "Bash(python *)",
    "Bash(python:*)",
    "WebSearch",
    "WebFetch",
    "Skill",
    "Agent",
    "AskUserQuestion",
]

_TIMEOUT_REPLY = "（Claude Code 响应超时，请简化指令或重试。）"
_EMPTY_REPLY = "（Claude Code 未返回文本，请检查本机是否已登录 `claude` CLI。）"


def build_prompt(user_message: str, resume_session_id: str | None) -> str:
    """Append the OpenAIR workbench preamble for a fresh (non-resume) turn."""
    if resume_session_id:
        return user_message
    return (
        f"{user_message}\n\n"
        "（OpenAIR 工作台会话：请基于当前仓库与任务上下文作答。）"
    )


def extract_text_from_message(message: Any) -> str:
    """Concatenate TextBlock text from an AssistantMessage (empty otherwise)."""
    from claude_agent_sdk import AssistantMessage, TextBlock

    parts: list[str] = []
    if isinstance(message, AssistantMessage):
        for block in message.content:
            if isinstance(block, TextBlock):
                parts.append(block.text)
    return "\n".join(parts).strip()


def normalize_delta(prev: str, current: str) -> tuple[str, str]:
    """Compute the incremental text delta between two assistant snapshots.

    Shared by both stream paths (was ``_normalize_delta`` in claude_code_runner;
    the worker inlined an equivalent). Returns ``(delta, new_prev)``.
    """
    if not current:
        return "", prev
    if current.startswith(prev):
        delta = current[len(prev):]
        return delta, prev + delta
    if prev.endswith(current):
        return "", prev
    return current, prev + current


@dataclass(frozen=True)
class TurnResult:
    """One non-stream Claude turn result.

    ``usage`` is the normalized token usage (snake_case) from the final
    ResultMessage, or None when the turn ended without one (timeout). The inline
    runtime consumes it directly; the worker wraps it with ``to_wire_usage`` for
    the callback payload.
    """

    reply: str
    session_id: str | None
    usage: dict[str, Any] | None


async def run_turn(
    *,
    options: Any,
    prompt: str,
    resume_session_id: str | None,
    timeout_seconds: int,
    empty_reply: str = _EMPTY_REPLY,
) -> TurnResult:
    """Run one non-stream Claude turn (U-03 shared core).

    Iterates ``claude_agent_sdk.query``, collecting assistant text + the final
    session id + normalized usage. On timeout the reply is a local error message
    and usage is None — per S03-14-01 §5 a failed call with no usage records a
    0-token ledger row (no deduction).

    ``empty_reply`` lets the worker pass a container-appropriate message (the
    inline default references the local CLI login, which is misleading inside a
    worker container).
    """
    from claude_agent_sdk import ResultMessage, query

    session_id: str | None = resume_session_id
    usage: dict[str, Any] | None = None

    async def _collect() -> tuple[str, str | None, dict[str, Any] | None]:
        sid = resume_session_id
        parts: list[str] = []
        local_usage: dict[str, Any] | None = None
        async for message in query(prompt=prompt, options=options):
            chunk = extract_text_from_message(message)
            if chunk:
                parts.append(chunk)
            if isinstance(message, ResultMessage):
                sid = message.session_id or sid
                local_usage = normalize_claude_sdk_usage(getattr(message, "usage", None)) or local_usage
        return "\n".join(parts).strip(), sid, local_usage

    try:
        reply, session_id, usage = await asyncio.wait_for(_collect(), timeout=timeout_seconds)
        timed_out = False
    except asyncio.TimeoutError:
        reply = _TIMEOUT_REPLY
        timed_out = True
        usage = None

    collected_reply = reply
    if not reply:
        reply = empty_reply
    # Align with run_turn_stream: a completed reply without provider usage still
    # settles via the S03-14-01 §5 character estimate (no silent free turns).
    if usage is None and not timed_out and collected_reply:
        from llm_core.token_usage import estimate_usage_from_text

        usage = estimate_usage_from_text(collected_reply)
    return TurnResult(reply=reply, session_id=session_id, usage=usage)


async def run_turn_stream(
    *,
    options: Any,
    prompt: str,
    resume_session_id: str | None,
    timeout_seconds: int,
    initial_timeout_seconds: int | None = None,
    heartbeat_interval_seconds: float | None = None,
    emit_tool_events: bool = False,
    tool_progress_label: Callable[[str], str] | None = None,
    tool_progress_label_done: Callable[[str, bool, bool], str] | None = None,
    extract_tool_results: Callable[[Any], list[dict[str, Any]]] | None = None,
    timeout_reply: str = _TIMEOUT_REPLY,
    heartbeat_text_fn: Callable[[int], str] | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Stream one Claude turn (U-03 shared core).

    Yields the existing event protocol used by both runtimes:
      - ``{"type":"assistant_delta","text":...}``
      - ``{"type":"progress","text":...}``
      - ``{"type":"tool_use",...}`` / ``{"type":"tool_result",...}`` (only when
        ``emit_tool_events`` — the inline runtime wants them; the worker doesn't)
      - ``{"type":"done","sessionId":...,"usage":...,"error":?}``

    Flags reconcile the two runtimes' genuine divergence:
    - ``initial_timeout_seconds`` / ``heartbeat_interval_seconds``: the inline
      runtime uses a staged timeout (shorter before any output) + idle
      heartbeats; the worker uses a single flat timeout. When both are None the
      flat-timeout path runs.
    - ``emit_tool_events`` + the three tool callables: only the inline runtime
      passes them (its ``_tool_progress_label`` / ``_extract_tool_results_from_message``).
    - Producer errors are always captured and surfaced in the ``done`` event
      (backports the worker's honest behavior to the inline runtime, which
      previously swallowed them).
    """
    from claude_agent_sdk import AssistantMessage, ResultMessage, StreamEvent, TaskProgressMessage, query

    session_id: str | None = resume_session_id
    emitted = ""
    saw_raw_stream_delta = False
    final_usage: dict[str, Any] | None = None
    staged = initial_timeout_seconds is not None

    async def _consume() -> AsyncIterator[dict[str, Any]]:
        nonlocal session_id, emitted, saw_raw_stream_delta, final_usage
        tool_names: dict[str, str] = {}
        async for message in query(prompt=prompt, options=options):
            if isinstance(message, StreamEvent):
                raw = message.event or {}
                if raw.get("type") == "content_block_delta":
                    delta = ((raw.get("delta") or {}).get("text") or "")
                    if delta:
                        saw_raw_stream_delta = True
                        emitted += delta
                        yield {"type": "assistant_delta", "text": delta}
                elif emit_tool_events and raw.get("type") == "content_block_start":
                    content_block = raw.get("content_block") or {}
                    if content_block.get("type") == "tool_use":
                        tool_use_id = content_block.get("id") or ""
                        tool_name = content_block.get("name") or ""
                        tool_names[tool_use_id] = tool_name
                        yield {
                            "type": "tool_use",
                            "toolUseId": tool_use_id,
                            "toolName": tool_name,
                            "input": content_block.get("input") or {},
                        }
                        if tool_progress_label:
                            yield {"type": "progress", "text": tool_progress_label(tool_name)}
            elif isinstance(message, AssistantMessage):
                if emit_tool_events and extract_tool_results:
                    for tool_result_event in extract_tool_results(message):
                        yield tool_result_event
                        if tool_progress_label_done:
                            yield {
                                "type": "progress",
                                "text": tool_progress_label_done(
                                    tool_names.get(tool_result_event["toolUseId"], ""),
                                    True,
                                    bool(tool_result_event["isError"]),
                                ),
                            }
                if saw_raw_stream_delta:
                    continue
                current = extract_text_from_message(message)
                delta, emitted = normalize_delta(emitted, current)
                if delta:
                    yield {"type": "assistant_delta", "text": delta}
            elif isinstance(message, TaskProgressMessage):
                if message.description:
                    yield {"type": "progress", "text": message.description}
            elif isinstance(message, ResultMessage):
                session_id = message.session_id or session_id
                final_usage = normalize_claude_sdk_usage(getattr(message, "usage", None)) or final_usage

    queue: asyncio.Queue = asyncio.Queue()
    SENTINEL = object()
    producer_error: dict[str, str | None] = {"msg": None}

    async def _producer() -> None:
        try:
            async for event in _consume():
                await queue.put(event)
        except Exception as exc:  # noqa: BLE001 — surface in done, don't crash the stream
            producer_error["msg"] = f"{type(exc).__name__}: {exc}"
        finally:
            await queue.put(SENTINEL)

    producer_task = asyncio.create_task(_producer())
    timed_out = False
    last_event_at = time.monotonic()
    try:
        while True:
            if staged:
                timeout = timeout_seconds if emitted else initial_timeout_seconds
                remaining = timeout - (time.monotonic() - last_event_at)
                if remaining <= 0:
                    timed_out = True
                    break
                get_timeout = min(heartbeat_interval_seconds or remaining, remaining)
            else:
                get_timeout = timeout_seconds
            try:
                item = await asyncio.wait_for(queue.get(), timeout=get_timeout)
            except asyncio.TimeoutError:
                if staged and heartbeat_interval_seconds is not None:
                    elapsed = max(1, int(time.monotonic() - last_event_at))
                    yield {"type": "progress", "text": (heartbeat_text_fn(elapsed) if heartbeat_text_fn else f"已等待 {elapsed} 秒…")}
                    continue
                timed_out = True
                break
            if item is SENTINEL:
                break
            last_event_at = time.monotonic()
            yield item  # type: ignore[misc]
    finally:
        if not producer_task.done():
            producer_task.cancel()
            try:
                await producer_task
            except asyncio.CancelledError:
                pass
            except Exception:  # noqa: BLE001
                pass
        if timed_out:
            from llm_core.token_usage import to_wire_usage

            if not emitted:
                yield {"type": "assistant_delta", "text": timeout_reply}
            yield {"type": "done", "sessionId": session_id, "error": "timeout", "usage": to_wire_usage(final_usage)}
            return

    from llm_core.token_usage import estimate_usage_from_text, to_wire_usage

    # Producer crashed mid-stream: surface the real cause instead of an empty reply.
    err = producer_error["msg"]
    if err:
        if not emitted:
            yield {"type": "assistant_delta", "text": f"（Claude Code 调用失败：{err}）"}
        yield {"type": "done", "sessionId": session_id, "error": err, "usage": to_wire_usage(final_usage)}
        return

    usage = final_usage or (estimate_usage_from_text(emitted) if emitted else None)
    yield {"type": "done", "sessionId": session_id, "usage": to_wire_usage(usage)}
