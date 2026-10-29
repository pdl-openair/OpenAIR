from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.config import settings
from app.models.research_orchestration import (
    AcceptanceCriterion,
    AgentInteractionRecord,
    ActorRef,
    ArtifactV12,
    CandidateEvaluation,
    CapabilityCallV12,
    ConfirmationV12,
    CoverageClaim,
    CoverageTarget,
    DispatchRecord,
    ExecutorRef,
    ExecutionAssignment,
    ExecutionDecision,
    MultiAgentRecipeDefinition,
    PlanningAttempt,
    RegistrySnapshotRef,
    ResearchDesignCoverageReport,
    ResearchDesignRef,
    ResearchDesignVersion,
    ResultV1,
    ResearchTaskV12,
    RuntimeReadinessSnapshot,
    SubstageDependency,
    SubstageInstance,
    SubstageRuntimeState,
    WorkUnit,
)
from app.models.schemas import TaskOut
from app.modules.research_orchestration_repository import (
    FeatureDisabledError,
    PersistenceBackendUnsupportedError,
    ResearchOrchestrationRepository,
)


NOW = datetime(2026, 7, 28, tzinfo=timezone.utc)
FIXTURE_ROOT = Path(__file__).resolve().parents[3] / "packages" / "shared" / "test-fixtures"


def _fixture(name: str) -> dict:
    return json.loads((FIXTURE_ROOT / name).read_text(encoding="utf-8"))


def _task_payload() -> dict:
    return {
        "taskId": "task_contract",
        "revision": 0,
        "status": "draft",
        "executionMode": "stage_confirmation",
        "pendingExecutionMode": None,
        "executionPolicyVersion": "execution-policy.v1",
        "activeResearchDesignRef": None,
        "registrySnapshotRef": {
            "schemaVersion": 1,
            "contentHash": "sha256:" + "a" * 64,
        },
        "planningAttempts": [],
        "researchDesignVersions": [],
        "reentryExecutionInstances": [],
        "substageRuntimeStates": [],
        "executionDecisions": [],
        "workUnits": [],
        "capabilityCalls": [],
        "results": [],
        "artifacts": [],
        "confirmations": [],
        "agentInteractionRecords": [],
        "progressProjection": None,
        "finalResultAuthority": None,
        "scientificStates": [],
        "completionDecisions": [],
        "finalResults": [],
        "startedAt": None,
        "finishedAt": None,
    }


def _full_task_payload() -> dict:
    return deepcopy(_fixture("research-orchestration-v1.2-valid.json")["researchTask"])


def _draft_successor_design(task: dict) -> dict:
    successor = deepcopy(task["researchDesignVersions"][0])
    successor.update(
        {
            "version": 2,
            "supersedesVersion": 1,
            "status": "draft",
            "coverageReport": None,
            "confirmedAt": None,
            "confirmationRef": None,
        }
    )
    return successor


def _confirmed_successor_design(task: dict) -> tuple[dict, dict]:
    successor = deepcopy(task["researchDesignVersions"][0])
    successor.update(
        {
            "version": 2,
            "supersedesVersion": 1,
            "status": "confirmed",
            "confirmationRef": "confirmation_design_contract_v2",
        }
    )
    successor["coverageReport"]["designRef"]["version"] = 2
    confirmation = deepcopy(task["confirmations"][0])
    confirmation.update(
        {
            "confirmationId": "confirmation_design_contract_v2",
            "targetVersion": 2,
        }
    )
    return successor, confirmation


def _clear_work_unit_dependents(task: dict) -> None:
    task["substageRuntimeStates"] = []
    task["capabilityCalls"] = []
    task["results"] = []
    task["artifacts"] = []
    task["agentInteractionRecords"] = []


_RUNTIME_MUTATIONS = _fixture("research-orchestration-v1.2-invalid.json")["runtimeMutationMatrix"]


def _mutated_shared_contract(mutation: dict) -> tuple[type, object]:
    valid = _fixture("research-orchestration-v1.2-valid.json")
    target_models = {
        "researchTask": ResearchTaskV12,
        "multiAgentRecipeDefinition": MultiAgentRecipeDefinition,
        "dispatchRecord": DispatchRecord,
    }
    candidate = deepcopy(valid[mutation["target"]])
    for field, value in mutation.get("setupFields", {}).items():
        if field not in candidate:
            raise AssertionError(f"unknown runtime mutation setup field: {field}")
        candidate[field] = deepcopy(value)
    parent = candidate
    for segment in mutation["path"][:-1]:
        parent = parent[segment]
    final_segment = mutation["path"][-1]
    if mutation["operation"] == "delete":
        del parent[final_segment]
    else:
        value = float("inf") if mutation.get("valueToken") == "positiveInfinity" else mutation["value"]
        parent[final_segment] = deepcopy(value)
    return target_models[mutation["target"]], candidate


def _work_unit(*, source_mode: str, dispatchable: bool, executor_type: str) -> dict:
    return {
        "workUnitId": "wu_contract",
        "idempotencyKey": "workunit:decision_contract:assignment_contract",
        "taskId": "task_contract",
        "executionDecisionId": "decision_contract",
        "assignmentId": "assignment_contract",
        "researchDesignVersion": 1,
        "substageInstanceId": "sub_contract",
        "substageInstanceRevision": 1,
        "substageDefinitionId": "sdr:question_refinement",
        "substageDefinitionVersion": 1,
        "registrySnapshotRef": {"schemaVersion": 1, "contentHash": "sha256:" + "a" * 64},
        "responsibleActorRef": {
            "actorType": "orchestrator",
            "actorId": "legacy_unknown" if source_mode == "legacy_projection" else "openair_orchestrator",
            "definitionVersion": None,
        },
        "executorRef": {
            "executorType": executor_type,
            "executorId": "legacy_unknown" if executor_type == "legacy_unknown" else "literature_reviewer",
            "definitionVersion": None if executor_type == "legacy_unknown" else 1,
        },
        "instruction": "Inspect the supplied evidence.",
        "inputArtifactRefs": [],
        "inputMemoryRefs": [],
        "allowedSkillRefs": [],
        "authorizationSnapshot": {
            "policyVersion": "authorization.v1",
            "allowedMemoryScopes": [],
            "allowedExternalActionKinds": [],
            "confirmationRefs": [],
        },
        "budgetSnapshot": {
            "boundary": {
                "maxModelCalls": 1,
                "maxToolCalls": 0,
                "maxWallTimeSec": 60,
                "maxCostAmount": 1.0,
                "currency": "USD",
            },
            "consumedModelCalls": 0,
            "consumedToolCalls": 0,
            "consumedCostAmount": 0,
        },
        "riskSnapshot": {
            "boundary": {
                "maxRiskClass": "low",
                "prohibitedActions": [],
                "humanGateTriggers": [],
            },
            "evaluatedRiskClass": "low",
            "triggeredHumanGates": [],
        },
        "expectedArtifactTypes": ["evidence_summary"],
        "acceptanceCriteria": [],
        "stopConditions": [],
        "dependencyWorkUnitIds": [],
        "executionGroupId": None,
        "policyVersion": "execution-policy.v1",
        "sourceMode": source_mode,
        "dispatchable": dispatchable,
        "status": "queued",
        "terminalReasonCode": None,
        "dispatchedAt": NOW.isoformat(),
        "frozenAt": NOW.isoformat(),
        "finishedAt": None,
    }


def test_research_task_round_trip_uses_camel_case_and_forbids_extra() -> None:
    payload = _task_payload()
    task = ResearchTaskV12.model_validate(payload)
    assert task.model_dump(mode="json", by_alias=True) == payload

    for field, value in (
        ("creationPlan", {"stages": []}),
        ("stageDetails", []),
        ("selectedCapabilities", [{"id": "agent_should_not_enter_v12"}]),
        ("orchestratorDecision", {"primaryAgentId": "agent_should_not_enter_v12"}),
    ):
        legacy_command = deepcopy(payload)
        legacy_command[field] = value
        with pytest.raises(ValidationError, match=field):
            ResearchTaskV12.model_validate(legacy_command)


def test_all_core_models_round_trip_the_shared_valid_json_fixture() -> None:
    fixture = _fixture("research-orchestration-v1.2-valid.json")
    task = fixture["researchTask"]
    design = task["researchDesignVersions"][0]
    instance = design["instances"][0]
    coverage = design["coverageReport"]
    decision = task["executionDecisions"][0]
    assignment = decision["assignments"][0]
    cases = [
        (ResearchTaskV12, task),
        (PlanningAttempt, task["planningAttempts"][0]),
        (ResearchDesignRef, task["activeResearchDesignRef"]),
        (RegistrySnapshotRef, task["registrySnapshotRef"]),
        (ResearchDesignVersion, design),
        (ResearchDesignCoverageReport, coverage),
        (CoverageTarget, coverage["targets"][0]),
        (CoverageClaim, coverage["claims"][0]),
        (AcceptanceCriterion, instance["acceptanceCriteria"][0]),
        (SubstageInstance, instance),
        (SubstageRuntimeState, task["substageRuntimeStates"][0]),
        (ActorRef, assignment["responsibleActorRef"]),
        (ExecutorRef, assignment["executorRef"]),
        (RuntimeReadinessSnapshot, decision["runtimeReadiness"][0]),
        (CandidateEvaluation, decision["candidateEvaluations"][0]),
        (ExecutionAssignment, assignment),
        (ExecutionDecision, decision),
        (WorkUnit, task["workUnits"][0]),
        (CapabilityCallV12, task["capabilityCalls"][0]),
        (ArtifactV12, task["artifacts"][0]),
        (ConfirmationV12, task["confirmations"][0]),
        (AgentInteractionRecord, task["agentInteractionRecords"][0]),
        (MultiAgentRecipeDefinition, fixture["multiAgentRecipeDefinition"]),
        (DispatchRecord, fixture["dispatchRecord"]),
    ]

    for model, payload in cases:
        assert model.model_validate(payload).model_dump(mode="json", by_alias=True) == payload


def test_formal_result_is_distinct_and_traces_work_unit_call_and_artifact() -> None:
    payload = _full_task_payload()
    result = {
        "resultId": "result_contract_full",
        "workUnitId": payload["workUnits"][0]["workUnitId"],
        "capabilityCallId": payload["capabilityCalls"][0]["capabilityCallId"],
        "storageType": "local",
        "storageScope": payload["taskId"],
        "storageRef": "durable/research-dispatch/dispatch_contract_full/result.json",
        "contentSha256": "b" * 64,
        "mimeType": "application/json",
        "checkStatus": "pending",
        "checkResult": {"reasonCode": "durable_review_required"},
        "sourceRefs": ["material_contract"],
        "artifactRefs": [payload["artifacts"][0]["artifactId"]],
        "scientificStateVersions": [],
        "createdAt": "2026-07-28T00:00:07Z",
    }
    payload["results"] = [result]
    payload["artifacts"][0]["sourceResultId"] = result["resultId"]

    task = ResearchTaskV12.model_validate(payload)

    assert task.results[0].result_id != task.artifacts[0].artifact_id
    assert task.results[0].work_unit_id == task.work_units[0].work_unit_id
    assert task.results[0].capability_call_id == task.capability_calls[0].capability_call_id
    assert task.artifacts[0].source_result_id == task.results[0].result_id

    invalid = deepcopy(result)
    invalid["checkStatus"] = "failed"
    invalid["scientificStateVersions"] = [1]
    with pytest.raises(ValidationError, match="only a passed Result"):
        ResultV1.model_validate(invalid)


def test_shared_invalid_json_fixture_is_rejected() -> None:
    invalid = _fixture("research-orchestration-v1.2-invalid.json")
    for model, key in (
        (RegistrySnapshotRef, "registrySnapshotRef"),
        (PlanningAttempt, "planningAttempt"),
        (DispatchRecord, "dispatchRecord"),
    ):
        with pytest.raises(ValidationError):
            model.model_validate(invalid[key])


@pytest.mark.parametrize("mutation", _RUNTIME_MUTATIONS, ids=lambda row: row["name"])
def test_shared_runtime_mutation_matrix_is_rejected_by_python(mutation: dict) -> None:
    model, candidate = _mutated_shared_contract(mutation)

    with pytest.raises(ValidationError):
        model.model_validate(candidate)


def test_invalid_refs_revisions_and_duplicate_identity_are_rejected() -> None:
    invalid = _task_payload()
    invalid["registrySnapshotRef"]["schemaVersion"] = 0
    with pytest.raises(ValidationError):
        ResearchTaskV12.model_validate(invalid)

    duplicate = _task_payload()
    duplicate["workUnits"] = [
        _work_unit(source_mode="live", dispatchable=True, executor_type="agent"),
        _work_unit(source_mode="live", dispatchable=True, executor_type="agent"),
    ]
    with pytest.raises(ValidationError, match="workUnitId"):
        ResearchTaskV12.model_validate(duplicate)

    fixture = _fixture("research-orchestration-v1.2-valid.json")
    unknown_call_ref = fixture["researchTask"]
    unknown_call_ref["capabilityCalls"][0]["workUnitId"] = "wu_missing"
    with pytest.raises(ValidationError, match="WorkUnit"):
        ResearchTaskV12.model_validate(unknown_call_ref)

    unknown_active_decision = _fixture("research-orchestration-v1.2-valid.json")["researchTask"]
    unknown_active_decision["substageRuntimeStates"][0]["activeExecutionDecisionId"] = "decision_missing"
    with pytest.raises(ValidationError, match="active ExecutionDecision"):
        ResearchTaskV12.model_validate(unknown_active_decision)

    unknown_work_unit_dependency = _fixture("research-orchestration-v1.2-valid.json")["researchTask"]
    unknown_work_unit_dependency["workUnits"][0]["dependencyWorkUnitIds"] = ["wu_missing"]
    with pytest.raises(ValidationError, match="dependency"):
        ResearchTaskV12.model_validate(unknown_work_unit_dependency)

    cyclic_work_unit_dependency = _fixture("research-orchestration-v1.2-valid.json")["researchTask"]
    cyclic_work_unit_dependency["workUnits"][0]["dependencyWorkUnitIds"] = ["wu_contract_full"]
    with pytest.raises(ValidationError, match="cycle"):
        ResearchTaskV12.model_validate(cyclic_work_unit_dependency)

    unknown_runtime_work_unit = _fixture("research-orchestration-v1.2-valid.json")["researchTask"]
    unknown_runtime_work_unit["substageRuntimeStates"][0]["workUnitIds"] = ["wu_missing"]
    with pytest.raises(ValidationError, match="SubstageRuntimeState WorkUnit"):
        ResearchTaskV12.model_validate(unknown_runtime_work_unit)

    unknown_runtime_artifact = _fixture("research-orchestration-v1.2-valid.json")["researchTask"]
    unknown_runtime_artifact["substageRuntimeStates"][0]["artifactRefs"] = ["artifact_missing"]
    with pytest.raises(ValidationError, match="SubstageRuntimeState Artifact"):
        ResearchTaskV12.model_validate(unknown_runtime_artifact)

    unknown_call_output = _fixture("research-orchestration-v1.2-valid.json")["researchTask"]
    unknown_call_output["capabilityCalls"][0]["outputArtifactRefs"] = ["artifact_missing"]
    with pytest.raises(ValidationError, match="output Artifact"):
        ResearchTaskV12.model_validate(unknown_call_output)


def test_design_coverage_dependency_decision_and_recipe_invariants_are_rejected() -> None:
    fixture = _fixture("research-orchestration-v1.2-valid.json")

    invalid_criterion_ref = deepcopy(fixture["researchTask"])
    invalid_criterion_ref["researchDesignVersions"][0]["coverageReport"]["claims"][0][
        "acceptanceCriterionRefs"
    ] = ["criterion_missing"]
    with pytest.raises(ValidationError, match="criterion"):
        ResearchTaskV12.model_validate(invalid_criterion_ref)

    cyclic_design = deepcopy(fixture["researchTask"]["researchDesignVersions"][0])
    cyclic_design["instances"][0]["dependencies"] = [
        {
            "dependencyId": "dependency_self",
            "upstreamInstanceRef": {
                "substageInstanceId": "sub_contract_full",
                "instanceRevision": 1,
            },
            "conditionType": "instance_completed",
            "requiredArtifactTypes": [],
            "confirmationTargetRef": None,
        }
    ]
    with pytest.raises(ValidationError, match="cycle"):
        ResearchDesignVersion.model_validate(cyclic_design)

    half_closed = deepcopy(fixture["researchTask"]["executionDecisions"][0])
    half_closed["outcome"] = "succeeded"
    with pytest.raises(ValidationError, match="closed"):
        ExecutionDecision.model_validate(half_closed)

    invalid_recipe = deepcopy(fixture["multiAgentRecipeDefinition"])
    invalid_recipe["resultAssemblyPolicyTemplate"]["assemblerTemplateKey"] = None
    with pytest.raises(ValidationError, match="assembler"):
        MultiAgentRecipeDefinition.model_validate(invalid_recipe)


def test_live_work_unit_cannot_use_legacy_unknown() -> None:
    with pytest.raises(ValidationError, match="legacy_unknown"):
        WorkUnit.model_validate(
            _work_unit(source_mode="live", dispatchable=True, executor_type="legacy_unknown")
        )

    projected = WorkUnit.model_validate(
        _work_unit(source_mode="legacy_projection", dispatchable=False, executor_type="legacy_unknown")
    )
    assert projected.dispatchable is False
    assert projected.executor_ref == ExecutorRef(
        executorType="legacy_unknown", executorId="legacy_unknown", definitionVersion=None
    )
    assert projected.responsible_actor_ref == ActorRef(
        actorType="orchestrator", actorId="legacy_unknown", definitionVersion=None
    )


@pytest.fixture()
def explicitly_enabled_v1(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "research_orchestration_v1", True)
    yield


def test_disabled_feature_flag_guards_all_domain_writes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "research_orchestration_v1", False)
    repository = ResearchOrchestrationRepository()
    with pytest.raises(FeatureDisabledError):
        repository.save(
            ResearchTaskV12.model_validate(_task_payload()),
            owner_id="owner-a",
            workspace_id="workspace-a",
            expected_task_revision=0,
        )

    monkeypatch.setattr(settings, "research_orchestration_v1", True)
    monkeypatch.setattr(settings, "persistence_backend", "json")
    with pytest.raises(PersistenceBackendUnsupportedError):
        repository.save(
            ResearchTaskV12.model_validate(_task_payload()),
            owner_id="owner-a",
            workspace_id="workspace-a",
            expected_task_revision=0,
            automatic_dispatch=True,
        )


def test_explicit_flag_enable_still_fails_closed_before_any_json_backend_side_effect(
    explicitly_enabled_v1: None,
) -> None:
    connection_calls = 0

    def forbidden_connection():
        nonlocal connection_calls
        connection_calls += 1
        raise AssertionError("JSON fail-closed must not open a persistence transaction")

    runtime_settings = SimpleNamespace(
        research_orchestration_v1=True,
        persistence_backend="json",
        postgres_host="unused",
        postgres_port=5432,
        postgres_user="unused",
        postgres_password="unused",
        postgres_database="unused",
    )
    repository = ResearchOrchestrationRepository(
        connection_factory=forbidden_connection,
        runtime_settings=runtime_settings,
    )
    with pytest.raises(PersistenceBackendUnsupportedError):
        repository.save(
            ResearchTaskV12.model_validate(_task_payload()),
            owner_id="owner-a",
            workspace_id="workspace-a",
            expected_task_revision=0,
            automatic_dispatch=True,
        )
    assert connection_calls == 0


def test_public_task_response_has_one_canonical_design_and_work_unit_contract() -> None:
    assert {
        "activeResearchDesignRef",
        "pendingResearchDesignRef",
        "researchDesignVersions",
        "workUnits",
    }.issubset(TaskOut.model_fields)


def test_reference_payload_is_below_one_mib() -> None:
    encoded = ResearchTaskV12.model_validate(_task_payload()).model_dump_json(by_alias=True).encode("utf-8")
    assert len(encoded) <= 1024 * 1024


@pytest.mark.parametrize(
    "mutation",
    (
        lambda successor: successor["instances"][0].__setitem__("objective", "A later, different objective."),
        lambda successor: successor["instances"][0]["acceptanceCriteria"][0].__setitem__(
            "description", "A later, different acceptance criterion."
        ),
    ),
)
def test_cross_design_reused_instance_revision_is_semantically_immutable(mutation) -> None:
    task = _full_task_payload()
    successor = _draft_successor_design(task)
    mutation(successor)

    for designs in (
        [task["researchDesignVersions"][0], successor],
        [successor, task["researchDesignVersions"][0]],
    ):
        candidate = deepcopy(task)
        candidate["researchDesignVersions"] = designs
        with pytest.raises(ValidationError, match="instanceRevision"):
            ResearchTaskV12.model_validate(candidate)


def test_cross_design_reused_instance_revision_rejects_dependency_changes() -> None:
    task = _full_task_payload()
    successor = _draft_successor_design(task)
    upstream = deepcopy(successor["instances"][0])
    upstream["substageInstanceId"] = "sub_contract_upstream"
    upstream["objective"] = "Prepare the frozen upstream evidence."
    upstream["acceptanceCriteria"][0]["criterionId"] = "criterion_contract_upstream"
    successor["instances"].append(upstream)
    successor["instances"][0]["dependencies"] = [
        {
            "dependencyId": "dependency_contract_upstream",
            "upstreamInstanceRef": {
                "substageInstanceId": "sub_contract_upstream",
                "instanceRevision": 1,
            },
            "conditionType": "instance_completed",
            "requiredArtifactTypes": [],
            "confirmationTargetRef": None,
        }
    ]

    candidate = deepcopy(task)
    candidate["researchDesignVersions"].append(successor)
    with pytest.raises(ValidationError, match="instanceRevision"):
        ResearchTaskV12.model_validate(candidate)


def test_identical_cross_design_instance_snapshot_is_order_independent() -> None:
    task = _full_task_payload()
    successor = _draft_successor_design(task)

    for designs in (
        [task["researchDesignVersions"][0], successor],
        [successor, task["researchDesignVersions"][0]],
    ):
        candidate = deepcopy(task)
        candidate["researchDesignVersions"] = designs
        ResearchTaskV12.model_validate(candidate)


def test_work_unit_uses_its_decision_design_not_a_cross_design_last_writer() -> None:
    task = _full_task_payload()
    successor = _draft_successor_design(task)
    successor["instances"][0]["substageDefinitionId"] = "sdr:later_definition"

    for designs in (
        [task["researchDesignVersions"][0], successor],
        [successor, task["researchDesignVersions"][0]],
    ):
        candidate = deepcopy(task)
        candidate["researchDesignVersions"] = designs
        with pytest.raises(ValidationError, match="instanceRevision|Definition"):
            ResearchTaskV12.model_validate(candidate)


@pytest.mark.parametrize("status", ("draft", "superseded", "cancelled"))
def test_dispatched_decision_cannot_reference_an_unconfirmed_design(status: str) -> None:
    task = _full_task_payload()
    task["activeResearchDesignRef"] = None
    task["researchDesignVersions"][0]["status"] = status

    with pytest.raises(ValidationError, match="confirmed Design"):
        ResearchTaskV12.model_validate(task)


def test_live_queued_work_unit_cannot_reference_an_unconfirmed_design() -> None:
    task = _full_task_payload()
    task["activeResearchDesignRef"] = None
    task["researchDesignVersions"][0]["status"] = "draft"
    task["executionDecisions"][0]["status"] = "proposed"

    with pytest.raises(ValidationError, match="confirmed Design"):
        ResearchTaskV12.model_validate(task)


def test_confirmed_successor_requires_its_own_user_confirmation() -> None:
    task = _full_task_payload()
    successor, _unused_confirmation = _confirmed_successor_design(task)
    original = task["researchDesignVersions"][0]
    successor["confirmationRef"] = original["confirmationRef"]
    original["status"] = "superseded"
    task["researchDesignVersions"].append(successor)
    task["activeResearchDesignRef"]["version"] = successor["version"]

    with pytest.raises(ValidationError, match="matching approved Confirmation"):
        ResearchTaskV12.model_validate(task)


def test_project_completion_requirements_cover_every_target_output() -> None:
    task = _full_task_payload()
    design = task["researchDesignVersions"][0]
    design["understanding"]["targetOutputs"].append("second final output")

    with pytest.raises(ValidationError, match="cover every target output exactly once"):
        ResearchTaskV12.model_validate(task)


def test_project_completion_requirement_cannot_drift_from_target_output() -> None:
    task = _full_task_payload()
    design = task["researchDesignVersions"][0]
    design["projectCompletionRequirements"][0]["description"] = "different meaning"

    with pytest.raises(ValidationError, match="description must match its target output"):
        ResearchTaskV12.model_validate(task)


def test_only_one_active_decision_can_use_a_selection_request_sequence() -> None:
    task = _full_task_payload()
    duplicate = deepcopy(task["executionDecisions"][0])
    duplicate["executionDecisionId"] = "decision_contract_duplicate"
    duplicate["status"] = "selected"
    task["executionDecisions"].append(duplicate)

    with pytest.raises(ValidationError, match="selectionRequestId.*decisionSequence"):
        ResearchTaskV12.model_validate(task)


def test_terminal_history_can_coexist_with_a_later_active_decision_revision() -> None:
    task = _full_task_payload()
    historical = deepcopy(task["executionDecisions"][0])
    historical.update(
        {
            "executionDecisionId": "decision_contract_history",
            "selectionRequestId": "selection_contract_history",
            "decisionSequence": 1,
            "status": "closed",
            "outcome": "succeeded",
            "closedAt": NOW.isoformat(),
        }
    )
    active = task["executionDecisions"][0]
    active.update(
        {
            "selectionRequestId": "selection_contract_revision",
            "decisionSequence": 2,
            "supersedesExecutionDecisionId": "decision_contract_history",
            "revisionReasonCode": "new_evidence",
        }
    )
    task["executionDecisions"] = [historical, active]

    assert ResearchTaskV12.model_validate(task).execution_decisions[1].status.value == "dispatched"


def test_superseded_design_with_closed_decision_and_completed_work_unit_is_valid_history() -> None:
    task = _full_task_payload()
    successor, confirmation = _confirmed_successor_design(task)
    task["researchDesignVersions"][0]["status"] = "superseded"
    task["researchDesignVersions"].append(successor)
    task["confirmations"].append(confirmation)
    task["activeResearchDesignRef"]["version"] = 2
    task["executionDecisions"][0].update(
        {
            "status": "closed",
            "outcome": "succeeded",
            "closedAt": NOW.isoformat(),
        }
    )
    task["workUnits"][0].update(
        {
            "status": "completed",
            "finishedAt": NOW.isoformat(),
        }
    )
    task["substageRuntimeStates"] = []

    validated = ResearchTaskV12.model_validate(task)

    assert validated.active_research_design_ref is not None
    assert validated.active_research_design_ref.version == 2


@pytest.mark.parametrize("decision_status", ("selected", "dispatched"))
def test_superseded_design_cannot_hold_an_active_decision(decision_status: str) -> None:
    task = _full_task_payload()
    task["activeResearchDesignRef"] = None
    task["researchDesignVersions"][0]["status"] = "superseded"
    task["executionDecisions"][0]["status"] = decision_status
    task["workUnits"] = []
    _clear_work_unit_dependents(task)

    with pytest.raises(ValidationError, match="current confirmed Design"):
        ResearchTaskV12.model_validate(task)


@pytest.mark.parametrize("work_unit_status", ("queued", "running", "reviewing"))
def test_superseded_design_cannot_hold_an_active_work_unit(work_unit_status: str) -> None:
    task = _full_task_payload()
    task["activeResearchDesignRef"] = None
    task["researchDesignVersions"][0]["status"] = "superseded"
    task["executionDecisions"][0].update(
        {
            "status": "closed",
            "outcome": "succeeded",
            "closedAt": NOW.isoformat(),
        }
    )
    task["workUnits"][0].update({"status": work_unit_status, "finishedAt": None})
    _clear_work_unit_dependents(task)

    with pytest.raises(ValidationError, match="active WorkUnit.*dispatched Decision.*current confirmed Design"):
        ResearchTaskV12.model_validate(task)


@pytest.mark.parametrize("decision_status", ("proposed", "selected"))
def test_proposed_or_selected_decision_cannot_own_any_work_unit(decision_status: str) -> None:
    task = _full_task_payload()
    task["executionDecisions"][0]["status"] = decision_status
    task["workUnits"][0].update({"status": "queued", "finishedAt": None})
    _clear_work_unit_dependents(task)

    with pytest.raises(ValidationError, match="proposed or selected Decision cannot own a WorkUnit"):
        ResearchTaskV12.model_validate(task)


def test_confirmed_design_dispatched_decision_and_queued_work_unit_are_valid() -> None:
    task = _full_task_payload()
    task["workUnits"][0].update({"status": "queued", "finishedAt": None})
    _clear_work_unit_dependents(task)

    validated = ResearchTaskV12.model_validate(task)

    assert validated.work_units[0].status.value == "queued"


@pytest.mark.parametrize("decision_status", ("closed", "superseded", "cancelled"))
def test_terminal_decision_cannot_hold_an_active_work_unit(decision_status: str) -> None:
    task = _full_task_payload()
    task["executionDecisions"][0].update(
        {
            "status": decision_status,
            "outcome": "succeeded" if decision_status == "closed" else None,
            "closedAt": NOW.isoformat() if decision_status == "closed" else None,
        }
    )
    task["workUnits"][0].update({"status": "queued", "finishedAt": None})
    _clear_work_unit_dependents(task)

    with pytest.raises(ValidationError, match="active WorkUnit.*dispatched Decision.*current confirmed Design"):
        ResearchTaskV12.model_validate(task)


_EVIDENCE_REQUIRED_INTERACTIONS = (
    "handoff_requested",
    "handoff_completed",
    "review_submitted",
    "acceptance_recorded",
    "rejection_recorded",
)


def _interaction_task(interaction_type: str) -> dict:
    task = _full_task_payload()
    interaction = task["agentInteractionRecords"][0]
    interaction["interactionType"] = interaction_type
    interaction["inputArtifactRefs"] = []
    interaction["outputArtifactRefs"] = []
    return task


@pytest.mark.parametrize("interaction_type", _EVIDENCE_REQUIRED_INTERACTIONS)
def test_evidence_bearing_interactions_reject_summary_without_artifact_or_verdict(
    interaction_type: str,
) -> None:
    with pytest.raises(ValidationError, match="Artifact evidence or a structured verdict"):
        ResearchTaskV12.model_validate(_interaction_task(interaction_type))


@pytest.mark.parametrize("interaction_type", _EVIDENCE_REQUIRED_INTERACTIONS)
def test_evidence_bearing_interactions_accept_real_artifact_evidence(interaction_type: str) -> None:
    task = _interaction_task(interaction_type)
    interaction = task["agentInteractionRecords"][0]
    artifact_field = "inputArtifactRefs" if interaction_type == "handoff_requested" else "outputArtifactRefs"
    interaction[artifact_field] = ["artifact_contract_full"]

    ResearchTaskV12.model_validate(task)


@pytest.mark.parametrize(
    ("interaction_type", "verdict"),
    (
        ("handoff_requested", {"verdictType": "handoff", "decision": "approved", "rationaleCode": "ready"}),
        ("handoff_completed", {"verdictType": "handoff", "decision": "approved", "rationaleCode": "delivered"}),
        ("review_submitted", {"verdictType": "review", "decision": "insufficient_evidence", "rationaleCode": "scope_gap"}),
        ("acceptance_recorded", {"verdictType": "acceptance", "decision": "approved", "rationaleCode": "criteria_met"}),
        ("rejection_recorded", {"verdictType": "rejection", "decision": "rejected", "rationaleCode": "criteria_failed"}),
    ),
)
def test_evidence_bearing_interactions_accept_only_legal_structured_verdicts(
    interaction_type: str, verdict: dict
) -> None:
    task = _interaction_task(interaction_type)
    task["agentInteractionRecords"][0]["verdict"] = verdict

    ResearchTaskV12.model_validate(task)


def test_interaction_verdict_type_and_decision_must_match_the_interaction() -> None:
    task = _interaction_task("acceptance_recorded")
    task["agentInteractionRecords"][0]["verdict"] = {
        "verdictType": "acceptance",
        "decision": "rejected",
        "rationaleCode": "not_allowed",
    }

    with pytest.raises(ValidationError, match="acceptance_recorded"):
        ResearchTaskV12.model_validate(task)


def test_artifact_evidence_does_not_bypass_interaction_verdict_compatibility() -> None:
    task = _interaction_task("acceptance_recorded")
    interaction = task["agentInteractionRecords"][0]
    interaction["outputArtifactRefs"] = ["artifact_contract_full"]
    interaction["verdict"] = {
        "verdictType": "review",
        "decision": "approved",
        "rationaleCode": "wrong_verdict_family",
    }

    with pytest.raises(ValidationError, match="acceptance_recorded"):
        ResearchTaskV12.model_validate(task)


def test_non_evidence_interactions_do_not_gain_an_artifact_requirement() -> None:
    task = _interaction_task("challenge_raised")
    ResearchTaskV12.model_validate(task)


@pytest.mark.parametrize("invalid", (1, 0, "true", "false"))
def test_strict_boolean_fields_reject_python_coercions(invalid: object) -> None:
    task = _full_task_payload()
    task["researchDesignVersions"][0]["coverageReport"]["targets"][0]["required"] = invalid

    with pytest.raises(ValidationError):
        ResearchTaskV12.model_validate(task)


@pytest.mark.parametrize("invalid", (True, 1.0, "1"))
def test_strict_integer_fields_reject_boolean_float_and_string_coercions(invalid: object) -> None:
    task = _full_task_payload()
    task["researchDesignVersions"][0]["version"] = invalid

    with pytest.raises(ValidationError):
        ResearchTaskV12.model_validate(task)


def test_strict_timestamp_and_hash_boundaries_and_real_json_validation_entry() -> None:
    raw_fixture = (FIXTURE_ROOT / "research-orchestration-v1.2-valid.json").read_text(encoding="utf-8")
    raw_task = json.dumps(json.loads(raw_fixture)["researchTask"])
    assert ResearchTaskV12.model_validate_json(raw_task).task_id == "task_contract_full"

    numeric_timestamp = _full_task_payload()
    numeric_timestamp["agentInteractionRecords"][0]["createdAt"] = 0
    with pytest.raises(ValidationError):
        ResearchTaskV12.model_validate(numeric_timestamp)

    naive_timestamp = _full_task_payload()
    naive_timestamp["agentInteractionRecords"][0]["createdAt"] = "2026-07-28T00:00:08"
    with pytest.raises(ValidationError):
        ResearchTaskV12.model_validate(naive_timestamp)

    for content_hash in (
        "sha256:" + "A" * 64,
        "sha256:" + "a" * 63,
        "not-a-sha256",
    ):
        with pytest.raises(ValidationError):
            RegistrySnapshotRef.model_validate({"schemaVersion": 1, "contentHash": content_hash})
