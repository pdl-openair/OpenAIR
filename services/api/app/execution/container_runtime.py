"""Container-based ExecutionRuntime: launches/reuses a Docker worker per task.

Implements the ``ExecutionRuntime`` protocol by:
1. Serialising the ``ExecutionRequest`` (into env vars for a fresh launch, or
   into a turn payload handed to a warm worker via ``/internal/worker/claim``).
2. Acquiring a warm worker for the task if one is idle (keepalive), else
   launching a fresh container via ``ContainerManager``.
3. Waiting for the worker to call back with results (via ``WorkerCallbackRegistry``).
4. Returning the ``ExecutionResult`` — and, in keepalive mode, parking the
   worker as idle instead of destroying it.

For streaming, the worker posts individual events to the API, which are
bridged to the awaiting ``asyncio.Queue``.

Lifecycle modes
---------------
* **Keepalive on** (``OPENAIR_WORKER_KEEPALIVE=1``, container runtime): workers
  run in long-lived "server mode". After a turn the container is parked idle and
  reused for the next turn of the same task (no cross-task/cross-owner reuse →
  zero cross-tenant leakage). A reaper evicts idle workers past the keepalive
  window and enforces the per-user cap.
* **Keepalive off**: original behaviour — one fresh one-shot container per turn,
  destroyed in ``finally``. Zero regression.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from collections.abc import AsyncIterator
from typing import Any

from app.config import settings
from app.execution.container_manager import get_container_manager, keepalive_container_name
from app.execution.execution_context import get_task_workspace_dir
from app.execution.runtime_boundary import (
    ExecutionRequest,
    ExecutionResult,
    _gate_user_quota,
    require_materialized_skill_dependency,
    require_observable_provider_transport,
)
from app.execution.worker_callback import get_callback_registry
from app.execution.worker_pool import WarmWorker, get_worker_pool

logger = logging.getLogger(__name__)


def _keepalive_active() -> bool:
    """Keepalive is disabled when execution-scoped callback tokens are required.

    A warm process spans multiple execution epochs. Until the claim protocol can
    rotate a callback token atomically per turn, reusing the first turn's token
    would reopen same-task cross-execution callback replay. One-shot containers
    are the fail-closed production contract for per-task-token mode.
    """
    return (
        settings.runtime_type == "container"
        and settings.worker_keepalive
        and not settings.worker_per_task_token
    )


def _prepare_workspace_isolation(task_id: str, task_dir: Any) -> int | None:
    """Phase-2 #3: prepare per-task gid isolation when the flag is on.

    Returns the task gid when isolation is armed, or ``None`` when the flag is off (legacy
    behaviour). FAILS CLOSED (raises) when the flag is on but isolation can't be armed (API
    not root / chown failed): never launch a worker half-isolated — it would run as openair
    and read legacy openair-owned sibling dirs via uid, exactly the leak isolation exists to
    prevent. The raise fails the turn loudly instead of running insecure.
    """
    if not getattr(settings, "worker_workspace_isolation", False):
        return None
    from app.execution.workspace_isolation import prepare_task_workspace

    gid = prepare_task_workspace(task_id, task_dir)
    if gid is None:
        raise RuntimeError(
            f"OPENAIR_WORKER_WORKSPACE_ISOLATION is on but isolation could not be armed "
            f"for task={task_id} (the API must run as root to chown task dirs). Refusing "
            f"to launch the worker fail-closed rather than run half-isolated."
        )
    return gid


def _exec_kwargs_from_request(request: ExecutionRequest) -> dict[str, Any]:
    """Extract the per-turn execution kwargs (same fields the worker expects)."""
    ek: dict[str, Any] = {
        "substage_key": request.substage_key,
        "l2_claude_name": request.l2_claude_name,
        "available_skills": request.available_skills,
        "spec_template": request.spec_template,
        "output_file": request.output_file,
        "input_files": request.input_files,
        "requested_skill_id": request.requested_skill_id,
        "l3_skills_meta": request.l3_skills_meta,
        "existing_l3_plan": request.existing_l3_plan,
    }
    return {k: v for k, v in ek.items() if v is not None}


def _turn_payload(
    request: ExecutionRequest, execution_id: str, streaming: bool
) -> dict[str, Any]:
    """Serialise one turn for hand-off to a warm worker via /worker/claim."""
    return {
        "execution_id": execution_id,
        "message": request.message,
        "mode": request.mode,
        # Deliberately NOT propagating request.session_id: the worker's ~/.claude
        # does not contain a task-level session created elsewhere (or by a
        # different/previous worker), and resuming a non-existent session makes
        # `claude` exit 1 ("No conversation found with session ID"). This mirrors
        # the one-shot env path, which intentionally omits OPENAIR_SESSION_ID so
        # every turn runs as fresh-context (per the GLM / fresh-context design).
        "session_id": None,
        "task": request.task,
        "exec_kwargs": _exec_kwargs_from_request(request),
        "streaming": streaming,
    }


def _serialise_env(
    request: ExecutionRequest,
    execution_id: str = "",
    streaming: bool = False,
    *,
    execution_epoch: str = "",
    server_mode: bool = False,
    task_gid: int | None = None,
) -> dict[str, str]:
    """Build the container environment.

    The first turn is ALWAYS baked into env (the worker has to bootstrap anyway,
    so it reads the first message from env exactly like one-shot — robust, no
    in-memory hand-off needed for the bootstrap turn). ``server_mode=True`` adds
    the server-mode markers so that, after running the env turn, the worker
    long-polls ``/worker/claim`` for subsequent turns of the same task (warm
    reuse). Defaults keep the historical single-arg call ``_serialise_env(request)``
    working (one-shot env).
    """
    env: dict[str, str] = {
        "OPENAIR_TASK_ID": request.task_id,
        "OPENAIR_MESSAGE": request.message,
        "OPENAIR_MODE": request.mode,
        "OPENAIR_TASK_JSON": json.dumps(request.task, ensure_ascii=False),
        "OPENAIR_EXEC_KWARGS_JSON": json.dumps(
            _exec_kwargs_from_request(request), ensure_ascii=False
        ),
        "OPENAIR_CLAUDE_PERMISSION_MODE": settings.claude_permission_mode,
        "OPENAIR_EXECUTION_ID": execution_id,
        "OPENAIR_STREAMING": "1" if streaming else "0",
    }

    if server_mode:
        # After the env turn, the worker long-polls /worker/claim for subsequent
        # turns of this task (warm reuse). Keepalive/poll timeouts drive its
        # self-lease so it exits even if the API reaper is unavailable.
        env["OPENAIR_WORKER_SERVER"] = "1"
        env["OPENAIR_WORKER_KEEPALIVE_SECONDS"] = str(settings.worker_keepalive_seconds)
        env["OPENAIR_WORKER_POLL_TIMEOUT_SECONDS"] = str(settings.worker_poll_timeout_seconds)

    # Pass through Claude Code / Anthropic environment variables
    # Supports both official Anthropic API and third-party providers (e.g. Zhipu GLM)
    _CLAUDE_ENV_PASSTHROUGH = [
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_BASE_URL",
        "ANTHROPIC_MODEL",
        "ANTHROPIC_DEFAULT_HAIKU_MODEL",
        "ANTHROPIC_DEFAULT_SONNET_MODEL",
        "ANTHROPIC_DEFAULT_OPUS_MODEL",
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC",
        "API_TIMEOUT_MS",
    ]
    for var in _CLAUDE_ENV_PASSTHROUGH:
        val = os.environ.get(var, "").strip()
        if val:
            env[var] = val

    # Proxy: rewrite 127.0.0.1/localhost → host.docker.internal for containers
    # Container's localhost is itself, not the host machine
    for proxy_var in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        val = os.environ.get(proxy_var, "").strip()
        if val:
            val = val.replace("127.0.0.1", "host.docker.internal").replace("localhost", "host.docker.internal")
            env[proxy_var] = val
    # NOTE: intentionally do NOT pass request.session_id to the worker.
    # Each worker container is ephemeral (removed after one turn), so Claude's
    # session state in ~/.claude/ does not survive between turns. Resuming a
    # session_id here makes `claude` exit 1 at startup:
    #   "No conversation found with session ID: <id>"
    # The container runtime therefore runs every turn as a fresh-context
    # conversation (per the GLM / fresh-context design). If persistent
    # cross-turn resume is ever needed, mount a shared volume at the worker's
    # ~/.claude and re-enable OPENAIR_SESSION_ID here.
    if request.owner_id:
        env["OPENAIR_OWNER_ID"] = request.owner_id
    # Phase-2 #3: advertise isolation AND convey the gid ONLY when prep actually succeeded
    # (task_gid is not None). The worker entrypoint skips its recursive /workspace chown off
    # the OPENAIR_WORKSPACE_ISOLATION flag, so advertising isolation without a gid leaves the
    # task dir neither chowned to openair:openair (legacy chown skipped) nor to root:<gid>
    # (prep failed): no isolation AND a likely-unwritable workspace → the task fails. On any
    # prep miss (flag off, API not root, chown failed) set NEITHER so the entrypoint runs the
    # full legacy path (chown /workspace → openair:openair writable). task_gid is non-None
    # iff the flag is on AND prepare_task_workspace succeeded, so gating on it is exact.
    if task_gid is not None:
        env["OPENAIR_WORKSPACE_ISOLATION"] = "1"
        env["OPENAIR_WORKER_TASK_GID"] = str(task_gid)

    # Phase-1 user-skill delivery: object-scoped presigned-GET manifest.
    # The MinIO root key is deliberately NOT injected into the worker env — a
    # malicious user skill must not be able to read/write arbitrary tenants'
    # objects. Instead the API (which holds the root key) signs short-lived,
    # single-object GET URLs for THIS owner's own skill files only, and the worker
    # fetches them directly from object storage. No owner-header-spoofable endpoint
    # is exposed, so cross-tenant skill reads are closed at the delivery layer.
    if (
        settings.artifact_storage == "minio"
        and request.owner_id
        and request.user_skill_names
    ):
        try:
            from app.modules.user_skills_service import build_presigned_skills_manifest

            manifest = build_presigned_skills_manifest(
                request.owner_id,
                request.user_skill_names,
                ttl_seconds=settings.artifact_presign_ttl_seconds,
            )
            if manifest:
                env["OPENAIR_USER_SKILLS_MANIFEST"] = json.dumps(
                    manifest, ensure_ascii=False
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "could not build user-skills manifest for owner=%s: %s",
                request.owner_id, exc,
            )

    # Unified mmp-platform MCP server URL. The worker rebuilds ClaudeAgentOptions
    # from env (see claude_code_runner._build_options) and needs this to inject
    # mcp_servers — same as the inline API process. Without it, mmp-* skills run
    # in the worker without their platform tools (login/model_query/etc.).
    if settings.mmp_mcp_url:
        env["OPENAIR_MMP_MCP_URL"] = settings.mmp_mcp_url

    # Callback configuration
    callback_url = getattr(settings, "worker_callback_url", "http://host.docker.internal:8000")
    env["OPENAIR_WORKER_CALLBACK_URL"] = callback_url
    worker_token = getattr(settings, "worker_auth_token", "")
    # Phase-2 #1: when per-task tokens are enabled, inject a task-scoped HMAC token instead
    # of the global shared secret — so a malicious user skill that reads env can only ever
    # steal a token bound to THIS task. Off ⇒ legacy global token (unchanged).
    if getattr(settings, "worker_per_task_token", False) is True:
        if request.task_id and request.owner_id and execution_id and execution_epoch:
            from app.execution.worker_tokens import mint_worker_token

            env["OPENAIR_WORKER_AUTH_TOKEN"] = mint_worker_token(
                request.task_id,
                request.owner_id,
                execution_id,
                execution_epoch,
            )
        else:
            # Fail closed: per-task mode must NEVER fall back to the global shared secret —
            # that is exactly the cross-tenant poisoning vector this mode exists to close.
            # A missing owner_id/task_id here is a caller bug (owner_id must ride on every
            # real turn); inject nothing so the worker fails to auth loudly instead of using
            # the shared secret it was never supposed to have.
            logger.error(
                "worker_per_task_token on but callback identity is incomplete "
                "(task=%s owner=%s execution=%s epoch=%s); refusing to inject global token",
                request.task_id, request.owner_id, execution_id, execution_epoch,
            )
    elif worker_token:
        env["OPENAIR_WORKER_AUTH_TOKEN"] = worker_token

    return env


def _make_execution_id(task_id: str) -> str:
    """Generate a unique execution ID."""
    import uuid
    return f"exec-{task_id[:8]}-{uuid.uuid4().hex[:8]}"


def _make_execution_epoch() -> str:
    """Generate an unguessable fence for one callback execution."""
    import uuid

    return uuid.uuid4().hex


class ContainerRuntime:
    """Per-task containerized execution runtime.

    Launches (or reuses) a Docker container for each ExecutionRequest. The
    container runs Claude Code in isolation and calls back with results.
    """

    async def _acquire_or_launch(
        self,
        request: ExecutionRequest,
        execution_id: str,
        execution_epoch: str,
        streaming: bool,
    ) -> tuple[WarmWorker | None, bool]:
        """Get a worker ready to run this turn.

        Returns ``(warm_worker, one_shot)``:
        * keepalive reused  → ``(ww, False)`` — warm worker already running.
        * keepalive fresh   → ``(ww, False)`` — new server-mode container launched.
        * keepalive off     → ``(None, True)`` — one-shot container launched.
        """
        manager = get_container_manager()
        task_dir = get_task_workspace_dir(request.task_id)
        workspace_bind = str(task_dir.parent)
        owner_id = request.owner_id or ""
        # Phase-2 #3: prepare per-task gid isolation (no-op unless the flag is on). The gid
        # is conveyed to the worker via env (OPENAIR_WORKER_TASK_GID) — NOT Docker group_add,
        # which su-exec discards on privilege drop.
        task_gid = _prepare_workspace_isolation(request.task_id, task_dir)

        if not _keepalive_active():
            env = _serialise_env(
                request,
                execution_id,
                streaming,
                execution_epoch=execution_epoch,
                server_mode=False,
                task_gid=task_gid,
            )
            manager.launch(
                request.task_id, execution_id, env, workspace_bind,
                owner_id=owner_id, keepalive=False,
            )
            return None, True

        pool = get_worker_pool()

        # 1. Try to reuse an idle warm worker for this task.
        ww = pool.acquire(request.task_id, owner_id)
        if ww is not None:
            pool.offer_turn(ww, _turn_payload(request, execution_id, streaming))
            # The worker is already up — it should claim within milliseconds.
            # If it doesn't within the grace window, it's dead: evict + relaunch.
            if await pool.wait_for_claim(ww, settings.worker_claim_grace_seconds):
                return ww, False
            logger.warning(
                "warm worker did not claim within grace; evicting task=%s container=%s",
                request.task_id, ww.container_id[:12],
            )
            manager.stop_and_remove_container(ww.container_id)
            pool.evict(ww)

        # 2. Fresh keepalive launch (server mode). The per-turn payload is
        #    offered immediately; the worker claims it once booted. We do NOT
        #    wait-for-claim here (cold boot may exceed the grace window); the
        #    normal turn timeout covers a worker that never comes up.
        #
        # Guard against double-launching for the same task, and clear any stale
        # same-named container so Docker doesn't 409 on the (task-stable) name.
        existing = pool.get(request.task_id)
        if existing is not None:
            if not existing.is_idle:
                # A turn is already in flight on this task. Turns are sequential
                # per task; reaching here means true concurrency — refuse rather
                # than race the in-flight worker / leak a second container.
                raise RuntimeError(
                    f"task {request.task_id} already has an in-flight worker; "
                    "concurrent turns on a single task are not supported"
                )
            # Idle yet not reused by acquire() ⇒ owner mismatch (defensive; with
            # owner_id carried through it shouldn't happen). Evict the parked
            # worker so the fresh launch (same task-based name) doesn't collide.
            logger.warning(
                "evicting parked worker with mismatched owner task=%s", request.task_id
            )
            manager.stop_and_remove_container(existing.container_id)
            pool.evict(existing)
        # No tracked worker remains. Any Docker container still holding the
        # task's name is an untracked orphan (e.g. leftover from a previous API
        # process) — clear it before creating the new one.
        manager.remove_container_by_name(keepalive_container_name(request.task_id))

        env = _serialise_env(
            request,
            execution_id,
            streaming,
            execution_epoch=execution_epoch,
            server_mode=True,
            task_gid=task_gid,
        )
        cid = manager.launch(
            request.task_id, execution_id, env, workspace_bind,
            owner_id=owner_id, keepalive=True,
        )
        ww = pool.register(request.task_id, owner_id, cid)
        # First turn is baked into env (worker runs it on boot, like one-shot) —
        # no in-memory offer_turn needed for the bootstrap turn. The worker then
        # long-polls /worker/claim; subsequent turns reuse it via offer_turn.
        return ww, False

    @staticmethod
    def _handle_failure(ww: WarmWorker | None, one_shot: bool, execution_id: str) -> None:
        """Tear down the worker on timeout/exception: evict (keepalive) or cleanup (one-shot)."""
        manager = get_container_manager()
        if ww is not None:
            manager.stop_and_remove_container(ww.container_id)
            get_worker_pool().evict(ww)
        elif one_shot:
            manager.stop_and_cleanup(execution_id)

    async def submit(self, request: ExecutionRequest) -> ExecutionResult:
        """Submit a non-streaming execution request to a worker container."""
        _gate_user_quota(request.owner_id)
        require_materialized_skill_dependency(request)
        require_observable_provider_transport(request)
        execution_id = _make_execution_id(request.task_id)
        execution_epoch = _make_execution_epoch()
        authority_expires_at = datetime.now(timezone.utc) + timedelta(
            seconds=max(1, settings.worker_token_ttl_seconds)
        )
        registry = get_callback_registry()
        manager = get_container_manager()
        keepalive = _keepalive_active()
        pool = get_worker_pool() if keepalive else None

        future = registry.register(
            execution_id,
            task_id=request.task_id,
            owner_id=request.owner_id or "",
            execution_epoch=execution_epoch,
            expires_at=authority_expires_at,
        )
        timeout = getattr(settings, "worker_timeout_seconds", 600)

        ww: WarmWorker | None = None
        one_shot = False

        try:
            ww, one_shot = await self._acquire_or_launch(
                request, execution_id, execution_epoch, streaming=False
            )
            logger.info(
                "turn dispatched execution_id=%s task_id=%s keepalive=%s one_shot=%s",
                execution_id, request.task_id, keepalive, one_shot,
            )

            # Wait for callback or timeout
            try:
                result_dict = await asyncio.wait_for(future, timeout=timeout)
                usage = result_dict.get("usage")
                audit: dict[str, Any] = {"usage": usage} if usage else {}
                return ExecutionResult(
                    reply=result_dict.get("reply", ""),
                    session_id=result_dict.get("session_id"),
                    status=result_dict.get("status", "completed"),
                    error=result_dict.get("error"),
                    runtime=result_dict.get("runtime", "claude_code"),
                    audit=audit,
                )
            except asyncio.TimeoutError:
                logger.warning("container execution timed out: execution_id=%s", execution_id)
                registry.cancel(execution_id)
                self._handle_failure(ww, one_shot, execution_id)
                ww = None
                one_shot = False
                return ExecutionResult(
                    reply="（容器执行超时）",
                    status="timeout",
                    error="Container execution timed out",
                    runtime="container",
                )

        except Exception as exc:
            logger.exception("container execution failed: execution_id=%s", execution_id)
            registry.cleanup(execution_id)
            self._handle_failure(ww, one_shot, execution_id)
            ww = None
            one_shot = False
            return ExecutionResult(
                reply="",
                status="failed",
                error=f"{type(exc).__name__}: {exc}",
                runtime="container",
            )
        finally:
            registry.cleanup(execution_id)
            # Success path: park (keepalive) or remove (one-shot).
            if ww is not None and pool is not None:
                pool.release(ww)
            elif one_shot:
                try:
                    manager.cleanup(execution_id)
                except Exception:
                    pass

    async def submit_stream(self, request: ExecutionRequest) -> AsyncIterator[dict[str, Any]]:
        """Submit a streaming execution request to a worker container."""
        _gate_user_quota(request.owner_id)
        require_materialized_skill_dependency(request)
        require_observable_provider_transport(request)
        execution_id = _make_execution_id(request.task_id)
        execution_epoch = _make_execution_epoch()
        authority_expires_at = datetime.now(timezone.utc) + timedelta(
            seconds=max(1, settings.worker_token_ttl_seconds)
        )
        registry = get_callback_registry()
        manager = get_container_manager()
        keepalive = _keepalive_active()
        pool = get_worker_pool() if keepalive else None

        # Register both streaming Queue and completion Future
        queue = registry.register_stream(
            execution_id,
            task_id=request.task_id,
            owner_id=request.owner_id or "",
            execution_epoch=execution_epoch,
            expires_at=authority_expires_at,
        )
        future = registry.register(
            execution_id,
            task_id=request.task_id,
            owner_id=request.owner_id or "",
            execution_epoch=execution_epoch,
            expires_at=authority_expires_at,
        )

        timeout = getattr(settings, "worker_timeout_seconds", 600)

        ww: WarmWorker | None = None
        one_shot = False
        dispatched = False

        try:
            ww, one_shot = await self._acquire_or_launch(
                request, execution_id, execution_epoch, streaming=True
            )
            dispatched = True
            logger.info(
                "streaming turn dispatched execution_id=%s task_id=%s keepalive=%s",
                execution_id, request.task_id, keepalive,
            )

            # Yield events from the queue
            timed_out = False
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=timeout)
                except asyncio.TimeoutError:
                    timed_out = True
                    break

                if event is None:
                    # Sentinel: worker finished, no more events
                    break
                yield event

            if timed_out:
                yield {"type": "assistant_delta", "text": "（容器执行超时）"}
                yield {"type": "done", "sessionId": None, "error": "timeout"}
                registry.cancel(execution_id)
                self._handle_failure(ww, one_shot, execution_id)
                ww = None
                one_shot = False
                return

            # Wait for the final result callback (should already be resolved).
            # If the worker finished with an error and produced no streamed text,
            # surface the real cause — otherwise the user sees the misleading
            # "Claude Code 未返回文本" instead of the actual failure reason.
            final_reply = ""
            final_error: str | None = None
            final_session_id: str | None = None
            try:
                result_dict = await asyncio.wait_for(future, timeout=10)
                final_reply = (result_dict.get("reply") or "").strip()
                final_error = result_dict.get("error")
                final_session_id = result_dict.get("session_id")
            except asyncio.TimeoutError:
                final_error = "callback timeout"

            if final_error and not final_reply:
                yield {
                    "type": "assistant_delta",
                    "text": f"（Worker 执行失败：{final_error}）",
                }
                yield {"type": "done", "sessionId": final_session_id, "error": final_error}

        except Exception as exc:
            logger.exception("streaming container failed: execution_id=%s", execution_id)
            yield {"type": "assistant_delta", "text": f"容器执行失败：{exc}"}
            yield {"type": "done", "sessionId": None, "error": str(exc)}
            if dispatched:
                self._handle_failure(ww, one_shot, execution_id)
                ww = None
                one_shot = False
        finally:
            registry.cleanup(execution_id)
            # Success path: park (keepalive) or remove (one-shot).
            if ww is not None and pool is not None:
                pool.release(ww)
            elif one_shot:
                try:
                    manager.cleanup(execution_id)
                except Exception:
                    pass
