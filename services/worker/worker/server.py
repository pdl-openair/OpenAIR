"""Worker server mode: long-lived claim loop for keepalive reuse.

When the API launches a server-mode worker (``OPENAIR_WORKER_SERVER=1``), this
loop long-polls ``POST /internal/worker/claim`` for the next turn of the bound
task, runs it (reusing the already-warm Python process + Claude Agent SDK),
posts results via the normal callback path, and repeats.

If no turn arrives within the keepalive window, the worker self-exits
(self-lease) so resources are reclaimed even if the API-side reaper is
unavailable. The worker is bound to a single task (and therefore a single
owner) for its entire life — it never crosses tenants.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)


def _turn_to_config(
    turn: dict[str, Any], *, task_id: str, owner_id: str
) -> dict[str, Any]:
    """Map a /worker/claim turn payload to the config-dict shape used by
    ``worker.entrypoint._run_blocking`` / ``_run_streaming``."""
    return {
        "task_id": task_id,
        "execution_id": turn["execution_id"],
        "message": turn["message"],
        "mode": turn.get("mode", "execution"),
        "session_id": turn.get("session_id"),
        "task": turn.get("task") or {},
        "exec_kwargs": turn.get("exec_kwargs") or {},
        "streaming": bool(turn.get("streaming", False)),
        "permission_mode": os.environ.get("OPENAIR_CLAUDE_PERMISSION_MODE", "bypassPermissions"),
        "owner_id": owner_id,
        "user_skill_names": [],
    }


async def _claim_turn(
    client: httpx.AsyncClient,
    callback_url: str,
    token: str,
    task_id: str,
    owner_id: str,
    poll_timeout: float,
) -> dict[str, Any] | None:
    """Long-poll one claim request. Returns the turn dict or ``None``."""
    url = f"{callback_url}/internal/worker/claim"
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if token:
        headers["X-OpenAIR-Worker-Token"] = token
    payload = {"task_id": task_id, "owner_id": owner_id}
    try:
        resp = await client.post(url, json=payload, headers=headers, timeout=poll_timeout + 10)
    except (httpx.TimeoutException, httpx.TransportError) as exc:
        logger.debug("claim poll returned no turn: %s", exc)
        return None
    if resp.status_code >= 400:
        logger.warning("claim failed: %s %s", resp.status_code, resp.text[:200])
        return None
    try:
        data = resp.json()
    except Exception:
        logger.warning("claim returned non-JSON: %s", resp.text[:200])
        return None
    turn = data.get("turn")
    return turn if isinstance(turn, dict) else None


async def run_server_loop(
    task_id: str, owner_id: str, user_skill_names: list[str]
) -> None:
    """Long-poll for turns and run them until the idle lease expires."""
    # Imported lazily so the module imports cleanly outside the worker image.
    from worker.entrypoint import _run_blocking, _run_streaming

    callback_url = os.environ.get(
        "OPENAIR_WORKER_CALLBACK_URL", "http://host.docker.internal:8000"
    )
    token = os.environ.get("OPENAIR_WORKER_AUTH_TOKEN", "")
    keepalive = int(os.environ.get("OPENAIR_WORKER_KEEPALIVE_SECONDS", "180"))
    poll_timeout = int(os.environ.get("OPENAIR_WORKER_POLL_TIMEOUT_SECONDS", "25"))

    idle_since = time.monotonic()
    logger.info(
        "server loop start task=%s owner=%s keepalive=%ss poll=%ss",
        task_id, owner_id, keepalive, poll_timeout,
    )

    async with httpx.AsyncClient() as client:
        while True:
            turn = await _claim_turn(
                client, callback_url, token, task_id, owner_id, poll_timeout
            )
            if turn is None:
                if time.monotonic() - idle_since >= keepalive:
                    logger.info("idle lease expired (%ss); exiting", keepalive)
                    return
                continue

            idle_since = time.monotonic()
            config = _turn_to_config(turn, task_id=task_id, owner_id=owner_id)
            logger.info(
                "server loop: running turn execution_id=%s streaming=%s",
                config["execution_id"], config["streaming"],
            )
            try:
                if config["streaming"]:
                    await _run_streaming(config, user_skill_names)
                else:
                    await _run_blocking(config, user_skill_names)
            except Exception:
                # The run helpers normally post their own failed result; this is
                # a safety net so the API's awaiting future always resolves.
                logger.exception("server loop: turn crashed; reporting failure")
                try:
                    from worker.callback import post_result

                    post_result(
                        config["execution_id"],
                        reply="",
                        status="failed",
                        error="worker turn crashed",
                        runtime="worker",
                    )
                except Exception:
                    pass
            logger.info("server loop: turn done execution_id=%s", config["execution_id"])
