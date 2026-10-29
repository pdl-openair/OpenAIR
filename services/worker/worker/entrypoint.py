"""Worker entrypoint — runs inside the Docker container.

Lifecycle:
1. Read configuration from environment variables
2. Download user-private skills from MinIO (if configured)
3. Execute Claude Code (blocking or streaming)
4. Post results back to the API server via HTTP callback
5. Exit
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from typing import Any

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("worker")

# ---------------------------------------------------------------------------
# Environment variable schema
# ---------------------------------------------------------------------------
# OPENAIR_TASK_ID          — task being executed
# OPENAIR_EXECUTION_ID     — unique ID for this container run
# OPENAIR_MESSAGE          — user message / prompt
# OPENAIR_MODE             — "creation" | "execution"
# OPENAIR_SESSION_ID       — optional session resume ID
# OPENAIR_TASK_JSON        — serialized task dict (JSON string)
# OPENAIR_EXEC_KWARGS_JSON — serialized execution kwargs (JSON string)
# OPENAIR_STREAMING        — "1" for streaming mode, "0" (default) for blocking
# OPENAIR_CLAUDE_PERMISSION_MODE — Claude permission mode
# OPENAIR_OWNER_ID         — user ID for downloading user skills
# OPENAIR_USER_SKILL_NAMES — JSON array of user skill names to download


def _load_config() -> dict[str, Any]:
    """Read and validate all configuration from environment variables."""
    task_id = os.environ.get("OPENAIR_TASK_ID", "")
    execution_id = os.environ.get("OPENAIR_EXECUTION_ID", "")
    message = os.environ.get("OPENAIR_MESSAGE", "")
    mode = os.environ.get("OPENAIR_MODE", "execution")
    session_id = os.environ.get("OPENAIR_SESSION_ID") or None
    task_json = os.environ.get("OPENAIR_TASK_JSON", "{}")
    exec_kwargs_json = os.environ.get("OPENAIR_EXEC_KWARGS_JSON", "{}")
    streaming = os.environ.get("OPENAIR_STREAMING", "0").strip() in ("1", "true", "yes")
    permission_mode = os.environ.get("OPENAIR_CLAUDE_PERMISSION_MODE", "bypassPermissions")
    owner_id = os.environ.get("OPENAIR_OWNER_ID", "")
    user_skill_names_json = os.environ.get("OPENAIR_USER_SKILL_NAMES", "[]")

    if not task_id:
        raise ValueError("OPENAIR_TASK_ID is required")
    if not execution_id:
        raise ValueError("OPENAIR_EXECUTION_ID is required")
    if not message:
        raise ValueError("OPENAIR_MESSAGE is required")

    try:
        task = json.loads(task_json)
    except json.JSONDecodeError as exc:
        raise ValueError(f"OPENAIR_TASK_JSON is not valid JSON: {exc}") from exc

    try:
        exec_kwargs = json.loads(exec_kwargs_json)
    except json.JSONDecodeError as exc:
        raise ValueError(f"OPENAIR_EXEC_KWARGS_JSON is not valid JSON: {exc}") from exc

    try:
        user_skill_names = json.loads(user_skill_names_json)
    except json.JSONDecodeError:
        user_skill_names = []

    return {
        "task_id": task_id,
        "execution_id": execution_id,
        "message": message,
        "mode": mode,
        "session_id": session_id,
        "task": task,
        "exec_kwargs": exec_kwargs,
        "streaming": streaming,
        "permission_mode": permission_mode,
        "owner_id": owner_id,
        "user_skill_names": user_skill_names,
    }


def _prepare_workspace(task_id: str) -> None:
    """Ensure the task workspace directory exists."""
    workspace_root = os.environ.get("OPENAIR_WORKSPACE_ROOT", "/workspace")
    ws_dir = os.path.join(workspace_root, task_id)
    os.makedirs(ws_dir, exist_ok=True)
    logger.info("workspace ready: %s", ws_dir)


def _download_user_skills(owner_id: str, skill_names: list[str]) -> list[str]:
    """Download user-private skills from MinIO."""
    from worker.user_skills import download_user_skills

    if not owner_id:
        logger.info("no owner_id, skipping user skills download")
        return []

    logger.info("downloading user skills for owner=%s names=%s", owner_id, skill_names)
    downloaded = download_user_skills(owner_id, skill_names if skill_names else None)
    logger.info("downloaded %d user skills: %s", len(downloaded), downloaded)
    return downloaded


def _parse_user_skill_names() -> list[str]:
    """Parse OPENAIR_USER_SKILL_NAMES (JSON array) into a list (server mode)."""
    raw = os.environ.get("OPENAIR_USER_SKILL_NAMES", "[]")
    try:
        names = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return [n for n in names if isinstance(n, str)]


def _is_server_mode() -> bool:
    return os.environ.get("OPENAIR_WORKER_SERVER", "").strip().lower() in ("1", "true", "yes")


async def _run_blocking(config: dict[str, Any], user_skill_names: list[str]) -> None:
    """Execute Claude Code in blocking mode, then post the result."""
    from worker.callback import post_result
    from worker.runner import run_chat

    logger.info("running blocking execution for task=%s", config["task_id"])

    try:
        reply, session_id, usage = await run_chat(
            config["task"],
            config["message"],
            mode=config["mode"],
            resume_session_id=config["session_id"],
            permission_mode=config["permission_mode"],
            user_skill_names=user_skill_names,
            **config["exec_kwargs"],
        )
        post_result(
            config["execution_id"],
            reply=reply,
            session_id=session_id,
            status="completed",
            runtime="claude_code",
            usage=usage,
        )
        logger.info("blocking execution completed, reply_len=%d", len(reply))

    except Exception as exc:
        logger.exception("blocking execution failed")
        post_result(
            config["execution_id"],
            reply="",
            status="failed",
            error=f"{type(exc).__name__}: {exc}",
            runtime="claude_code",
        )


async def _run_streaming(config: dict[str, Any], user_skill_names: list[str]) -> None:
    """Execute Claude Code in streaming mode, posting events as they arrive."""
    from worker.callback import post_result, post_stream_event
    from worker.runner import run_chat_stream

    logger.info("running streaming execution for task=%s", config["task_id"])

    session_id: str | None = config["session_id"]
    error: str | None = None
    usage: dict[str, Any] | None = None
    reply_parts: list[str] = []

    try:
        async for event in run_chat_stream(
            config["task"],
            config["message"],
            mode=config["mode"],
            resume_session_id=config["session_id"],
            permission_mode=config["permission_mode"],
            user_skill_names=user_skill_names,
            **config["exec_kwargs"],
        ):
            event_type = event.get("type")
            if event_type == "assistant_delta":
                text = event.get("text") or ""
                if text:
                    reply_parts.append(text)
            elif event_type == "done":
                session_id = event.get("sessionId") or session_id
                usage = event.get("usage") or usage
                if event.get("error"):
                    error = event.get("error")

            # Post each event to the API server
            post_stream_event(config["execution_id"], event)

        reply = "".join(reply_parts).strip()
        post_result(
            config["execution_id"],
            reply=reply,
            session_id=session_id,
            status="failed" if error else "completed",
            error=error,
            runtime="claude_code",
            usage=usage,
        )
        logger.info("streaming execution completed, reply_len=%d", len(reply))

    except Exception as exc:
        logger.exception("streaming execution failed")
        post_result(
            config["execution_id"],
            reply="".join(reply_parts).strip(),
            status="failed",
            error=f"{type(exc).__name__}: {exc}",
            runtime="claude_code",
        )


async def main() -> None:
    """Worker main entry point."""
    logger.info("=== OpenAIR Worker Starting ===")

    # --- Server mode (keepalive): bound to one task for its whole life. ---
    # The FIRST turn is baked into env (the bootstrap turn — worker has to boot
    # anyway, so it reads the first message from env exactly like one-shot). After
    # that it long-polls /internal/worker/claim for subsequent turns of the same
    # task (warm reuse). Putting the first turn in env avoids an in-memory
    # hand-off race for the bootstrap turn.
    if _is_server_mode():
        task_id = os.environ.get("OPENAIR_TASK_ID", "")
        owner_id = os.environ.get("OPENAIR_OWNER_ID", "")
        if not task_id:
            logger.critical("server mode requires OPENAIR_TASK_ID")
            sys.exit(1)

        _prepare_workspace(task_id)
        user_skill_names = _download_user_skills(owner_id, _parse_user_skill_names())
        logger.info("server mode: task=%s owner=%s skills=%d", task_id, owner_id, len(user_skill_names))

        # First (bootstrap) turn from env.
        config = _load_config()
        if config["streaming"]:
            await _run_streaming(config, user_skill_names)
        else:
            await _run_blocking(config, user_skill_names)
        logger.info("server mode: first turn done (env), entering claim loop")

        # Subsequent turns via /worker/claim (warm reuse).
        from worker.server import run_server_loop
        await run_server_loop(task_id, owner_id, user_skill_names)
        logger.info("=== OpenAIR Worker Finished (server) ===")
        return

    # --- One-shot mode (original behaviour): read everything from env, run once. ---
    try:
        config = _load_config()
    except ValueError as exc:
        logger.critical("configuration error: %s", exc)
        # Try to report the error back if we have enough info
        exec_id = os.environ.get("OPENAIR_EXECUTION_ID", "")
        if exec_id:
            from worker.callback import post_result
            post_result(exec_id, reply="", status="failed", error=str(exc), runtime="worker")
        sys.exit(1)

    logger.info(
        "task_id=%s execution_id=%s mode=%s streaming=%s",
        config["task_id"],
        config["execution_id"],
        config["mode"],
        config["streaming"],
    )

    # Prepare workspace
    _prepare_workspace(config["task_id"])

    # Download user skills
    user_skill_names = _download_user_skills(
        config["owner_id"],
        config["user_skill_names"],
    )

    # Execute
    if config["streaming"]:
        await _run_streaming(config, user_skill_names)
    else:
        await _run_blocking(config, user_skill_names)

    logger.info("=== OpenAIR Worker Finished ===")


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    asyncio.run(main())
