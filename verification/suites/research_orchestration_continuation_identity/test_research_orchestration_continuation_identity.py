from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime

from app.models.research_orchestration import (
    InstanceRef,
    ResearchTaskV12,
    TaskExecutionMode,
    execution_instances_for_design,
)
from app.modules import research_execution_decision_service as decision_service
from app.modules.repeatable_definition_reentry import (
    materialize_repeatable_definition_reentries,
    reactivate_repeatable_definition_instances,
)
from app.modules.research_orchestration_repository import ResearchOrchestrationRepository
from tests.support.research_execution import multi_artifact_selection_fixture
from tests.support.research_execution_decision import freeze_prepared_selection_for_test
from tests.support.research_lifecycle import (
    build_confirmed_research_task,
    load_structural_research_task_payload,
)


REPEAT_BOUNDARY = datetime.fromisoformat("2026-07-28T00:00:10+00:00")


def _completed_repeatable_task() -> ResearchTaskV12:
    base = build_confirmed_research_task()
    payload = base.model_dump(mode="json", by_alias=True)
    instance = payload["researchDesignVersions"][0]["instances"][0]
    instance["repeatableReentryAuthority"] = {
        "policyVersion": "repeatable_definition_reentry_v1",
        "triggerDefinitionIds": [instance["substageDefinitionId"]],
    }

    structural = load_structural_research_task_payload()
    decision = deepcopy(structural["executionDecisions"][0])
    decision.update(
        {
            "status": "closed",
            "outcome": "succeeded",
            "closedAt": "2026-07-28T00:00:09Z",
        }
    )
    work = deepcopy(structural["workUnits"][0])
    work.update(
        {
            "status": "completed",
            "finishedAt": "2026-07-28T00:00:07Z",
        }
    )
    artifact = deepcopy(structural["artifacts"][0])
    artifact["artifactState"] = "usable"
    payload["executionDecisions"] = [decision]
    payload["workUnits"] = [work]
    payload["artifacts"] = [artifact]
    payload["capabilityCalls"] = deepcopy(structural["capabilityCalls"])
    payload["agentInteractionRecords"] = []
    payload["substageRuntimeStates"][0].update(
        {
            "status": "completed",
            "activeExecutionDecisionId": None,
            "workUnitIds": [work["workUnitId"]],
            "artifactRefs": [artifact["artifactId"]],
            "updatedAt": "2026-07-28T00:00:09Z",
        }
    )
    payload["scientificStates"].append(
        {
            "scientificStateVersion": 2,
            "researchDesignVersion": 1,
            "scientificFacts": {},
            "executionState": {},
            "sourceRefs": [artifact["artifactId"]],
            "createdAt": "2026-07-28T00:00:09Z",
        }
    )
    payload["status"] = "running"
    payload["startedAt"] = "2026-07-28T00:00:04Z"
    return ResearchTaskV12.model_validate(payload)


class _Cursor:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, *_args, **_kwargs) -> None:
        return None

    def fetchone(self):
        return None


class _Connection:
    def cursor(self) -> _Cursor:
        return _Cursor()


class _UnitOfWork:
    @contextmanager
    def transaction(self):
        yield _Connection()


def test_repository_continuation_reactivates_the_confirmed_task_identity() -> None:
    task = _completed_repeatable_task()
    original_design = task.research_design_versions[0]
    original_instance = original_design.instances[0]
    written: dict[str, ResearchTaskV12] = {}

    repository = ResearchOrchestrationRepository.__new__(
        ResearchOrchestrationRepository
    )
    repository._uow = _UnitOfWork()
    repository._require_write_enabled = lambda: None
    repository._require_postgres = lambda: None
    repository._execution_stop_active = lambda _payload: False
    repository._load_locked_task = lambda _cursor, **_kwargs: (
        task,
        task.model_dump(mode="json", by_alias=True),
    )
    repository._apply_pending_execution_policy_at_boundary = (
        lambda _cursor, **_kwargs: (
            task,
            {"current_mode": TaskExecutionMode.AUTOMATIC_RESEARCH.value},
        )
    )
    repository._reconcile_task_lifecycle_with_frozen_snapshot = (
        lambda current, **_kwargs: current
    )
    repository._write_locked_task = (
        lambda _cursor, _raw, projected: written.setdefault("task", projected)
    )

    directive = repository.prepare_execution_continuation(
        task_id=task.task_id,
        owner_id="owner",
        workspace_id="workspace",
        completed_execution_decision_id=task.execution_decisions[0].execution_decision_id,
        changed_by="owner",
    )

    projected = written["task"]
    projected_design = projected.research_design_versions[0]
    assert directive.action == "start"
    assert projected.reentry_execution_instances == []
    assert len(projected_design.instances) == len(original_design.instances) == 1
    assert len(execution_instances_for_design(projected, projected_design)) == 1
    assert projected_design.instances[0] == original_instance
    assert projected.substage_runtime_states[0].substage_instance_id == (
        original_instance.substage_instance_id
    )
    assert projected.substage_runtime_states[0].status.value == "ready"


def test_repeat_freezes_new_execution_objects_against_the_same_substage() -> None:
    task = _completed_repeatable_task()
    original_design = task.research_design_versions[0]
    original_instance = original_design.instances[0]
    original_decision = task.execution_decisions[0]
    original_work_unit = task.work_units[0]

    repeated = reactivate_repeatable_definition_instances(
        task,
        completed_execution_decision_id=original_decision.execution_decision_id,
        occurred_at=REPEAT_BOUNDARY,
    )
    plan = decision_service.plan_execution_continuation(
        repeated,
        completed_execution_decision_id=original_decision.execution_decision_id,
        execution_mode=TaskExecutionMode.AUTOMATIC_RESEARCH,
    )
    _, resolution, snapshot, facts, readiness = multi_artifact_selection_fixture(
        completion_coverage="progress_capable"
    )
    prepared = decision_service.prepare_selection(
        repeated,
        instance_id=original_instance.substage_instance_id,
        resolution=resolution,
        snapshot_envelope=snapshot,
        facts=facts,
        runtime_readiness=readiness,
        selection_request_id=f"continuation:{original_decision.execution_decision_id}",
        selection_command_hash="continuation-repeat-command",
        expected_task_revision=repeated.revision,
        owner_id="owner",
        workspace_id="workspace",
        decided_at=REPEAT_BOUNDARY,
    )
    materialized = freeze_prepared_selection_for_test(
        prepared,
        repeated,
        prepared.final_tie_candidate_ids[0],
    )

    assert plan.action == "start"
    assert materialized.decision.execution_decision_id != (
        original_decision.execution_decision_id
    )
    assert materialized.decision.decision_sequence == 2
    assert materialized.decision.substage_instance_id == (
        original_instance.substage_instance_id
    )
    assert materialized.decision.substage_instance_revision == (
        original_instance.instance_revision
    )
    assert len(materialized.work_units) == 1
    assert materialized.work_units[0].work_unit_id != original_work_unit.work_unit_id
    assert materialized.work_units[0].substage_instance_id == (
        original_instance.substage_instance_id
    )
    assert materialized.task.research_design_versions[0].instances == (
        original_design.instances
    )
    assert materialized.task.reentry_execution_instances == []
    assert len(materialized.task.execution_decisions) == 2
    assert len(materialized.task.work_units) == 2


def test_historical_execution_record_can_recover_without_becoming_project_task() -> None:
    task = _completed_repeatable_task()
    design = task.research_design_versions[0]
    legacy = materialize_repeatable_definition_reentries(
        task,
        completed_execution_decision_id=task.execution_decisions[0].execution_decision_id,
        occurred_at=REPEAT_BOUNDARY,
    )
    historical = legacy.reentry_execution_instances[0].instance
    payload = legacy.model_dump(mode="json", by_alias=True)
    decision = payload["executionDecisions"][0]
    work_unit = payload["workUnits"][0]
    artifact = payload["artifacts"][0]
    decision.update(
        {
            "substageInstanceId": historical.substage_instance_id,
            "substageInstanceRevision": historical.instance_revision,
        }
    )
    work_unit.update(
        {
            "substageInstanceId": historical.substage_instance_id,
            "substageInstanceRevision": historical.instance_revision,
        }
    )
    for call in payload["capabilityCalls"]:
        call.update(
            {
                "substageInstanceId": historical.substage_instance_id,
            }
        )
    artifact["substageInstanceId"] = historical.substage_instance_id
    formal_runtime, historical_runtime = payload["substageRuntimeStates"]
    formal_runtime.update({"workUnitIds": [], "artifactRefs": []})
    historical_runtime.update(
        {
            "status": "blocked_recoverable",
            "workUnitIds": [work_unit["workUnitId"]],
            "artifactRefs": [artifact["artifactId"]],
            "statusReasonCode": "execution_interrupted",
        }
    )
    interrupted = ResearchTaskV12.model_validate(payload)
    _, resolution, snapshot, facts, readiness = multi_artifact_selection_fixture(
        completion_coverage="progress_capable"
    )
    historical_ref = InstanceRef(
        substageInstanceId=historical.substage_instance_id,
        instanceRevision=historical.instance_revision,
    )
    resolution = resolution.model_copy(update={"instance_ref": historical_ref})

    class Repository:
        def load(self, **_kwargs):
            return interrupted

        def load_registry_snapshot(self, _content_hash):
            return snapshot

        def load_execution_workflow_lineage(self, **_kwargs):
            return None

        def load_catalog_execution_binding(self, **_kwargs):
            return None

        def commit_prepared_selection(self, prepared, materialize):
            materialized = materialize(
                interrupted,
                prepared.final_tie_candidate_ids[0],
            )
            return decision_service.SelectionCommitResult(
                task=materialized.task,
                decision=materialized.decision,
                replayed=False,
            )

    service = decision_service.ResearchExecutionDecisionService(
        repository=Repository(),
        resolver=None,
        facts_source=None,
        now=lambda: REPEAT_BOUNDARY,
    )

    def validate_prepared(prepared, current):
        validated = {}
        for candidate_id in prepared.final_tie_candidate_ids:
            execution = decision_service.prepare_selection_execution(
                prepared,
                current,
                candidate_id,
            )
            validated[candidate_id] = decision_service.ValidatedSelectionPreparation(
                execution,
                execution,
            )
        return validated

    service._validate_prepared_selection_inputs = validate_prepared
    recovered = service.materialize_recovery(
        decision_service.RecoveryMaterializationRequest(
            recovery_identity="historical-recovery",
            directive_kind="research_revision",
            task_id=interrupted.task_id,
            original_execution_decision_id=interrupted.execution_decisions[
                0
            ].execution_decision_id,
            original_work_unit_id=interrupted.work_units[0].work_unit_id,
            selection_request_id="historical-recovery-selection",
            expected_task_revision=interrupted.revision,
            owner_id="owner",
            workspace_id="workspace",
        ),
        resolution=resolution,
        facts=facts,
        runtime_readiness=readiness,
    )

    assert len(recovered.task.research_design_versions[0].instances) == len(
        design.instances
    )
    assert execution_instances_for_design(recovered.task, design) == tuple(
        design.instances
    )
    assert len(recovered.task.reentry_execution_instances) == 1
    assert recovered.decision.execution_decision_id != decision["executionDecisionId"]
    assert recovered.decision.substage_instance_id == historical.substage_instance_id
    assert recovered.task.work_units[-1].substage_instance_id == (
        historical.substage_instance_id
    )


def test_legacy_reentry_record_cannot_enlarge_next_task_authority() -> None:
    task = _completed_repeatable_task()
    design = task.research_design_versions[0]
    legacy = materialize_repeatable_definition_reentries(
        task,
        completed_execution_decision_id=task.execution_decisions[0].execution_decision_id,
        occurred_at=REPEAT_BOUNDARY,
    )

    assert len(legacy.reentry_execution_instances) == 1
    assert legacy.reentry_execution_instances[0].instance.substage_instance_id != (
        design.instances[0].substage_instance_id
    )
    assert execution_instances_for_design(legacy, design) == tuple(design.instances)
    plan = decision_service.plan_execution_continuation(
        legacy,
        completed_execution_decision_id=task.execution_decisions[0].execution_decision_id,
        execution_mode=TaskExecutionMode.AUTOMATIC_RESEARCH,
    )
    assert plan.action == "none"
