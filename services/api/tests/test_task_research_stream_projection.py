from __future__ import annotations

from pathlib import Path

import pytest

from app.modules.task_events import (
    _research_event_source_identity,
    _stable_event_id,
    project_research_activity_events,
    validate_research_activity_event,
)


def _event(
    event_type: str,
    event_id: str,
    *,
    phase: str,
    status: str = "executing",
    work_unit_id: str = "work-1",
    occurred_at: str = "2026-08-06T00:00:00Z",
    **payload: object,
) -> dict:
    return {
        "id": event_id,
        "type": event_type,
        "taskId": "task-1",
        "runId": "decision-1",
        "occurredAt": occurred_at,
        "payload": {
            "phase": phase,
            "status": status,
            "workUnitId": work_unit_id,
            **payload,
        },
    }


def test_high_value_lifecycle_events_are_primary() -> None:
    projected = project_research_activity_events([
        _event("orchestrator_lifecycle", "orchestrator-start", phase="started"),
        _event("agent_lifecycle", "work-start", phase="started"),
        _event("handoff_completed", "handoff-1", phase="completed"),
    ])
    assert [event["headline"] for event in projected] == [
        "研究目标已经开始",
        "研究步骤开始",
        "工作已经交接",
    ]
    assert {event["presentationLevel"] for event in projected} == {"primary"}
    assert projected[-1]["eventType"] == "meaningful_progress"


def test_multiple_failed_planning_attempts_remain_visible() -> None:
    projected = project_research_activity_events([
        _event(
            "planning_lifecycle",
            "planning-fail-1",
            phase="failed",
            status="failed",
            work_unit_id="",
            planningAttemptId="pa-1",
            occurred_at="2026-10-08T10:08:14Z",
        ),
        _event(
            "planning_lifecycle",
            "planning-fail-2",
            phase="failed",
            status="failed",
            work_unit_id="",
            planningAttemptId="pa-2",
            occurred_at="2026-10-08T10:08:15Z",
        ),
        _event(
            "planning_lifecycle",
            "planning-start-2",
            phase="started",
            status="started",
            work_unit_id="",
            planningAttemptId="pa-2",
            occurred_at="2026-10-08T10:08:14.500Z",
        ),
    ])

    assert [event["headline"] for event in projected] == [
        "本次研究方案生成未完成",
        "本次研究方案生成未完成",
        "正在组织研究方案",
    ]
    assert projected[0]["payload"]["activityKey"] == "planning-attempt:pa-1"
    assert projected[0]["occurredAt"] == "2026-10-08T10:08:14Z"


def test_planning_and_research_design_facts_become_public_activity_without_private_reasoning() -> None:
    projected = project_research_activity_events([
        _event("planning_lifecycle", "planning-start", phase="started", work_unit_id="", planningAttemptId="pa-1"),
        _event(
            "planning_lifecycle",
            "planning-complete",
            phase="completed",
            status="completed",
            work_unit_id="",
            planningAttemptId="pa-1",
            occurred_at="2026-08-06T00:00:01Z",
        ),
        _event(
            "research_design_lifecycle",
            "design-formed",
            phase="formed",
            status="draft",
            work_unit_id="",
            researchDesignId="rd-1",
            designVersion=1,
            occurred_at="2026-08-06T00:00:02Z",
        ),
        _event(
            "research_design_lifecycle",
            "design-confirmed",
            phase="confirmed",
            status="confirmed",
            work_unit_id="",
            researchDesignId="rd-1",
            designVersion=1,
            occurred_at="2026-08-06T00:00:03Z",
        ),
    ])

    assert [event["headline"] for event in projected] == [
        "正在组织研究方案",
        "研究方案已形成",
        "研究方案已确认，准备进入执行",
    ]
    assert all(event["eventType"] == "meaningful_progress" for event in projected)
    assert all(event["workUnitId"] is None for event in projected)
    assert [event["payload"]["activityKey"] for event in projected] == [
        "planning-attempt:pa-1",
        "research-design:rd-1:v1",
        "research-design:rd-1:v1",
    ]
    assert "planning-complete" not in str(projected)
    assert not any(
        phrase in str(projected)
        for phrase in ("正在思考", "正在推理", "模型认为", "正在权衡", "planner", "provider")
    )


def test_planning_event_identity_is_stable_when_execution_source_appears() -> None:
    projected = project_research_activity_events([
        _event(
            "research_design_lifecycle",
            "design-v2-formed",
            phase="formed",
            work_unit_id="",
            researchDesignId="rd-1",
            designVersion=2,
        ),
    ])[0]
    pre_run_source = _research_event_source_identity(
        task_id="task-1", source_identity="task:task-1:pre-run", event=projected,
    )
    execution_source = _research_event_source_identity(
        task_id="task-1", source_identity="decision-1", event=projected,
    )
    assert pre_run_source == execution_source == "task:task-1:planning"
    before = _stable_event_id(
        owner_id="owner-1", workspace_id="workspace-1", task_id="task-1",
        source_identity=pre_run_source, source_event_id=projected["sourceEventId"],
    )
    after = _stable_event_id(
        owner_id="owner-1", workspace_id="workspace-1", task_id="task-1",
        source_identity=execution_source, source_event_id=projected["sourceEventId"],
    )
    assert before == after

    execution_event = validate_research_activity_event({
        "sourceEventId": "research-activity-v2:lifecycle:task-1:run:decision-1:agent:started",
        "eventType": "research_progress",
        "presentationLevel": "primary",
        "workUnitId": "work-1",
        "headline": "研究步骤开始",
        "summary": None,
        "invalidateScopes": ["collaboration"],
        "payload": {"phase": "started"},
        "occurredAt": "2026-08-06T00:00:00Z",
    })
    assert _research_event_source_identity(
        task_id="task-1", source_identity="decision-1", event=execution_event,
    ) == "decision-1"
    execution_a = _stable_event_id(
        owner_id="owner-1", workspace_id="workspace-1", task_id="task-1",
        source_identity="decision-1", source_event_id=execution_event["sourceEventId"],
    )
    execution_b = _stable_event_id(
        owner_id="owner-1", workspace_id="workspace-1", task_id="task-1",
        source_identity="decision-2", source_event_id=execution_event["sourceEventId"],
    )
    assert execution_a != execution_b

def test_terminal_artifact_and_review_events_only_request_authoritative_reads() -> None:
    projected = project_research_activity_events([
        _event("artifact_persisted", "artifact-1", phase="persisted", artifactRefs=["artifact-1"]),
        _event("mandatory_review_lifecycle", "review-1", phase="completed", status="completed"),
        _event("execution_terminal", "terminal-1", phase="completed", status="completed"),
    ])
    text = " ".join(f"{row['headline']} {row.get('summary') or ''}" for row in projected)
    assert "产物可用" not in text
    assert "复核通过" not in text
    assert "研究已经完成" not in text
    assert {"artifact", "review", "task"}.issubset(
        {scope for row in projected for scope in row["invalidateScopes"]}
    )


def test_distinct_skill_activity_is_preserved_without_raw_internal_names() -> None:
    lifecycle = [
        _event(
            "skill_lifecycle",
            "skill-search",
            phase="completed",
            status="completed",
            callId="call-search",
            capabilityId="literature_search",
        ),
        _event(
            "skill_lifecycle",
            "skill-screen",
            phase="completed",
            status="completed",
            callId="call-screen",
            capabilityId="source_screening",
            occurred_at="2026-08-06T00:00:01Z",
        ),
        _event(
            "skill_lifecycle",
            "skill-internal",
            phase="completed",
            status="completed",
            callId="call-internal",
            capabilityId="provider_internal_thing",
            occurred_at="2026-08-06T00:00:02Z",
        ),
    ]
    projected = project_research_activity_events(lifecycle)
    assert [event["headline"] for event in projected] == ["文献检索已完成", "资料筛选已完成"]
    assert all(event["presentationLevel"] == "detail" for event in projected)
    assert all(event["eventType"] == "research_tool_summary" for event in projected)
    assert len({event["payload"]["activityKey"] for event in projected}) == 2
    assert "provider_internal_thing" not in str(projected)


@pytest.mark.parametrize(
    "unsafe",
    [
        {"sourceEventId": "x", "eventType": "x", "headline": "x", "occurredAt": "now", "prompt": "hidden"},
        {"sourceEventId": "x", "eventType": "x", "headline": "x", "occurredAt": "now", "toolArguments": {}},
        {"sourceEventId": "x", "eventType": "x", "headline": "Bearer secret-token", "occurredAt": "now"},
        {"sourceEventId": "x", "eventType": "x", "headline": "x", "occurredAt": "now", "stackTrace": "raw"},
    ],
)
def test_sensitive_fields_and_values_are_rejected(unsafe: dict) -> None:
    with pytest.raises(ValueError):
        validate_research_activity_event(unsafe)


def test_only_allowlisted_payload_fields_survive() -> None:
    safe = validate_research_activity_event({
        "sourceEventId": "source-1",
        "eventType": "research_progress",
        "presentationLevel": "primary",
        "headline": "获得了有意义的新进展",
        "summary": "阶段结果已形成。",
        "occurredAt": "2026-08-06T00:00:00Z",
        "invalidateScopes": ["task", "unknown", "task"],
        "payload": {
            "artifactRefs": ["artifact-1"],
            "aggregateCount": 2,
            "activityKey": "safe-call-key",
            "phase": "completed",
            "raw": "must not survive",
        },
    })
    assert safe["invalidateScopes"] == ["task"]
    assert safe["payload"] == {
        "artifactRefs": ["artifact-1"],
        "aggregateCount": 2,
        "activityKey": "safe-call-key",
        "phase": "completed",
    }


def test_sse_route_contract_is_persistent_header_based_and_cancellable() -> None:
    route = Path(__file__).parents[1] / "app" / "routes" / "tasks.py"
    source = route.read_text(encoding="utf-8")
    assert '@router.get("/{task_id}/research-events")' in source
    assert 'Header(alias="Last-Event-ID")' in source
    assert "repository.subscribe(" in source
    assert "repository.list_after" in source
    assert '"type": "stream_reset"' in source
    assert 'yield ": heartbeat\\n"' in source
    assert "except asyncio.CancelledError" in source
    assert "listener.close()" in source
    assert "afterEventId" not in source[source.index('@router.get("/{task_id}/research-events")'):source.index('@router.patch("/{task_id}"')]


def test_migration_uses_notify_only_as_wakeup_and_has_bounded_retention() -> None:
    migration = Path(__file__).parents[1] / "migrations" / "20260806_01_task_research_stream.sql"
    source = migration.read_text(encoding="utf-8")
    assert "openair_task_stream_events" in source
    assert "expires_at" in source
    assert "INTERVAL '7 days'" in source
    assert "pg_notify" in source
    assert "Notification contains only routing metadata" in source
    assert "payload JSONB" in source
    assert "UNIQUE(owner_id, workspace_id, task_id, source_identity, source_event_id)" in source
    assert "openair_validate_task_stream_scope" in source
    assert "source_identity, event_sequence" in source


def test_b25_activity_names_prefer_user_names_then_registry_names(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.modules import agent_registry, task_execution_graph

    class _Agent:
        displayName = "研究问题聚焦"

    class _Registry:
        def get(self, agent_id: str):
            return _Agent() if agent_id == "builtin-agent" else None

    monkeypatch.setattr(agent_registry, "load_agent_registry", lambda: _Registry())
    monkeypatch.setattr(
        task_execution_graph,
        "_find_skill_entry",
        lambda skill_id: {"skill_id": skill_id, "display_name": "材料证据整理"}
        if skill_id == "builtin-skill"
        else None,
    )

    projected = project_research_activity_events([
        _event("agent_lifecycle", "agent-user", phase="started", agentId="opaque-user-agent", userFacingLabel="我的论文复核助手"),
        _event("skill_lifecycle", "skill-user", phase="completed", status="completed", callId="user-skill", skillId="opaque-user-skill", userFacingLabel="我的材料筛选"),
        _event("agent_lifecycle", "agent-built-in", phase="started", agentId="builtin-agent", occurred_at="2026-08-06T00:00:01Z"),
        _event("skill_lifecycle", "skill-built-in", phase="completed", status="completed", callId="builtin-skill-call", skillId="builtin-skill", occurred_at="2026-08-06T00:00:02Z"),
    ])

    assert [event["headline"] for event in projected] == [
        "我的论文复核助手 开始工作",
        "我的材料筛选已完成",
        "研究问题聚焦 开始工作",
        "材料证据整理已完成",
    ]


def test_b25_activity_names_fail_closed_instead_of_showing_raw_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.modules import agent_registry, task_execution_graph

    class _RawAgent:
        displayName = "opaque_agent_registry_key"

    class _Registry:
        def get(self, agent_id: str):
            return _RawAgent() if agent_id == "opaque_agent_registry_key" else None

    monkeypatch.setattr(agent_registry, "load_agent_registry", lambda: _Registry())
    monkeypatch.setattr(
        task_execution_graph,
        "_find_skill_entry",
        lambda skill_id: {"skill_id": skill_id, "display_name": skill_id}
        if skill_id == "opaque_skill_registry_key"
        else None,
    )

    projected = project_research_activity_events([
        _event("agent_lifecycle", "agent-raw", phase="started", agentId="opaque_agent_registry_key"),
        _event("skill_lifecycle", "skill-raw", phase="completed", status="completed", callId="raw-skill", skillId="opaque_skill_registry_key"),
    ])

    assert [event["headline"] for event in projected] == ["研究步骤开始"]
    assert "opaque_agent_registry_key" not in str(projected)
    assert "opaque_skill_registry_key" not in str(projected)


def test_b25_user_visible_gate_messages_are_research_language_only() -> None:
    repository = Path(__file__).parents[1] / "app" / "modules" / "collaboration_repository.py"
    source = repository.read_text(encoding="utf-8")

    assert "specialist dispatch" not in source
    assert "修订执行" not in source
    assert "Execution will continue from the confirmed boundary." not in source
    assert "继续下一步研究前，需要你确认。" in source
    assert "已开始新一轮研究。" in source

