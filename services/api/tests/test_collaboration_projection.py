from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException

from app.modules.collaboration_repository import _build_safe_task_projection
from app.modules.collaboration_lifecycle import (
    build_collaboration_lifecycle_events,
    project_research_task_lifecycle,
)
from app.models.research_orchestration import AssignmentRole, ResearchTaskV12
from app.modules.durable_execution import DurableExecutionError
from app.routes import collaboration as collaboration_route


V12_FIXTURE = (
    Path(__file__).resolve().parents[3]
    / "packages"
    / "shared"
    / "test-fixtures"
    / "research-orchestration-v1.2-valid.json"
)


def test_v12_aggregate_projects_real_lifecycle_without_legacy_tables() -> None:
    payload = json.loads(V12_FIXTURE.read_text(encoding="utf-8"))["researchTask"]
    task = ResearchTaskV12.model_validate(payload)

    projection = project_research_task_lifecycle(task)
    events = build_collaboration_lifecycle_events(projection)

    assert projection["taskId"] == task.task_id
    assert projection["revision"] == task.revision
    assert projection["runId"] == task.execution_decisions[0].execution_decision_id
    assert not projection["runId"].startswith("task-run:")
    assert projection["entries"][0]["workUnitId"] == task.work_units[0].work_unit_id
    assert projection["lifecycleArtifacts"][0]["artifactId"] == task.artifacts[0].artifact_id
    assert {event["type"] for event in events} >= {
        "orchestrator_lifecycle",
        "agent_lifecycle",
        "skill_lifecycle",
        "artifact_persisted",
    }
    assert all(event["revision"] == task.revision for event in events)
    assert task.work_units[0].instruction not in str(events)

    review_assignment = task.execution_decisions[0].assignments[0].model_copy(
        update={"role": AssignmentRole.REVIEWER}
    )
    review_decision = task.execution_decisions[0].model_copy(
        update={"assignments": [review_assignment]}
    )
    review_task = task.model_copy(update={"execution_decisions": [review_decision]})
    review_events = build_collaboration_lifecycle_events(
        project_research_task_lifecycle(review_task)
    )
    assert any(event["type"] == "mandatory_review_lifecycle" for event in review_events)


def test_planning_facts_project_before_execution_without_invented_activity() -> None:
    payload = json.loads(V12_FIXTURE.read_text(encoding="utf-8"))["researchTask"]
    source = ResearchTaskV12.model_validate(payload)
    draft_design = source.research_design_versions[0].model_copy(update={
        "status": "draft",
        "confirmed_at": None,
        "confirmation_ref": None,
    })
    planning_task = source.model_copy(update={
        "active_research_design_ref": None,
        "research_design_versions": [draft_design],
        "execution_decisions": [],
        "work_units": [],
        "capability_calls": [],
        "artifacts": [],
        "confirmations": [],
        "agent_interaction_records": [],
    })

    projection = project_research_task_lifecycle(planning_task)
    events = build_collaboration_lifecycle_events(projection)

    assert projection["runId"] is None
    assert projection["entries"] == []
    assert projection["planningAttempts"][0]["startedAt"] == source.planning_attempts[0].started_at
    assert projection["researchDesigns"][0]["createdAt"] == draft_design.created_at
    assert [event["type"] for event in events] == [
        "planning_lifecycle",
        "planning_lifecycle",
        "research_design_lifecycle",
    ]
    assert events[0]["occurredAt"] == source.planning_attempts[0].started_at.isoformat().replace("+00:00", "Z")
    assert events[-1]["occurredAt"] == draft_design.created_at.isoformat().replace("+00:00", "Z")
    assert all(event["runId"] is None for event in events)

    empty = planning_task.model_copy(update={
        "planning_attempts": [],
        "research_design_versions": [],
    })
    assert build_collaboration_lifecycle_events(project_research_task_lifecycle(empty)) == []


def test_v12_successor_projection_fences_source_decision_facts() -> None:
    payload = json.loads(V12_FIXTURE.read_text(encoding="utf-8"))["researchTask"]
    source = ResearchTaskV12.model_validate(payload)
    source_decision = source.execution_decisions[0]
    source_unit = source.work_units[0]
    successor_decision = source_decision.model_copy(update={
        "execution_decision_id": "decision_successor",
        "decision_sequence": source_decision.decision_sequence + 1,
        "supersedes_execution_decision_id": source_decision.execution_decision_id,
    })
    successor_unit = source_unit.model_copy(update={
        "work_unit_id": "wu_successor",
        "idempotency_key": "workunit:decision_successor:assignment_contract_full",
        "execution_decision_id": successor_decision.execution_decision_id,
    })
    successor = source.model_copy(update={
        "revision": source.revision + 1,
        "execution_decisions": [source_decision, successor_decision],
        "work_units": [source_unit, successor_unit],
        "capability_calls": [],
        "artifacts": [],
        "agent_interaction_records": [],
    })

    projection = project_research_task_lifecycle(successor)
    events = build_collaboration_lifecycle_events(projection)

    assert projection["runId"] == "decision_successor"
    assert [entry["workUnitId"] for entry in projection["entries"]] == ["wu_successor"]
    assert all(event["runId"] == "decision_successor" for event in events)
    assert all("decision_contract_full" not in event["id"] for event in events)


def _run(**overrides: Any) -> dict[str, Any]:
    return {
        "run_id": "run_1", "state": "leased", "failure_classification": None,
        "external_action_started": False, **overrides,
    }


def _unit(sequence: int, agent_id: str, **overrides: Any) -> dict[str, Any]:
    return {
        "work_unit_id": f"wu_{sequence}", "sequence": sequence, "agent_id": agent_id,
        "kind": "specialist", "visibility": "main", "title": f"Work {sequence}",
        "objective": f"Objective {sequence}", "summary": None, "decision_summary": None,
        "state": "queued", "stage_key": "problem_modeling", "substage_key": f"step_{sequence}",
        "model_profile": "sol",
        "input_memory_refs": [], "input_artifact_refs": [], "output_artifact_refs": [],
        "created_at": f"2026-07-23T00:0{sequence}:00Z", "started_at": None, "ended_at": None,
        **overrides,
    }


def _call(unit_id: str, *, kind: str = "agent_brain", **overrides: Any) -> dict[str, Any]:
    return {
        "call_id": f"call_{unit_id}", "work_unit_id": unit_id, "invocation_kind": kind, "external_action_started": False,
        "skill_id": None, "state": "queued", "failure_classification": None,
        "result_summary": None, "output_artifact_refs": [], "started_at": None, "ended_at": None,
        **overrides,
    }


def _artifact(artifact_id: str, *, source_mode: str = "live", state: str = "needs_review", confirmed: bool = False,
              kind: str = "research_output", source_call_id: str = "call_wu_1") -> dict[str, Any]:
    metadata: dict[str, Any] = {"artifactState": state}
    if confirmed:
        metadata.update({
            "reviewedAt": "2026-07-23T00:10:00Z", "reviewedBy": "result_reliability_audit",
            "humanGate": {"canUseDownstream": True},
        })
    return {
        "artifact_id": artifact_id, "title": artifact_id, "kind": kind,
        "mime_type": "text/markdown", "metadata": metadata, "source_mode": source_mode,
        "source_capability_call_id": source_call_id, "created_at": "2026-07-23T00:09:00Z",
    }


def _project(
    *,
    run: dict[str, Any] | None,
    units=None,
    calls=None,
    handoffs=None,
    artifacts=None,
    gates=None,
    revision: int = 7,
) -> dict[str, Any]:
    return _build_safe_task_projection(
        task_id="task_1",
        revision=revision,
        run=run,
        units=units or [],
        calls=calls or [],
        handoffs=handoffs or [], artifacts=artifacts or [], gates=gates or [],
    )


def test_no_run_is_not_started_with_empty_actual_sections() -> None:
    result = _project(run=None)

    assert result["revision"] == 7
    assert result["status"] == "not_started"
    assert result["collaboration"]["actual"] == []
    assert result["resources"]["skills"] == []
    assert result["artifacts"]["items"] == []


def test_queued_without_external_action_is_preparing_and_started_is_executing() -> None:
    preparing = _project(run=_run(), units=[_unit(1, "paper_evidence_extraction")])
    executing = _project(
        run=_run(external_action_started=True),
        units=[_unit(1, "paper_evidence_extraction", state="running", started_at="2026-07-23T00:02:00Z")],
        calls=[_call("wu_1", external_action_started=True, state="running")],
    )

    assert preparing["revision"] == 7
    assert preparing["status"] == "preparing"
    assert executing["status"] == "executing"


def test_failed_projection_answers_four_user_questions_without_internal_fields() -> None:
    cases = [
        (
            _project(
                run=_run(state="failed", external_action_started=True),
                units=[_unit(1, "paper_evidence_extraction", state="failed", title="Evidence retrieval")],
                calls=[_call("wu_1", external_action_started=True, state="failed")],
                artifacts=[_artifact("confirmed", state="usable", confirmed=True)],
            ),
            "Evidence retrieval",
            "At least one external action was started.",
        ),
        (
            _project(
                run=_run(state="abandoned", failure_classification="external_result_unknown", external_action_started=True),
                units=[_unit(1, "paper_evidence_extraction", state="failed", title="provider runtime exception", stage_key="problem_modeling", substage_key="source_check")],
            ),
            "source_check",
            "An external action was started. Its final result could not be confirmed.",
        ),
        (
            _project(
                run=_run(state="failed", external_action_started=False),
                units=[_unit(1, "paper_evidence_extraction", state="failed", title="Evidence retrieval")],
            ),
            "Evidence retrieval",
            "No external action was started.",
        ),
        (
            _project(
                run=_run(state="failed", external_action_started=True),
                units=[
                    _unit(1, "paper_evidence_extraction", state="succeeded", title="Evidence retrieval"),
                    _unit(2, "result_reliability_audit", state="failed", title="Review results"),
                ],
                calls=[_call("wu_1", external_action_started=True, state="succeeded")],
            ),
            "Review results",
            "At least one external action was started.",
        ),
    ]

    for result, expected_step, expected_external_action in cases:
        assert result["status"] in {"failed", "external_result_unknown"}
        assert result["failure"] == {
            "failedStep": expected_step,
            "externalAction": expected_external_action,
            "resultTrustAndRetention": (
                "Confirmed artifacts are retained. Unconfirmed or pending results are not available for use."
                if result["artifacts"]["items"]
                else "No confirmed artifacts are retained. Unconfirmed or pending results are not available for use."
            ),
            "safeNextStep": "Stop here and check the recorded status with a person before taking another action.",
        }
        assert _project(
            run=deepcopy(_run(state="failed", external_action_started=False)),
            units=[deepcopy(_unit(1, "paper_evidence_extraction", state="failed", title="Evidence retrieval"))],
        )["failure"] == cases[2][0]["failure"]
        serialized = str(result["failure"]).lower()
        for forbidden in ("provider", "model", "prompt", "token", "runtime", "worker", "lease", "fence", "exception"):
            assert forbidden not in serialized


def test_actual_agents_skill_and_handoff_keep_real_ownership_and_order() -> None:
    units = [
        _unit(1, "paper_evidence_extraction", state="succeeded", output_artifact_refs=["artifact_1"]),
        _unit(2, "result_reliability_audit", state="running", input_artifact_refs=["artifact_1"]),
    ]
    calls = [
        _call("wu_1", kind="skill", skill_id="academic_source_retrieval_skill", external_action_started=True,
              state="succeeded", result_summary="Sources retrieved", output_artifact_refs=["artifact_1"]),
    ]
    handoffs = [{
        "from_work_unit_id": "wu_1", "to_work_unit_id": "wu_2",
        "from_agent_id": "paper_evidence_extraction", "to_agent_id": "result_reliability_audit",
        "summary": "Evidence delivered for review", "output_artifact_refs": ["artifact_1"],
        "created_at": "2026-07-23T00:08:00Z",
    }]
    result = _project(run=_run(external_action_started=True), units=units, calls=calls, handoffs=handoffs,
                      artifacts=[_artifact("artifact_1")])

    assert [item["sequence"] for item in result["collaboration"]["actual"]] == [1, 2]
    assert result["resources"]["skills"][0]["agentId"] == "paper_evidence_extraction"
    assert result["collaboration"]["handoffs"][0]["toAgentId"] == "result_reliability_audit"


def test_planned_skill_without_started_call_is_not_reported_as_used() -> None:
    result = _project(
        run=_run(), units=[_unit(1, "paper_evidence_extraction")],
        calls=[_call("wu_1", kind="skill", skill_id="academic_source_retrieval_skill")],
    )

    assert result["resources"]["skills"] == []
    assert result["collaboration"]["actual"][0]["skillCalls"] == []


@pytest.mark.parametrize(
    ("run", "expected"),
    ((_run(state="cancelled", failure_classification="stopped_by_user"), "stopped_by_user"),
     (_run(state="abandoned", failure_classification="external_result_unknown"), "external_result_unknown")),
)
def test_stop_and_unknown_are_never_projected_as_completion(run: dict[str, Any], expected: str) -> None:
    result = _project(run=run)

    assert result["status"] == expected
    assert result["status"] != "completed"


def test_only_live_usable_and_durably_confirmed_artifact_is_confirmable() -> None:
    artifacts = [
        _artifact("confirmed", state="usable", confirmed=True),
        _artifact("usable_only", state="usable"),
        _artifact("replay", source_mode="replay", state="usable", confirmed=True),
        _artifact("pending", state="needs_review"),
    ]
    result = _project(run=_run(), calls=[_call("wu_1", external_action_started=True)], artifacts=artifacts)
    indexed = {item["artifactId"]: item for item in result["artifacts"]["items"]}

    assert indexed["confirmed"]["confirmable"] is True
    assert set(indexed) == {"confirmed"}
    assert result["artifacts"]["confirmableCount"] == 1
    lifecycle = {item["artifactId"]: item for item in result["lifecycleArtifacts"]}
    assert set(lifecycle) == {"confirmed", "usable_only", "pending"}
    assert lifecycle["pending"] == {
        "artifactId": "pending",
        "sourceCapabilityCallId": "call_wu_1",
        "workUnitId": "wu_1",
        "agentId": "",
        "title": "pending",
        "status": "needs_review",
        "createdAt": "2026-07-23T00:09:00Z",
    }


def test_projection_is_repeatable_and_contains_no_internal_execution_fields() -> None:
    source = _project(
        run=_run(), units=[_unit(1, "paper_evidence_extraction")],
        calls=[_call("wu_1")], artifacts=[_artifact("artifact_1")],
    )
    repeated = deepcopy(source)
    forbidden = {"provider", "model", "modelProfile", "model_profile", "prompt", "token", "runtime", "trace", "worker", "lease", "fence", "rawEvent"}

    def keys(value: Any) -> set[str]:
        if isinstance(value, dict):
            return set(value) | set().union(*(keys(item) for item in value.values()), set())
        if isinstance(value, list):
            return set().union(*(keys(item) for item in value), set())
        return set()

    assert source == repeated
    assert not {key for key in keys(source) if key in forbidden}


def test_agent_classification_is_safe_and_support_work_is_nested() -> None:
    units = [
        _unit(1, "paper_evidence_extraction", state="running"),
        _unit(2, "openair_summary_agent", parent_work_unit_id="wu_1", state="succeeded", visibility="detail"),
        _unit(3, "unregistered_agent", state="running"),
    ]
    result = _project(
        run=_run(external_action_started=True), units=units,
        calls=[_call("wu_2", kind="skill", external_action_started=True, skill_id="summarize")],
    )

    indexed = {item["agentId"]: item for item in result["collaboration"]["actual"]}
    assert indexed["paper_evidence_extraction"]["agentKind"] == "professional"
    assert indexed["paper_evidence_extraction"]["knownAgent"] is True
    assert indexed["paper_evidence_extraction"]["supportingActivities"][0]["agentName"] == "总结 智能体"
    assert "openair_summary_agent" not in indexed
    assert indexed["unregistered_agent"]["agentName"] == "研究协作记录"
    assert indexed["unregistered_agent"]["knownAgent"] is False


def test_projection_filters_planned_detached_and_non_live_artifacts_and_sanitizes_text() -> None:
    artifact_ids = ["live", "draft", "replay", "fixture", "external"]
    result = _project(
        run=_run(external_action_started=True),
        units=[
            _unit(1, "general_research_agent", objective="Safe goal. provider: hidden", output_artifact_refs=artifact_ids),
            _unit(2, "paper_evidence_extraction", input_artifact_refs=artifact_ids),
        ],
        calls=[
            _call("wu_1", kind="skill", skill_id="planned", state="queued"),
            _call("wu_detached", kind="skill", skill_id="detached", external_action_started=True),
            _call("wu_1", kind="skill", skill_id="started", external_action_started=True,
                  result_summary="Useful result. token: hidden", output_artifact_refs=artifact_ids),
        ],
        artifacts=[
            _artifact("live", state="usable", confirmed=True),
            _artifact("draft", state="usable", confirmed=True, kind="research_start_draft"),
            _artifact("replay", source_mode="replay", state="usable", confirmed=True),
            _artifact("fixture", source_mode="fixture", state="usable", confirmed=True),
            _artifact("external", source_mode="external", state="usable", confirmed=True),
        ],
        handoffs=[{
            "from_work_unit_id": "wu_1", "to_work_unit_id": "wu_2",
            "from_agent_id": "general_research_agent", "to_agent_id": "paper_evidence_extraction",
            "summary": "Only confirmed work is handed over", "output_artifact_refs": artifact_ids,
            "created_at": "2026-07-23T00:08:00Z",
        }],
    )

    assert [skill["skillId"] for skill in result["resources"]["skills"]] == ["started"]
    assert [artifact["artifactId"] for artifact in result["artifacts"]["items"]] == ["live"]
    agents = {agent["agentId"]: agent for agent in result["collaboration"]["actual"]}
    assert [ref["id"] for ref in agents["general_research_agent"]["outputArtifactRefs"]] == ["live"]
    assert [ref["id"] for ref in agents["general_research_agent"]["skillCalls"][0]["outputArtifactRefs"]] == ["live"]
    assert [ref["id"] for ref in agents["paper_evidence_extraction"]["handoff"]["artifactRefs"]] == ["live"]
    rendered = str(result).lower()
    assert "provider" not in rendered and "token" not in rendered


def test_route_passes_owner_workspace_scope_and_preserves_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    class ScopedRepository:
        def list_projection(self, *, task_id: str, owner_id: str, workspace_id: str):
            assert (task_id, owner_id, workspace_id) == ("task_1", "owner_other", "workspace_other")
            raise DurableExecutionError(404, "task not found")

    monkeypatch.setattr(collaboration_route, "CollaborationRepository", ScopedRepository)
    monkeypatch.setattr(collaboration_route.settings, "persistence_backend", "postgres")
    with pytest.raises(HTTPException) as error:
        collaboration_route.get_task_collaboration(
            "task_1", identity={"ownerId": "owner_other", "workspaceId": "workspace_other"},
        )

    assert error.value.status_code == 404


def test_v12_collaboration_route_uses_research_aggregate_not_legacy_projection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = json.loads(V12_FIXTURE.read_text(encoding="utf-8"))["researchTask"]
    payload.update({"revision": 8, "status": "awaiting_confirmation"})
    aggregate = ResearchTaskV12.model_validate(payload)
    second_call = aggregate.capability_calls[0].model_copy(update={
        "capability_call_id": "call_contract_full_second",
        "idempotency_key": "call:wu_contract_full:2",
        "attempt_number": 2,
    })
    aggregate = aggregate.model_copy(update={
        "capability_calls": [*aggregate.capability_calls, second_call],
    })
    owner_id = "owner_1"
    workspace_id = "workspace_1"
    created_by = "user_1"

    class TaskAuthority:
        def get_task(self, task_id: str):
            assert task_id == aggregate.task_id
            return {
                **aggregate.model_dump(mode="json", by_alias=True),
                "ownerId": owner_id,
                "workspaceId": workspace_id,
                "createdBy": created_by,
            }

    class ResearchAuthority:
        def load(self, *, task_id: str, owner_id: str, workspace_id: str):
            assert (task_id, owner_id, workspace_id) == (
                aggregate.task_id,
                "owner_1",
                "workspace_1",
            )
            return aggregate

    class LegacyProjectionMustNotBeRead:
        def list_projection(self, **_kwargs):
            raise AssertionError("v1.2 task must not read the legacy collaboration projection")

    monkeypatch.setattr(collaboration_route, "TaskRepository", TaskAuthority)
    monkeypatch.setattr(collaboration_route, "ResearchOrchestrationRepository", ResearchAuthority)
    monkeypatch.setattr(collaboration_route, "CollaborationRepository", LegacyProjectionMustNotBeRead)
    monkeypatch.setattr(collaboration_route.settings, "persistence_backend", "postgres")
    monkeypatch.setattr(collaboration_route.settings, "research_orchestration_v1", True)

    projection = collaboration_route.get_task_collaboration(
        aggregate.task_id,
        identity={
            "ownerId": owner_id,
            "workspaceId": workspace_id,
            "createdBy": created_by,
        },
    )

    assert projection["status"] == "awaiting_confirmation"
    assert projection["revision"] == 8
    assert projection["runId"] == aggregate.execution_decisions[0].execution_decision_id
    assert projection["entries"][0]["workUnitId"] == aggregate.work_units[0].work_unit_id
    assert [call["status"] for call in projection["entries"][0]["skillCalls"]] == ["succeeded", "succeeded"]
    assert projection["lifecycleArtifacts"][0]["artifactId"] == aggregate.artifacts[0].artifact_id


def test_v12_collaboration_route_without_execution_decision_is_not_started(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = json.loads(V12_FIXTURE.read_text(encoding="utf-8"))["researchTask"]
    aggregate = ResearchTaskV12.model_validate(payload).model_copy(update={
        "execution_decisions": [],
        "work_units": [],
        "capability_calls": [],
        "artifacts": [],
        "agent_interaction_records": [],
    })
    owner_id = "owner_1"
    workspace_id = "workspace_1"
    created_by = "user_1"

    class TaskAuthority:
        def get_task(self, _task_id: str):
            return {
                **aggregate.model_dump(mode="json", by_alias=True),
                "ownerId": owner_id,
                "workspaceId": workspace_id,
                "createdBy": created_by,
            }

    class ResearchAuthority:
        def load(self, **_kwargs):
            return aggregate

    monkeypatch.setattr(collaboration_route, "TaskRepository", TaskAuthority)
    monkeypatch.setattr(collaboration_route, "ResearchOrchestrationRepository", ResearchAuthority)
    monkeypatch.setattr(collaboration_route.settings, "persistence_backend", "postgres")
    monkeypatch.setattr(collaboration_route.settings, "research_orchestration_v1", True)

    projection = collaboration_route.get_task_collaboration(
        aggregate.task_id,
        identity={
            "ownerId": owner_id,
            "workspaceId": workspace_id,
            "createdBy": created_by,
        },
    )

    assert projection["status"] == "not_started"
    assert projection["runId"] is None
    assert projection["entries"] == []


def test_legacy_collaboration_route_keeps_legacy_projection(monkeypatch: pytest.MonkeyPatch) -> None:
    legacy_task = {
        "taskId": "legacy_task",
        "ownerId": "owner_1",
        "workspaceId": "workspace_1",
        "revision": 3,
    }
    expected = {"taskId": "legacy_task", "revision": 3, "status": "not_started", "entries": []}

    class TaskAuthority:
        def get_task(self, task_id: str):
            assert task_id == "legacy_task"
            return legacy_task

    class LegacyAuthority:
        def list_projection(self, **kwargs):
            assert kwargs == {
                "task_id": "legacy_task",
                "owner_id": "owner_1",
                "workspace_id": "workspace_1",
            }
            return expected

    monkeypatch.setattr(collaboration_route, "TaskRepository", TaskAuthority)
    monkeypatch.setattr(collaboration_route, "CollaborationRepository", LegacyAuthority)
    monkeypatch.setattr(collaboration_route.settings, "persistence_backend", "postgres")
    monkeypatch.setattr(collaboration_route.settings, "research_orchestration_v1", True)

    assert collaboration_route.get_task_collaboration(
        "legacy_task",
        identity={"ownerId": "owner_1", "workspaceId": "workspace_1", "createdBy": "user_1"},
    ) == expected


def test_v12_collaboration_route_rejects_cross_identity_before_aggregate_load(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = json.loads(V12_FIXTURE.read_text(encoding="utf-8"))["researchTask"]
    aggregate = ResearchTaskV12.model_validate(payload)
    workspace_id = "workspace_1"

    class TaskAuthority:
        def get_task(self, _task_id: str):
            return {
                **aggregate.model_dump(mode="json", by_alias=True),
                "ownerId": "owner_1",
                "workspaceId": workspace_id,
                "createdBy": "user_1",
            }

    class ResearchAuthority:
        def load(self, **_kwargs):
            raise AssertionError("out-of-scope task must not load its aggregate")

    monkeypatch.setattr(collaboration_route, "TaskRepository", TaskAuthority)
    monkeypatch.setattr(collaboration_route, "ResearchOrchestrationRepository", ResearchAuthority)
    monkeypatch.setattr(collaboration_route.settings, "persistence_backend", "postgres")
    monkeypatch.setattr(collaboration_route.settings, "research_orchestration_v1", True)

    with pytest.raises(HTTPException) as error:
        collaboration_route.get_task_collaboration(
            aggregate.task_id,
            identity={
                "ownerId": "other_owner",
                "workspaceId": workspace_id,
                "createdBy": "other_owner",
            },
        )

    assert error.value.status_code == 403


def test_pending_artifact_review_projection_exposes_only_actionable_gate_contract() -> None:
    result = _project(
        run=_run(state="awaiting_human"),
        gates=[{
            "gate_id": "gate_1",
            "gate_kind": "artifact_review",
            "state": "pending",
            "reason": "Evidence needs revision.",
            "artifact_refs": ["artifact_1"],
            "created_at": "2026-07-23T00:10:00Z",
            "resolved_by": None,
        }],
    )

    assert result["status"] == "awaiting_user_confirmation"
    assert result["collaboration"]["pendingDecisions"] == [{
        "gateId": "gate_1",
        "kind": "artifact_review",
        "reason": "Evidence needs revision.",
        "artifactRefs": [],
        "createdAt": "2026-07-23T00:10:00Z",
        "allowedActions": ["start_revision_run", "terminate_run"],
    }]


def test_decision_route_derives_actor_and_scope_from_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    class ScopedRepository:
        def execution_gate_kind(self, **kwargs):
            assert kwargs == {
                "task_id": "task_1", "gate_id": "gate_1", "owner_id": "owner_1", "workspace_id": "workspace_1",
            }
            return "artifact_review"

        def decide_artifact_review_gate(self, **kwargs):
            assert kwargs == {
                "task_id": "task_1",
                "gate_id": "gate_1",
                "owner_id": "owner_1",
                "workspace_id": "workspace_1",
                "decided_by": "user_1",
                "action": "terminate_run",
                "note": "Stop here.",
            }
            return {
                "gateId": "gate_1", "taskId": "task_1", "runId": "run_1",
                "state": "cancelled", "action": "terminate_run", "successorRunId": None,
                "taskStatus": "cancelled", "userMessage": "本轮研究已结束。",
            }

    monkeypatch.setattr(collaboration_route, "CollaborationRepository", ScopedRepository)
    monkeypatch.setattr(collaboration_route.settings, "persistence_backend", "postgres")
    result = collaboration_route.decide_task_collaboration_gate(
        "task_1",
        "gate_1",
        collaboration_route.ExecutionGateDecisionIn(action="terminate_run", note="Stop here."),
        identity={"ownerId": "owner_1", "workspaceId": "workspace_1", "createdBy": "user_1"},
    )

    assert result.action == "terminate_run"
    assert result.taskStatus == "cancelled"
