"""Small execution-test helpers shared by focused API tests."""

from __future__ import annotations

from copy import deepcopy

from app.models.research_orchestration import ResearchTaskV12, RuntimeReadinessSnapshot
from app.modules.research_orchestration_resolver import ResolutionFacts, ResolutionResult

from tests.support.research_lifecycle import (
    build_confirmed_research_task,
    load_structural_research_task_payload,
)


class NoWorkflowLineageRepository:
    """Minimal repository port for tests without a Workflow-backed Design."""

    def load_execution_workflow_lineage(self, **_kwargs):
        return None


def execution_input_context_for_test(
    task: ResearchTaskV12,
    **kwargs,
) -> dict:
    design = next(
        row
        for row in task.research_design_versions
        if row.version == kwargs["research_design_version"]
    )
    latest_state = max(
        task.scientific_states,
        key=lambda row: row.scientific_state_version,
        default=None,
    )
    refs = sorted(str(ref) for ref in kwargs["input_artifact_refs"])
    snapshots = {
        str(row.get("artifactId") or row.get("artifact_id") or ""): dict(row)
        for row in kwargs["input_artifact_snapshots"]
    }
    materials = []
    for ref in refs:
        artifact = next(row for row in task.artifacts if row.artifact_id == ref)
        material = artifact.model_dump(mode="json", by_alias=True)
        material.update(snapshots.get(ref, {}))
        materials.append(material)
    return {
        "canonicalUserQuery": design.goal,
        "executionIdentity": {"task_id": task.task_id},
        "invocationContext": dict(kwargs.get("invocation_context") or {}),
        "scientificState": (
            {} if latest_state is None else dict(latest_state.scientific_facts)
        ),
        "scientificStatePresent": latest_state is not None,
        "inputArtifactRefs": refs,
        "artifacts": materials,
    }


def multi_artifact_selection_fixture(*, completion_coverage: str):
    """Build the focused multi-artifact selection facts used by two test modules."""

    task = build_confirmed_research_task(
        expected_artifact_types=["evidence_summary", "comparison_matrix"]
    )
    structural = load_structural_research_task_payload()
    decision = deepcopy(structural["executionDecisions"][0])
    candidate = deepcopy(decision["candidateEvaluations"][0])
    candidate["preference"]["completionCoverage"] = completion_coverage
    resolution = ResolutionResult(
        designRef={"researchDesignId": "rd_contract_full", "version": 1},
        instanceRef={"substageInstanceId": "sub_contract_full", "instanceRevision": 1},
        registrySnapshotRef=decision["registrySnapshotRef"],
        candidateEvaluations=[candidate],
        preferredCandidateEvaluationRefs=[],
    )
    snapshot = {
        "definitions": {
            "substages": [
                {
                    "definitionId": "sdr:evidence_synthesis",
                    "definitionVersion": 1,
                    "enabled": True,
                    "inputContract": {
                        "requiredInputRefs": {"status": "known", "values": []}
                    },
                }
            ],
            "skills": [
                {
                    "skillId": "evidence_synthesis_skill",
                    "definitionVersion": 1,
                    "sourceContract": {
                        "frozenSchemas": {
                            "input": {
                                "sha256": "a" * 64,
                                "schema": {
                                    "type": "object",
                                    "additionalProperties": False,
                                },
                            }
                        }
                    },
                }
            ],
            "agents": [],
        },
        "bindings": {
            "skills": [
                {
                    "bindingId": "bind:sdr:evidence_synthesis:skill:evidence_synthesis_skill",
                    "bindingVersion": 1,
                    "definitionId": "sdr:evidence_synthesis",
                    "definitionVersion": 1,
                    "skillId": "evidence_synthesis_skill",
                    "skillDefinitionVersion": 1,
                    "enabled": True,
                }
            ],
            "agents": [],
            "agentSkills": [],
        },
    }
    facts = ResolutionFacts(
        runtimeReadiness={},
        providerReadiness={},
        authorizedCandidateRefs=set(),
        availableInputRefs=set(),
        applicableRuleRefs=set(),
        satisfiedHumanGateRefs=set(),
        riskAllowed=True,
        budgetAvailable=True,
        policyVersion="registry-policy.v1",
        activeWorkUnitCounts={},
        lastAssignedAt={},
    )
    readiness = RuntimeReadinessSnapshot.model_validate(decision["runtimeReadiness"][0])
    return task, resolution, snapshot, facts, {"skill:evidence_synthesis_skill:1": readiness}
