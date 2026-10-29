"""Focused known-bad / recovery / known-good coverage for multi typed Artifacts."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.execution.research_dispatch_provider import DispatchSubmissionCompleted
from app.models.research_orchestration import ResearchTaskV12, RuntimeReadinessSnapshot
from app.modules.research_execution_decision_service import ExecutionPreflightBlocked
from app.modules import research_execution_decision_service as decision_service
from app.modules.research_orchestration_repository import (
    AuthorizedDispatch,
    ResearchOrchestrationPersistenceError,
    ResearchOrchestrationRepository,
    dispatch_capability_call_id,
)
from app.modules.research_orchestration_resolver import ResolutionResult
from tests.support.research_execution import (
    multi_artifact_selection_fixture as _multi_artifact_selection_fixture,
)
from tests.support.research_execution_decision import (
    freeze_prepared_selection_for_test as _freeze_prepared_selection_for_test,
)
from tests.support.research_lifecycle import build_confirmed_research_task


FIXTURE = (
    Path(__file__).resolve().parents[3]
    / "packages"
    / "shared"
    / "test-fixtures"
    / "research-orchestration-v1.2-valid.json"
)
NOW = datetime(2026, 7, 28, 0, 2, tzinfo=timezone.utc)
EXPECTED_TYPES = (
    "structured_context",
    "recommendation_parameter_set",
    "run_preferences",
)
NODE1_OUTPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": list(EXPECTED_TYPES),
    "properties": {
        "structured_context": {"type": "object"},
        "recommendation_parameter_set": {"type": "object"},
        "run_preferences": {"type": "object"},
        # Optional / non-required noise must never become an Artifact by itself.
        "noise_field": {"type": "string"},
    },
}
SNAPSHOT_HASH = (
    "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
)


def _repo(
    *,
    output_schema: dict | None = None,
) -> ResearchOrchestrationRepository:
    schema = deepcopy(NODE1_OUTPUT_SCHEMA if output_schema is None else output_schema)
    snapshot = {
        "definitions": {
            "substages": [],
            "skills": [
                {
                    "skillId": "pvd_research_request_understanding",
                    "definitionVersion": 1,
                    "sourceContract": {
                        "frozenSchemas": {
                            "output": {"sha256": "b" * 64, "schema": schema}
                        }
                    },
                }
            ],
            "agents": [],
        }
    }
    repository = ResearchOrchestrationRepository.__new__(ResearchOrchestrationRepository)
    repository.load_registry_snapshot = lambda *_args, **_kwargs: snapshot
    repository._reconcile_task_lifecycle_with_frozen_snapshot = (
        lambda task, occurred_at=None: task
    )
    return repository


def _provider_payload() -> str:
    return json.dumps(
        {
            "structured_context": {"contract": {"kind": "pvd", "version": "1"}},
            "recommendation_parameter_set": {"parameters": [{"parameter_key": "power"}]},
            "run_preferences": {"batch_size": 1},
            "noise_field": "must-not-be-copied",
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _completion_task_and_fence() -> tuple[ResearchTaskV12, AuthorizedDispatch]:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))["researchTask"]
    decision = payload["executionDecisions"][0]
    assignment = decision["assignments"][0]
    decision.update(
        {
            "purpose": "completion",
            "status": "dispatched",
            "outcome": None,
            "closedAt": None,
            "registrySnapshotRef": {
                "schemaVersion": 1,
                "contentHash": SNAPSHOT_HASH,
            },
            "resultAssemblyPolicy": {
                "mode": "artifact_set",
                "assemblerAssignmentId": None,
                "finalArtifactRequirements": [
                    {
                        "artifactType": artifact_type,
                        "minimumCount": 1,
                        "sourceAssignmentIds": [assignment["assignmentId"]],
                    }
                    for artifact_type in EXPECTED_TYPES
                ],
            },
        }
    )
    assignment["expectedArtifactTypes"] = list(EXPECTED_TYPES)
    assignment["completionCoverage"] = "completion_capable"
    assignment["executorRef"] = {
        "executorType": "skill",
        "executorId": "pvd_research_request_understanding",
        "definitionVersion": 1,
    }
    payload["workUnits"][0].update(
        {
            "expectedArtifactTypes": list(EXPECTED_TYPES),
            "status": "running",
            "finishedAt": None,
            "responsibleActorRef": assignment["responsibleActorRef"],
            "executorRef": assignment["executorRef"],
            "allowedSkillRefs": assignment.get("allowedSkillRefs") or [],
        }
    )
    payload["artifacts"] = []
    payload["results"] = []
    payload["agentInteractionRecords"] = []
    payload["substageRuntimeStates"][0].update(
        {
            "status": "running",
            "activeExecutionDecisionId": decision["executionDecisionId"],
            "artifactRefs": [],
        }
    )
    fence = AuthorizedDispatch(
        dispatch_id="dispatch-multi-artifact",
        task_id=payload["taskId"],
        execution_decision_id=decision["executionDecisionId"],
        work_unit_id=payload["workUnits"][0]["workUnitId"],
        idempotency_key="workunit:multi-artifact",
        lease_owner="worker-multi",
        attempt_count=1,
        authorization_task_revision=payload["revision"],
        work_unit_payload={
            "executorRef": {
                "executorType": "skill",
                "executorId": "pvd_research_request_understanding",
                "definitionVersion": 1,
            }
        },
    )
    payload["capabilityCalls"][0].update(
        {
            "capabilityCallId": dispatch_capability_call_id(fence),
            "idempotencyKey": "call:multi-artifact:1",
            "status": "running",
            "outputArtifactRefs": [],
            "finishedAt": None,
        }
    )
    return ResearchTaskV12.model_validate(payload), fence


def _progress_task_and_fence(
    expected_types: tuple[str, ...] = EXPECTED_TYPES,
) -> tuple[ResearchTaskV12, AuthorizedDispatch]:
    task, fence = _completion_task_and_fence()
    payload = task.model_dump(mode="json", by_alias=True)
    decision = payload["executionDecisions"][0]
    assignment = decision["assignments"][0]
    decision.update(
        {
            "purpose": "progress",
            "completionPolicy": None,
            "progressPolicy": {
                "requiredAssignmentIds": [assignment["assignmentId"]],
                "requiredArtifactTypes": [],
                "acceptedResultCodes": "progressed",
            },
            "resultAssemblyPolicy": {
                "mode": "primary_output",
                "assemblerAssignmentId": None,
                "finalArtifactRequirements": [],
            },
        }
    )
    decision["candidateEvaluations"][0]["preference"][
        "completionCoverage"
    ] = "progress_capable"
    assignment["completionCoverage"] = "progress_capable"
    assignment["expectedArtifactTypes"] = list(expected_types)
    payload["workUnits"][0]["expectedArtifactTypes"] = list(expected_types)
    return ResearchTaskV12.model_validate(payload), fence


def _schema_validated_multi_artifact_fixture():
    """Single primary Skill composition that formally owns three Artifact types."""
    task = build_confirmed_research_task(expected_artifact_types=list(EXPECTED_TYPES))
    structural = json.loads(FIXTURE.read_text(encoding="utf-8"))["researchTask"]
    decision = deepcopy(structural["executionDecisions"][0])
    candidate = deepcopy(decision["candidateEvaluations"][0])
    candidate["preference"]["completionCoverage"] = "completion_capable"
    candidate["candidateRef"] = {
        "skillId": "pvd_research_request_understanding",
        "definitionVersion": 1,
    }
    candidate["candidateEvaluationId"] = "eval:pvd_request_understanding"
    candidate["sourceBindingRefs"] = [
        {
            "bindingId": "bind:pvd:research_goal_equipment_constraints:skill:pvd_research_request_understanding",
            "bindingVersion": 1,
        }
    ]
    candidate["eligible"] = True
    candidate["preference"]["executableNow"] = True
    resolution = ResolutionResult(
        designRef={"researchDesignId": "rd_contract_full", "version": 1},
        instanceRef={"substageInstanceId": "sub_contract_full", "instanceRevision": 1},
        registrySnapshotRef=decision["registrySnapshotRef"],
        candidateEvaluations=[candidate],
        preferredCandidateEvaluationRefs=[],
    )
    snapshot = {
        "schemaVersion": 1,
        "definitions": {
            "substages": [
                {
                    "definitionId": "sdr:evidence_synthesis",
                    "definitionVersion": 1,
                    "pack": "pvd",
                    "inputContract": {
                        "requiredInputRefs": {"status": "known", "values": []}
                    },
                    "executionComposition": {
                        "semantics": "ordered_required",
                        "workflowId": "pvd_process_optimization_workflow",
                        "skillPack": "pvd",
                        "sourceAuthority": "schemas/pvd/substage-skill-map.yaml",
                        "requiredSkillRefs": [
                            {
                                "skillId": "pvd_research_request_understanding",
                                "definitionVersion": 1,
                            }
                        ],
                    },
                }
            ],
            "skills": [
                {
                    "skillId": "pvd_research_request_understanding",
                    "definitionVersion": 1,
                    "sourceContract": {
                        "review_policy": {
                            "contract_version": 1,
                            "mode": "schema_validated_only",
                        },
                        "structured_output": {"contract_version": 1},
                        "frozenSchemas": {
                            "input": {
                                "sha256": "a" * 64,
                                "schema": {
                                    "type": "object",
                                    "additionalProperties": False,
                                },
                            },
                            "output": {
                                "sha256": "b" * 64,
                                "schema": deepcopy(NODE1_OUTPUT_SCHEMA),
                            },
                        },
                    },
                }
            ],
            "agents": [],
        },
        "bindings": {
            "skills": [
                {
                    "bindingId": "bind:pvd:research_goal_equipment_constraints:skill:pvd_research_request_understanding",
                    "bindingVersion": 1,
                    "definitionId": "sdr:evidence_synthesis",
                    "definitionVersion": 1,
                    "skillId": "pvd_research_request_understanding",
                    "skillDefinitionVersion": 1,
                    "enabled": True,
                }
            ],
            "agents": [],
            "agentSkills": [],
        },
    }
    from app.modules.research_orchestration_resolver import ResolutionFacts

    facts = ResolutionFacts(
        runtimeReadiness={},
        providerReadiness={},
        authorizedCandidateRefs={"skill:pvd_research_request_understanding:1"},
        availableInputRefs=set(),
        applicableRuleRefs=set(),
        satisfiedHumanGateRefs=set(),
        riskAllowed=True,
        budgetAvailable=True,
        policyVersion="registry-policy.v1",
        activeWorkUnitCounts={},
        lastAssignedAt={},
    )
    readiness_row = deepcopy(decision["runtimeReadiness"][0])
    readiness_row["capabilityRef"] = {
        "skillId": "pvd_research_request_understanding",
        "definitionVersion": 1,
    }
    readiness_row["runtimeBindingId"] = (
        "registry:skill:pvd_research_request_understanding:1"
    )
    readiness = {
        "skill:pvd_research_request_understanding:1": RuntimeReadinessSnapshot.model_validate(
            readiness_row
        )
    }
    return task, resolution, snapshot, facts, readiness


def test_known_bad_single_skill_three_artifacts_complete() -> None:
    task, fence = _completion_task_and_fence()
    content = _provider_payload()
    content_sha = hashlib.sha256(content.encode("utf-8")).hexdigest()

    updated, catalog_artifacts, _dispatches, _message = _repo()._task_with_completed_result(
        task,
        fence=fence,
        result=DispatchSubmissionCompleted(
            title="PVD request understanding",
            mime_type="application/json",
            content=content,
            provider_id="direct_skill_runtime",
            adapter="durable_skill_runtime",
        ),
        artifact_contract="direct_skill:pvd_research_request_understanding:v1",
        artifact_schema_digest="a" * 64,
        content_sha256=content_sha,
        storage_type="external",
        storage_scope=task.task_id,
        storage_ref="durable/research-dispatch/dispatch-multi-artifact/result.json",
        now=NOW,
    )

    refs = [
        f"artifact:dispatch-multi-artifact:{artifact_type}"
        for artifact_type in EXPECTED_TYPES
    ]
    assert [row.artifact_type for row in updated.artifacts] == list(EXPECTED_TYPES)
    assert [row.artifact_id for row in updated.artifacts] == refs
    assert updated.capability_calls[0].output_artifact_refs == refs
    assert updated.results[0].artifact_refs == refs
    assert updated.work_units[0].status.value == "completed"
    assert updated.execution_decisions[0].status.value == "closed"
    assert updated.execution_decisions[0].outcome.value == "succeeded"
    assert isinstance(catalog_artifacts, list)
    assert len(catalog_artifacts) == 3
    source = json.loads(content)
    for entry, artifact_type in zip(catalog_artifacts, EXPECTED_TYPES, strict=True):
        body = json.loads(entry["contentOverride"])
        assert body == source[artifact_type]
        assert "noise_field" not in body
        assert entry["metadata"]["artifactType"] == artifact_type
    assert "noise_field" not in {row.artifact_type for row in updated.artifacts}


def test_progress_projection_uses_frozen_work_unit_result_identity() -> None:
    task, fence = _progress_task_and_fence()
    provider_payload = json.loads(_provider_payload())
    provider_payload["structured_context"] = {
        "experimental_system_context": {
            "contract": "pvd-experimental-system-context/v1",
            "context_status": "partial",
            "target_material": None,
            "substrate": None,
            "target_substrate_distance_mm": None,
            "power_supply_temperature": None,
            "target_angle": None,
            "field_metadata": {
                field: {
                    "status": "missing",
                    "reason": "not reported",
                    "provenance": [],
                }
                for field in (
                    "target_material",
                    "substrate",
                    "target_substrate_distance_mm",
                    "power_supply_temperature",
                    "target_angle",
                )
            },
            "additional_facts": [],
        },
        "rounds": [],
    }
    content = json.dumps(
        provider_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    projected = {
        "pvd_research_request_context": provider_payload["structured_context"]
    }

    updated, catalog_artifacts, _dispatches, _message = (
        _repo()._task_with_completed_result(
            task,
            fence=fence,
            result=DispatchSubmissionCompleted(
                title="PVD request understanding",
                mime_type="application/json",
                content=content,
                provider_id="direct_skill_runtime",
                adapter="durable_skill_runtime",
            ),
            artifact_contract="direct_skill:pvd_research_request_understanding:v1",
            artifact_schema_digest="a" * 64,
            content_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
            storage_type="external",
            storage_scope=task.task_id,
            storage_ref="durable/research-dispatch/dispatch-multi-artifact/result.json",
            now=NOW,
            scientific_state_fact_projections=projected,
            scientific_state_projection_declaration={
                "contract_version": 2,
                "materializer_ref": "pvd:research-request-understanding:v1",
                "facts": [
                    {
                        "fact_key": "pvd_research_request_context",
                        "source_field": "structured_context",
                    }
                ],
            },
        )
    )

    assert [row.artifact_type for row in updated.artifacts] == list(EXPECTED_TYPES)
    assert updated.results[0].artifact_refs == [
        f"artifact:dispatch-multi-artifact:{artifact_type}"
        for artifact_type in EXPECTED_TYPES
    ]
    assert updated.results[0].scientific_state_versions
    assert updated.scientific_states[-1].source_refs == [
        "artifact:dispatch-multi-artifact:structured_context"
    ]
    assert isinstance(catalog_artifacts, list)


def test_infrastructure_progress_without_result_identity_keeps_zero_artifacts() -> None:
    task, fence = _progress_task_and_fence(())

    updated, catalog_artifact, _dispatches, _message = (
        _repo()._task_with_completed_result(
            task,
            fence=fence,
            result=DispatchSubmissionCompleted(
                title="Cache refresh",
                mime_type="application/json",
                content='{"status":"refreshed"}',
                provider_id="infrastructure",
                adapter="internal",
            ),
            artifact_contract="infrastructure_progress_v1",
            artifact_schema_digest="a" * 64,
            content_sha256="b" * 64,
            storage_type="local",
            storage_scope=task.task_id,
            storage_ref="durable/research-dispatch/dispatch-multi-artifact/result.json",
            now=NOW,
        )
    )

    assert updated.artifacts == []
    assert updated.results[0].artifact_refs == []
    assert catalog_artifact is None


def test_progress_projection_without_frozen_result_identity_fails_closed() -> None:
    task, fence = _progress_task_and_fence(())

    with pytest.raises(
        ResearchOrchestrationPersistenceError,
        match="scientific_state_projection_without_artifact",
    ):
        _repo()._task_with_completed_result(
            task,
            fence=fence,
            result=DispatchSubmissionCompleted(
                title="Untraceable projection",
                mime_type="application/json",
                content='{"fact":"value"}',
                provider_id="direct_skill_runtime",
                adapter="durable_skill_runtime",
            ),
            artifact_contract="projection_v1",
            artifact_schema_digest="a" * 64,
            content_sha256="b" * 64,
            storage_type="local",
            storage_scope=task.task_id,
            storage_ref="durable/research-dispatch/dispatch-multi-artifact/result.json",
            now=NOW,
            scientific_state_fact_projections={"fact": "value"},
            scientific_state_projection_declaration={
                "contract_version": 1,
                "facts": [{"fact_key": "fact", "source_field": "fact"}],
            },
        )


def test_markdown_result_is_the_artifact_content_with_provenance() -> None:
    task, fence = _completion_task_and_fence()
    payload = task.model_dump(mode="json", by_alias=True)
    assignment = payload["executionDecisions"][0]["assignments"][0]
    assignment["expectedArtifactTypes"] = ["research_note"]
    payload["workUnits"][0]["expectedArtifactTypes"] = ["research_note"]
    payload["executionDecisions"][0]["resultAssemblyPolicy"] = {
        "mode": "primary_output",
        "assemblerAssignmentId": None,
        "finalArtifactRequirements": [
            {
                "artifactType": "research_note",
                "minimumCount": 1,
                "sourceAssignmentIds": [assignment["assignmentId"]],
            }
        ],
    }
    task = ResearchTaskV12.model_validate(payload)
    content = "# Finding\n\nThe observation is reproducible.\n"

    updated, catalog_artifact, _dispatches, _message = (
        _repo()._task_with_completed_result(
            task,
            fence=fence,
            result=DispatchSubmissionCompleted(
                title="Research note",
                mime_type="text/markdown",
                content=content,
                provider_id="direct_skill_runtime",
                adapter="durable_skill_runtime",
            ),
            artifact_contract="research_note_v1",
            artifact_schema_digest="a" * 64,
            content_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
            storage_type="local",
            storage_scope=task.task_id,
            storage_ref="durable/research-dispatch/dispatch-multi-artifact/result.json",
            now=NOW,
        )
    )

    artifact = updated.artifacts[0]
    assert artifact.artifact_type == "research_note"
    assert artifact.source_result_id == updated.results[0].result_id
    assert updated.results[0].artifact_refs == [artifact.artifact_id]
    assert catalog_artifact["format"] == "markdown"
    assert "contentOverride" not in catalog_artifact


def test_schema_required_field_removed_rejects_mapping() -> None:
    task, fence = _completion_task_and_fence()
    broken = deepcopy(NODE1_OUTPUT_SCHEMA)
    broken["required"] = [
        "structured_context",
        "recommendation_parameter_set",
    ]
    with pytest.raises(
        ResearchOrchestrationPersistenceError,
        match="producer_multi_artifact_schema_unauthorized",
    ):
        _repo(output_schema=broken)._task_with_completed_result(
            task,
            fence=fence,
            result=DispatchSubmissionCompleted(
                title="PVD request understanding",
                mime_type="application/json",
                content=_provider_payload(),
                provider_id="direct_skill_runtime",
                adapter="durable_skill_runtime",
            ),
            artifact_contract="direct_skill:pvd_research_request_understanding:v1",
            artifact_schema_digest="a" * 64,
            content_sha256=hashlib.sha256(_provider_payload().encode("utf-8")).hexdigest(),
            storage_type="external",
            storage_scope=task.task_id,
            storage_ref="durable/research-dispatch/dispatch-multi-artifact/result.json",
            now=NOW,
        )


def test_schema_required_field_renamed_rejects_mapping() -> None:
    task, fence = _completion_task_and_fence()
    broken = deepcopy(NODE1_OUTPUT_SCHEMA)
    broken["required"] = [
        "structured_context",
        "recommendation_parameter_set",
        "run_prefs",
    ]
    broken["properties"]["run_prefs"] = broken["properties"].pop("run_preferences")
    with pytest.raises(
        ResearchOrchestrationPersistenceError,
        match="producer_multi_artifact_schema_unauthorized",
    ):
        _repo(output_schema=broken)._task_with_completed_result(
            task,
            fence=fence,
            result=DispatchSubmissionCompleted(
                title="PVD request understanding",
                mime_type="application/json",
                content=_provider_payload(),
                provider_id="direct_skill_runtime",
                adapter="durable_skill_runtime",
            ),
            artifact_contract="direct_skill:pvd_research_request_understanding:v1",
            artifact_schema_digest="a" * 64,
            content_sha256=hashlib.sha256(_provider_payload().encode("utf-8")).hexdigest(),
            storage_type="external",
            storage_scope=task.task_id,
            storage_ref="durable/research-dispatch/dispatch-multi-artifact/result.json",
            now=NOW,
        )


def test_recovery_resumes_missing_artifacts_without_provider_rerun() -> None:
    task, fence = _completion_task_and_fence()
    content = _provider_payload()
    content_sha = hashlib.sha256(content.encode("utf-8")).hexdigest()
    first_type = EXPECTED_TYPES[0]
    payload = task.model_dump(mode="json", by_alias=True)
    call_id = dispatch_capability_call_id(fence)
    result_id = f"result:{fence.dispatch_id}"
    first_artifact_id = f"artifact:{fence.dispatch_id}:{first_type}"
    payload["results"] = [
        {
            "resultId": result_id,
            "workUnitId": fence.work_unit_id,
            "capabilityCallId": call_id,
            "storageType": "external",
            "storageScope": task.task_id,
            "storageRef": "durable/research-dispatch/dispatch-multi-artifact/result.json",
            "contentSha256": content_sha,
            "mimeType": "application/json",
            "checkStatus": "passed",
            "checkResult": {
                "reasonCode": "initial_contract_validation_passed",
                "artifactContract": "direct_skill:pvd_research_request_understanding:v1",
                "artifactSchemaDigest": "a" * 64,
            },
            "sourceRefs": [],
            "artifactRefs": [first_artifact_id],
            "scientificStateVersions": [],
            "createdAt": "2026-07-28T00:01:30Z",
        }
    ]
    payload["artifacts"] = [
        {
            "artifactId": first_artifact_id,
            "workUnitId": fence.work_unit_id,
            "substageInstanceId": payload["workUnits"][0]["substageInstanceId"],
            "sourceCapabilityCallId": call_id,
            "artifactType": first_type,
            "artifactState": "usable",
            "version": 1,
            "sourceRefs": [],
            "validatorResult": {"status": "passed"},
            "lineageKind": "capability_call_output",
            "sourceResultId": result_id,
        }
    ]
    payload["capabilityCalls"][0].update(
        {
            "status": "external_result_unknown",
            "outputArtifactRefs": [first_artifact_id],
            "finishedAt": "2026-07-28T00:01:30Z",
        }
    )
    payload["workUnits"][0].update(
        {
            "status": "external_result_unknown",
            "finishedAt": "2026-07-28T00:01:30Z",
        }
    )
    interrupted = ResearchTaskV12.model_validate(payload)
    recovery_fence = AuthorizedDispatch(
        **{**fence.__dict__, "result_recovery_from_unknown": True}
    )

    recovered, catalog_artifacts, _dispatches, message = _repo()._task_with_completed_result(
        interrupted,
        fence=recovery_fence,
        result=DispatchSubmissionCompleted(
            title="PVD request understanding",
            mime_type="application/json",
            content=content,
            provider_id="direct_skill_runtime",
            adapter="durable_skill_runtime",
        ),
        artifact_contract="direct_skill:pvd_research_request_understanding:v1",
        artifact_schema_digest="a" * 64,
        content_sha256=content_sha,
        storage_type="external",
        storage_scope=task.task_id,
        storage_ref="durable/research-dispatch/dispatch-multi-artifact/result.json",
        now=NOW,
    )

    refs = [
        f"artifact:dispatch-multi-artifact:{artifact_type}"
        for artifact_type in EXPECTED_TYPES
    ]
    assert [row.artifact_id for row in recovered.artifacts] == refs
    assert recovered.results[0].artifact_refs == refs
    assert recovered.capability_calls[0].output_artifact_refs == refs
    assert recovered.work_units[0].status.value == "completed"
    assert recovered.execution_decisions[0].outcome.value == "succeeded"
    assert isinstance(catalog_artifacts, list)
    assert [row["artifact_id"] for row in catalog_artifacts] == refs[1:]
    assert message is None


def test_known_good_single_artifact_completion_identity_unchanged() -> None:
    task, fence = _completion_task_and_fence()
    payload = task.model_dump(mode="json", by_alias=True)
    payload["executionDecisions"][0]["resultAssemblyPolicy"] = {
        "mode": "primary_output",
        "assemblerAssignmentId": None,
        "finalArtifactRequirements": [
            {
                "artifactType": "structured_context",
                "minimumCount": 1,
                "sourceAssignmentIds": [
                    payload["executionDecisions"][0]["assignments"][0]["assignmentId"]
                ],
            }
        ],
    }
    payload["executionDecisions"][0]["assignments"][0]["expectedArtifactTypes"] = [
        "structured_context"
    ]
    payload["workUnits"][0]["expectedArtifactTypes"] = ["structured_context"]
    task = ResearchTaskV12.model_validate(payload)
    content = json.dumps({"structured_context": {"ok": True}}, separators=(",", ":"))

    updated, catalog_artifact, _dispatches, _message = _repo()._task_with_completed_result(
        task,
        fence=fence,
        result=DispatchSubmissionCompleted(
            title="single",
            mime_type="application/json",
            content=content,
            provider_id="direct_skill_runtime",
            adapter="durable_skill_runtime",
        ),
        artifact_contract="direct_skill:single:v1",
        artifact_schema_digest="a" * 64,
        accepted_artifact_content=content,
        source_identity_acceptance={
            "status": "validated",
            "acceptedCount": 1,
            "rejectedCount": 0,
            "records": [{
                "recordPath": "/paper_packets/0",
                "status": "accepted",
                "reasons": [],
            }],
        },
        content_sha256="b" * 64,
        storage_type="external",
        storage_scope=task.task_id,
        storage_ref="durable/research-dispatch/dispatch-multi-artifact/result.json",
        now=NOW,
    )

    assert catalog_artifact["artifact_id"] == "artifact:dispatch-multi-artifact"
    assert updated.artifacts[0].artifact_type == "structured_context"
    assert updated.capability_calls[0].output_artifact_refs == [
        "artifact:dispatch-multi-artifact"
    ]
    result = updated.results[0]
    artifact = updated.artifacts[0]
    assert result.check_result["sourceIdentityAcceptance"]["acceptedCount"] == 1
    assert artifact.validator_result["sourceIdentityAcceptance"]["acceptedCount"] == 1
    assert artifact.source_result_id == result.result_id


def test_true_no_coverage_still_fail_closed() -> None:
    task, resolution, snapshot, facts, readiness = _multi_artifact_selection_fixture(
        completion_coverage="completion_capable"
    )
    # Force every candidate into a non-executable preference state.
    rebuilt = []
    for row in resolution.candidate_evaluations:
        payload = row.model_dump(mode="json", by_alias=True)
        payload["eligible"] = True
        payload["preference"]["executableNow"] = False
        payload["preference"]["executionBlockerCodes"] = ["provider_unavailable"]
        rebuilt.append(type(row).model_validate(payload))
    resolution = ResolutionResult(
        designRef=resolution.design_ref,
        instanceRef=resolution.instance_ref,
        registrySnapshotRef=resolution.registry_snapshot_ref,
        candidateEvaluations=rebuilt,
        preferredCandidateEvaluationRefs=[],
    )

    with pytest.raises(ExecutionPreflightBlocked) as blocked:
        decision_service.prepare_selection(
            task,
            instance_id="sub_contract_full",
            resolution=resolution,
            snapshot_envelope=snapshot,
            facts=facts,
            runtime_readiness=readiness,
            selection_request_id="selection-no-coverage",
            selection_command_hash="command-no-coverage",
            expected_task_revision=task.revision,
            owner_id="owner",
            workspace_id="workspace",
            decided_at=datetime.fromisoformat("2026-07-28T00:00:04+00:00"),
        )

    assert blocked.value.code in {
        "runtime_unavailable",
        "llm_runtime_unavailable",
        "authorization_required",
        "budget_expansion_required",
        "policy_prohibited",
        "limited_coverage",
    }


def test_missing_typed_field_fail_closed() -> None:
    task, fence = _completion_task_and_fence()
    with pytest.raises(
        ResearchOrchestrationPersistenceError,
        match="producer_multi_artifact_field_missing",
    ):
        _repo()._task_with_completed_result(
            task,
            fence=fence,
            result=DispatchSubmissionCompleted(
                title="incomplete",
                mime_type="application/json",
                content=json.dumps({"structured_context": {}}),
                provider_id="direct_skill_runtime",
                adapter="durable_skill_runtime",
            ),
            artifact_contract="direct_skill:pvd:v1",
            artifact_schema_digest="a" * 64,
            content_sha256="c" * 64,
            storage_type="external",
            storage_scope=task.task_id,
            storage_ref="durable/research-dispatch/dispatch-multi-artifact/result.json",
            now=NOW,
        )


def test_selection_freeze_known_bad_path_green() -> None:
    task, resolution, snapshot, facts, readiness = _schema_validated_multi_artifact_fixture()
    prepared = decision_service.prepare_selection(
        task,
        instance_id="sub_contract_full",
        resolution=resolution,
        snapshot_envelope=snapshot,
        facts=facts,
        runtime_readiness=readiness,
        selection_request_id="selection-known-bad-green",
        selection_command_hash="command-known-bad-green",
        expected_task_revision=task.revision,
        owner_id="owner",
        workspace_id="workspace",
        decided_at=datetime.fromisoformat("2026-07-28T00:00:04+00:00"),
        workflow_lineage={
            "workflowId": "pvd_process_optimization_workflow",
            "skillPack": "pvd",
            "planningAttemptId": "plan-pvd-1",
            "researchDesignId": "rd_contract_full",
            "researchDesignVersion": 1,
        },
    )
    materialized = _freeze_prepared_selection_for_test(
        prepared, task, "skill:pvd_research_request_understanding:1"
    )
    assert materialized.decision.purpose.value == "completion"
    assert materialized.decision.result_assembly_policy.mode.value == "artifact_set"
    assert [
        row.artifact_type
        for row in materialized.decision.result_assembly_policy.final_artifact_requirements
    ] == list(EXPECTED_TYPES)
    assert materialized.task.work_units[-1].expected_artifact_types == list(EXPECTED_TYPES)


def test_free_planner_progress_preserves_definition_result_identity() -> None:
    task, resolution, snapshot, facts, readiness = _schema_validated_multi_artifact_fixture()
    snapshot["definitions"]["substages"][0].pop("executionComposition")
    snapshot["bindings"]["skills"][0]["scientificStateProjection"] = {
        "contract_version": 2,
        "materializer_ref": "pvd:research-request-understanding:v1",
        "facts": [
            {
                "fact_key": "pvd_research_request_context",
                "source_field": "structured_context",
            }
        ],
    }
    candidate_payloads = []
    for candidate in resolution.candidate_evaluations:
        payload = candidate.model_dump(mode="json", by_alias=True)
        payload["preference"]["completionCoverage"] = "progress_capable"
        candidate_payloads.append(type(candidate).model_validate(payload))
    resolution = ResolutionResult(
        designRef=resolution.design_ref,
        instanceRef=resolution.instance_ref,
        registrySnapshotRef=resolution.registry_snapshot_ref,
        candidateEvaluations=candidate_payloads,
        preferredCandidateEvaluationRefs=[],
    )
    prepared = decision_service.prepare_selection(
        task,
        instance_id="sub_contract_full",
        resolution=resolution,
        snapshot_envelope=snapshot,
        facts=facts,
        runtime_readiness=readiness,
        selection_request_id="selection-free-progress",
        selection_command_hash="command-free-progress",
        expected_task_revision=task.revision,
        owner_id="owner",
        workspace_id="workspace",
        decided_at=datetime.fromisoformat("2026-07-28T00:00:04+00:00"),
    )
    materialized = _freeze_prepared_selection_for_test(
        prepared, task, "skill:pvd_research_request_understanding:1"
    )

    assert materialized.decision.purpose.value == "progress"
    assert materialized.decision.assignments[0].expected_artifact_types == list(
        EXPECTED_TYPES
    )
    assert materialized.task.work_units[-1].expected_artifact_types == list(
        EXPECTED_TYPES
    )
