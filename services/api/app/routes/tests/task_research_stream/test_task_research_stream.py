from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.routes import tasks as task_routes


class _Listener:
    def __init__(self) -> None:
        self.closed = False

    def wait(self, _timeout: float) -> bool:
        return False

    def close(self) -> None:
        self.closed = True


class _Repository:
    cursor_expired = False
    instances: list["_Repository"] = []

    def __init__(self) -> None:
        self.listener = _Listener()
        self.listed_after: list[int] = []
        self.__class__.instances.append(self)

    def subscribe(self, **_scope: object) -> _Listener:
        return self.listener

    def append_many(self, **_payload: object) -> int:
        return 0

    def cursor(self, **_scope: object) -> SimpleNamespace:
        return SimpleNamespace(sequence=0 if self.cursor_expired else 7, expired=self.cursor_expired)

    def list_after(self, *, sequence: int, **_scope: object) -> list[dict[str, object]]:
        self.listed_after.append(sequence)
        return [{
            "id": "event-8",
            "type": "meaningful_progress",
            "taskId": "task-1",
            "sequence": 8,
            "occurredAt": "2026-09-27T00:00:00Z",
            "payload": {"headline": "progress"},
        }]


def _install_route_fakes(monkeypatch: pytest.MonkeyPatch, *, expired: bool) -> None:
    _Repository.cursor_expired = expired
    _Repository.instances.clear()
    monkeypatch.setattr(task_routes.settings, "persistence_backend", "postgres")
    monkeypatch.setattr(task_routes.settings, "research_orchestration_v1", True)
    monkeypatch.setattr(
        task_routes,
        "_read_task_research_stream_state",
        lambda task_id, identity: ({"taskId": task_id}, {"taskId": task_id, "runId": "run-1"}),
    )
    monkeypatch.setattr(task_routes, "TaskResearchEventRepository", _Repository)
    monkeypatch.setattr(task_routes, "build_collaboration_lifecycle_events", lambda _value: [])
    monkeypatch.setattr(task_routes, "project_research_activity_events", lambda _value: [])


@pytest.mark.asyncio
async def test_valid_cursor_replays_next_event_and_closes_listener(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_route_fakes(monkeypatch, expired=False)
    response = await task_routes.stream_task_research_events(
        "task-1",
        identity={"ownerId": "owner-1", "workspaceId": "workspace-1"},
        last_event_id="event-7",
    )
    iterator = response.body_iterator
    chunk = await anext(iterator)
    await iterator.aclose()

    repository = _Repository.instances[-1]
    assert "event: meaningful_progress" in chunk
    assert "stream_reset" not in chunk
    assert repository.listed_after == [7]
    assert repository.listener.closed is True


@pytest.mark.asyncio
async def test_expired_cursor_resets_before_replay_and_closes_listener(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_route_fakes(monkeypatch, expired=True)
    response = await task_routes.stream_task_research_events(
        "task-1",
        identity={"ownerId": "owner-1", "workspaceId": "workspace-1"},
        last_event_id="expired-event",
    )
    iterator = response.body_iterator
    chunk = await anext(iterator)
    await iterator.aclose()

    repository = _Repository.instances[-1]
    assert "event: stream_reset" in chunk
    assert "cursor_expired_or_source_changed" in chunk
    assert repository.listed_after == []
    assert repository.listener.closed is True
