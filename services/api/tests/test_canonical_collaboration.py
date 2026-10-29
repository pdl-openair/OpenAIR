from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.models.research_orchestration import ResearchTaskV12
from app.modules.canonical_collaboration import (
    CanonicalCollaborationAuthorityMismatch,
    project_canonical_collaboration,
)
from app.modules.artifact_catalog import is_artifact_user_visible
from app.modules.collaboration_lifecycle import project_research_task_lifecycle


FIXTURE = (
    Path(__file__).resolve().parents[3]
    / "packages"
    / "shared"
    / "test-fixtures"
    / "research-orchestration-v1.2-valid.json"
)


def test_prestart_block_lifecycle_stays_awaiting_confirmation_without_execution_decision() -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))["researchTask"]
    source = ResearchTaskV12.model_validate(payload)
    blocked = source.model_copy(update={
        "status": "awaiting_confirmation",
        "started_at": None,
        "execution_decisions": [],
        "work_units": [],
        "capability_calls": [],
        "artifacts": [],
        "agent_interaction_records": [],
    })

    projection = project_research_task_lifecycle(blocked)

    assert projection["status"] == "awaiting_confirmation"
    assert projection["runId"] is None
    assert projection["entries"] == []


def _authority(task, *, items=None, allowed=None) -> dict:
    return {
        "schemaVersion": "workbench_user_actions_v1",
        "lifecycleKind": "canonical_v1_2",
        "taskId": task.task_id,
        "revision": task.revision,
        "allowedActions": list(allowed or []),
        "items": list(items or []),
    }


def _visible_ids(task) -> frozenset[str]:
    return frozenset(row.artifact_id for row in task.artifacts)


def _unit(
    work_unit_id: str,
    assignment_id: str,
    *,
    status: str = "running",
    dependencies: list[str] | None = None,
    reason: str | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        work_unit_id=work_unit_id,
        assignment_id=assignment_id,
        execution_decision_id="decision_1",
        research_design_version=1,
        substage_instance_id="substage_1",
        substage_instance_revision=1,
        instruction=f"objective {work_unit_id}",
        responsible_actor_ref=SimpleNamespace(actor_id=f"actor_{work_unit_id}", actor_type="agent"),
        status=status,
        execution_group_id=None,
        dependency_work_unit_ids=list(dependencies or []),
        input_artifact_refs=[],
        terminal_reason_code=reason,
        dispatched_at="2026-08-06T00:00:00Z",
        finished_at=None,
    )


def _task(
    *,
    primary_status: str = "running",
    artifact_state: str | None = None,
    materialize_reviewer: bool = False,
    primary_reason: str | None = None,
    task_status: str = "running",
) -> SimpleNamespace:
    primary_assignment = SimpleNamespace(assignment_id="assignment_primary", role="primary")
    reviewer_assignment = SimpleNamespace(assignment_id="assignment_reviewer", role="reviewer")
    assignments = [primary_assignment, reviewer_assignment]
    decision = SimpleNamespace(
        execution_decision_id="decision_1",
        decision_sequence=1,
        decided_at="2026-08-06T00:00:00Z",
        assignments=assignments,
        status="dispatched",
        outcome=None,
        research_design_version=1,
        substage_instance_id="substage_1",
        substage_instance_revision=1,
        closed_at=None,
    )
    units = [
        _unit(
            "wu_primary",
            "assignment_primary",
            status=primary_status,
            reason=primary_reason,
        )
    ]
    if materialize_reviewer:
        units.append(
            _unit(
                "wu_reviewer",
                "assignment_reviewer",
                status="queued",
                dependencies=["wu_primary"],
            )
        )
    artifacts = []
    if artifact_state is not None:
        artifacts.append(
            SimpleNamespace(
                artifact_id="artifact_1",
                work_unit_id="wu_primary",
                artifact_type="analysis_report",
                artifact_state=artifact_state,
                version=1,
                source_capability_call_id="call_1",
            )
        )
    return SimpleNamespace(
        task_id="task_1",
        revision=8,
        status=task_status,
        active_research_design_ref=SimpleNamespace(research_design_id="design_1", version=1),
        research_design_versions=[
            SimpleNamespace(
                research_design_id="design_1",
                version=1,
                instances=[SimpleNamespace(substage_instance_id="substage_1", instance_revision=1)],
            )
        ],
        substage_runtime_states=[
            SimpleNamespace(substage_instance_id="substage_1", instance_revision=1, status="running")
        ],
        execution_decisions=[decision],
        work_units=units,
        artifacts=artifacts,
        agent_interaction_records=[],
    )


def test_projects_current_decision_as_canonical_collaboration_snapshot() -> None:
    task = ResearchTaskV12.model_validate(json.loads(FIXTURE.read_text(encoding="utf-8"))["researchTask"])

    snapshot = project_canonical_collaboration(
        task, action_authority=_authority(task), visible_artifact_ids=_visible_ids(task)
    )

    assert snapshot["schemaVersion"] == "canonical_collaboration_v1"
    assert snapshot["lifecycleKind"] == "canonical_v1_2"
    assert snapshot["taskId"] == task.task_id
    assert snapshot["revisionFence"] == {
        "kind": "research_task_v1_2_revision",
        "revision": task.revision,
    }
    assert snapshot["sourceIdentity"] == task.execution_decisions[0].execution_decision_id
    assert snapshot["executionDecision"]["executionDecisionId"] == snapshot["sourceIdentity"]
    assert {row["workUnitId"] for row in snapshot["workUnits"]} == {
        row.work_unit_id for row in task.work_units
        if row.execution_decision_id == snapshot["sourceIdentity"]
    }
    assert all(row["workUnitId"] in {unit["workUnitId"] for unit in snapshot["workUnits"]} for row in snapshot["artifacts"])


def test_dependency_and_materialized_reviewer_are_projected_from_durable_relations() -> None:
    task = _task(artifact_state="needs_review", materialize_reviewer=True)

    snapshot = project_canonical_collaboration(
        task, action_authority=_authority(task), visible_artifact_ids=_visible_ids(task)
    )
    by_id = {row["workUnitId"]: row for row in snapshot["workUnits"]}

    assert by_id["wu_primary"]["waiting"]["kind"] == "system_review"
    assert by_id["wu_primary"]["reviewerWorkUnitIds"] == ["wu_reviewer"]
    assert by_id["wu_reviewer"]["waiting"] == {
        "kind": "dependency",
        "reason": "正在等待前置研究工作完成。",
        "reasonCode": "dependency_pending",
        "blockingWorkUnitIds": ["wu_primary"],
    }


def test_planned_reviewer_is_not_shown_before_work_unit_materialization() -> None:
    task = _task(artifact_state="needs_review", materialize_reviewer=False)

    snapshot = project_canonical_collaboration(
        task, action_authority=_authority(task), visible_artifact_ids=_visible_ids(task)
    )

    assert snapshot["workUnits"][0]["waiting"]["kind"] == "system_review"
    assert snapshot["workUnits"][0]["reviewerWorkUnitIds"] == []


def test_waiting_user_action_and_allowed_actions_use_authority_snapshot_exactly() -> None:
    task = _task(artifact_state="needs_input")
    authority = _authority(
        task,
        allowed=["supplement_input", "stop"],
        items=[
            {
                "itemId": "provide-input:wu_primary",
                "kind": "provide_input",
                "reason": "当前材料不足，请补充研究材料。",
                "workUnitId": "wu_primary",
                "allowedActions": ["supplement_input"],
                "commandContexts": {},
            }
        ],
    )

    snapshot = project_canonical_collaboration(
        task, action_authority=authority, visible_artifact_ids=_visible_ids(task)
    )

    assert snapshot["workUnits"][0]["waiting"]["kind"] == "user_action"
    assert snapshot["allowedActions"] == ["supplement_input", "stop"]
    assert snapshot["userActionItems"][0]["allowedActions"] == ["supplement_input"]
    assert snapshot["userActionItems"][0]["artifactRefs"] == ["artifact_1"]


def test_external_unknown_is_recoverable_and_never_invents_retry() -> None:
    task = _task(primary_status="external_result_unknown")
    authority = _authority(
        task,
        items=[
            {
                "itemId": "external-result:wu_primary",
                "kind": "external_result_unknown",
                "reason": "需要核对外部执行结果。",
                "workUnitId": "wu_primary",
                "allowedActions": ["reconcile_external_result"],
                "commandContexts": {},
            }
        ],
    )

    snapshot = project_canonical_collaboration(
        task, action_authority=authority, visible_artifact_ids=_visible_ids(task)
    )

    assert snapshot["workUnits"][0]["waiting"]["kind"] == "blocked_recoverable"
    assert snapshot["userActionItems"][0]["allowedActions"] == ["reconcile_external_result"]
    assert "explicit_retry" not in snapshot["userActionItems"][0]["allowedActions"]


def test_runtime_and_budget_blocks_are_mapped_to_safe_product_semantics() -> None:
    task = _task(primary_status="blocked", primary_reason="budget_exceeded")

    snapshot = project_canonical_collaboration(
        task, action_authority=_authority(task), visible_artifact_ids=_visible_ids(task)
    )

    waiting = snapshot["workUnits"][0]["waiting"]
    assert waiting["kind"] == "runtime_unavailable"
    assert waiting["reason"] == "当前资源预算不足，执行已安全暂停。"
    assert "budget_exceeded" not in waiting["reason"]


def test_terminal_task_does_not_expose_conflicting_waits_or_actions() -> None:
    task = _task(primary_status="cancelled", task_status="cancelled")

    snapshot = project_canonical_collaboration(
        task, action_authority=_authority(task), visible_artifact_ids=_visible_ids(task)
    )

    assert snapshot["workUnits"][0]["waiting"] is None
    assert snapshot["allowedActions"] == []
    assert snapshot["userActionItems"] == []


def test_action_authority_must_match_task_and_revision() -> None:
    task = _task()
    authority = _authority(task)
    authority["revision"] = task.revision - 1

    with pytest.raises(CanonicalCollaborationAuthorityMismatch):
        project_canonical_collaboration(task, action_authority=authority)


def test_all_collaboration_artifact_fields_use_catalog_visibility_authority() -> None:
    task = _task(artifact_state=None)
    rows = [
        ("visible-usable", "usable", "user"),
        ("visible-review", "needs_review", "public"),
        ("visible-historical", "usable", "user"),
        ("hidden", "usable", "hidden"),
        ("internal", "usable", "internal"),
        ("diagnostic", "needs_review", "diagnostic"),
        ("audit", "usable", "audit"),
        ("developer", "usable", "developer"),
    ]
    task.artifacts = [
        SimpleNamespace(
            artifact_id=artifact_id,
            work_unit_id="wu_historical" if artifact_id == "visible-historical" else "wu_primary",
            artifact_type="analysis_report",
            artifact_state=state,
            version=1,
            source_capability_call_id=f"call-{artifact_id}",
        )
        for artifact_id, state, _visibility in rows
    ]
    all_ids = [artifact_id for artifact_id, _state, _visibility in rows]
    task.work_units[0].input_artifact_refs = list(all_ids)
    task.agent_interaction_records = [
        SimpleNamespace(
            execution_decision_id="decision_1",
            interaction_id="handoff-1",
            interaction_type="handoff_completed",
            source_work_unit_id="wu_primary",
            target_work_unit_id="wu_primary",
            input_artifact_refs=list(all_ids),
            output_artifact_refs=[],
            summary="handoff",
            verdict=None,
            created_at="2026-08-06T00:01:00Z",
        ),
        SimpleNamespace(
            execution_decision_id="decision_1",
            interaction_id="review-1",
            interaction_type="review_submitted",
            source_work_unit_id="wu_primary",
            target_work_unit_id="wu_primary",
            input_artifact_refs=[],
            output_artifact_refs=list(all_ids),
            summary="review",
            verdict=SimpleNamespace(decision="approved", rationale_code="checked"),
            created_at="2026-08-06T00:02:00Z",
        ),
    ]
    visible_ids = frozenset(
        artifact_id
        for artifact_id, _state, visibility in rows
        if is_artifact_user_visible({"artifactId": artifact_id, "artifactVisibility": visibility})
    )
    authority = _authority(
        task,
        items=[{
            "itemId": "provide-input:wu_primary",
            "kind": "provide_input",
            "reason": "Provide the current input.",
            "workUnitId": "wu_primary",
            "artifactRefs": list(all_ids),
            "allowedActions": ["supplement_input"],
        }],
    )

    snapshot = project_canonical_collaboration(
        task,
        action_authority=authority,
        visible_artifact_ids=visible_ids,
    )

    assert {row["artifactId"] for row in snapshot["artifacts"]} == visible_ids
    assert snapshot["sourceIdentity"] == "decision_1"
    by_id = {row["artifactId"]: row for row in snapshot["artifacts"]}
    assert by_id["visible-usable"]["artifactState"] == "usable"
    assert by_id["visible-review"]["artifactState"] == "needs_review"
    unit = snapshot["workUnits"][0]
    assert set(unit["inputArtifactRefs"]) == visible_ids
    assert set(unit["outputArtifactRefs"]) == {"visible-usable", "visible-review"}
    assert unit["needsReviewArtifactRefs"] == ["visible-review"]
    assert set(snapshot["handoffs"][0]["artifactRefs"]) == visible_ids
    assert set(snapshot["reviews"][0]["artifactRefs"]) == visible_ids
    assert set(snapshot["userActionItems"][0]["artifactRefs"]) == visible_ids
    hidden_ids = set(all_ids) - set(visible_ids)
    assert all(artifact_id not in str(snapshot) for artifact_id in hidden_ids)


def test_collaboration_artifacts_fail_closed_and_visible_result_survives_failure_stop() -> None:
    task = _task(primary_status="failed", task_status="cancelled", artifact_state="usable")
    task.work_units[0].input_artifact_refs = ["artifact_1"]

    closed = project_canonical_collaboration(task, action_authority=_authority(task))
    assert closed["artifacts"] == []
    assert closed["workUnits"][0]["inputArtifactRefs"] == []
    assert closed["workUnits"][0]["outputArtifactRefs"] == []

    visible = project_canonical_collaboration(
        task,
        action_authority=_authority(task),
        visible_artifact_ids={"artifact_1"},
    )
    assert visible["taskStatus"] == "cancelled"
    assert visible["workUnits"][0]["status"] == "failed"
    assert visible["artifacts"] == [{
        "artifactId": "artifact_1",
        "workUnitId": "wu_primary",
        "artifactType": "analysis_report",
        "artifactState": "usable",
        "version": 1,
        "sourceCapabilityCallId": "call_1",
    }]


def test_canonical_route_reads_research_and_action_authority(monkeypatch) -> None:
    pytest.importorskip("bcrypt")
    pytest.importorskip("psycopg")
    from app.routes import collaboration as collaboration_route

    task = ResearchTaskV12.model_validate(json.loads(FIXTURE.read_text(encoding="utf-8"))["researchTask"])

    class Repository:
        def load(self, *, task_id: str, owner_id: str, workspace_id: str):
            assert (task_id, owner_id, workspace_id) == (task.task_id, "owner_1", "workspace_1")
            return task

    class ControlRepository:
        def get_task(self, task_id: str):
            assert task_id == task.task_id
            return {
                "taskId": task.task_id,
                "ownerId": "owner_1",
                "workspaceId": "workspace_1",
                "revision": task.revision,
                "stopReceipt": None,
            }

    class PublicArtifactRepository:
        def list_by_scope(self, owner_id: str, workspace_id: str, **kwargs):
            assert (owner_id, workspace_id) == ("owner_1", "workspace_1")
            assert kwargs["source_task_id"] == task.task_id
            rows = [{"id": row.artifact_id} for row in task.artifacts]
            return rows, len(rows)

    def action_authority(*, task_payload, owner_id, workspace_id, actor_id):
        assert task_payload["taskId"] == task.task_id
        assert task_payload["revision"] == task.revision
        assert (owner_id, workspace_id, actor_id) == ("owner_1", "workspace_1", "user_1")
        return _authority(task)

    monkeypatch.setattr(collaboration_route, "ResearchOrchestrationRepository", Repository)
    monkeypatch.setattr(collaboration_route, "TaskRepository", ControlRepository)
    monkeypatch.setattr(collaboration_route, "ArtifactRepository", PublicArtifactRepository)
    monkeypatch.setattr(collaboration_route, "project_workbench_user_actions", action_authority)
    monkeypatch.setattr(collaboration_route.settings, "persistence_backend", "postgres")
    monkeypatch.setattr(collaboration_route.settings, "research_orchestration_v1", True)

    snapshot = collaboration_route.get_canonical_task_collaboration(
        task.task_id,
        identity={"ownerId": "owner_1", "workspaceId": "workspace_1", "createdBy": "user_1"},
    )

    assert snapshot["schemaVersion"] == "canonical_collaboration_v1"


def test_collaboration_visibility_repository_failure_fails_closed(monkeypatch) -> None:
    pytest.importorskip("bcrypt")
    pytest.importorskip("psycopg")
    from app.routes import collaboration as collaboration_route

    class FailingArtifactRepository:
        def list_by_scope(self, *args, **kwargs):
            raise RuntimeError("catalog unavailable")

    monkeypatch.setattr(collaboration_route, "ArtifactRepository", FailingArtifactRepository)

    assert collaboration_route._visible_artifact_ids_for_collaboration(
        task_id="task-1",
        owner_id="owner-1",
        workspace_id="workspace-1",
    ) == frozenset()
