"""HTTP callback client: worker → API server.

The worker posts execution results and stream events back to the API server
via the ``/internal/worker/`` endpoints.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_CALLBACK_URL = os.environ.get("OPENAIR_WORKER_CALLBACK_URL", "http://host.docker.internal:8000")
_WORKER_TOKEN = os.environ.get("OPENAIR_WORKER_AUTH_TOKEN", "")

_TIMEOUT_SECONDS = 30


def post_result(
    execution_id: str,
    *,
    reply: str,
    session_id: str | None = None,
    status: str = "completed",
    error: str | None = None,
    runtime: str = "claude_code",
    usage: dict[str, Any] | None = None,
) -> None:
    """Post the final execution result back to the API server.

    ``usage`` carries the normalized token usage (S03-14-02 ledger); None when
    the turn produced no usage (e.g. timeout before any model output).
    """
    url = f"{_CALLBACK_URL}/internal/worker/result"
    payload: dict[str, Any] = {
        "execution_id": execution_id,
        "reply": reply,
        "session_id": session_id,
        "status": status,
        "error": error,
        "runtime": runtime,
        "usage": usage,
    }
    _post(url, payload)


def post_stream_event(
    execution_id: str,
    event: dict[str, Any],
) -> None:
    """Post a single streaming event to the API server."""
    url = f"{_CALLBACK_URL}/internal/worker/stream-event"
    payload: dict[str, Any] = {
        "execution_id": execution_id,
        "event": event,
    }
    _post(url, payload)


def post_log(
    execution_id: str,
    level: str,
    message: str,
) -> None:
    """Post a log message to the API server (optional, for observability)."""
    url = f"{_CALLBACK_URL}/internal/worker/log"
    payload: dict[str, Any] = {
        "execution_id": execution_id,
        "level": level,
        "message": message,
    }
    try:
        _post(url, payload)
    except Exception:
        pass  # logs are best-effort


def _post(url: str, payload: dict[str, Any]) -> None:
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if _WORKER_TOKEN:
        headers["X-OpenAIR-Worker-Token"] = _WORKER_TOKEN

    with httpx.Client(timeout=_TIMEOUT_SECONDS) as client:
        resp = client.post(url, json=payload, headers=headers)
        if resp.status_code >= 400:
            logger.warning("callback POST %s → %s: %s", url, resp.status_code, resp.text[:200])
        else:
            logger.info("callback POST %s → %s", url, resp.status_code)
