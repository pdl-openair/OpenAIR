from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timezone
from importlib import import_module
from pathlib import Path
from threading import Lock
from typing import Any

import pytest
from app.models.research_orchestration import ResearchTaskV12
from app.modules.research_orchestration_repository import TaskRevisionConflictError
from pydantic import ValidationError

CASES = json.loads(
    (Path(__file__).parent / "fixtures" / "research_design_v1_cases.json").read_text(encoding="utf-8")
)
SNAPSHOT = {
    "schemaVersion": 1,
    "contentHash": "sha256:" + "b" * 64,
}


def _service_module():
    return import_module("app.modules.research_design_service")


class FakeDefinitionSource:
    def __init__(self) -> None:
        self.definitions = {
            "sdr:evidence_synthesis": (1, "solution_design"),
            "sdr:source_collection": (3, "problem_modeling"),
            "opentrajectory:progressive_reading": (1, "execution_validation"),
        }

    def snapshot_ref(self) -> dict[str, Any]:
        return deepcopy(SNAPSHOT)

    def resolve(self, definition_id: str):
        module = _service_module()
        if definition_id not in self.definitions:
            return None
        version, stage = self.definitions[definition_id]
        return module.ResolvedDefinition(
            definition_id=definition_id,
            definition_version=version,
            stage_key=stage,
        )

    def definition_is_eligible_for_workflow_admission(self, definition_id, admission):
        return definition_id in self.definitions

    def system_recovery_target(self, reason_code: str):
        if reason_code != "semantic_material_insufficient":
            return None
        return {
            "reasonCode": reason_code,
            "definitionId": "opentrajectory:progressive_reading",
            "objective": "Recover source material needed by the blocked research step.",
            "expectedArtifactTypes": ["source_materials"],
        }


class FakeRepository:
    def __init__(self, task: ResearchTaskV12) -> None:
        self.task = task
        self.saves = 0
        self.lifecycle_facts: list[object] = []
        self.lock = Lock()
        self.workflow_admission = None

    def load(self, *, task_id: str, owner_id: str, workspace_id: str) -> ResearchTaskV12:
        assert task_id == self.task.task_id
        return self.task.model_copy(deep=True)

    def load_planning_workflow_admission(
        self, *, task_id: str, owner_id: str, workspace_id: str
    ):
        assert task_id == self.task.task_id
        return self.workflow_admission

    def save(
        self,
        task: ResearchTaskV12,
        *,
        owner_id: str,
        workspace_id: str,
        expected_task_revision: int,
        **_kwargs: object,
    ) -> ResearchTaskV12:
        with self.lock:
            lifecycle_fact = _kwargs.get("lifecycle_fact")
            if lifecycle_fact is not None:
                self.lifecycle_facts.append(lifecycle_fact)
            if self.task.revision != expected_task_revision or task.revision != expected_task_revision:
                raise TaskRevisionConflictError("task_revision_conflict")
            payload = task.model_dump(mode="json", by_alias=True)
            payload["revision"] = expected_task_revision + 1
            self.task = ResearchTaskV12.model_validate(payload)
            self.saves += 1
            return self.task.model_copy(deep=True)


def _clock() -> datetime:
    return datetime(2026, 7, 29, 1, 2, 3, tzinfo=timezone.utc)


def _service_and_repo():
    module = _service_module()
    task = module.new_research_task(task_id="task_design", registry_snapshot_ref=SNAPSHOT)
    repository = FakeRepository(task)
    service = module.ResearchDesignService(
        repository=repository,
        definition_source=FakeDefinitionSource(),
        clock=_clock,
    )
    return module, service, repository


def _plan(service, repository, proposal: dict[str, Any], request_id: str = "request_one"):
    module = _service_module()
    observed: list[tuple[str, str]] = []

    def provider(idempotency_key: str):
        attempt = repository.task.planning_attempts[-1]
        observed.append((attempt.status.value, idempotency_key))
        return module.PlannerProviderResult(
            proposal=deepcopy(proposal),
            provider_call_ref=f"provider:{request_id}",
        )

    result = service.plan(
        task_id="task_design",
        owner_id="owner",
        workspace_id="workspace",
        request_id=request_id,
        expected_task_revision=repository.task.revision,
        provider=provider,
    )
    assert observed == [("started", f"planner:{request_id}")]
    return result


def test_planned_design_owns_project_completion_requirements() -> None:
    _, service, repository = _service_and_repo()
    planned = _plan(service, repository, CASES["simple"])
    design = planned.research_design_versions[-1]
    target_outputs = CASES["simple"]["understanding"]["targetOutputs"]

    assert [
        row.target_output_ref for row in design.project_completion_requirements
    ] == [
        f"understanding.targetOutputs[{index}]"
        for index in range(len(target_outputs))
    ]
    assert [
        row.description for row in design.project_completion_requirements
    ] == target_outputs
    assert not any(
        dimension in {row.description for row in design.project_completion_requirements}
        for dimension in design.understanding.auxiliary_research_dimensions
    )
    assert len({
        row.completion_requirement_id for row in design.project_completion_requirements
    }) == len(target_outputs)
    assert design.workflow_provenance_ref is None


def test_workflow_provenance_is_frozen_into_research_design() -> None:
    _, service, repository = _service_and_repo()
    repository.workflow_admission = {
        "workflowId": "workflow_test",
        "workflowVersion": 3,
        "workflowContentHash": "a" * 64,
        "skillPack": "test-pack",
        "workflowDefinitionSnapshot": {"definition": {}},
    }

    planned = _plan(service, repository, CASES["simple"])
    provenance = planned.research_design_versions[-1].workflow_provenance_ref

    assert provenance is not None
    assert provenance.workflow_id == "workflow_test"
    assert provenance.workflow_version == 3
    assert provenance.content_hash == f"sha256:{'a' * 64}"


def test_incomplete_workflow_provenance_cannot_form_research_design() -> None:
    module, service, repository = _service_and_repo()
    repository.workflow_admission = {
        "workflowId": "workflow_test",
        "skillPack": "test-pack",
        "workflowDefinitionSnapshot": {"definition": {}},
    }

    with pytest.raises(module.DefinitionResolutionError, match="provenance"):
        _plan(service, repository, CASES["simple"])


def test_semantic_gap_recovery_creates_draft_then_requires_direct_confirmation() -> None:
    module, service, repository = _service_and_repo()
    planned = _plan(service, repository, CASES["simple"])
    design = planned.research_design_versions[-1]
    confirmed = service.confirm(
        task_id="task_design", owner_id="owner", workspace_id="workspace", actor_id="researcher",
        design_id=design.research_design_id, version=design.version, confirmation_id="confirmed_v1",
        decision="approved", expected_task_revision=planned.revision,
    )
    assert repository.lifecycle_facts == [module.ResearchDesignLifecycleFact.DESIGN_APPROVED]
    payload = confirmed.model_dump(mode="json", by_alias=True)
    payload["executionMode"] = "automatic_research"
    payload["startedAt"] = "2026-01-01T00:00:00Z"
    payload["substageRuntimeStates"][0].update({
        "status": "skipped", "statusReasonCode": "semantic_material_insufficient",
    })
    repository.task = ResearchTaskV12.model_validate(payload)
    historical_runtime = list(repository.task.substage_runtime_states)

    first = service.recover_confirmed_semantic_material_gap(
        task_id="task_design", owner_id="owner", workspace_id="workspace",
        blocked_instance_id=design.instances[0].substage_instance_id,
        gap_reason_code="semantic_material_insufficient", expected_task_revision=repository.task.revision,
    )
    assert repository.lifecycle_facts == [
        module.ResearchDesignLifecycleFact.DESIGN_APPROVED,
        module.ResearchDesignLifecycleFact.SEMANTIC_MATERIAL_GAP_RECOVERY_DRAFTED,
    ]
    replay = service.recover_confirmed_semantic_material_gap(
        task_id="task_design", owner_id="owner", workspace_id="workspace",
        blocked_instance_id=design.instances[0].substage_instance_id,
        gap_reason_code="semantic_material_insufficient", expected_task_revision=first.revision,
    )

    v1, v2 = first.research_design_versions
    assert v1.status.value == "confirmed"
    assert v1.confirmation_ref == "confirmed_v1"
    assert v2.status.value == "draft"
    assert v2.confirmation_ref is None
    assert v2.confirmed_at is None
    assert v2.coverage_report is not None
    assert v2.coverage_report.evaluation_mode.value == "deterministic"
    assert v2.coverage_report.semantic_evaluation_ref is None
    assert v2.coverage_report.status.value == "passed_structural"
    assert first.active_research_design_ref.version == 1
    assert first.status.value == "awaiting_confirmation"
    assert first.substage_runtime_states == historical_runtime
    assert len(first.confirmations) == 1
    assert replay == first

    recovery = [
        row for row in v2.instances
        if row.substage_definition_id == "opentrajectory:progressive_reading"
    ]
    assert len(recovery) == 1

    activated = service.confirm(
        task_id="task_design", owner_id="owner", workspace_id="workspace", actor_id="researcher",
        design_id=v2.research_design_id, version=v2.version, confirmation_id="confirmed_v2",
        decision="approved", expected_task_revision=first.revision,
    )
    assert repository.lifecycle_facts == [
        module.ResearchDesignLifecycleFact.DESIGN_APPROVED,
        module.ResearchDesignLifecycleFact.SEMANTIC_MATERIAL_GAP_RECOVERY_DRAFTED,
        module.ResearchDesignLifecycleFact.DESIGN_APPROVED,
    ]
    active_v1, active_v2 = activated.research_design_versions
    assert active_v1.status.value == "superseded"
    assert active_v2.status.value == "confirmed"
    assert active_v2.confirmation_ref == "confirmed_v2"
    assert active_v2.confirmed_at is not None
    assert activated.active_research_design_ref.version == 2
    assert activated.status.value == "running"
    assert activated.substage_runtime_states[: len(historical_runtime)] == historical_runtime
    current_keys = {
        (row.substage_instance_id, row.instance_revision)
        for row in active_v2.instances
    }
    runtime_keys = {
        (row.substage_instance_id, row.instance_revision)
        for row in activated.substage_runtime_states
    }
    assert current_keys.issubset(runtime_keys)


def test_recovery_draft_cannot_expand_confirmed_task_limit() -> None:
    module, service, repository = _service_and_repo()
    proposal = deepcopy(CASES["simple"])
    proposal["scopeBoundary"]["maxSubstageInstances"] = 1
    planned = _plan(service, repository, proposal)
    design = planned.research_design_versions[-1]
    confirmed = service.confirm(
        task_id="task_design", owner_id="owner", workspace_id="workspace", actor_id="researcher",
        design_id=design.research_design_id, version=design.version, confirmation_id="confirmed_limit_v1",
        decision="approved", expected_task_revision=planned.revision,
    )
    payload = confirmed.model_dump(mode="json", by_alias=True)
    payload["executionMode"] = "automatic_research"
    payload["startedAt"] = "2026-01-01T00:00:00Z"
    payload["substageRuntimeStates"][0].update({
        "status": "skipped", "statusReasonCode": "semantic_material_insufficient",
    })
    repository.task = ResearchTaskV12.model_validate(payload)

    recovered = service.recover_confirmed_semantic_material_gap(
        task_id="task_design", owner_id="owner", workspace_id="workspace",
        blocked_instance_id=design.instances[0].substage_instance_id,
        gap_reason_code="semantic_material_insufficient", expected_task_revision=repository.task.revision,
    )
    draft = recovered.research_design_versions[-1]

    assert draft.status.value == "draft"
    assert draft.coverage_report is not None
    assert draft.coverage_report.status.value == "needs_revision"
    assert len(draft.coverage_report.overloaded_instance_refs) == 1
    with pytest.raises(module.ResearchDesignConfirmationError, match="coverage is incomplete"):
        service.confirm(
            task_id="task_design", owner_id="owner", workspace_id="workspace", actor_id="researcher",
            design_id=draft.research_design_id, version=draft.version, confirmation_id="confirmed_limit_v2",
            decision="approved", expected_task_revision=recovered.revision,
        )


def test_proposal_is_strict_and_forbids_agent_skill_and_self_reported_coverage() -> None:
    module = _service_module()
    for field, value in (
        ("selectedCapabilities", []),
        ("agentId", "agent_forbidden"),
        ("skillId", "skill_forbidden"),
        ("coverageStatus", "passed_semantic"),
    ):
        invalid = deepcopy(CASES["simple"])
        invalid[field] = value
        with pytest.raises(module.ResearchDesignProposalError):
            module.validate_research_design_proposal(invalid)

    nested = deepcopy(CASES["simple"])
    nested["instances"][0]["agentId"] = "agent_forbidden"
    with pytest.raises(module.ResearchDesignProposalError):
        module.validate_research_design_proposal(nested)


@pytest.mark.parametrize(
    "mutation",
    [
        "unknown_dependency",
        "cycle",
        "empty_acceptance",
        "empty_output",
        "criterion_artifact_not_emitted",
        "dependency_artifact_not_emitted",
    ],
)
def test_proposal_rejects_invalid_instance_topology_and_acceptance(mutation: str) -> None:
    module = _service_module()
    invalid = deepcopy(CASES["sdr"])
    if mutation == "unknown_dependency":
        invalid["instances"][0]["dependencies"][0]["upstreamInstanceKey"] = "missing"
    elif mutation == "cycle":
        invalid["instances"][1]["dependencies"] = [
            {
                "dependencyKey": "cycle",
                "upstreamInstanceKey": "synthesize",
                "conditionType": "instance_completed",
                "requiredArtifactTypes": [],
                "confirmationTargetRef": None,
            }
        ]
    elif mutation == "empty_acceptance":
        invalid["instances"][0]["acceptanceCriteria"] = []
    elif mutation == "empty_output":
        invalid["instances"][0]["expectedArtifactTypes"] = []
    elif mutation == "criterion_artifact_not_emitted":
        invalid["instances"][0]["acceptanceCriteria"][0]["requiredArtifactType"] = "fabricated_output"
    else:
        invalid["instances"][0]["dependencies"][0]["requiredArtifactTypes"] = ["fabricated_output"]
    with pytest.raises(module.ResearchDesignProposalError):
        module.validate_research_design_proposal(invalid)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("criterion_source", "understanding.unknown[0]"),
        ("claim_source", "understanding.unknown[0]"),
    ],
)
def test_proposal_rejects_unknown_cross_references(field: str, value: str) -> None:
    module = _service_module()
    invalid = deepcopy(CASES["simple"])
    if field == "criterion_source":
        invalid["instances"][0]["acceptanceCriteria"][0]["coverageSourcePaths"] = [value]
        invalid["coverageClaimCandidates"] = []
    elif field == "claim_source":
        invalid["coverageClaimCandidates"][0]["sourcePath"] = value
    elif field == "claim_instance":
        invalid["coverageClaimCandidates"][0]["instanceKeys"] = [value]
    else:
        invalid["coverageClaimCandidates"][0]["acceptanceCriterionKeys"] = [value]

    with pytest.raises(module.ResearchDesignProposalError) as captured:
        module.validate_research_design_proposal(invalid)
    assert captured.value.safe_error_code == "research_design_cross_reference_invalid"
    assert captured.value.safe_field_paths


def test_proposal_derives_candidate_references_from_actual_criterion_paths() -> None:
    module = _service_module()
    compatible = deepcopy(CASES["simple"])
    compatible["coverageClaimCandidates"][0]["instanceKeys"] = ["invented"]
    compatible["coverageClaimCandidates"][0]["acceptanceCriterionKeys"] = [
        "invented:criterion"
    ]

    proposal = module.validate_research_design_proposal(compatible)

    candidate = proposal.coverage_claim_candidates[0]
    assert candidate.instance_keys == ["summarize"]
    assert candidate.acceptance_criterion_keys == ["summarize:output"]


def test_proposal_downgrades_candidate_without_a_supporting_criterion_path() -> None:
    module = _service_module()
    invalid = deepcopy(CASES["simple"])
    invalid["coverageClaimCandidates"][0]["instanceKeys"] = ["missing"]
    invalid["coverageClaimCandidates"][0]["acceptanceCriterionKeys"] = ["missing:criterion"]
    invalid["understanding"]["evidenceBoundaries"] = [
        "No external evidence has been verified."
    ]
    invalid["instances"][0]["acceptanceCriteria"][0]["coverageSourcePaths"] = [
        "understanding.evidenceBoundaries[0]"
    ]

    proposal = module.validate_research_design_proposal(invalid)

    assert proposal.coverage_claim_candidates == []


def test_proposal_normalizes_criterion_references_from_declared_coverage_paths() -> None:
    module = _service_module()
    compatible = deepcopy(CASES["simple"])
    compatible["coverageClaimCandidates"][0]["acceptanceCriterionKeys"] = ["output"]

    proposal = module.validate_research_design_proposal(compatible)

    assert proposal.coverage_claim_candidates[0].acceptance_criterion_keys == ["summarize:output"]

    ambiguous = deepcopy(CASES["simple"])
    second = deepcopy(ambiguous["instances"][0])
    second["instanceKey"] = "summarize_again"
    ambiguous["instances"].append(second)
    ambiguous["coverageClaimCandidates"][0]["instanceKeys"] = ["summarize", "summarize_again"]
    ambiguous["coverageClaimCandidates"][0]["acceptanceCriterionKeys"] = ["output"]
    proposal = module.validate_research_design_proposal(ambiguous)
    assert proposal.coverage_claim_candidates[0].instance_keys == [
        "summarize",
        "summarize_again",
    ]
    assert proposal.coverage_claim_candidates[0].acceptance_criterion_keys == [
        "summarize:output",
        "summarize_again:output",
    ]


@pytest.mark.parametrize(
    "alias",
    [
        "understanding.target_outputs[0]",
        "/understanding/targetOutputs/0",
        "understanding.targetOutputs.0",
    ],
)
def test_proposal_normalizes_only_equivalent_coverage_source_paths(alias: str) -> None:
    module = _service_module()
    compatible = deepcopy(CASES["simple"])
    compatible["instances"][0]["acceptanceCriteria"][0]["coverageSourcePaths"] = [alias]
    compatible["coverageClaimCandidates"][0]["sourcePath"] = alias

    proposal = module.validate_research_design_proposal(compatible)

    assert proposal.instances[0].acceptance_criteria[0].coverage_source_paths == [
        "understanding.targetOutputs[0]"
    ]
    assert proposal.coverage_claim_candidates[0].source_path == "understanding.targetOutputs[0]"


def test_proposal_reconciles_invalid_criterion_path_from_its_explicit_valid_claim() -> None:
    module = _service_module()
    compatible = deepcopy(CASES["simple"])
    compatible["instances"][0]["acceptanceCriteria"][0]["coverageSourcePaths"] = [
        "scopeBoundary.allowedObjectiveKinds[0]"
    ]

    proposal = module.validate_research_design_proposal(compatible)

    assert proposal.instances[0].acceptance_criteria[0].coverage_source_paths == [
        "understanding.targetOutputs[0]"
    ]

    unclaimed = deepcopy(compatible)
    unclaimed["coverageClaimCandidates"] = []
    with pytest.raises(module.ResearchDesignProposalError):
        module.validate_research_design_proposal(unclaimed)


def test_proposal_reconciles_mismatched_valid_paths_from_an_explicit_claim() -> None:
    module = _service_module()
    compatible = deepcopy(CASES["simple"])
    compatible["understanding"]["evidenceBoundaries"] = [
        "No external evidence has been verified."
    ]
    compatible["instances"][0]["acceptanceCriteria"][0]["coverageSourcePaths"] = [
        "understanding.evidenceBoundaries[0]"
    ]

    proposal = module.validate_research_design_proposal(compatible)

    assert proposal.instances[0].acceptance_criteria[0].coverage_source_paths == [
        "understanding.evidenceBoundaries[0]",
        "understanding.targetOutputs[0]",
    ]
    assert proposal.coverage_claim_candidates[0].acceptance_criterion_keys == [
        "summarize:output"
    ]


@pytest.mark.parametrize("mutation", ["criterion_kind", "evaluator", "dependency", "budget"])
def test_proposal_rejects_values_that_cannot_form_domain_models(mutation: str) -> None:
    module = _service_module()
    invalid = deepcopy(CASES["simple"])
    if mutation == "criterion_kind":
        invalid["instances"][0]["acceptanceCriteria"][0]["criterionKind"] = "completeness"
    elif mutation == "evaluator":
        invalid["instances"][0]["acceptanceCriteria"][0]["evaluatorRef"] = "unexpected"
    elif mutation == "dependency":
        invalid = deepcopy(CASES["sdr"])
        invalid["instances"][0]["dependencies"][0]["requiredArtifactTypes"] = []
    else:
        invalid["budgetBoundary"]["maxCostAmount"] = 1.0
        invalid["budgetBoundary"]["currency"] = None

    with pytest.raises(module.ResearchDesignProposalError) as captured:
        module.validate_research_design_proposal(invalid)
    assert captured.value.safe_field_paths


def test_simple_and_sdr_proposals_create_real_order_independent_instance_dags() -> None:
    _, service, repository = _service_and_repo()
    simple = _plan(service, repository, CASES["simple"])
    assert len(simple.research_design_versions[-1].instances) == 1

    sdr = _plan(service, repository, CASES["sdr"], request_id="request_sdr")
    design = sdr.research_design_versions[-1]
    assert len(design.instances) == 2
    instances = {row.objective: row for row in design.instances}
    synthesis = instances["Synthesize the collected evidence."]
    collection = instances["Collect a bounded traceable evidence set."]
    assert synthesis.dependencies[0].upstream_instance_ref.substage_instance_id == collection.substage_instance_id
    assert synthesis.expected_artifact_types == ["research_synthesis"]
    assert synthesis.acceptance_criteria


def test_public_task_schema_preserves_typed_research_design_versions() -> None:
    from app.models.schemas import TaskOut
    from app.modules.research_orchestration_repository import (
        _research_design_compatibility_projection,
    )
    from app.modules.task_public_projection import project_public_task
    from app.modules.task_repository import _with_research_design_compatibility_projection

    _, service, repository = _service_and_repo()
    planned = _plan(service, repository, CASES["simple"])
    payload = planned.model_dump(mode="json", by_alias=True)
    payload.update({
        "ownerId": "owner",
        "workspaceId": "workspace",
        "createdBy": "owner",
        "title": "Research design",
        "currentStage": "problem_modeling",
        "resultSource": "live",
        "stages": [
            "problem_modeling",
            "solution_design",
            "execution_validation",
            "insight_generation",
        ],
    })

    serialized = project_public_task(payload).model_dump(mode="json", by_alias=True)

    assert serialized["researchDesignVersions"][0]["researchDesignId"]
    assert serialized["researchDesignVersions"][0]["coverageReport"]
    assert serialized["activeResearchDesignRef"] is None
    assert serialized["pendingResearchDesignRef"] == {
        "researchDesignId": planned.research_design_versions[-1].research_design_id,
        "version": planned.research_design_versions[-1].version,
    }

    design = planned.research_design_versions[-1]
    confirmed = service.confirm(
        task_id="task_design",
        owner_id="owner",
        workspace_id="workspace",
        actor_id="actor",
        design_id=design.research_design_id,
        version=design.version,
        confirmation_id="confirmation_public_projection",
        decision="approved",
        expected_task_revision=planned.revision,
    )
    confirmed_payload = confirmed.model_dump(mode="json", by_alias=True)
    confirmed_payload.update(_research_design_compatibility_projection(confirmed))
    confirmed_payload.update({
        "ownerId": "owner",
        "workspaceId": "workspace",
        "createdBy": "owner",
        "title": "Research design",
        "currentStage": "problem_modeling",
        "resultSource": "live",
        "stages": [
            "problem_modeling",
            "solution_design",
            "execution_validation",
            "insight_generation",
        ],
    })

    public_confirmed = TaskOut.model_validate(confirmed_payload).model_dump(mode="json")

    assert public_confirmed["confirmations"] == [{
        "id": "confirmation_public_projection",
        "stage": design.instances[0].stage_key.value,
        "decision": "approved",
        "note": f"research_design:{design.research_design_id}:v{design.version}",
        "createdAt": _clock().isoformat(),
    }]

    canonical_payload = confirmed.model_dump(mode="json", by_alias=True)
    canonical_payload.update({
        "ownerId": "owner",
        "workspaceId": "workspace",
        "createdBy": "owner",
        "title": "Research design",
        "currentStage": "problem_modeling",
        "resultSource": "live",
        "stages": [
            "problem_modeling",
            "solution_design",
            "execution_validation",
            "insight_generation",
        ],
    })
    projected = _with_research_design_compatibility_projection(canonical_payload)
    authoritative_public = project_public_task(canonical_payload).model_dump(mode="json", by_alias=True)

    assert authoritative_public["creationPlan"]["planningSource"] == "research_design_v1_2"
    assert authoritative_public["activeResearchDesignRef"] == {
        "researchDesignId": design.research_design_id,
        "version": design.version,
    }
    assert authoritative_public["pendingResearchDesignRef"] is None
    assert TaskOut.model_validate(projected).confirmations[0].id == "confirmation_public_projection"


def test_planner_persists_draft_without_execution_authority() -> None:
    _module, service, repository = _service_and_repo()

    planned = _plan(service, repository, CASES["simple"])

    assert planned.research_design_versions[-1].status.value == "draft"
    assert planned.active_research_design_ref is None
    assert repository.load(task_id="task_design", owner_id="owner", workspace_id="workspace") == planned


def test_planner_revision_supersedes_prior_draft_without_execution_authority() -> None:
    _module, service, repository = _service_and_repo()

    first = _plan(service, repository, CASES["simple"], request_id="first")
    second = _plan(service, repository, CASES["simple"], request_id="second")

    assert [row.status.value for row in second.research_design_versions] == ["superseded", "draft"]
    assert first.active_research_design_ref is None
    assert second.active_research_design_ref is None


def test_contract_rejects_active_ref_to_pending_draft() -> None:
    _module, service, repository = _service_and_repo()
    planned = _plan(service, repository, CASES["simple"])
    draft = planned.research_design_versions[-1]
    payload = planned.model_dump(mode="json", by_alias=True)
    payload["activeResearchDesignRef"] = {
        "researchDesignId": draft.research_design_id,
        "version": draft.version,
    }

    with pytest.raises(ValidationError, match="confirmed Design"):
        ResearchTaskV12.model_validate(payload)

def test_persisted_domain_payload_keeps_canonical_confirmation_and_recovers_legacy_rows() -> None:
    from app.modules.research_orchestration_repository import (
        _extract_contract_payload,
        _persisted_domain_payload,
        _research_design_compatibility_projection,
    )

    _, service, repository = _service_and_repo()
    planned = _plan(service, repository, CASES["simple"])
    design = planned.research_design_versions[-1]
    confirmed = service.confirm(
        task_id="task_design",
        owner_id="owner",
        workspace_id="workspace",
        actor_id="actor",
        design_id=design.research_design_id,
        version=design.version,
        confirmation_id="confirmation_canonical_storage",
        decision="approved",
        expected_task_revision=planned.revision,
    )

    persisted = _persisted_domain_payload(confirmed)
    assert persisted["confirmations"] == [
        confirmed.confirmations[0].model_dump(mode="json", by_alias=True)
    ]
    assert ResearchTaskV12.model_validate(_extract_contract_payload(persisted)) == confirmed

    legacy = {
        **persisted,
        "createdBy": "actor",
        "confirmations": _research_design_compatibility_projection(confirmed)["confirmations"],
    }
    recovered = ResearchTaskV12.model_validate(_extract_contract_payload(legacy))
    assert recovered.confirmations == confirmed.confirmations


def test_semantic_instance_change_increments_revision_and_unchanged_snapshot_reuses_it() -> None:
    _, service, repository = _service_and_repo()
    first = _plan(service, repository, CASES["simple"])
    unchanged = _plan(service, repository, CASES["simple"], request_id="request_same")
    assert unchanged.research_design_versions[-1].instances[0] == first.research_design_versions[-1].instances[0]

    changed = deepcopy(CASES["simple"])
    changed["instances"][0]["objective"] = "Create a revised bounded evidence summary."
    revised = _plan(service, repository, changed, request_id="request_changed")
    prior = unchanged.research_design_versions[-1].instances[0]
    current = revised.research_design_versions[-1].instances[0]
    assert current.substage_instance_id == prior.substage_instance_id
    assert current.instance_revision == prior.instance_revision + 1


def test_upstream_change_propagates_dependency_revision_to_downstream_instance() -> None:
    _, service, repository = _service_and_repo()
    first = _plan(service, repository, CASES["sdr"])
    changed = deepcopy(CASES["sdr"])
    changed["instances"][1]["objective"] = "Collect a revised bounded traceable evidence set."
    second = _plan(service, repository, changed, request_id="request_dependency_revision")

    prior = {row.substage_instance_id: row for row in first.research_design_versions[-1].instances}
    current = {row.substage_instance_id: row for row in second.research_design_versions[-1].instances}
    assert all(current[key].instance_revision == row.instance_revision + 1 for key, row in prior.items())


def test_server_derives_coverage_and_downgrades_forged_full_claim() -> None:
    _, service, repository = _service_and_repo()
    forged = deepcopy(CASES["simple"])
    forged["coverageClaimCandidates"][0]["acceptanceCriterionKeys"] = []
    result = _plan(service, repository, forged)
    report = result.research_design_versions[-1].coverage_report
    assert report is not None
    assert report.status.value == "needs_revision"
    assert report.uncovered_required_target_ids
    assert all(claim.coverage_mode.value != "full" for claim in report.claims)


def test_target_output_coverage_uses_bound_artifact_type_not_natural_language_description() -> None:
    _, service, repository = _service_and_repo()
    proposal = deepcopy(CASES["simple"])
    proposal["understanding"]["targetOutputs"][0] = "A bounded natural-language evidence summary"

    result = _plan(service, repository, proposal)
    report = result.research_design_versions[-1].coverage_report

    assert report is not None
    target = next(row for row in report.targets if row.source_path == "understanding.targetOutputs[0]")
    assert target.description == "A bounded natural-language evidence summary"
    assert target.required_artifact_types == [
        proposal["instances"][0]["acceptanceCriteria"][0]["requiredArtifactType"]
    ]
    assert report.status.value == "passed_structural"


def test_compiled_mixed_target_requirements_remain_fully_covered() -> None:
    from app.models.research_planner_semantic import PlannerSemanticProposalV2
    from app.modules.research_design_compiler import compile_research_design_proposal

    candidate = "Generate candidate structures"
    comparison = "Compare the candidate against the reference"
    semantic = PlannerSemanticProposalV2.model_validate({
        "schemaVersion": "planner-semantic.v2",
        "goal": "Generate candidates and compare them with the reference.",
        "understanding": {
            "userIntent": "Generate candidates and compare them with the reference.",
            "materialSummary": "One bounded source set is available.",
            "primaryResearchQuestions": ["Which generated candidates compare favorably with the reference?"],
            "auxiliaryResearchDimensions": ["Candidate generation conditions"],
            "targetOutputs": [candidate, comparison],
            "evidenceBoundaries": [], "missingInputs": [], "riskNotes": [],
        },
        "constraints": [], "requestedExternalActions": [],
        "workItems": [{
            "localKey": "synthesize", "objective": "Generate and compare bounded candidates.",
            "definitionId": "sdr:evidence_synthesis", "inputRefs": ["material:one"],
            "expectedArtifactTypes": ["evidence_summary"],
            "targetOutputRefs": [candidate, comparison], "dependsOnLocalKeys": [],
        }],
        "targetRequirements": [
            {"workItemLocalKey": "synthesize", "targetOutputRef": candidate, "completionMode": "artifact_presence"},
            {"workItemLocalKey": "synthesize", "targetOutputRef": comparison, "completionMode": "professional_judgment"},
        ],
    })
    proposal = compile_research_design_proposal(
        semantic,
        definition_catalog=[{
            "definitionId": "sdr:evidence_synthesis", "definitionVersion": 1,
            "stage": "solution_design", "label": "Evidence synthesis",
            "description": "Synthesize bounded evidence.",
            "instancePolicy": {"mode": "repeatable"}, "requiredInputRefs": ["material:one"],
            "artifactTypes": ["evidence_summary"], "criterionRefs": ["comparison_validator"],
        }],
        raw_input="Generate candidates and compare them with the reference.",
    ).model_dump(mode="json", by_alias=True)

    _, service, repository = _service_and_repo()
    result = _plan(service, repository, proposal, request_id="request_mixed_target_requirements")
    report = result.research_design_versions[-1].coverage_report

    assert report is not None
    assert report.status.value == "passed_structural"
    assert report.uncovered_required_target_ids == []
    target_claims = [
        claim for claim in report.claims
        if claim.target_id in {target.target_id for target in report.targets if target.source_path.startswith("understanding.targetOutputs[")}
    ]
    assert len(target_claims) == 2
    assert all(claim.coverage_mode.value == "full" for claim in target_claims)


def test_planning_attempt_is_started_before_provider_and_has_terminal_states_without_replay() -> None:
    module, service, repository = _service_and_repo()
    result = _plan(service, repository, CASES["simple"])
    assert result.planning_attempts[-1].status.value == "completed"
    assert result.planning_attempts[-1].planner_provider_call_ref == "provider:request_one"

    calls: list[str] = []

    def unknown_provider(key: str):
        calls.append(key)
        assert repository.task.planning_attempts[-1].status.value == "started"
        raise module.PlannerTransportUnknown(
            "transport result unknown",
            provider_call_ref="provider:request_unknown",
        )

    with pytest.raises(module.PlannerTransportUnknown):
        service.plan(
            task_id="task_design",
            owner_id="owner",
            workspace_id="workspace",
            request_id="request_unknown",
            expected_task_revision=repository.task.revision,
            provider=unknown_provider,
        )
    assert calls == ["planner:request_unknown"]
    assert repository.task.planning_attempts[-1].status.value == "failed"
    assert repository.task.planning_attempts[-1].planner_provider_call_ref == "provider:request_unknown"
    with pytest.raises(module.PlanningAttemptReplayError):
        service.plan(
            task_id="task_design",
            owner_id="owner",
            workspace_id="workspace",
            request_id="request_unknown",
            expected_task_revision=repository.task.revision,
            provider=unknown_provider,
        )
    assert calls == ["planner:request_unknown"]


def test_invalid_provider_proposal_persists_failed_terminal_with_provider_ref() -> None:
    module, service, repository = _service_and_repo()
    invalid = deepcopy(CASES["simple"])
    invalid["selectedCapabilities"] = []

    def provider(_key: str):
        return module.PlannerProviderResult(
            proposal=invalid,
            provider_call_ref="provider:invalid-proposal",
        )

    with pytest.raises(module.ResearchDesignProposalError):
        service.plan(
            task_id="task_design",
            owner_id="owner",
            workspace_id="workspace",
            request_id="request_invalid_proposal",
            expected_task_revision=repository.task.revision,
            provider=provider,
        )
    attempt = repository.task.planning_attempts[-1]
    assert attempt.status.value == "failed"
    assert attempt.planner_provider_call_ref == "provider:invalid-proposal"


def test_additional_action_claim_is_single_winner_under_task_cas() -> None:
    module, service, repository = _service_and_repo()
    _plan(service, repository, CASES["simple"])
    revision = repository.task.revision
    attempt_id = repository.task.planning_attempts[-1].planning_attempt_id

    def claim(action: str) -> str:
        try:
            service.claim_additional_action(
                task_id="task_design",
                owner_id="owner",
                workspace_id="workspace",
                planning_attempt_id=attempt_id,
                action_type=action,
                expected_task_revision=revision,
            )
            return "won"
        except (TaskRevisionConflictError, module.AdditionalActionConflictError):
            return "lost"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(claim, ["semantic_adequacy", "planner_repair"]))
    assert sorted(outcomes) == ["lost", "won"]


def test_definition_source_is_required_and_unknown_definition_fails_before_provider() -> None:
    module = _service_module()
    task = module.new_research_task(task_id="task_design", registry_snapshot_ref=SNAPSHOT)
    repository = FakeRepository(task)
    service = module.ResearchDesignService(repository=repository, definition_source=None, clock=_clock)
    calls: list[str] = []

    with pytest.raises(module.DefinitionSourceUnavailableError):
        service.plan(
            task_id="task_design",
            owner_id="owner",
            workspace_id="workspace",
            request_id="request_no_source",
            expected_task_revision=0,
            provider=lambda key: calls.append(key),
        )
    assert calls == []

    class MismatchedSnapshotSource(FakeDefinitionSource):
        def snapshot_ref(self):
            return {"schemaVersion": 1, "contentHash": "sha256:" + "e" * 64}

    mismatched = module.ResearchDesignService(
        repository=repository,
        definition_source=MismatchedSnapshotSource(),
        clock=_clock,
    )
    with pytest.raises(module.DefinitionResolutionError, match="snapshot"):
        mismatched.plan(
            task_id="task_design",
            owner_id="owner",
            workspace_id="workspace",
            request_id="request_bad_snapshot",
            expected_task_revision=0,
            provider=lambda key: calls.append(key),
        )
    assert calls == []

    _, service, repository = _service_and_repo()
    unknown = deepcopy(CASES["simple"])
    unknown["instances"][0]["definitionId"] = "sdr:unknown"
    with pytest.raises(module.DefinitionResolutionError):
        _plan(service, repository, unknown)
    assert repository.task.planning_attempts[-1].status.value == "failed"


def test_generic_planning_rejects_definition_outside_frozen_workflow_admission() -> None:
    module = _service_module()

    class Source(FakeDefinitionSource):
        def definition_is_eligible_for_workflow_admission(self, definition_id, admission):
            return definition_id != "opentrajectory:intent_understanding" or admission == {
                "workflowId": "opentrajectory_research_workflow",
                "skillPack": "opentrajectory",
            }

        def resolve(self, definition_id):
            if definition_id == "opentrajectory:intent_understanding":
                return module.ResolvedDefinition(definition_id, 1, "problem_modeling")
            return super().resolve(definition_id)

    task = module.new_research_task(task_id="task_design", registry_snapshot_ref=SNAPSHOT)
    repository = FakeRepository(task)
    repository.workflow_admission = None
    service = module.ResearchDesignService(repository=repository, definition_source=Source(), clock=_clock)
    proposal = deepcopy(CASES["simple"])
    proposal["instances"][0]["definitionId"] = "opentrajectory:intent_understanding"

    with pytest.raises(module.DefinitionResolutionError, match="workflow admission"):
        _plan(service, repository, proposal)
    assert repository.task.planning_attempts[-1].status.value == "failed"


@pytest.mark.parametrize(
    ("version", "stage"),
    [(0, "solution_design"), (1, "not_a_stage")],
)
def test_definition_source_rejects_wrong_version_or_stage(version: int, stage: str) -> None:
    module = _service_module()

    class InvalidDefinitionSource(FakeDefinitionSource):
        def resolve(self, definition_id: str):
            return module.ResolvedDefinition(
                definition_id=definition_id,
                definition_version=version,
                stage_key=stage,
            )

    task = module.new_research_task(task_id="task_design", registry_snapshot_ref=SNAPSHOT)
    repository = FakeRepository(task)
    service = module.ResearchDesignService(
        repository=repository,
        definition_source=InvalidDefinitionSource(),
        clock=_clock,
    )
    with pytest.raises(module.DefinitionResolutionError):
        _plan(service, repository, CASES["simple"])
    assert repository.task.planning_attempts[-1].status.value == "failed"


@pytest.mark.parametrize(
    ("decision", "expected_status"),
    [("request_revision", "draft"), ("rejected", "cancelled")],
)
def test_non_approved_confirmation_is_atomic_and_never_activates(
    decision: str,
    expected_status: str,
) -> None:
    module, service, repository = _service_and_repo()
    planned = _plan(service, repository, CASES["simple"])
    design = planned.research_design_versions[-1]

    result = service.confirm(
        task_id="task_design",
        owner_id="owner",
        workspace_id="workspace",
        actor_id="actor",
        design_id=design.research_design_id,
        version=design.version,
        confirmation_id=f"confirmation_{decision}",
        decision=decision,
        expected_task_revision=planned.revision,
    )
    assert result.research_design_versions[-1].status.value == expected_status
    assert result.confirmations[-1].decision == decision
    assert result.active_research_design_ref is None
    assert result.execution_decisions == []
    assert result.work_units == []
    assert result.capability_calls == []
    assert result.substage_runtime_states == []
    assert result.scientific_states == []


def test_confirmation_requires_matching_approval_coverage_and_preserves_zero_execution_facts() -> None:
    module, service, repository = _service_and_repo()
    planned = _plan(service, repository, CASES["simple"])
    design = planned.research_design_versions[-1]

    confirmed = service.confirm(
        task_id="task_design",
        owner_id="owner",
        workspace_id="workspace",
        actor_id="actor",
        design_id=design.research_design_id,
        version=design.version,
        confirmation_id="confirmation_one",
        decision="approved",
        expected_task_revision=planned.revision,
    )
    assert confirmed.active_research_design_ref.version == design.version
    assert confirmed.execution_decisions == []
    assert confirmed.work_units == []
    assert confirmed.capability_calls == []
    assert confirmed.artifacts == []
    assert confirmed.agent_interaction_records == []
    assert confirmed.completion_decisions == []
    assert confirmed.final_results == []
    assert confirmed.final_result_authority is None
    assert len(confirmed.scientific_states) == 1
    initial_state = confirmed.scientific_states[0]
    assert initial_state.scientific_state_version == 1
    assert initial_state.research_design_version == design.version
    assert initial_state.scientific_facts == {}
    assert initial_state.execution_state == {}
    assert initial_state.source_refs == []
    assert initial_state.created_at == _clock()
    assert [row.status.value for row in confirmed.substage_runtime_states] == ["ready"]
    runtime = confirmed.substage_runtime_states[0]
    assert (runtime.substage_instance_id, runtime.instance_revision) == (
        design.instances[0].substage_instance_id,
        design.instances[0].instance_revision,
    )
    assert runtime.active_execution_decision_id is None
    assert runtime.work_unit_ids == []
    assert runtime.artifact_refs == []
    assert runtime.status_reason_code is None


def test_research_design_contract_rejects_empty_instances() -> None:
    _module, service, repository = _service_and_repo()
    planned = _plan(service, repository, CASES["simple"])
    payload = planned.model_dump(mode="json", by_alias=True)
    payload["researchDesignVersions"][-1]["instances"] = []

    with pytest.raises(ValidationError, match="at least 1 item"):
        ResearchTaskV12.model_validate(payload)


def test_confirmation_rejects_empty_design_before_activation() -> None:
    module, service, repository = _service_and_repo()
    planned = _plan(service, repository, CASES["simple"])
    design = planned.research_design_versions[-1]
    empty_design = design.model_copy(update={"instances": []})
    repository.task = planned.model_copy(update={"research_design_versions": [empty_design]})
    before = repository.task
    saves = repository.saves

    with pytest.raises(module.ResearchDesignConfirmationError, match="at least one executable"):
        service.confirm(
            task_id="task_design",
            owner_id="owner",
            workspace_id="workspace",
            actor_id="actor",
            design_id=design.research_design_id,
            version=design.version,
            confirmation_id="confirmation_empty",
            decision="approved",
            expected_task_revision=repository.task.revision,
        )
    assert repository.task == before
    assert repository.saves == saves


def test_concurrent_identical_confirmation_replays_revision_cas_winner() -> None:
    from threading import Barrier, get_ident

    module, service, repository = _service_and_repo()
    planned = _plan(service, repository, CASES["simple"])
    design = planned.research_design_versions[-1]

    class ConcurrentConfirmRepository(FakeRepository):
        def __init__(self, task: ResearchTaskV12) -> None:
            super().__init__(task)
            self.initial_load_barrier = Barrier(2)
            self.initial_load_threads: set[int] = set()
            self.initial_load_lock = Lock()

        def load(self, *, task_id: str, owner_id: str, workspace_id: str) -> ResearchTaskV12:
            snapshot = super().load(
                task_id=task_id,
                owner_id=owner_id,
                workspace_id=workspace_id,
            )
            thread_id = get_ident()
            with self.initial_load_lock:
                should_wait = thread_id not in self.initial_load_threads
                if should_wait:
                    self.initial_load_threads.add(thread_id)
            if should_wait:
                self.initial_load_barrier.wait(timeout=5)
            return snapshot

    concurrent_repository = ConcurrentConfirmRepository(planned)
    concurrent_service = module.ResearchDesignService(
        repository=concurrent_repository,
        definition_source=FakeDefinitionSource(),
        clock=_clock,
    )

    def confirm_once() -> ResearchTaskV12:
        return concurrent_service.confirm(
            task_id="task_design",
            owner_id="owner",
            workspace_id="workspace",
            actor_id="actor",
            design_id=design.research_design_id,
            version=design.version,
            confirmation_id="confirmation_concurrent",
            decision="approved",
            expected_task_revision=planned.revision,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(confirm_once), pool.submit(confirm_once)]
        results = [future.result(timeout=10) for future in futures]

    assert concurrent_repository.saves == 1
    assert concurrent_repository.task.revision == planned.revision + 1
    assert len(concurrent_repository.task.confirmations) == 1
    assert concurrent_repository.task.confirmations[0].confirmation_id == "confirmation_concurrent"
    assert len(concurrent_repository.task.scientific_states) == 1
    assert results == [concurrent_repository.task, concurrent_repository.task]


def test_needs_revision_coverage_cannot_be_confirmed_and_writes_nothing() -> None:
    module, service, repository = _service_and_repo()
    forged = deepcopy(CASES["simple"])
    forged["coverageClaimCandidates"] = []
    planned = _plan(service, repository, forged)
    design = planned.research_design_versions[-1]
    before = repository.task
    saves = repository.saves

    with pytest.raises(module.ResearchDesignConfirmationError, match="coverage"):
        service.confirm(
            task_id="task_design", owner_id="owner", workspace_id="workspace", actor_id="actor",
            design_id=design.research_design_id, version=design.version,
            confirmation_id="confirmation_blocked", decision="approved",
            expected_task_revision=planned.revision,
        )
    assert repository.task == before
    assert repository.saves == saves


def test_confirmation_is_idempotent_stale_safe_and_atomically_supersedes_prior_version() -> None:
    module, service, repository = _service_and_repo()
    first = _plan(service, repository, CASES["simple"])
    first_design = first.research_design_versions[-1]
    confirmed_first = service.confirm(
        task_id="task_design", owner_id="owner", workspace_id="workspace", actor_id="actor",
        design_id=first_design.research_design_id, version=1, confirmation_id="confirmation_one",
        decision="approved", expected_task_revision=first.revision,
    )
    saves = repository.saves
    replay = service.confirm(
        task_id="task_design", owner_id="owner", workspace_id="workspace", actor_id="actor",
        design_id=first_design.research_design_id, version=1, confirmation_id="confirmation_one",
        decision="approved", expected_task_revision=first.revision,
    )
    assert replay == confirmed_first
    assert repository.saves == saves
    assert len(replay.substage_runtime_states) == 1
    assert len(replay.scientific_states) == 1

    accepted_facts = {
        "acceptedClaims": [{"claimId": "claim_from_design_one"}],
    }
    accepted_source_refs = ["source:accepted-design-one"]
    prior_execution_state = {
        "terminalSubstages": True,
        "substageCompletion": [{"substageInstanceId": "old-instance"}],
        "activeExecutionDecisionId": "decision-old",
    }
    accepted_state = confirmed_first.scientific_states[0].model_copy(
        update={
            "scientific_facts": deepcopy(accepted_facts),
            "execution_state": deepcopy(prior_execution_state),
            "source_refs": list(accepted_source_refs),
        }
    )
    repository.task = confirmed_first.model_copy(
        update={"scientific_states": [accepted_state]}
    )

    second = _plan(service, repository, CASES["sdr"], request_id="request_revision")
    second_design = second.research_design_versions[-1]
    confirmed_second = service.confirm(
        task_id="task_design", owner_id="owner", workspace_id="workspace", actor_id="actor",
        design_id=second_design.research_design_id, version=2, confirmation_id="confirmation_two",
        decision="approved", expected_task_revision=second.revision,
    )
    old = confirmed_second.research_design_versions[0]
    assert old.status.value == "superseded"
    assert old.goal == first_design.goal
    assert old.instances == first_design.instances
    assert old.coverage_report == first_design.coverage_report
    assert old.confirmed_at == confirmed_first.research_design_versions[0].confirmed_at
    assert old.confirmation_ref == "confirmation_one"
    assert confirmed_second.active_research_design_ref.version == 2
    assert len(confirmed_second.scientific_states) == 2
    assert confirmed_second.scientific_states[0] == accepted_state
    current_state = confirmed_second.scientific_states[-1]
    assert current_state.scientific_state_version == 2
    assert current_state.research_design_version == second_design.version
    assert current_state.scientific_facts == accepted_facts
    assert current_state.source_refs == accepted_source_refs
    assert current_state.execution_state == {}
    first_runtime = {
        (row.substage_instance_id, row.instance_revision): row.status.value
        for row in confirmed_first.substage_runtime_states
    }
    confirmed_runtime = {
        (row.substage_instance_id, row.instance_revision): row.status.value
        for row in confirmed_second.substage_runtime_states
    }
    assert all(confirmed_runtime[key] == status for key, status in first_runtime.items())
    assert {
        (row.substage_instance_id, row.instance_revision): confirmed_runtime[
            (row.substage_instance_id, row.instance_revision)
        ]
        for row in second_design.instances
    } == {
        (row.substage_instance_id, row.instance_revision): (
            "planned" if row.dependencies else "ready"
        )
        for row in second_design.instances
    }
    assert confirmed_second.status.value == "not_started"

    before = repository.task
    with pytest.raises((TaskRevisionConflictError, module.ResearchDesignConfirmationError)):
        service.confirm(
            task_id="task_design", owner_id="owner", workspace_id="workspace", actor_id="actor",
            design_id=second_design.research_design_id, version=2, confirmation_id="confirmation_conflict",
            decision="approved", expected_task_revision=second.revision,
        )
    assert repository.task == before

    with pytest.raises(module.ConfirmationIdentityConflictError):
        service.confirm(
            task_id="task_design", owner_id="owner", workspace_id="workspace", actor_id="other_actor",
            design_id=first_design.research_design_id, version=1, confirmation_id="confirmation_one",
            decision="approved", expected_task_revision=repository.task.revision,
        )


def test_workflow_responsibility_coverage_is_persisted_and_publicly_readable() -> None:
    _, service, repository = _service_and_repo()
    repository.workflow_admission = {
        "workflowId": "adaptive_workflow",
        "workflowVersion": 2,
        "workflowContentHash": "d" * 64,
        "skillPack": "test-pack",
        "workflowDefinitionSnapshot": {"definition": {}},
    }
    proposal = deepcopy(CASES["simple"])
    proposal["workflowResponsibilityCoverage"] = [
        {
            "responsibilityKey": "evidence_synthesis",
            "definitionId": "sdr:evidence_synthesis",
            "requirement": "core",
            "coverageMode": "planned_work",
            "instanceKeys": ["summarize"],
            "reusedResultRefs": [],
            "rationale": "This current project must synthesize the evidence.",
        },
        {
            "responsibilityKey": "conditional_diagnosis",
            "definitionId": "sdr:evidence_synthesis",
            "requirement": "conditional",
            "coverageMode": "not_selected",
            "instanceKeys": [],
            "reusedResultRefs": [],
            "rationale": "No discrepancy currently establishes the diagnostic entry condition.",
        },
    ]

    planned = _plan(service, repository, proposal, request_id="workflow_coverage")
    design = planned.research_design_versions[-1]
    coverage = {row.responsibility_key: row for row in design.workflow_responsibility_coverage}

    assert coverage["evidence_synthesis"].coverage_mode == "planned_work"
    assert len(coverage["evidence_synthesis"].instance_refs) == 1
    assert coverage["conditional_diagnosis"].coverage_mode == "not_selected"
    assert coverage["conditional_diagnosis"].instance_refs == []

    public = service.read_design(
        task_id="task_design",
        owner_id="owner",
        workspace_id="workspace",
    )
    public_coverage = {
        row["responsibilityKey"]: row for row in public["workflowResponsibilityCoverage"]
    }
    assert public_coverage["evidence_synthesis"]["coverageMode"] == "planned_work"
    assert public_coverage["conditional_diagnosis"]["coverageMode"] == "not_selected"


def test_planning_source_usage_is_planning_provenance_not_research_design_authority() -> None:
    module, service, repository = _service_and_repo()
    from app.models.research_orchestration import PlanningSourceUsage

    usage = PlanningSourceUsage(
        sourceKind="research_package",
        sourceId="rp-method-alpha",
        sourceVersion="2.0",
        sourceArtifactRef="rp_artifact_exact_version",
        sourceContentHash="sha256:" + "a" * 64,
        adoptedSourcePaths=["/method/goal", "/method/logic/0"],
        notAdoptedSourcePaths=["/reference_cases/0/conditions/0"],
        revalidationSourcePaths=["/applicability/limits/0"],
    )

    def provider(_idempotency_key: str):
        return module.PlannerProviderResult(
            proposal=deepcopy(CASES["simple"]),
            provider_call_ref="provider:source-aware",
            planning_source_usages=(usage,),
        )

    planned = service.plan(
        task_id="task_design",
        owner_id="owner",
        workspace_id="workspace",
        request_id="request_source_aware",
        expected_task_revision=repository.task.revision,
        provider=provider,
    )
    attempt = planned.planning_attempts[-1]
    design = planned.research_design_versions[-1]

    assert attempt.status.value == "completed"
    assert attempt.planning_source_usages == [usage]
    assert design.coverage_report is not None
    assert design.coverage_report.planning_attempt_id == attempt.planning_attempt_id
    design_payload = design.model_dump(mode="json", by_alias=True)
    assert "planningSourceUsages" not in design_payload
    assert "researchPackage" not in json.dumps(design_payload, sort_keys=True)

    confirmed = service.confirm(
        task_id="task_design",
        owner_id="owner",
        workspace_id="workspace",
        actor_id="researcher",
        design_id=design.research_design_id,
        version=design.version,
        confirmation_id="confirm_source_aware",
        decision="approved",
        expected_task_revision=planned.revision,
    )
    confirmed_design = confirmed.research_design_versions[-1]
    assert confirmed_design.status.value == "confirmed"
    assert confirmed_design.coverage_report.planning_attempt_id == attempt.planning_attempt_id
