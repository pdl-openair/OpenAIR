"""Task SSE event encoding and stream generators (extracted from routes/tasks).

This module owns the server-sent-events machinery behind the task routes:
SSE framing/encoding helpers, the revision-consistent durable state reader,
and the durable task event generator (snapshot -> lifecycle replay ->
poll/heartbeat with resume fencing). Route-level wiring (request parsing,
dependency injection) stays in ``app.routes.tasks`` and passes the repository
classes plus timing constants in at call time, so tests can keep monkey
patching those names on the routes module.

Behavior is preserved byte-for-byte from the pre-extraction routes: SSE
framing, event ordering, heartbeat cadence, resume/fencing rules and error
paths are unchanged.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Mapping
from datetime import UTC, datetime

from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from app.auth import RequestIdentity, ensure_task_in_scope
from app.config import settings
from app.modules.collaboration_lifecycle import (
    build_collaboration_lifecycle_events,
    lifecycle_sequence,
    project_research_task_lifecycle,
)
from app.modules.collaboration_repository import CollaborationRepository
from app.modules.durable_execution import DurableExecutionError
from app.modules.research_orchestration_repository import ResearchOrchestrationRepository
from app.modules.research_progress_projection import recompute_progress_projection
from app.modules.task_events import subscribe_task_events, unsubscribe_task_events
from app.modules.task_message_repository import TaskMessageRepository
from app.modules.task_repository import TaskRepository
from app.modules.task_store import store

_log = logging.getLogger(__name__)


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _task_event_resume_sequence(event_id: str, *, task_id: str, run_id: str) -> int | None:
    if f":{task_id}:run:{run_id}:" not in event_id:
        return None
    if not event_id.startswith(("heartbeat:", "task-revision:")):
        return None
    for part in reversed(event_id.split(":")):
        if part.startswith("s") and part[1:].lstrip("-").isdigit():
            return int(part[1:])
    return None


def _durable_event_scope(
    *, task_id: str, run_id: str | None, identity: RequestIdentity,
) -> dict[str, str | None]:
    return {
        "taskId": task_id,
        "runId": run_id,
        "ownerId": identity["ownerId"],
        "workspaceId": identity["workspaceId"],
    }


def encode_task_sse_event(event: Mapping[str, object]) -> str:
    """Encode one task event using standard SSE framing."""
    event_id = str(event.get("id") or "")
    event_type = str(event.get("type") or "message")
    data = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
    return f"id: {event_id}\nevent: {event_type}\ndata: {data}\n\n"


def encode_research_sse_event(event: Mapping[str, object], *, retry_ms: int = 3000) -> str:
    """Encode one user-safe research event with explicit reconnect guidance."""
    event_id = str(event.get("id") or "")
    event_type = str(event.get("type") or "message")
    data = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
    id_line = f"id: {event_id}\n" if event_id else ""
    return f"{id_line}event: {event_type}\nretry: {retry_ms}\ndata: {data}\n\n"


def read_durable_task_state(
    task_id: str,
    identity: RequestIdentity,
    *,
    task_repository: TaskRepository | None,
    collaboration_repository: CollaborationRepository | None,
    orchestration_repository: ResearchOrchestrationRepository | None,
) -> tuple[dict, dict]:
    """Read one revision-consistent authoritative state for stream projection.

    Callers own repository construction; repositories must be provided
    whenever the durable (postgres orchestration) backend is active.
    """
    if (
        task_repository is None
        or collaboration_repository is None
        or orchestration_repository is None
    ):
        raise HTTPException(status_code=500, detail="durable stream repositories unavailable")
    current_task: dict = {}
    collaboration: dict = {}
    for _attempt in range(3):
        current_task = ensure_task_in_scope(task_repository.get_task(task_id), identity)
        if (
            isinstance(current_task.get("researchDesignVersions"), list)
            and isinstance(current_task.get("registrySnapshotRef"), dict)
            and isinstance(current_task.get("executionPolicyVersion"), str)
        ):
            aggregate = orchestration_repository.load(
                task_id=task_id,
                owner_id=identity["ownerId"],
                workspace_id=identity["workspaceId"],
            )
            collaboration = project_research_task_lifecycle(aggregate)
        else:
            collaboration = collaboration_repository.list_projection(
                task_id=task_id,
                owner_id=identity["ownerId"],
                workspace_id=identity["workspaceId"],
            )
        if collaboration.get("revision") == current_task.get("revision"):
            return current_task, collaboration
    # Revision drift persisted across retries — a write is in flight. The
    # collaboration projection is a derived read model (already recomputed
    # each attempt); serve best-available and let SSE/convergence correct,
    # rather than hard-failing the read (a 409 here blanks the workbench UI).
    _log.warning(
        "durable collaboration revision drift for task %s (task=%s collaboration=%s); "
        "serving best-available",
        task_id,
        current_task.get("revision"),
        collaboration.get("revision"),
    )
    return current_task, collaboration


def read_task_research_stream_state(task_id: str, identity: RequestIdentity) -> tuple[dict, dict]:
    """Read one revision-consistent authoritative state for stream projection."""
    task, collaboration = read_durable_task_state(
        task_id,
        identity,
        task_repository=TaskRepository(),
        collaboration_repository=CollaborationRepository(),
        orchestration_repository=ResearchOrchestrationRepository(),
    )
    from app.modules.research_planning_history import load_planning_thought_history
    from app.modules.research_start_repository import ResearchStartRepository

    with ResearchStartRepository()._connect() as conn:
        with conn.cursor() as cur:
            collaboration["planningThoughtHistory"] = load_planning_thought_history(cur, task_id)
    return task, collaboration


def open_task_event_stream(  # noqa: C901, PLR0915 (moved verbatim from routes/tasks.py)
    *,
    task_id: str,
    identity: RequestIdentity,
    after_event_id: str | None,
    last_event_id: str | None,
    task_repository_cls: type[TaskRepository],
    collaboration_repository_cls: type[CollaborationRepository],
    orchestration_repository_cls: type[ResearchOrchestrationRepository],
    task_message_repository_cls: type[TaskMessageRepository],
    heartbeat_seconds: float,
    refresh_seconds: float,
) -> StreamingResponse:
    """Open the durable/legacy task event SSE stream for one task.

    Repository classes and timing constants are injected by the route so the
    routes module stays the single monkey-patch surface for tests.
    """
    durable_backend = bool(
        settings.research_orchestration_v1 and settings.persistence_backend == "postgres"
    )
    task_repository = task_repository_cls() if durable_backend else None
    collaboration_repository = collaboration_repository_cls() if durable_backend else None
    orchestration_repository = orchestration_repository_cls() if durable_backend else None

    def read_durable_state() -> tuple[dict, dict]:
        return read_durable_task_state(
            task_id,
            identity,
            task_repository=task_repository,
            collaboration_repository=collaboration_repository,
            orchestration_repository=orchestration_repository,
        )

    if durable_backend:
        try:
            task, collaboration_projection = read_durable_state()
        except DurableExecutionError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    else:
        task = ensure_task_in_scope(store.get_task(task_id), identity)
        collaboration_projection = {}
    # PostgreSQL can contain compatibility tasks created before (or outside)
    # research orchestration. Only tasks with a durable projection use the
    # projection SSE contract; a malformed non-null projection still fails
    # closed in durable_payload below.
    orchestration_projection = durable_backend and task.get("progressProjection") is not None
    subscription = subscribe_task_events(task_id)

    def durable_payload(current_task: dict, run_id: str | None) -> dict:
        projection = current_task.get("progressProjection")
        revision = current_task["revision"]
        if not isinstance(projection, dict):
            raise HTTPException(status_code=409, detail="durable_progress_projection_missing")
        # Several write paths (execution-policy change, controller-command) bump
        # task.revision without recomputing the progress projection. Recompute it
        # from the canonical task via the pure reducer so content + stamp stay in
        # sync; if recomputation itself fails, fall back to re-stamping the cached
        # snapshot. Either way we never fail the SSE stream mid-flight — a
        # mid-stream 409 blanks the workbench UI. (Genuine progress writes go
        # through project_task_progress, which keeps content and stamp in sync, so
        # a stale stamp reliably means unchanged content.)
        if projection.get("revision") != revision:
            recomputed = recompute_progress_projection(current_task, revision)
            projection = (
                recomputed if recomputed is not None else {**projection, "revision": revision}
            )
        return {"revision": revision, "runId": run_id, "projection": projection}

    async def event_iter():  # noqa: C901, PLR0912, PLR0915 (moved verbatim from routes/tasks.py)
        emitted_ids: set[str] = set()
        resume_after = after_event_id or last_event_id
        cursor_applied = False
        last_sequence = -1
        current_revision = int(task.get("revision") or 0)
        current_run_id = str(collaboration_projection.get("runId") or "") or None
        seen_run_ids = {current_run_id} if current_run_id else set()
        if settings.persistence_backend == "postgres":
            known_max_seq = task_message_repository_cls().latest_seq(
                task_id=task_id, owner_id=identity["ownerId"], workspace_id=identity["workspaceId"],
            ) or 0
            known_message_ids: set[str] = set()
        else:
            known_max_seq = 0
            known_message_ids = {
                str(message.get("id"))
                for message in task.get("agentMessages") or []
                if isinstance(message, dict) and str(message.get("id") or "").strip()
            }
        last_heartbeat_at = asyncio.get_running_loop().time()

        def cursor_matches_run(cursor: str | None, run_id: str | None) -> bool:
            if not cursor or not run_id:
                return False
            return (
                cursor.startswith(f"lifecycle:{task_id}:run:{run_id}:")
                or cursor.startswith(f"snapshot:{task_id}:run:{run_id}:")
                or cursor.startswith(f"task-revision:{task_id}:run:{run_id}:")
                or cursor.startswith(f"heartbeat:{task_id}:run:{run_id}:")
            )

        def with_scope(event: dict, run_id: str | None) -> dict:
            return {
                **event,
                "runId": run_id,
                "scope": _durable_event_scope(task_id=task_id, run_id=run_id, identity=identity),
            }

        def snapshot_event(current_task: dict, run_id: str | None) -> dict:
            revision = int(current_task.get("revision") or 0)
            run_part = run_id or "none"
            return with_scope(
                {
                    "id": f"snapshot:{task_id}:run:{run_part}:r{revision}",
                    "type": "snapshot",
                    "taskId": task_id,
                    "revision": revision if durable_backend else None,
                    "sequence": -1 if durable_backend else None,
                    "updatedAt": current_task.get("updatedAt"),
                    "emittedAt": utc_now(),
                    "payload": (
                        durable_payload(current_task, run_id)
                        if orchestration_projection
                        else {"reason": "stream_opened"}
                    ),
                },
                run_id,
            )

        def prepare_lifecycle_events(projection: dict, current_task: dict) -> list[dict]:
            nonlocal cursor_applied, last_sequence
            events = build_collaboration_lifecycle_events(projection)
            run_id = str(projection.get("runId") or "") or None
            if resume_after and not cursor_applied and cursor_matches_run(resume_after, run_id):
                cursor_index = next(
                    (index for index, item in enumerate(events) if item["id"] == resume_after),
                    -1,
                )
                if cursor_index >= 0:
                    prior = events[: cursor_index + 1]
                    emitted_ids.update(item["id"] for item in prior)
                    last_sequence = max((int(item["sequence"]) for item in prior), default=-1)
                else:
                    resume_sequence = _task_event_resume_sequence(
                        resume_after,
                        task_id=task_id,
                        run_id=run_id or "",
                    )
                    if resume_sequence is not None:
                        prior = [
                            item for item in events if int(item["sequence"]) <= resume_sequence
                        ]
                        emitted_ids.update(item["id"] for item in prior)
                        last_sequence = resume_sequence
                cursor_applied = True
            prepared: list[dict] = []
            for event in events:
                sequence = int(event["sequence"])
                if event["id"] in emitted_ids or sequence < last_sequence:
                    continue
                prepared.append(
                    with_scope({
                        **event,
                        "updatedAt": current_task.get("updatedAt"),
                        "emittedAt": utc_now(),
                    }, run_id)
                )
                emitted_ids.add(event["id"])
                last_sequence = sequence
            return prepared

        try:
            if not cursor_matches_run(resume_after, current_run_id):
                cursor_applied = True
                yield encode_task_sse_event(snapshot_event(task, current_run_id))
            if durable_backend:
                for lifecycle_event in prepare_lifecycle_events(collaboration_projection, task):
                    yield encode_task_sse_event(lifecycle_event)
                    await asyncio.sleep(0)
            while True:
                try:
                    event = await asyncio.wait_for(
                        subscription.queue.get(),
                        timeout=(
                            refresh_seconds if durable_backend else heartbeat_seconds
                        ),
                    )
                    if not durable_backend:
                        yield encode_task_sse_event(with_scope(event, None))
                        continue
                except TimeoutError:
                    event = None

                if durable_backend:
                    try:
                        refreshed, refreshed_collaboration = read_durable_state()
                    except DurableExecutionError:
                        _log.exception(
                            "durable collaboration SSE refresh failed for task %s", task_id
                        )
                        return
                    refreshed_revision = int(refreshed.get("revision") or 0)
                    refreshed_run_id = str(refreshed_collaboration.get("runId") or "") or None
                    if refreshed_revision < current_revision:
                        _log.warning(
                            "ignored stale task SSE refresh for %s: revision %s < %s",
                            task_id, refreshed_revision, current_revision,
                        )
                        continue
                    if refreshed_run_id != current_run_id and refreshed_run_id in seen_run_ids:
                        _log.warning(
                            "ignored previously fenced task SSE run for %s: %s",
                            task_id, refreshed_run_id,
                        )
                        continue
                    if refreshed_run_id != current_run_id:
                        current_run_id = refreshed_run_id
                        if current_run_id:
                            seen_run_ids.add(current_run_id)
                        current_revision = refreshed_revision
                        emitted_ids.clear()
                        last_sequence = -1
                        cursor_applied = True
                        yield encode_task_sse_event(snapshot_event(refreshed, current_run_id))
                    lifecycle_events = prepare_lifecycle_events(refreshed_collaboration, refreshed)
                    for lifecycle_event in lifecycle_events:
                        yield encode_task_sse_event(lifecycle_event)
                        await asyncio.sleep(0)
                    if refreshed_revision != current_revision:
                        if settings.persistence_backend == "postgres":
                            new_messages = task_message_repository_cls().messages_after(
                                task_id=task_id,
                                owner_id=identity["ownerId"],
                                workspace_id=identity["workspaceId"],
                                after_seq=known_max_seq,
                            )
                            if new_messages:
                                known_max_seq = max(
                                    known_max_seq, max(m["seq"] for m in new_messages)
                                )
                            new_message_payloads = [
                                {"messageId": str(m.get("id")), "messageSeq": m["seq"]}
                                for m in new_messages
                            ]
                        else:
                            new_message_ids = [
                                str(message.get("id"))
                                for message in refreshed.get("agentMessages") or []
                                if isinstance(message, dict)
                                and str(message.get("id") or "").strip()
                                and str(message["id"]) not in known_message_ids
                            ]
                            known_message_ids.update(new_message_ids)
                            new_message_payloads = [
                                {"messageId": mid, "messageSeq": None}
                                for mid in new_message_ids
                            ]
                        current_revision = refreshed_revision
                        task_sequence = max(
                            last_sequence, lifecycle_sequence(refreshed.get("updatedAt"))
                        )
                        last_sequence = task_sequence
                        for payload_entry in new_message_payloads:
                            yield encode_task_sse_event(
                                with_scope({
                                    "id": (
                                        f"agent-message:{task_id}:run:{current_run_id or 'none'}"
                                        f":r{current_revision}:{payload_entry['messageId']}"
                                    ),
                                    "type": "agent_message_appended",
                                    "taskId": task_id,
                                    "revision": current_revision,
                                    "sequence": task_sequence,
                                    "updatedAt": refreshed.get("updatedAt"),
                                    "emittedAt": utc_now(),
                                    "payload": payload_entry,
                                }, current_run_id)
                            )
                        yield encode_task_sse_event(
                            with_scope({
                                "id": (
                                    f"task-revision:{task_id}:run:{current_run_id or 'none'}"
                                    f":r{current_revision}:s{task_sequence}"
                                ),
                                "type": "task_updated",
                                "taskId": task_id,
                                "revision": current_revision,
                                "sequence": task_sequence,
                                "updatedAt": refreshed.get("updatedAt"),
                                "emittedAt": utc_now(),
                                "payload": (
                                    durable_payload(refreshed, current_run_id)
                                    if refreshed.get("progressProjection") is not None
                                    else {
                                        "revision": current_revision,
                                        "runId": current_run_id,
                                        "reason": "durable_facts_changed",
                                    }
                                ),
                            }, current_run_id)
                        )

                if event is None:
                    now = asyncio.get_running_loop().time()
                    if now - last_heartbeat_at < heartbeat_seconds:
                        continue
                    last_heartbeat_at = now
                    heartbeat_at = utc_now()
                    heartbeat_bucket = int(
                        datetime.now(UTC).timestamp() / heartbeat_seconds
                    )
                    heartbeat_id = (
                        f"heartbeat:{task_id}:run:{current_run_id or 'none'}"
                        f":r{current_revision}:s{last_sequence}:b{heartbeat_bucket}"
                    )
                    if heartbeat_id == resume_after:
                        continue
                    yield encode_task_sse_event(
                        with_scope({
                            "id": heartbeat_id,
                            "type": "heartbeat",
                            "taskId": task_id,
                            "revision": current_revision if durable_backend else None,
                            "sequence": last_sequence if durable_backend else None,
                            "updatedAt": None,
                            "emittedAt": heartbeat_at,
                            "payload": (
                                {
                                    "revision": current_revision,
                                    "lastSequence": last_sequence,
                                    "transient": True,
                                }
                                if durable_backend
                                else {}
                            ),
                        }, current_run_id)
                    )
        except asyncio.CancelledError:
            raise
        except HTTPException as exc:
            # Safety net: HTTPException after SSE headers are sent becomes an ASGI
            # RuntimeError. Close the stream quietly; the client will reconnect.
            _log.warning(
                "task events stream closed after response started taskId=%s status=%s detail=%s",
                task_id,
                exc.status_code,
                exc.detail,
            )
            return
        finally:
            unsubscribe_task_events(subscription)

    return StreamingResponse(
        event_iter(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
