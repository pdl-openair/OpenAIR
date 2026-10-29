from __future__ import annotations

from app.models.research_orchestration import ResearchTaskV12
from app.modules.artifact_field_projection import merge_artifact_field_projection
from app.modules.capability_applicability_producer import (
    RESEARCH_TASK_FACT_REF,
    produce_capability_applicability_results,
)
from app.modules.conditional_capability_binding import (
    CapabilityApplicabilityStatus,
    evaluate_skill_binding_eligibility,
)
from app.modules.research_execution_decision_service import build_production_resolution_facts
from app.modules.research_orchestration_resolver import build_registry_snapshot
from tests.support.research_lifecycle import build_confirmed_research_task
from tests.test_cs_reproduction_execution_infrastructure import _design_for_definition

DEFINITION_ID = "cs_reproduction:execution_readiness_validation"
RESOURCE_CAPABILITY = "skill:resource_identity_acquisition:1"
ENVIRONMENT_CAPABILITY = "skill:environment_setup:1"


def _readiness_definition(envelope: dict) -> dict:
    return max(
        (
            row
            for row in envelope["definitions"]["substages"]
            if row["definitionId"] == DEFINITION_ID and row["enabled"]
        ),
        key=lambda row: row["definitionVersion"],
    )


def _design_artifact_projection(*, empty_work: bool) -> dict:
    content = (
        '{"status":"ready","resource_acquisition_requests":[],"requirements":[],"verify_imports":[]}'
        if empty_work
        else '{"status":"ready","resource_acquisition_requests":[{"repo_id":"demo/model"}],'
        '"requirements":["numpy"],"verify_imports":["torch"]}'
    )
    return merge_artifact_field_projection({"status": "passed"}, content)


def _research_task_with_design(*, include_design: bool, empty_work: bool) -> dict:
    task = build_confirmed_research_task()
    snapshot = build_registry_snapshot()
    definition = _readiness_definition(snapshot.envelope)
    design = _design_for_definition(task.research_design_versions[0], definition)
    payload = task.model_dump(mode="json", by_alias=True)
    payload["researchDesignVersions"] = [design.model_dump(mode="json", by_alias=True)]
    coverage = payload["researchDesignVersions"][0]["coverageReport"]
    coverage["claims"][0]["acceptanceCriterionRefs"] = [
        row.criterion_id for row in design.instances[0].acceptance_criteria
    ]
    coverage["targets"][0]["requiredArtifactTypes"] = list(
        design.instances[0].expected_artifact_types
    )
    if include_design:
        instance_id = design.instances[0].substage_instance_id
        projection = _design_artifact_projection(empty_work=empty_work)
        payload["artifacts"] = [
            {
                "artifactId": "artifact-design",
                "workUnitId": "work-design",
                "substageInstanceId": instance_id,
                "sourceCapabilityCallId": None,
                "artifactType": "reproduction_experiment_design",
                "artifactState": "usable",
                "version": 1,
                "sourceRefs": [],
                "validatorResult": projection,
            }
        ]
    else:
        payload["artifacts"] = []
    return payload


def _binding(envelope: dict, skill_id: str, definition_version: int) -> dict:
    return next(
        row
        for row in envelope["bindings"]["skills"]
        if row["definitionId"] == DEFINITION_ID
        and row["definitionVersion"] == definition_version
        and row["skillId"] == skill_id
        and row["enabled"]
    )


def _assert_eligibility(
    envelope: dict,
    *,
    definition_version: int,
    research_task: dict,
    expected: CapabilityApplicabilityStatus,
) -> None:
    definition_identity = (DEFINITION_ID, definition_version)
    results = produce_capability_applicability_results(
        snapshot=envelope,
        definition_identity=definition_identity,
        authoritative_runtime_facts={RESEARCH_TASK_FACT_REF: research_task},
    )
    assert results[RESOURCE_CAPABILITY].status is expected
    assert results[ENVIRONMENT_CAPABILITY].status is expected
    for skill_id, capability_ref in (
        ("resource_identity_acquisition", RESOURCE_CAPABILITY),
        ("environment_setup", ENVIRONMENT_CAPABILITY),
    ):
        eligibility = evaluate_skill_binding_eligibility(
            _binding(envelope, skill_id, definition_version),
            capability_ref=capability_ref,
            authoritative_runtime_fact_refs={RESEARCH_TASK_FACT_REF},
            applicability_results=results,
        )
        assert eligibility is expected


def test_empty_work_withholds_resource_and_environment_capabilities() -> None:
    snapshot = build_registry_snapshot()
    envelope = snapshot.envelope
    definition = _readiness_definition(envelope)
    assert definition["definitionVersion"] == 3
    research_task = _research_task_with_design(include_design=True, empty_work=True)
    _assert_eligibility(
        envelope,
        definition_version=definition["definitionVersion"],
        research_task=research_task,
        expected=CapabilityApplicabilityStatus.INELIGIBLE,
    )


def test_non_empty_work_keeps_resource_and_environment_eligible() -> None:
    snapshot = build_registry_snapshot()
    envelope = snapshot.envelope
    definition = _readiness_definition(envelope)
    research_task = _research_task_with_design(include_design=True, empty_work=False)
    _assert_eligibility(
        envelope,
        definition_version=definition["definitionVersion"],
        research_task=research_task,
        expected=CapabilityApplicabilityStatus.ELIGIBLE,
    )


def test_missing_design_keeps_resource_and_environment_eligible_for_material_unavailable() -> None:
    snapshot = build_registry_snapshot()
    envelope = snapshot.envelope
    definition = _readiness_definition(envelope)
    payload = _research_task_with_design(include_design=False, empty_work=True)
    _assert_eligibility(
        envelope,
        definition_version=definition["definitionVersion"],
        research_task=payload,
        expected=CapabilityApplicabilityStatus.ELIGIBLE,
    )

    locked_task = ResearchTaskV12.model_validate(payload)
    instance = locked_task.research_design_versions[0].instances[0]
    facts, _readiness = build_production_resolution_facts(
        locked_task,
        instance,
        envelope,
    )
    assert RESOURCE_CAPABILITY in facts.authorized_candidate_refs
    assert ENVIRONMENT_CAPABILITY in facts.authorized_candidate_refs
