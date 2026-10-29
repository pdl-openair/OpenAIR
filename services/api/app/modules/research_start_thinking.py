"""Append-only, user-facing thinking for a research-start session.

The structured provider answer stays in ``message.content``. This module only
folds model reasoning into a transcript the workbench can show without
replacing earlier thoughts.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable, Mapping
from typing import Any

_INTERNAL = re.compile(
    r"(?:\bplanner\b|\bcompiler\b|\bschema\b|\bworkflow\b|\bworker\b|"
    r"substageinstance|tool\s*call|\bprompt\b|规划器|编译器|工作流路由|工具调用|提示词)",
    re.IGNORECASE,
)
_SENTENCE_END = re.compile(r"(?<=[。！？\n])")
_MAX_PUBLIC_CHARS = 4000


def public_thinking_text(raw: str) -> str:
    """Keep research-language reasoning and drop internal process sentences."""
    text = raw.replace("\r\n", "\n").strip()
    if not text:
        return ""
    if not _SENTENCE_END.search(text):
        return "" if _INTERNAL.search(text) else text[:_MAX_PUBLIC_CHARS]
    kept: list[str] = []
    for piece in _SENTENCE_END.split(text):
        fragment = piece.strip()
        if not fragment or _INTERNAL.search(fragment):
            continue
        kept.append(fragment)
    return "".join(kept).strip()[:_MAX_PUBLIC_CHARS]


def fold_thinking_transcript(
    existing: Any,
    *,
    key: str,
    text: str,
    seal: bool,
    occurred_at: str | None = None,
) -> list[dict[str, str]]:
    """Grow the open entry for ``key``. Formed entries stay unchanged."""
    items = _coerce_items(existing)
    current = next((item for item in items if item["key"] == key), None)
    if current is not None and current["state"] == "formed":
        return items
    for item in items:
        if item["state"] == "forming" and item["key"] != key:
            item["state"] = "formed"
    if current is None:
        if not text:
            return items
        items.append(
            {
                "key": key,
                "state": "formed" if seal else "forming",
                "text": text,
                **({"occurredAt": occurred_at} if occurred_at else {}),
            }
        )
        return items
    if text:
        current["text"] = text
    if seal and current["text"]:
        current["state"] = "formed"
    return items


def project_public_thoughts(value: Any) -> list[dict[str, str]]:
    """Public outlet for the collaboration panel. Empty input stays absent upstream."""
    thoughts: list[dict[str, str]] = []
    for item in _coerce_items(value):
        text = public_thinking_text(item["text"])
        if not text:
            continue
        thoughts.append(
            {
                "key": item["key"],
                "state": "forming" if item["state"] == "forming" else "formed",
                "text": text,
                **({"occurredAt": item["occurredAt"]} if item.get("occurredAt") else {}),
            }
        )
    return thoughts


def reasoning_delta(chunk: Mapping[str, Any]) -> str:
    choices = chunk.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], Mapping):
        return ""
    delta = choices[0].get("delta")
    if not isinstance(delta, Mapping):
        return ""
    parts: list[str] = []
    for field in ("reasoning_content", "reasoning"):
        value = delta.get(field)
        if isinstance(value, str) and value:
            parts.append(value)
    return "".join(parts)


def reasoning_from_chat_body(body: Mapping[str, Any]) -> str:
    """Reasoning attached to a finished chat body. Never the answer content."""
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], Mapping):
        return ""
    message = choices[0].get("message")
    if not isinstance(message, Mapping):
        return ""
    for field in ("reasoning_content", "reasoning"):
        value = message.get(field)
        if isinstance(value, str) and value.strip():
            return value
    return ""


class IncompleteChatStreamError(ValueError):
    """An SSE response ended without a Provider completion marker."""


def _stream_chunk(data: str) -> Mapping[str, Any] | None:
    try:
        chunk = json.loads(data)
    except ValueError:
        # Invalid data stays in the captured response. Final parsing will reject
        # it after the complete Provider response is durably recorded.
        return None
    return chunk if isinstance(chunk, Mapping) else None


def capture_chat_stream(lines: Iterable[str], on_reasoning: Callable[[str], None]) -> bytes:
    """Capture normalized response lines and publish thoughts as they arrive."""
    raw_lines: list[str] = []
    streamed = False
    completed = False
    for line in lines:
        raw_lines.append(line)
        stripped = line.strip()
        if not stripped.startswith("data:"):
            continue
        streamed = True
        data = stripped[5:].strip()
        if data == "[DONE]":
            completed = True
            break
        chunk = _stream_chunk(data)
        if chunk is not None:
            delta = reasoning_delta(chunk)
            if delta:
                on_reasoning(delta)
            choices = chunk.get("choices")
            if isinstance(choices, list) and choices and isinstance(choices[0], Mapping):
                completed = completed or bool(choices[0].get("finish_reason"))
    if streamed and not completed:
        raise IncompleteChatStreamError("provider stream did not reach completion")
    return "\n".join(raw_lines).encode("utf-8")


def consume_chat_stream(lines: Iterable[str], on_reasoning: Callable[[str], None]) -> bytes:
    """Normalize completed SSE only; preserve the existing plain JSON response form."""
    raw = capture_chat_stream(lines, on_reasoning)
    payloads = [
        line.strip()[5:].strip()
        for line in raw.decode("utf-8").splitlines()
        if line.strip().startswith("data:") and line.strip()[5:].strip() != "[DONE]"
    ]
    if not payloads:
        blob = raw.decode("utf-8").strip()
        if blob.startswith("{"):
            body = json.loads(blob)
            if isinstance(body, Mapping):
                reasoning = reasoning_from_chat_body(body)
                if reasoning:
                    on_reasoning(reasoning)
            return blob.encode("utf-8")
        raise ValueError("provider stream did not contain chat deltas")
    chunks = [json.loads(payload) for payload in payloads]
    return json.dumps(_assemble_chat_completion(chunks), ensure_ascii=False).encode("utf-8")


def _assemble_chat_completion(chunks: list[Any]) -> dict[str, Any]:
    content: list[str] = []
    reasoning: list[str] = []
    finish_reason = None
    response_id = None
    for chunk in chunks:
        if not isinstance(chunk, Mapping):
            continue
        if isinstance(chunk.get("id"), str) and chunk["id"].strip():
            response_id = chunk["id"].strip()
        choices = chunk.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], Mapping):
            continue
        choice = choices[0]
        if choice.get("finish_reason"):
            finish_reason = choice.get("finish_reason")
        delta = choice.get("delta")
        if not isinstance(delta, Mapping):
            continue
        if isinstance(delta.get("content"), str):
            content.append(delta["content"])
        piece = reasoning_delta(chunk)
        if piece:
            reasoning.append(piece)
    message: dict[str, Any] = {"role": "assistant", "content": "".join(content)}
    if reasoning:
        message["reasoning_content"] = "".join(reasoning)
    body: dict[str, Any] = {"choices": [{"finish_reason": finish_reason, "message": message}]}
    if response_id:
        body["id"] = response_id
    return body


def _coerce_items(value: Any) -> list[dict[str, str]]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return []
    if not isinstance(value, list):
        return []
    items: list[dict[str, str]] = []
    for raw in value:
        if not isinstance(raw, Mapping):
            continue
        key = str(raw.get("key") or "").strip()
        text = str(raw.get("text") or "")
        if not key or not text:
            continue
        state = "forming" if raw.get("state") == "forming" else "formed"
        items.append(
            {
                "key": key,
                "state": state,
                "text": text,
                **({"occurredAt": str(raw["occurredAt"])} if raw.get("occurredAt") else {}),
            }
        )
    return items
