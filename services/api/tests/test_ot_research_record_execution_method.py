from __future__ import annotations

from datetime import datetime

from app.models.research_orchestration import (
    InstanceRef,
    ResearchTaskV12,
    RuntimeReadinessSnapshot,
)
from app.modules import research_execution_decision_service as decision_service
from app.modules.registry_snapshot_repository import (
    InMemoryRegistrySnapshotRepository,
)
from app.modules.research_orchestration_resolver import (
    ResearchOrchestrationResolver,
    ResolutionFacts,
    build_registry_snapshot,
)
from tests.support.research_execution_decision import (
    freeze_prepared_selection_for_test,
)
from tests.support.research_lifecycle import build_confirmed_research_task


METHOD_ID = "opentrajectory_research_record_establishment_v1"
ROLE_SKILL_OUTPUTS = [
    (
        "research_context_modeling",
        "problem_framing_generation",
        "opentrajectory.problem_framing",
    ),
    (
        "retrieval_strategy",
        "retrieval_matrix_generation",
        "opentrajectory.retrieval_strategy_design",
    ),
    (
        "evidence_schema",
        "evidence_schema_design",
        "opentrajectory.evidence_schema_design",
    ),
    (
        "source_acquisition",
        "opentrajectory_source_acquisition",
        "opentrajectory.source_acquisition",
    ),
    (
        "source_screening",
        "opentrajectory_source_screening_selection",
        "opentrajectory.source_screening_selection",
    ),
    (
        "progressive_reading",
        "deepxiv_progressive_reading",
        "opentrajectory.progressive_reading",
    ),
    (
        "research_record_extraction",
        "evidence_node_extraction",
        "research_records",
    ),
]


def _mapping_keys(value) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {
            key
            for child in value.values()
            for key in _mapping_keys(child)
        }
    if isinstance(value, list):
        return {
            key
            for child in value
            for key in _mapping_keys(child)
        }
    return set()


def _task_for_research_record_method(snapshot_hash: str) -> ResearchTaskV12:
    payload = build_confirmed_research_task().model_dump(mode="json", by_alias=True)
    payload["registrySnapshotRef"] = {
        "schemaVersion": 1,
        "contentHash": snapshot_hash,
    }
    instance = payload["researchDesignVersions"][0]["instances"][0]
    instance.update(
        {
            "substageDefinitionId": "opentrajectory:research_record_establishment",
            "substageDefinitionVersion": 1,
            "stageKey": "execution_validation",
            "objective": "建立可追溯研究记录。",
            "inputRefs": [
                "research_goal",
                "raw_research_materials",
                "accepted_existing_research_records",
                "current_judgment_target",
            ],
            "expectedArtifactTypes": ["research_records"],
        }
    )
    criterion = instance["acceptanceCriteria"][0]
    criterion.update(
        {
            "requiredArtifactType": "research_records",
            "judgmentRequirement": "domain_professional",
            "evaluatorType": "agent",
            "evaluatorRef": "source_traceability_preserved",
        }
    )
    instance["acceptanceCriteria"] = [criterion]
    return ResearchTaskV12.model_validate(payload)


def _resolve_production_method():
    snapshot = build_registry_snapshot()
    task = _task_for_research_record_method(snapshot.content_hash)
    design = task.research_design_versions[0]
    instance = design.instances[0]
    skill_ids = {skill_id for _, skill_id, _ in ROLE_SKILL_OUTPUTS}
    authorized = {f"skill:{skill_id}:1" for skill_id in skill_ids} | {
        "agent:general_research_agent:1"
    }
    facts = ResolutionFacts(
        runtimeReadiness={candidate_id: True for candidate_id in authorized},
        providerReadiness={"openai_primary": True},
        authorizedCandidateRefs=authorized,
        availableInputRefs=set(instance.input_refs),
        applicableRuleRefs=set(),
        satisfiedHumanGateRefs=set(),
        riskAllowed=True,
        budgetAvailable=True,
        policyVersion="registry-policy.v1",
    )
    repository = InMemoryRegistrySnapshotRepository()
    repository.put(snapshot)
    resolution = ResearchOrchestrationResolver(repository).resolve(
        design=design,
        designRef=task.active_research_design_ref,
        instanceRef=InstanceRef(
            substageInstanceId=instance.substage_instance_id,
            instanceRevision=instance.instance_revision,
        ),
        registrySnapshotRef=task.registry_snapshot_ref,
        facts=facts,
    )
    readiness = {
        f"skill:{skill_id}:1": RuntimeReadinessSnapshot.model_validate(
            {
                "capabilityRef": {
                    "skillId": skill_id,
                    "definitionVersion": 1,
                },
                "ready": True,
                "runtimeBindingId": f"registry:{skill_id}:1",
                "checkedAt": "2026-09-17T23:59:00Z",
                "expiresAt": "2026-09-18T01:00:00Z",
                "reason": None,
            }
        )
        for skill_id in skill_ids
    }
    return task, snapshot, facts, resolution, readiness


def test_research_record_definition_freezes_one_formal_execution_method() -> None:
    snapshot = build_registry_snapshot().envelope
    definition = next(
        row
        for row in snapshot["definitions"]["substages"]
        if row["definitionId"] == "opentrajectory:research_record_establishment"
    )
    method = definition["executionMethod"]

    assert method["methodId"] == METHOD_ID
    assert method["completionRoleIds"] == ["research_record_extraction"]
    assert [row["roleId"] for row in method["roles"]] == [
        role_id for role_id, _, _ in ROLE_SKILL_OUTPUTS
    ]
    assert [
        row["qualificationContract"]["outputArtifactTypes"][0]
        for row in method["roles"]
    ] == [artifact_type for _, _, artifact_type in ROLE_SKILL_OUTPUTS]
    assert not {"skillId", "agentId", "model"} & _mapping_keys(method)

    bindings = {
        row["skillId"]: row
        for row in snapshot["bindings"]["skills"]
        if row["definitionId"]
        == "opentrajectory:research_record_establishment"
    }
    assert set(bindings) == {skill_id for _, skill_id, _ in ROLE_SKILL_OUTPUTS}

    skills = {
        row["skillId"]: row for row in snapshot["definitions"]["skills"]
    }
    for role_id, skill_id, artifact_type in ROLE_SKILL_OUTPUTS:
        assert f"{METHOD_ID}:{role_id}" in skills[skill_id]["executionMethodRoles"]
        assert artifact_type in skills[skill_id]["outputContract"]["artifactTypes"]

    extraction = skills["evidence_node_extraction"]
    assert extraction["judgmentSupport"]["domainProfessional"] is True


def test_research_record_method_is_selected_and_freezes_role_outputs() -> None:
    task, snapshot, facts, resolution, readiness = _resolve_production_method()
    general = next(
        row
        for row in resolution.candidate_evaluations
        if getattr(row.candidate_ref, "agent_id", None)
        == "general_research_agent"
    )
    assert not general.eligible

    eligible_roles = {
        role.role_ref: getattr(candidate.candidate_ref, "skill_id", None)
        for candidate in resolution.candidate_evaluations
        for role in candidate.execution_method_role_evaluations
        if role.eligible and role.executable_now
    }
    assert eligible_roles == {
        f"{METHOD_ID}:{role_id}": skill_id
        for role_id, skill_id, _ in ROLE_SKILL_OUTPUTS
    }

    design = task.research_design_versions[0]
    instance = design.instances[0]
    prepared = decision_service.prepare_selection(
        task,
        instance_id=instance.substage_instance_id,
        resolution=resolution,
        snapshot_envelope=snapshot.envelope,
        facts=facts,
        runtime_readiness=readiness,
        selection_request_id="test-ot-research-record-method",
        selection_command_hash="test-command",
        expected_task_revision=task.revision,
        owner_id="owner",
        workspace_id="workspace",
        decided_at=datetime.fromisoformat("2026-09-18T00:00:00+00:00"),
        workflow_lineage={
            "workflowId": "opentrajectory_research_workflow",
            "skillPack": "opentrajectory",
            "researchDesignId": design.research_design_id,
            "researchDesignVersion": design.version,
        },
    )
    materialized = freeze_prepared_selection_for_test(
        prepared,
        task,
        prepared.final_tie_candidate_ids[0],
    )

    assert materialized.decision.strategy.value == "execution_method"
    assert materialized.decision.purpose.value == "completion"
    assert [
        (
            row.execution_method_role_id,
            row.executor_ref.executor_id,
            list(row.expected_artifact_types),
        )
        for row in materialized.decision.assignments
    ] == [
        (role_id, skill_id, [artifact_type])
        for role_id, skill_id, artifact_type in ROLE_SKILL_OUTPUTS
    ]
    assert [
        row.execution_method_role_id for row in materialized.task.work_units
    ] == ["research_context_modeling"]
