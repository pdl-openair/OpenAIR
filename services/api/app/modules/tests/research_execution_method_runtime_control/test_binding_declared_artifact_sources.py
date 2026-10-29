"""Regression tests for binding-declared artifact sources (PVD S3 wiring gap).

Production defect (task_51845983, 2026-10-12): the S2 substage runs three skills
(request_understanding → evidence_retrieval_design → evidence_schema_design)
inside ONE open execution decision. ``_binding_declared_artifact_refs`` filtered
producer artifacts through ``_current_successful_source_decision_ids``, which
requires the producer decision to be CLOSED + SUCCEEDED. An in-flight decision
can never satisfy that, so the evidence_schema_design WorkUnit froze without the
request_understanding artifact, its required binding sources resolved to None,
and the finalizer crashed (AttributeError) — the downstream S3 dispatch then
failed with ``input_contract_invalid``.

Two behaviors are guarded here:

1. Completed LIVE producers inside the SAME decision being prepared are
   admissible binding sources (their USABLE artifacts are durable handoff
   material).
2. Cross-decision sources accept the platform's success-family outcomes
   (``progressed`` / ``partial``), matching repeatable reentry authority —
   previously only ``succeeded`` qualified, so a substage that closed as
   ``progressed`` (the normal PVD S1 close) could never serve as a source.
"""
from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

from app.models.research_orchestration import ResearchTaskV12
from app.modules import research_execution_decision_service as decision_service
from tests.support.research_lifecycle import build_confirmed_research_task

REPO_ROOT = Path(__file__).resolve().parents[3]

PRODUCER_SKILL = "sample_producer_skill"
CONSUMER_SKILL = "sample_consumer_skill"
PRODUCER_BINDING = "bind:pvd:intra_decision:skill:sample_producer_skill"
CONSUMER_BINDING = "bind:pvd:intra_decision:skill:sample_consumer_skill"
DECISION_ID = "decision:intra_decision_open"
ARTIFACT_ID = "artifact:producer:usable:1"


def _snapshot() -> dict:
    return {
        "definitions": {
            "skills": [
                {
                    "skillId": PRODUCER_SKILL,
                    "definitionVersion": 1,
                    "status": "ready",
                    "enabled": True,
                    "sourceContract": {},
                },
                {
                    "skillId": CONSUMER_SKILL,
                    "definitionVersion": 1,
                    "status": "ready",
                    "enabled": True,
                    "sourceContract": {},
                },
            ],
            "substages": [],
            "agents": [],
        },
        "bindings": {
            "skills": [
                {
                    "bindingId": PRODUCER_BINDING,
                    "bindingVersion": 1,
                    "definitionId": "pvd:intra_decision",
                    "definitionVersion": 1,
                    "skillId": PRODUCER_SKILL,
                    "skillDefinitionVersion": 1,
                    "enabled": True,
                },
                {
                    "bindingId": CONSUMER_BINDING,
                    "bindingVersion": 1,
                    "definitionId": "pvd:intra_decision",
                    "definitionVersion": 1,
                    "skillId": CONSUMER_SKILL,
                    "skillDefinitionVersion": 1,
                    "enabled": True,
                    "inputSources": {
                        "contractVersion": 2,
                        "fields": [
                            {
                                "targetField": "producer_packet",
                                "sourcePresence": "required",
                                "source": {
                                    "kind": "artifact",
                                    "bindingId": PRODUCER_BINDING,
                                    "bindingVersion": 1,
                                    "field": "$",
                                },
                            }
                        ],
                    },
                },
            ],
            "agents": [],
            "agentSkills": [],
        },
    }


def _producer_assignment() -> dict:
    return {
        "assignmentId": "assignment:producer",
        "executorRef": {
            "executorId": PRODUCER_SKILL,
            "executorType": "skill",
            "definitionVersion": 1,
        },
        "inputRefs": ["research_goal"],
        "dependencyAssignmentIds": [],
        "sourceBindingRefs": [
            {"bindingId": PRODUCER_BINDING, "bindingVersion": 1}
        ],
        "expectedArtifactTypes": ["producer_packet"],
        "role": "primary",
        "completionCoverage": "progress_capable",
        "acceptanceCriteria": [],
        "allowedSkillRefs": [],
        "objective": "produce the packet",
        "recipeTemplateKey": None,
        "responsibleActorRef": {
            "actorType": "orchestrator",
            "actorId": "openair_orchestrator",
            "definitionVersion": None,
        },
    }


def _consumer_assignment() -> dict:
    return {
        "assignmentId": "assignment:consumer",
        "executorRef": {
            "executorId": CONSUMER_SKILL,
            "executorType": "skill",
            "definitionVersion": 1,
        },
        "inputRefs": ["research_goal"],
        "dependencyAssignmentIds": ["assignment:producer"],
        "sourceBindingRefs": [
            {"bindingId": CONSUMER_BINDING, "bindingVersion": 1}
        ],
        "expectedArtifactTypes": ["consumer_output"],
        "role": "primary",
        "completionCoverage": "progress_capable",
        "acceptanceCriteria": [],
        "allowedSkillRefs": [],
        "objective": "consume the packet",
        "recipeTemplateKey": None,
        "responsibleActorRef": {
            "actorType": "orchestrator",
            "actorId": "openair_orchestrator",
            "definitionVersion": None,
        },
    }


def _producer_work_unit(instance: dict) -> dict:
    return {
        "workUnitId": "workunit:producer",
        "taskId": None,
        "assignmentId": "assignment:producer",
        "executionDecisionId": DECISION_ID,
        "executionGroupId": DECISION_ID,
        "executorRef": {
            "executorId": PRODUCER_SKILL,
            "executorType": "skill",
            "definitionVersion": 1,
        },
        "status": "completed",
        "sourceMode": "live",
        "dispatchable": True,
        "researchDesignVersion": 1,
        "registrySnapshotRef": {
            "schemaVersion": 1,
            "contentHash": "sha256:" + "a" * 64,
        },
        "substageInstanceId": instance["substageInstanceId"],
        "substageInstanceRevision": instance["instanceRevision"],
        "substageDefinitionId": instance["substageDefinitionId"],
        "substageDefinitionVersion": instance["substageDefinitionVersion"],
        "inputArtifactRefs": [],
        "inputArtifactSnapshots": [],
        "inputMemoryRefs": ["research_goal"],
        "dependencyWorkUnitIds": [],
        "frozenAt": "2026-10-12T16:27:34.000000Z",
        "dispatchedAt": "2026-10-12T16:27:34.000000Z",
        "finishedAt": "2026-10-12T16:28:53.000000Z",
        "idempotencyKey": f"workunit:{DECISION_ID}:assignment:producer",
        "instruction": "produce",
        "invocationContext": {},
        "riskSnapshot": {
            "boundary": {
                "maxRiskClass": "low",
                "prohibitedActions": ["external_publish"],
                "humanGateTriggers": [],
            },
            "evaluatedRiskClass": "low",
            "triggeredHumanGates": [],
        },
        "budgetSnapshot": {
            "boundary": {
                "maxModelCalls": 2,
                "maxToolCalls": 1,
                "maxWallTimeSec": 120,
                "maxCostAmount": 2.0,
                "currency": "USD",
            },
            "consumedModelCalls": 0,
            "consumedToolCalls": 0,
            "consumedCostAmount": 0.0,
        },
        "authorizationSnapshot": {
            "policyVersion": "authorization.v1",
            "allowedMemoryScopes": ["task"],
            "allowedExternalActionKinds": [],
            "confirmationRefs": ["confirmation_design_contract_full"],
        },
        "policyVersion": "execution-policy.v2.automatic_research",
        "stopConditions": ["acceptance_policy_terminal"],
        "terminalReasonCode": None,
        "expectedArtifactTypes": ["producer_packet"],
        "acceptanceCriteria": [],
        "allowedSkillRefs": [],
        "responsibleActorRef": {
            "actorType": "orchestrator",
            "actorId": "openair_orchestrator",
            "definitionVersion": None,
        },
    }


def _decision_payload(*, status: str, outcome: str | None) -> dict:
    return {
        "executionDecisionId": DECISION_ID,
        "taskId": None,
        "substageInstanceId": "sub_contract_full",
        "substageInstanceRevision": 1,
        "researchDesignVersion": 1,
        "registrySnapshotRef": {
            "schemaVersion": 1,
            "contentHash": "sha256:" + "a" * 64,
        },
        "strategy": "skill_composition",
        "status": status,
        "outcome": outcome,
        "decisionSequence": 1,
        "decidedAt": "2026-10-12T16:27:30.000000Z",
        "closedAt": None if status != "closed" else "2026-10-12T16:31:00.000000Z",
        "selectionRequestId": "selection-intra",
        "assignments": [_producer_assignment(), _consumer_assignment()],
        "candidateEvaluations": [],
        "runtimeReadiness": [],
        "preferredCandidateEvaluationRefs": [],
        "progressPolicy": {
            "requiredAssignmentIds": ["assignment:producer"],
            "requiredArtifactTypes": [],
            "acceptedResultCodes": "progressed",
        },
        "completionPolicy": None,
        "resultAssemblyPolicy": {
            "mode": "primary_output",
            "assemblerAssignmentId": None,
            "finalArtifactRequirements": [
                {
                    "artifactType": "producer_packet",
                    "minimumCount": 1,
                    "sourceAssignmentIds": ["assignment:producer"],
                }
            ],
        },
        "budgetSnapshot": {
            "boundary": {
                "maxModelCalls": 2,
                "maxToolCalls": 1,
                "maxWallTimeSec": 120,
                "maxCostAmount": 2.0,
                "currency": "USD",
            },
            "consumedModelCalls": 0,
            "consumedToolCalls": 0,
            "consumedCostAmount": 0.0,
        },
        "invocationContext": {},
        "multiAgentRecipeRef": None,
        "policyVersion": "execution-policy.v2.automatic_research",
        "purpose": "progress",
        "rationale": "test",
        "fallbackInvoked": False,
        "fallbackReasonCode": None,
        "revisionReasonCode": None,
        "supersedesExecutionDecisionId": None,
    }


def _artifact_payload() -> dict:
    return {
        "artifactId": ARTIFACT_ID,
        "workUnitId": "workunit:producer",
        "substageInstanceId": "sub_contract_full",
        "sourceCapabilityCallId": "call:dispatch:producer",
        "artifactType": "producer_packet",
        "artifactState": "usable",
        "version": 1,
        "sourceRefs": [],
        "validatorResult": {"status": "passed", "contentSha256": "a" * 64},
        "lineageKind": "capability_call_output",
        "sourceResultId": None,
    }


def _capability_call_payload() -> dict:
    return {
        "capabilityCallId": "call:dispatch:producer",
        "idempotencyKey": "call:workunit:producer:1",
        "attemptNumber": 1,
        "workUnitId": "workunit:producer",
        "substageInstanceId": "sub_contract_full",
        "requestingActorRef": {
            "actorType": "orchestrator",
            "actorId": "openair_orchestrator",
            "definitionVersion": None,
        },
        "capabilityType": "skill",
        "capabilityId": PRODUCER_SKILL,
        "runtimeBindingId": f"python_adapter:{PRODUCER_SKILL}",
        "status": "succeeded",
        "inputRefs": [],
        "outputArtifactRefs": [ARTIFACT_ID],
        "startedAt": "2026-10-12T16:27:40.000000Z",
        "finishedAt": "2026-10-12T16:28:50.000000Z",
    }


def _task_with(*, decision_status: str, decision_outcome: str | None) -> ResearchTaskV12:
    task = build_confirmed_research_task()
    payload = task.model_dump(mode="json", by_alias=True)
    instance = payload["researchDesignVersions"][0]["instances"][0]
    task_id = payload["taskId"]
    producer = _producer_work_unit(instance)
    producer["taskId"] = task_id
    payload["workUnits"] = [producer]
    decision = _decision_payload(status=decision_status, outcome=decision_outcome)
    decision["taskId"] = task_id
    payload["executionDecisions"] = [decision]
    payload["artifacts"] = [_artifact_payload()]
    payload["capabilityCalls"] = [_capability_call_payload()]
    return ResearchTaskV12.model_validate(payload)


def _consumer_assignment_model(task: ResearchTaskV12):
    decision = next(
        row for row in task.execution_decisions
        if row.execution_decision_id == DECISION_ID
    )
    return next(
        row for row in decision.assignments
        if row.assignment_id == "assignment:consumer"
    )


def test_same_decision_completed_producer_is_admissible_binding_source() -> None:
    """In-flight decision producers are durable handoff material (PVD S3 fix)."""
    task = _task_with(decision_status="dispatched", decision_outcome=None)
    refs = decision_service._binding_declared_artifact_refs(
        task,
        snapshot=_snapshot(),
        assignment=_consumer_assignment_model(task),
        research_design_version=1,
        same_decision_id=DECISION_ID,
    )
    assert refs == [ARTIFACT_ID]


def test_cross_decision_open_producer_stays_inadmissible() -> None:
    """The same-decision pass must not leak into foreign in-flight decisions."""
    task = _task_with(decision_status="dispatched", decision_outcome=None)
    refs = decision_service._binding_declared_artifact_refs(
        task,
        snapshot=_snapshot(),
        assignment=_consumer_assignment_model(task),
        research_design_version=1,
        same_decision_id="decision:another_open_decision",
    )
    assert refs == []


def test_cross_decision_progressed_outcome_is_admissible_source() -> None:
    """Closed `progressed` decisions are success-family (PVD S1 close fix)."""
    task = _task_with(decision_status="closed", decision_outcome="progressed")
    refs = decision_service._binding_declared_artifact_refs(
        task,
        snapshot=_snapshot(),
        assignment=_consumer_assignment_model(task),
        research_design_version=1,
        same_decision_id="decision:another_open_decision",
    )
    assert refs == [ARTIFACT_ID]


def test_cross_decision_failed_outcome_stays_inadmissible() -> None:
    task = _task_with(decision_status="closed", decision_outcome="failed")
    refs = decision_service._binding_declared_artifact_refs(
        task,
        snapshot=_snapshot(),
        assignment=_consumer_assignment_model(task),
        research_design_version=1,
        same_decision_id="decision:another_open_decision",
    )
    assert refs == []
