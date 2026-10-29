from __future__ import annotations

import pytest

from app.execution import agent_service
from app.modules.task_store import store


@pytest.fixture()
def isolated_store():
    original = store._tasks.copy()
    store._tasks.clear()
    try:
        yield
    finally:
        store._tasks.clear()
        store._tasks.update(original)


@pytest.mark.asyncio
async def test_new_task_session_lookup_does_not_submit_a_probe(monkeypatch: pytest.MonkeyPatch, isolated_store) -> None:
    task = store.create_task(
        {
            "title": "lazy session task",
            "goalSummary": "Run an explicitly selected research Skill.",
            "domain": "research_task",
            "status": "running",
            "skillPack": "sdr",
        }
    )

    class ProbeRuntime:
        async def submit(self, _request):
            raise AssertionError("session lookup must not start a runtime request")

    monkeypatch.setattr(agent_service.settings, "skill_exec_mode", "live")
    monkeypatch.setattr(agent_service, "get_execution_runtime", lambda: ProbeRuntime())

    assert await agent_service.ensure_task_session(task["taskId"]) is None
