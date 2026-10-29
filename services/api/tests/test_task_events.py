import asyncio
import json
from pathlib import Path

import pytest

from app.modules.task_events import publish_task_event, subscribe_task_events, unsubscribe_task_events
from app.modules.task_store import store
from app.routes import tasks as task_routes
from app.routes.tasks import stream_task_events
from app.config import settings
from app.models.research_orchestration import ResearchTaskV12


OWNER = "owner_task_events"
WORKSPACE = "ws_task_events"


@pytest.fixture(autouse=True)
def catalog_visibility_authority(monkeypatch: pytest.MonkeyPatch):
    """Keep SSE owner tests on their explicit projection fixtures.

    PostgreSQL task reads now consult the public Artifact catalog. These tests
    do not exercise that repository and must not fall through to a real DB.
    """

    class _ArtifactRepository:
        def list_by_scope(self, *args, **kwargs):
            return [], 0

    class _TaskMessageRepository:
        def latest_seq(self, **_kwargs):
            return 0

        def messages_after(self, **_kwargs):
            return []

    monkeypatch.setattr(task_routes, "ArtifactRepository", _ArtifactRepository)
    monkeypatch.setattr(task_routes, "TaskMessageRepository", _TaskMessageRepository)


def _decode_sse_event(chunk: str) -> dict:
    fields: dict[str, list[str]] = {}
    for raw_line in chunk.splitlines():
        if not raw_line or raw_line.startswith(":"):
            continue
        key, separator, raw_value = raw_line.partition(":")
        value = raw_value[1:] if separator and raw_value.startswith(" ") else raw_value
        fields.setdefault(key, []).append(value)
    event = json.loads("\n".join(fields["data"]))
    assert event["id"] == fields["id"][0]
    assert event["type"] == fields["event"][0]
    return event


def _empty_collaboration(task_id: str, revision: int) -> dict:
    return {
        "taskId": task_id,
        "revision": revision,
        "status": "not_started",
        "collaboration": {"actual": [], "handoffs": [], "needsUserDecision": False, "pendingDecisions": []},
        "progress": {"status": "not_started", "stages": []},
        "resources": {"skills": [], "materials": [], "sources": [], "files": []},
        "artifacts": {"items": [], "confirmableCount": 0},
        "entries": [],
    }


def _completed_collaboration(task_id: str, revision: int) -> dict:
    return {
        "taskId": task_id,
        "runId": "run_lifecycle",
        "runCreatedAt": "2026-07-31T00:00:00Z",
        "runStartedAt": "2026-07-31T00:00:01Z",
        "runEndedAt": "2026-07-31T00:00:08Z",
        "revision": revision,
        "status": "completed",
        "collaboration": {"actual": [], "handoffs": [], "needsUserDecision": False, "pendingDecisions": []},
        "progress": {"status": "completed", "stages": []},
        "resources": {"skills": [], "materials": [], "sources": [], "files": []},
        "artifacts": {
            "items": [{
                "artifactId": "artifact_1",
                "sourceCapabilityCallId": "call_agent",
                "title": "Question refinement",
                "status": "confirmed",
                "createdAt": "2026-07-31T00:00:04Z",
            }],
            "confirmableCount": 1,
        },
        "entries": [
            {
                "id": "wu_agent",
                "workUnitId": "wu_agent",
                "sequence": 1,
                "kind": "specialist",
                "agentId": "research_question_refinement",
                "title": "Refine the research question",
                "status": "completed",
                "assignedAt": "2026-07-31T00:00:01Z",
                "startedAt": "2026-07-31T00:00:02Z",
                "completedAt": "2026-07-31T00:00:05Z",
                "inputMemoryRefs": [{"id": "memory_shared"}],
                "inputArtifactRefs": [],
                "outputArtifactRefs": [{"id": "artifact_1"}],
                "skillCalls": [{
                    "callId": "call_skill",
                    "skillId": "question_refinement",
                    "status": "completed",
                    "startedAt": "2026-07-31T00:00:02Z",
                    "completedAt": "2026-07-31T00:00:03Z",
                    "outputArtifactRefs": [],
                }],
                "handoff": None,
            },
            {
                "id": "wu_review",
                "workUnitId": "wu_review",
                "sequence": 2,
                "kind": "review",
                "agentId": "result_reliability_audit",
                "title": "Independent result review",
                "status": "completed",
                "assignedAt": "2026-07-31T00:00:05Z",
                "startedAt": "2026-07-31T00:00:06Z",
                "completedAt": "2026-07-31T00:00:07Z",
                "inputMemoryRefs": [],
                "inputArtifactRefs": [{"id": "artifact_1"}],
                "outputArtifactRefs": [{"id": "artifact_1"}],
                "skillCalls": [],
                "handoff": {
                    "handoffId": "handoff_1",
                    "fromWorkUnitId": "wu_agent",
                    "toWorkUnitId": "wu_review",
                    "fromAgentId": "research_question_refinement",
                    "toAgentId": "result_reliability_audit",
                    "summary": "Pass the persisted artifact for independent review.",
                    "artifactRefs": [{"id": "artifact_1"}],
                    "occurredAt": "2026-07-31T00:00:05Z",
                },
            },
        ],
    }


def _patch_collaboration_repository(
    monkeypatch: pytest.MonkeyPatch,
    projection: dict,
) -> None:
    class _CollaborationRepository:
        def list_projection(self, *, task_id: str, owner_id: str, workspace_id: str) -> dict:
            assert task_id == projection["taskId"]
            assert owner_id == OWNER
            assert workspace_id == WORKSPACE
            return projection

    monkeypatch.setattr(task_routes, "CollaborationRepository", _CollaborationRepository)


def _create_task(title: str = "Event task") -> str:
    task = store.create_task(
        {
            "title": title,
            "goalSummary": "event stream regression",
            "ownerId": OWNER,
            "workspaceId": WORKSPACE,
            "createdBy": OWNER,
        }
    )
    return task["taskId"]


@pytest.mark.asyncio
async def test_task_event_fanout_delivers_updates_and_deletes() -> None:
    task_id = "task_event_unit"
    subscription = subscribe_task_events(task_id, max_queue_size=10)
    try:
        publish_task_event(task_id, "task_updated", updated_at="2026-07-08T00:00:00Z", payload={"reason": "unit"})
        updated = await asyncio.wait_for(subscription.queue.get(), timeout=1)
        assert updated["type"] == "task_updated"
        assert updated["taskId"] == task_id
        assert updated["payload"] == {"reason": "unit"}

        publish_task_event(task_id, "task_deleted", updated_at="2026-07-08T00:00:01Z", payload={})
        deleted = await asyncio.wait_for(subscription.queue.get(), timeout=1)
        assert deleted["type"] == "task_deleted"
        assert deleted["taskId"] == task_id
    finally:
        unsubscribe_task_events(subscription)


@pytest.mark.asyncio
async def test_task_events_stream_opens_with_snapshot() -> None:
    task_id = _create_task("Snapshot stream task")

    response = await stream_task_events(
        task_id,
        identity={"ownerId": OWNER, "workspaceId": WORKSPACE, "createdBy": OWNER},
    )
    assert response.media_type == "text/event-stream"
    assert response.headers["cache-control"] == "no-cache"
    assert response.headers["x-accel-buffering"] == "no"
    iterator = response.body_iterator
    try:
        line = await anext(iterator)
    finally:
        await iterator.aclose()

    event = _decode_sse_event(line)
    task = store.get_task(task_id)
    assert event["type"] == "snapshot"
    assert event["taskId"] == task_id
    assert event["runId"] is None
    assert event["scope"] == {
        "taskId": task_id,
        "runId": None,
        "ownerId": OWNER,
        "workspaceId": WORKSPACE,
    }
    assert event["updatedAt"] == task["updatedAt"]
    assert event["payload"] == {"reason": "stream_opened"}


@pytest.mark.asyncio
async def test_flag_on_sse_snapshot_uses_the_durable_projection_revision(monkeypatch: pytest.MonkeyPatch) -> None:
    task_id = "task_durable_projection"
    durable_task = {
        "taskId": task_id,
        "ownerId": OWNER,
        "workspaceId": WORKSPACE,
        "createdBy": OWNER,
        "revision": 9,
        "updatedAt": "2026-07-30T00:00:00Z",
        "progressProjection": {"revision": 9, "taskProgress": {"status": "running"}},
    }

    class _Repository:
        def get_task(self, requested_task_id: str) -> dict:
            assert requested_task_id == task_id
            return durable_task

    monkeypatch.setattr(settings, "research_orchestration_v1", True)
    monkeypatch.setattr(settings, "persistence_backend", "postgres")
    monkeypatch.setattr(task_routes, "TaskRepository", _Repository)
    _patch_collaboration_repository(monkeypatch, _empty_collaboration(task_id, 9))

    response = await task_routes.stream_task_events(
        task_id,
        identity={"ownerId": OWNER, "workspaceId": WORKSPACE, "createdBy": OWNER},
    )
    iterator = response.body_iterator
    try:
        event = _decode_sse_event(await anext(iterator))
    finally:
        await iterator.aclose()

    assert event["payload"]["revision"] == durable_task["revision"]
    assert event["payload"]["projection"] == durable_task["progressProjection"]


@pytest.mark.asyncio
async def test_sse_stale_projection_stamp_is_restamped_without_failing_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-progress writes bump task.revision; SSE re-stamps instead of 409 mid-stream."""
    task_id = "task_projection_lag"
    durable_task = {
        "taskId": task_id,
        "ownerId": OWNER,
        "workspaceId": WORKSPACE,
        "createdBy": OWNER,
        "revision": 10,
        "updatedAt": "2026-07-30T00:00:00Z",
        # Projection lagging the task revision is a common concurrent-write race.
        "progressProjection": {"revision": 9, "taskProgress": {"status": "running"}},
    }

    class _Repository:
        def get_task(self, requested_task_id: str) -> dict:
            assert requested_task_id == task_id
            return durable_task

    monkeypatch.setattr(settings, "research_orchestration_v1", True)
    monkeypatch.setattr(settings, "persistence_backend", "postgres")
    monkeypatch.setattr(task_routes, "TaskRepository", _Repository)
    _patch_collaboration_repository(monkeypatch, _empty_collaboration(task_id, 10))

    response = await task_routes.stream_task_events(
        task_id,
        identity={"ownerId": OWNER, "workspaceId": WORKSPACE, "createdBy": OWNER},
    )
    iterator = response.body_iterator
    try:
        event = _decode_sse_event(await anext(iterator))
    finally:
        await iterator.aclose()

    assert event["type"] == "snapshot"
    assert "reason" not in event["payload"]
    assert event["payload"]["revision"] == 10
    assert event["payload"]["projection"]["revision"] == 10
    assert event["payload"]["projection"]["taskProgress"] == {"status": "running"}


@pytest.mark.asyncio
async def test_flag_on_sse_snapshot_without_durable_projection_uses_legacy_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_id = "task_without_durable_projection"
    compatibility_task = {
        "taskId": task_id,
        "ownerId": OWNER,
        "workspaceId": WORKSPACE,
        "createdBy": OWNER,
        "revision": 0,
        "updatedAt": "2026-07-30T00:00:00Z",
    }

    class _Repository:
        def get_task(self, requested_task_id: str) -> dict:
            assert requested_task_id == task_id
            return compatibility_task

    monkeypatch.setattr(settings, "research_orchestration_v1", True)
    monkeypatch.setattr(settings, "persistence_backend", "postgres")
    monkeypatch.setattr(task_routes, "TaskRepository", _Repository)
    _patch_collaboration_repository(monkeypatch, _empty_collaboration(task_id, 0))

    response = await task_routes.stream_task_events(
        task_id,
        identity={"ownerId": OWNER, "workspaceId": WORKSPACE, "createdBy": OWNER},
    )
    iterator = response.body_iterator
    try:
        event = _decode_sse_event(await anext(iterator))
    finally:
        await iterator.aclose()

    assert event["payload"] == {"reason": "stream_opened"}


@pytest.mark.asyncio
async def test_durable_sse_emits_ordered_revision_bound_multiagent_lifecycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_id = "task_lifecycle"
    durable_task = {
        "taskId": task_id,
        "ownerId": OWNER,
        "workspaceId": WORKSPACE,
        "createdBy": OWNER,
        "revision": 12,
        "updatedAt": "2026-07-31T00:00:08Z",
        "progressProjection": {"revision": 12, "taskProgress": {"status": "completed"}},
    }

    class _Repository:
        def get_task(self, requested_task_id: str) -> dict:
            assert requested_task_id == task_id
            return durable_task

    monkeypatch.setattr(settings, "research_orchestration_v1", True)
    monkeypatch.setattr(settings, "persistence_backend", "postgres")
    monkeypatch.setattr(task_routes, "TaskRepository", _Repository)
    _patch_collaboration_repository(monkeypatch, _completed_collaboration(task_id, 12))

    response = await task_routes.stream_task_events(
        task_id,
        identity={"ownerId": OWNER, "workspaceId": WORKSPACE, "createdBy": OWNER},
    )
    iterator = response.body_iterator
    events: list[dict] = []
    try:
        while True:
            event = _decode_sse_event(await anext(iterator))
            events.append(event)
            if event["type"] == "execution_terminal":
                break
    finally:
        await iterator.aclose()

    assert events[0]["type"] == "snapshot"
    lifecycle = events[1:]
    assert {event["type"] for event in lifecycle} >= {
        "orchestrator_lifecycle",
        "agent_lifecycle",
        "skill_lifecycle",
        "mandatory_review_lifecycle",
        "handoff_completed",
        "artifact_persisted",
        "execution_terminal",
    }
    assert all(event["revision"] == 12 and event["payload"]["revision"] == 12 for event in lifecycle)
    assert all(event["runId"] == "run_lifecycle" for event in lifecycle)
    assert all(event["scope"] == {
        "taskId": task_id,
        "runId": "run_lifecycle",
        "ownerId": OWNER,
        "workspaceId": WORKSPACE,
    } for event in lifecycle)
    assert all(f":{task_id}:run:run_lifecycle:" in event["id"] for event in lifecycle)
    assert [event["sequence"] for event in lifecycle] == sorted(event["sequence"] for event in lifecycle)
    assert len({event["id"] for event in lifecycle}) == len(lifecycle)
    agent_started = next(
        event for event in lifecycle
        if event["type"] == "agent_lifecycle" and event["payload"]["phase"] == "started"
    )
    assert agent_started["payload"]["agentId"] == "research_question_refinement"
    assert agent_started["payload"]["inputMemoryRefs"] == ["memory_shared"]


@pytest.mark.asyncio
async def test_flag_on_v12_sse_reads_canonical_aggregate_not_legacy_collaboration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture_path = (
        Path(__file__).resolve().parents[3]
        / "packages"
        / "shared"
        / "test-fixtures"
        / "research-orchestration-v1.2-valid.json"
    )
    payload = json.loads(fixture_path.read_text(encoding="utf-8"))["researchTask"]
    aggregate = ResearchTaskV12.model_validate(payload)
    task = aggregate.model_dump(mode="json", by_alias=True)
    task.update({"ownerId": OWNER, "workspaceId": WORKSPACE, "createdBy": OWNER})

    class _TaskRepository:
        def get_task(self, requested_task_id: str) -> dict:
            assert requested_task_id == aggregate.task_id
            return task

    class _OrchestrationRepository:
        def load(self, *, task_id: str, owner_id: str, workspace_id: str) -> ResearchTaskV12:
            assert (task_id, owner_id, workspace_id) == (aggregate.task_id, OWNER, WORKSPACE)
            return aggregate

    class _LegacyRepository:
        def list_projection(self, **_kwargs) -> dict:
            raise AssertionError("v1.2 SSE must not read legacy collaboration tables")

    monkeypatch.setattr(settings, "research_orchestration_v1", True)
    monkeypatch.setattr(settings, "persistence_backend", "postgres")
    monkeypatch.setattr(task_routes, "TaskRepository", _TaskRepository)
    monkeypatch.setattr(task_routes, "ResearchOrchestrationRepository", _OrchestrationRepository)
    monkeypatch.setattr(task_routes, "CollaborationRepository", _LegacyRepository)

    response = await task_routes.stream_task_events(
        aggregate.task_id,
        identity={"ownerId": OWNER, "workspaceId": WORKSPACE, "createdBy": OWNER},
    )
    iterator = response.body_iterator
    events: list[dict] = []
    try:
        while True:
            event = _decode_sse_event(await anext(iterator))
            events.append(event)
            if event["type"] == "agent_lifecycle":
                break
    finally:
        await iterator.aclose()

    assert events[0]["type"] == "snapshot"
    assert events[-1]["revision"] == aggregate.revision
    assert events[-1]["payload"]["workUnitId"] == aggregate.work_units[0].work_unit_id


@pytest.mark.asyncio
async def test_durable_sse_last_event_id_resumes_without_duplicate_or_reorder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_id = "task_lifecycle_resume"
    durable_task = {
        "taskId": task_id,
        "ownerId": OWNER,
        "workspaceId": WORKSPACE,
        "createdBy": OWNER,
        "revision": 4,
        "updatedAt": "2026-07-31T00:00:08Z",
        "progressProjection": {"revision": 4, "taskProgress": {"status": "completed"}},
    }

    class _Repository:
        def get_task(self, _task_id: str) -> dict:
            return durable_task

    projection = _completed_collaboration(task_id, 4)
    monkeypatch.setattr(settings, "research_orchestration_v1", True)
    monkeypatch.setattr(settings, "persistence_backend", "postgres")
    monkeypatch.setattr(task_routes, "TaskRepository", _Repository)
    _patch_collaboration_repository(monkeypatch, projection)

    all_events = task_routes.build_collaboration_lifecycle_events(projection)
    cursor = next(event for event in all_events if event["type"] == "skill_lifecycle" and event["payload"]["phase"] == "completed")
    response = await task_routes.stream_task_events(
        task_id,
        identity={"ownerId": OWNER, "workspaceId": WORKSPACE, "createdBy": OWNER},
        last_event_id=cursor["id"],
    )
    iterator = response.body_iterator
    resumed: list[dict] = []
    try:
        while True:
            next_event = _decode_sse_event(await anext(iterator))
            resumed.append(next_event)
            if next_event["type"] == "execution_terminal":
                break
    finally:
        await iterator.aclose()

    expected = all_events[all_events.index(cursor) + 1:]
    assert [event["id"] for event in resumed] == [event["id"] for event in expected]
    assert len({event["id"] for event in resumed}) == len(resumed)
    assert all(event["sequence"] >= cursor["sequence"] for event in resumed)


@pytest.mark.asyncio
async def test_old_source_cursor_is_fenced_when_successor_is_authoritative(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_id = "task_successor_cursor"
    durable_task = {
        "taskId": task_id,
        "ownerId": OWNER,
        "workspaceId": WORKSPACE,
        "createdBy": OWNER,
        "revision": 8,
        "updatedAt": "2026-07-31T00:01:08Z",
        "progressProjection": {"revision": 8, "taskProgress": {"status": "completed"}},
    }
    successor = _completed_collaboration(task_id, 8)
    successor["runId"] = "run_successor"
    source = _completed_collaboration(task_id, 7)
    source["runId"] = "run_source"
    source_cursor = task_routes.build_collaboration_lifecycle_events(source)[-1]["id"]

    class _Repository:
        def get_task(self, requested_task_id: str) -> dict:
            assert requested_task_id == task_id
            return durable_task

    monkeypatch.setattr(settings, "research_orchestration_v1", True)
    monkeypatch.setattr(settings, "persistence_backend", "postgres")
    monkeypatch.setattr(task_routes, "TaskRepository", _Repository)
    _patch_collaboration_repository(monkeypatch, successor)

    response = await task_routes.stream_task_events(
        task_id,
        identity={"ownerId": OWNER, "workspaceId": WORKSPACE, "createdBy": OWNER},
        last_event_id=source_cursor,
    )
    iterator = response.body_iterator
    try:
        snapshot = _decode_sse_event(await anext(iterator))
        first_successor_event = _decode_sse_event(await anext(iterator))
    finally:
        await iterator.aclose()

    assert snapshot["type"] == "snapshot"
    assert snapshot["runId"] == "run_successor"
    assert first_successor_event["runId"] == "run_successor"
    assert ":run:run_source:" not in snapshot["id"]
    assert ":run:run_source:" not in first_successor_event["id"]


@pytest.mark.asyncio
async def test_refresh_and_last_event_reconnect_are_read_only_and_never_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_id = "task_read_only_reconnect"
    durable_task = {
        "taskId": task_id,
        "ownerId": OWNER,
        "workspaceId": WORKSPACE,
        "createdBy": OWNER,
        "revision": 6,
        "updatedAt": "2026-07-31T00:00:08Z",
        "progressProjection": {"revision": 6, "taskProgress": {"status": "completed"}},
    }
    reads = {"task": 0, "collaboration": 0}

    class _Repository:
        def get_task(self, requested_task_id: str) -> dict:
            assert requested_task_id == task_id
            reads["task"] += 1
            return durable_task

    class _CollaborationRepository:
        def list_projection(self, **kwargs) -> dict:
            assert kwargs == {"task_id": task_id, "owner_id": OWNER, "workspace_id": WORKSPACE}
            reads["collaboration"] += 1
            return _completed_collaboration(task_id, 6)

    def _dispatch_must_not_be_constructed(*_args, **_kwargs):
        raise AssertionError("SSE read/reconnect must not construct dispatch service")

    monkeypatch.setattr(settings, "research_orchestration_v1", True)
    monkeypatch.setattr(settings, "persistence_backend", "postgres")
    monkeypatch.setattr(task_routes, "TaskRepository", _Repository)
    monkeypatch.setattr(task_routes, "CollaborationRepository", _CollaborationRepository)
    monkeypatch.setattr(task_routes, "ResearchDispatchService", _dispatch_must_not_be_constructed)

    initial = await task_routes.stream_task_events(
        task_id,
        identity={"ownerId": OWNER, "workspaceId": WORKSPACE, "createdBy": OWNER},
    )
    initial_iterator = initial.body_iterator
    try:
        assert _decode_sse_event(await anext(initial_iterator))["type"] == "snapshot"
        cursor = _decode_sse_event(await anext(initial_iterator))["id"]
    finally:
        await initial_iterator.aclose()

    reconnect = await task_routes.stream_task_events(
        task_id,
        identity={"ownerId": OWNER, "workspaceId": WORKSPACE, "createdBy": OWNER},
        last_event_id=cursor,
    )
    reconnect_iterator = reconnect.body_iterator
    try:
        resumed = _decode_sse_event(await anext(reconnect_iterator))
    finally:
        await reconnect_iterator.aclose()

    assert resumed["id"] != cursor
    assert reads == {"task": 2, "collaboration": 2}


@pytest.mark.asyncio
async def test_durable_sse_poll_appends_new_facts_before_the_new_revision_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_id = "task_lifecycle_progression"
    initial_task = {
        "taskId": task_id,
        "ownerId": OWNER,
        "workspaceId": WORKSPACE,
        "createdBy": OWNER,
        "revision": 12,
        "updatedAt": "2026-07-31T00:00:02Z",
        "progressProjection": {"revision": 12, "taskProgress": {"status": "running"}},
    }
    completed_task = {
        **initial_task,
        "revision": 13,
        "updatedAt": "2026-07-31T00:00:08Z",
        "progressProjection": {"revision": 13, "taskProgress": {"status": "completed"}},
    }
    initial_projection = _completed_collaboration(task_id, 12)
    initial_projection.update({"status": "executing", "runEndedAt": "", "lifecycleArtifacts": []})
    initial_projection["entries"] = [initial_projection["entries"][0]]
    initial_projection["entries"][0].update({
        "status": "executing",
        "completedAt": "",
        "outputArtifactRefs": [],
    })
    initial_projection["entries"][0]["skillCalls"][0].update({
        "status": "executing",
        "completedAt": "",
    })
    completed_projection = _completed_collaboration(task_id, 13)
    completed_projection["lifecycleArtifacts"] = [{
        "artifactId": "artifact_1",
        "sourceCapabilityCallId": "call_agent",
        "workUnitId": "wu_agent",
        "agentId": "research_question_refinement",
        "title": "Question refinement",
        "status": "needs_review",
        "createdAt": "2026-07-31T00:00:04Z",
    }]

    state = {"index": -1}

    class _Repository:
        def get_task(self, _task_id: str) -> dict:
            state["index"] = min(state["index"] + 1, 1)
            return [initial_task, completed_task][state["index"]]

    class _CollaborationRepository:
        def list_projection(self, **_kwargs) -> dict:
            return [initial_projection, completed_projection][state["index"]]

    monkeypatch.setattr(settings, "research_orchestration_v1", True)
    monkeypatch.setattr(settings, "persistence_backend", "postgres")
    monkeypatch.setattr(task_routes, "TaskRepository", _Repository)
    monkeypatch.setattr(task_routes, "CollaborationRepository", _CollaborationRepository)
    monkeypatch.setattr(task_routes, "_TASK_EVENT_HEARTBEAT_SECONDS", 0.001)
    monkeypatch.setattr(task_routes, "_DURABLE_EVENT_REFRESH_SECONDS", 0.001)

    response = await task_routes.stream_task_events(
        task_id,
        identity={"ownerId": OWNER, "workspaceId": WORKSPACE, "createdBy": OWNER},
    )
    iterator = response.body_iterator
    events: list[dict] = []
    try:
        while True:
            event = _decode_sse_event(await anext(iterator))
            events.append(event)
            if event["type"] == "task_updated":
                break
    finally:
        await iterator.aclose()

    persistent = [event for event in events if event["type"] not in {"snapshot", "heartbeat"}]
    assert len({event["id"] for event in persistent}) == len(persistent)
    assert [event["sequence"] for event in persistent] == sorted(event["sequence"] for event in persistent)
    assert next(event for event in persistent if event["type"] == "artifact_persisted")["payload"]["status"] == "needs_review"
    terminal_index = next(index for index, event in enumerate(persistent) if event["type"] == "execution_terminal")
    revision_index = next(index for index, event in enumerate(persistent) if event["type"] == "task_updated")
    assert terminal_index < revision_index


@pytest.mark.asyncio
async def test_durable_sse_emits_agent_message_appended_from_table_seq_increment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """E1: in postgres mode the SSE emits one agent_message_appended per new
    message via the table seq increment (messages_after), not by scanning the
    payload's agentMessages array."""
    task_id = "task_msg_sse"
    initial = {"taskId": task_id, "ownerId": OWNER, "workspaceId": WORKSPACE, "createdBy": OWNER,
               "revision": 12, "updatedAt": "2026-07-31T00:00:02Z",
               "progressProjection": {"revision": 12, "taskProgress": {"status": "running"}}}
    refreshed = {**initial, "revision": 13, "updatedAt": "2026-07-31T00:00:08Z",
                 "progressProjection": {"revision": 13, "taskProgress": {"status": "running"}}}
    state = {"index": -1}

    class _Repository:
        def get_task(self, _task_id: str) -> dict:
            state["index"] = min(state["index"] + 1, 1)
            return [initial, refreshed][state["index"]]

    class _CollaborationRepository:
        def list_projection(self, **_kwargs) -> dict:
            return _completed_collaboration(task_id, [12, 13][state["index"]])

    class _MessageRepository:
        def latest_seq(self, **_kwargs) -> int:
            return 5  # stream-open anchor

        def messages_after(self, *, after_seq: int, **_kwargs) -> list[dict]:
            return [
                {"id": "msg_new_1", "seq": 6, "role": "assistant", "text": "a", "messageType": "chat"},
                {"id": "msg_new_2", "seq": 7, "role": "assistant", "text": "b", "messageType": "chat"},
            ]

    monkeypatch.setattr(settings, "research_orchestration_v1", True)
    monkeypatch.setattr(settings, "persistence_backend", "postgres")
    monkeypatch.setattr(task_routes, "TaskRepository", _Repository)
    monkeypatch.setattr(task_routes, "CollaborationRepository", _CollaborationRepository)
    monkeypatch.setattr(task_routes, "TaskMessageRepository", _MessageRepository)
    monkeypatch.setattr(task_routes, "_TASK_EVENT_HEARTBEAT_SECONDS", 0.001)
    monkeypatch.setattr(task_routes, "_DURABLE_EVENT_REFRESH_SECONDS", 0.001)

    response = await task_routes.stream_task_events(
        task_id,
        identity={"ownerId": OWNER, "workspaceId": WORKSPACE, "createdBy": OWNER},
    )
    events: list[dict] = []
    try:
        while True:
            event = _decode_sse_event(await anext(response.body_iterator))
            events.append(event)
            if event["type"] == "task_updated":
                break
    finally:
        await response.body_iterator.aclose()

    message_events = [event for event in events if event["type"] == "agent_message_appended"]
    assert [event["payload"]["messageId"] for event in message_events] == ["msg_new_1", "msg_new_2"]
    assert [event["payload"]["messageSeq"] for event in message_events] == [6, 7]


def test_durable_lifecycle_exposes_external_result_unknown_as_terminal_safe_fact() -> None:
    projection = _completed_collaboration("task_unknown_lifecycle", 17)
    projection.update({
        "status": "external_result_unknown",
        "failure": {
            "failedStep": "Question refinement",
            "externalAction": "An external action was started. Its final result could not be confirmed.",
            "resultTrustAndRetention": "No unconfirmed result is available for use.",
            "safeNextStep": "Check the recorded status before taking another action.",
        },
    })
    projection["entries"][1].update({
        "status": "external_result_unknown",
        "completedAt": "2026-07-31T00:00:07Z",
    })

    events = task_routes.build_collaboration_lifecycle_events(projection)

    terminal = next(event for event in events if event["type"] == "execution_terminal")
    review_unknown = next(
        event for event in events
        if event["type"] == "mandatory_review_lifecycle" and event["payload"]["phase"] == "unknown"
    )
    assert terminal["payload"]["phase"] == "unknown"
    assert terminal["payload"]["status"] == "external_result_unknown"
    assert terminal["payload"]["failure"]["failedStep"] == "Question refinement"
    assert review_unknown["payload"]["status"] == "external_result_unknown"


@pytest.mark.asyncio
async def test_durable_sse_heartbeat_carries_revision_and_last_sequence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_id = "task_lifecycle_heartbeat"
    durable_task = {
        "taskId": task_id,
        "ownerId": OWNER,
        "workspaceId": WORKSPACE,
        "createdBy": OWNER,
        "revision": 2,
        "updatedAt": "2026-07-31T00:00:00Z",
        "progressProjection": {"revision": 2, "taskProgress": {"status": "running"}},
    }

    class _Repository:
        def get_task(self, _task_id: str) -> dict:
            return durable_task

    monkeypatch.setattr(settings, "research_orchestration_v1", True)
    monkeypatch.setattr(settings, "persistence_backend", "postgres")
    monkeypatch.setattr(task_routes, "TaskRepository", _Repository)
    monkeypatch.setattr(task_routes, "_TASK_EVENT_HEARTBEAT_SECONDS", 0.001)
    monkeypatch.setattr(task_routes, "_DURABLE_EVENT_REFRESH_SECONDS", 0.001)
    _patch_collaboration_repository(monkeypatch, _empty_collaboration(task_id, 2))

    response = await task_routes.stream_task_events(
        task_id,
        identity={"ownerId": OWNER, "workspaceId": WORKSPACE, "createdBy": OWNER},
    )
    iterator = response.body_iterator
    try:
        assert _decode_sse_event(await anext(iterator))["type"] == "snapshot"
        heartbeat = _decode_sse_event(await anext(iterator))
    finally:
        await iterator.aclose()

    assert heartbeat["type"] == "heartbeat"
    assert heartbeat["revision"] == 2
    assert heartbeat["payload"] == {"revision": 2, "lastSequence": -1, "transient": True}
    assert heartbeat["id"].startswith(f"heartbeat:{task_id}:run:none:r2:s-1:b")
