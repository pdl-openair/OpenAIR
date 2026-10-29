from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.execution.research_dispatch_provider import DispatchSubmissionCompleted
from app.models.research_orchestration import (
    ResearchTaskV12,
    SubstageRuntimeStatus,
    TaskStatus,
)
from app.models.research_external_result import (
    ExplicitRetryCommand,
    ExternalExecutionState,
    ExternalQueryObservation,
    FrozenExternalQuery,
    ReconcileExternalResultCommand,
    ReconcileResolution,
)
from app.modules import research_dispatch_service as dispatch_service_module
from app.modules.controller_command_repository import ControllerCommandRepository
from app.modules.research_dispatch_service import ResearchDispatchService
from app.modules.research_external_recovery_service import (
    ExternalRecoveryNotAllowed,
    ExternalRecoveryService,
)
from app.modules.research_execution_decision_service import (
    ResearchExecutionDecisionService,
    SelectionCommitResult,
    reconcile_task_lifecycle_from_runtime,
)
from app.modules.research_orchestration_repository import (
    AuthorizedDispatch,
    DispatchFence,
    ExternalAttemptIdentity,
    initial_external_attempt_identity,
    ExecutionContinuationDirective,
    ResearchOrchestrationPersistenceError,
    ResearchOrchestrationRepository,
    TaskRevisionConflictError,
    dispatch_capability_call_id,
)
from app.modules.research_orchestration_resolver import ResolutionFacts, ResolutionResult
from app.modules.research_progress_projection import project_task_progress
from tests.support.research_execution import (
    NoWorkflowLineageRepository as _NoWorkflowLineageRepository,
    execution_input_context_for_test as _execution_input_context_for_test,
)


FIXTURE = (
    Path(__file__).resolve().parents[3]
    / "packages"
    / "shared"
    / "test-fixtures"
    / "research-orchestration-v1.2-valid.json"
)
NOW = datetime(2026, 7, 28, 0, 2, tzinfo=timezone.utc)


def _fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))["researchTask"]


def _progress_task(*, outcome: str = "progressed", runtime_status: str = "blocked_recoverable") -> ResearchTaskV12:
    payload = _fixture()
    payload["revision"] = 5
    decision = payload["executionDecisions"][0]
    decision.update(
        {
            "purpose": "progress",
            "completionPolicy": None,
            "progressPolicy": {
                "requiredAssignmentIds": ["assignment_contract_full"],
                "requiredArtifactTypes": [],
                "acceptedResultCodes": "progressed",
            },
            "status": "closed",
            "outcome": outcome,
            "closedAt": "2026-07-28T00:01:00Z",
        }
    )
    decision["candidateEvaluations"][0]["preference"]["completionCoverage"] = "progress_capable"
    decision["assignments"][0]["completionCoverage"] = "progress_capable"
    decision["assignments"][0]["expectedArtifactTypes"] = []
    payload["workUnits"][0].update(
        {
            "expectedArtifactTypes": [],
            "status": "completed",
            "finishedAt": "2026-07-28T00:00:59Z",
        }
    )
    payload["capabilityCalls"][0].update(
        {"status": "succeeded", "outputArtifactRefs": [], "finishedAt": "2026-07-28T00:00:59Z"}
    )
    payload["artifacts"] = []
    payload["agentInteractionRecords"] = []
    payload["substageRuntimeStates"][0].update(
        {
            "status": runtime_status,
            "activeExecutionDecisionId": None,
            "artifactRefs": [],
            "statusReasonCode": outcome,
        }
    )
    return ResearchTaskV12.model_validate(payload)


def _review_needs_revision_task() -> ResearchTaskV12:
    payload = _fixture()
    payload["revision"] = 5
    payload["executionDecisions"][0].update(
        {
            "status": "closed",
            "outcome": "rejected",
            "closedAt": "2026-07-28T00:01:00Z",
        }
    )
    payload["workUnits"][0].update(
        {"status": "completed", "finishedAt": "2026-07-28T00:00:59Z"}
    )
    payload["substageRuntimeStates"][0].update(
        {
            "status": "blocked_recoverable",
            "activeExecutionDecisionId": None,
            "statusReasonCode": "review_needs_revision",
        }
    )
    payload["artifacts"][0]["artifactState"] = "rejected_or_needs_revision"
    payload["agentInteractionRecords"][0]["verdict"] = {
        "verdictType": "review",
        "decision": "rejected",
        "rationaleCode": "review_needs_revision",
    }
    return ResearchTaskV12.model_validate(payload)


def _recovery_resolution(task: ResearchTaskV12) -> ResolutionResult:
    original = task.execution_decisions[0]
    candidate = original.candidate_evaluations[0].model_dump(mode="json", by_alias=True)
    # The recovery resolver is authoritative for the new revision.  Use an executable
    # progress candidate so this regression focuses on progression lineage, not review setup.
    candidate["preference"]["completionCoverage"] = "progress_capable"
    return ResolutionResult(
        designRef={"researchDesignId": "rd_contract_full", "version": 1},
        instanceRef={"substageInstanceId": "sub_contract_full", "instanceRevision": 1},
        registrySnapshotRef=task.registry_snapshot_ref,
        candidateEvaluations=[candidate],
        preferredCandidateEvaluationRefs=[],
    )


def _snapshot() -> dict:
    return {
        "definitions": {
            "substages": [
                {
                    "definitionId": "sdr:evidence_synthesis",
                    "definitionVersion": 1,
                    "inputContract": {"requiredInputRefs": {"status": "known", "values": []}},
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


def _facts(task: ResearchTaskV12) -> ResolutionFacts:
    current_state = (
        None
        if not task.scientific_states
        else task.scientific_states[-1].model_dump(mode="json", by_alias=True)
    )
    return ResolutionFacts(
        runtimeReadiness={},
        providerReadiness={},
        authorizedCandidateRefs=set(),
        availableInputRefs=set(task.research_design_versions[0].instances[0].input_refs),
        applicableRuleRefs=set(),
        satisfiedHumanGateRefs=set(),
        riskAllowed=True,
        budgetAvailable=True,
        policyVersion="registry-policy.v1",
        activeWorkUnitCounts={},
        lastAssignedAt={},
        currentScientificState=current_state,
    )


class _CommandCursor:
    def __init__(self, command: dict) -> None:
        self.command = command
        self._many: list[dict] = []
        self.rowcount = 0

    def execute(self, statement: str, params: tuple | list | None = None) -> None:
        sql = " ".join(statement.split())
        values = tuple(params or ())
        self._many = []
        self.rowcount = 0
        if sql.startswith("SELECT * FROM openair_controller_commands"):
            task_id, owner_id, workspace_id = values
            if (
                self.command["task_id"],
                self.command["owner_id"],
                self.command["workspace_id"],
                self.command["state"],
            ) == (task_id, owner_id, workspace_id, "received"):
                self._many = [self.command]
        elif "SET target_boundary=target_boundary ||" in sql:
            boundary_patch, command_id, task_id, owner_id, workspace_id = values
            if (
                self.command["command_id"] == command_id
                and self.command["task_id"] == task_id
                and self.command["owner_id"] == owner_id
                and self.command["workspace_id"] == workspace_id
                and self.command["state"] == "received"
                and "canonicalWorkUnitId" not in self.command["target_boundary"]
            ):
                self.command["target_boundary"].update(json.loads(boundary_patch))
                self.rowcount = 1

    def fetchall(self):
        return self._many


class _CommandRepository:
    def __init__(self, command: dict) -> None:
        self.command = command

    def _matches(self, *, task_id: str, command_id: str, owner_id: str, workspace_id: str) -> bool:
        return (
            self.command["task_id"],
            self.command["command_id"],
            self.command["owner_id"],
            self.command["workspace_id"],
        ) == (task_id, command_id, owner_id, workspace_id)

    def get_progression_context(self, **scope):
        if not self._matches(**scope):
            return None
        return {
            "commandId": self.command["command_id"],
            "taskId": self.command["task_id"],
            "kind": "supplement_input",
            "state": self.command["state"],
            "submittedTaskRevision": self.command["submitted_task_revision"],
            "lifecycleKind": "canonical_v1_2",
            "targetBoundary": deepcopy(self.command["target_boundary"]),
            "progressionLeaseOwner": self.command.get("progression_lease_owner"),
            "progressionLeaseExpiresAt": self.command.get("progression_lease_expires_at"),
        }




class _SelectionRepository(_NoWorkflowLineageRepository):
    def __init__(self, task: ResearchTaskV12, command: dict) -> None:
        self.task = task
        self.command = command
        self.cursor = _CommandCursor(command)
        self.commits = 0
        self.replays: dict[str, str] = {}

    def load(self, *, task_id: str, owner_id: str, workspace_id: str) -> ResearchTaskV12:
        assert task_id == self.task.task_id
        assert owner_id == "owner_1"
        assert workspace_id == "workspace_1"
        return self.task

    def load_registry_snapshot(self, _content_hash: str) -> dict:
        return _snapshot()

    def read_execution_input_context(self, **kwargs):
        return _execution_input_context_for_test(self.task, **kwargs)


    def commit_prepared_selection(self, prepared, materialize):
        replay_decision_id = self.replays.get(prepared.selection_request_id)
        if replay_decision_id is not None:
            decision = next(
                row for row in self.task.execution_decisions
                if row.execution_decision_id == replay_decision_id
            )
            return SelectionCommitResult(task=self.task, decision=decision, replayed=True)
        if self.task.revision != prepared.expected_task_revision:
            raise TaskRevisionConflictError("task_revision_conflict")
        self.commits += 1
        materialized = materialize(self.task, prepared.final_tie_candidate_ids[0])
        candidate = project_task_progress(
            materialized.task,
            revision=prepared.expected_task_revision + 1,
        )
        candidate_payload = ControllerCommandRepository.apply_canonical_at_materialization(
            self.cursor,
            task_payload=candidate.model_dump(mode="json", by_alias=True),
            owner_id=prepared.owner_id,
            workspace_id=prepared.workspace_id,
        )
        self.task = ResearchTaskV12.model_validate(candidate_payload)
        self.replays[prepared.selection_request_id] = materialized.decision.execution_decision_id
        committed_decision = next(
            row for row in self.task.execution_decisions
            if row.execution_decision_id == materialized.decision.execution_decision_id
        )
        return SelectionCommitResult(
            task=self.task,
            decision=committed_decision,
            replayed=False,
        )


def _command_for(task: ResearchTaskV12, command_id: str = "cmd_a1") -> dict:
    return {
        "command_id": command_id,
        "task_id": task.task_id,
        "owner_id": "owner_1",
        "workspace_id": "workspace_1",
        "state": "received",
        "payload": {"text": "只使用用户补充后的约束继续。"},
        "user_message": "只使用用户补充后的约束继续。",
        "submitted_task_revision": 4,
        "created_at": NOW,
        "target_boundary": {
            "knownWorkUnitIds": [row.work_unit_id for row in task.work_units],
            "submittedAfterExecutionDecisionId": task.execution_decisions[0].execution_decision_id,
        },
        # Model the route's already-held progression lease.
        "progression_lease_owner": "controller-command-route:test",
        "progression_lease_expires_at": datetime.now(timezone.utc) + timedelta(minutes=5),
    }


def _service_harness(task: ResearchTaskV12, command_id: str = "cmd_a1"):
    command = _command_for(task, command_id)
    repository = _SelectionRepository(task, command)
    commands = _CommandRepository(command)
    resolution = _recovery_resolution(task)
    facts = _facts(task)
    readiness = {
        "skill:evidence_synthesis_skill:1": task.execution_decisions[0].runtime_readiness[0]
    }

    class Resolver:
        def resolve(self, **_kwargs):
            return resolution

    decision_service = ResearchExecutionDecisionService(
        repository=repository,
        resolver=Resolver(),
        facts_source=lambda *_args: (facts, readiness),
        now=lambda: NOW,
    )
    service = ResearchDispatchService(
        repository,  # type: ignore[arg-type]
        decision_coordinator=decision_service,
        controller_command_repository=commands,
    )
    return service, repository, commands, command


def _running_progress_task_and_fence() -> tuple[ResearchTaskV12, AuthorizedDispatch]:
    payload = _fixture()
    decision = payload["executionDecisions"][0]
    decision.update(
        {
            "purpose": "progress",
            "completionPolicy": None,
            "progressPolicy": {
                "requiredAssignmentIds": ["assignment_contract_full"],
                "requiredArtifactTypes": [],
                "acceptedResultCodes": "progressed",
            },
            "status": "dispatched",
            "outcome": None,
            "closedAt": None,
        }
    )
    decision["candidateEvaluations"][0]["preference"]["completionCoverage"] = "progress_capable"
    decision["assignments"][0]["completionCoverage"] = "progress_capable"
    decision["assignments"][0]["expectedArtifactTypes"] = []
    payload["workUnits"][0].update(
        {"expectedArtifactTypes": [], "status": "running", "finishedAt": None}
    )
    payload["artifacts"] = []
    payload["agentInteractionRecords"] = []
    payload["substageRuntimeStates"][0].update(
        {
            "status": "running",
            "activeExecutionDecisionId": "decision_contract_full",
            "artifactRefs": [],
        }
    )
    initial_fence = DispatchFence(
        dispatch_id="dispatch-progress-a1",
        task_id="task_contract_full",
        execution_decision_id="decision_contract_full",
        work_unit_id="wu_contract_full",
        idempotency_key="workunit:progress-a1",
        lease_owner="worker-a1",
        attempt_count=1,
    )
    fence = AuthorizedDispatch(
        **initial_fence.__dict__,
        external_attempt=initial_external_attempt_identity(initial_fence),
        authorization_task_revision=4,
        work_unit_payload={"executorRef": {"executorType": "agent"}},
    )
    payload["capabilityCalls"][0].update(
        {
            "capabilityCallId": dispatch_capability_call_id(fence),
            "idempotencyKey": "call:progress-a1:1",
            "status": "running",
            "outputArtifactRefs": [],
            "finishedAt": None,
        }
    )
    return ResearchTaskV12.model_validate(payload), fence


@pytest.mark.parametrize(
    "error_code",
    (
        "provider_response_contract_invalid",
        "skill_input_invalid",
        "key_literature_grounded_candidates_missing",
        "key_literature_source_not_grounded",
        "future_internal_failure_code",
    ),
)
def test_known_rejected_execution_is_recoverable_independent_of_error_code(error_code: str) -> None:
    running, fence = _running_progress_task_and_fence()
    payload = running.model_dump(mode="json", by_alias=True)
    completion = _fixture()["executionDecisions"][0]
    payload["executionDecisions"][0].update(
        {
            "purpose": "completion",
            "completionPolicy": completion["completionPolicy"],
            "progressPolicy": None,
            "candidateEvaluations": completion["candidateEvaluations"],
            "assignments": completion["assignments"],
        }
    )
    payload["workUnits"][0]["expectedArtifactTypes"] = completion["assignments"][0][
        "expectedArtifactTypes"
    ]
    running = ResearchTaskV12.model_validate(payload)

    updated = _CanonicalReconciliationRepository()._task_with_outcome(
        running,
        fence,
        "known_rejected",
        error_code,
        NOW,
        external_action_started=True,
    )

    assert updated.work_units[0].status.value == "blocked"
    assert updated.capability_calls[0].status.value == "failed"
    assert updated.execution_decisions[0].status.value == "closed"
    assert updated.execution_decisions[0].outcome.value == "blocked"
    assert updated.substage_runtime_states[0].status.value == "blocked_recoverable"
    assert updated.substage_runtime_states[0].status_reason_code == error_code
    assert updated.status.value != "failed"


def test_t1_progress_repository_result_persists_capability_artifact_without_completing_substage() -> None:
    task, fence = _running_progress_task_and_fence()

    updated, catalog_artifact, dispatches, agent_message = (
        ResearchOrchestrationRepository._task_with_completed_result(
            task,
            fence=fence,
            result=DispatchSubmissionCompleted(
                title="Progress result",
                mime_type="application/json",
                content='{"result":"需要用户补充一个边界条件"}',
                provider_id="openai_primary",
                adapter="huiyan_chat_completions",
            ),
            artifact_contract="progress_result_v1",
            artifact_schema_digest="a" * 64,
            content_sha256="b" * 64,
            storage_type="local",
            storage_scope=task.task_id,
            storage_ref="durable/research-dispatch/dispatch-progress-a1/result.json",
            now=NOW,
            produced_artifact_type="general_research_reasoning",
        )
    )

    assert updated.work_units[0].status.value == "completed"
    assert updated.capability_calls[0].status.value == "succeeded"
    assert updated.capability_calls[0].output_artifact_refs == [
        "artifact:dispatch-progress-a1"
    ]
    assert len(updated.results) == 1
    assert updated.results[0].result_id == "result:dispatch-progress-a1"
    assert updated.results[0].work_unit_id == updated.work_units[0].work_unit_id
    assert updated.results[0].capability_call_id == updated.capability_calls[0].capability_call_id
    assert updated.results[0].check_status.value == "passed"
    assert updated.results[0].artifact_refs == ["artifact:dispatch-progress-a1"]
    assert len(updated.artifacts) == 1
    assert updated.artifacts[0].artifact_type == "general_research_reasoning"
    assert updated.artifacts[0].artifact_state.value == "usable"
    assert catalog_artifact is not None
    assert catalog_artifact["artifact_id"] == "artifact:dispatch-progress-a1"
    assert dispatches == ()
    assert agent_message is not None and agent_message["artifactRefs"] == [
        "artifact:dispatch-progress-a1"
    ]
    assert updated.execution_decisions[0].status.value == "closed"
    assert updated.execution_decisions[0].outcome.value == "progressed"
    assert updated.substage_runtime_states[0].status.value == "ready"
    assert (
        updated.substage_runtime_states[0].status_reason_code
        == "acceptance_criteria_unresolved"
    )


def test_result_recovery_reuses_a_materialized_result_without_duplicate_user_output() -> None:
    task, fence = _running_progress_task_and_fence()
    result = DispatchSubmissionCompleted(
        title="Progress result",
        mime_type="application/json",
        content='{"result":"需要用户补充一个边界条件"}',
        provider_id="openai_primary",
        adapter="huiyan_chat_completions",
    )
    first, _catalog_artifact, _dispatches, first_message = (
        ResearchOrchestrationRepository._task_with_completed_result(
            task, fence=fence, result=result, artifact_contract="progress_result_v1",
            artifact_schema_digest="a" * 64, content_sha256="b" * 64,
            storage_type="local", storage_scope=task.task_id,
            storage_ref="durable/research-dispatch/dispatch-progress-a1/result.json",
            now=NOW, produced_artifact_type="general_research_reasoning",
        )
    )
    assert first_message is not None
    payload = first.model_dump(mode="json", by_alias=True)
    payload["workUnits"][0].update({"status": "external_result_unknown", "finishedAt": None})
    payload["capabilityCalls"][0].update({"status": "external_result_unknown", "outputArtifactRefs": [], "finishedAt": NOW.isoformat()})
    recovery = ResearchTaskV12.model_validate(payload)
    recovery_fence = AuthorizedDispatch(**{**fence.__dict__, "result_recovery_from_unknown": True})
    recovered, catalog_artifact, _dispatches, recovered_message = (
        ResearchOrchestrationRepository._task_with_completed_result(
            recovery, fence=recovery_fence, result=result, artifact_contract="progress_result_v1",
            artifact_schema_digest="a" * 64, content_sha256="b" * 64,
            storage_type="local", storage_scope=task.task_id,
            storage_ref="durable/research-dispatch/dispatch-progress-a1/result.json",
            now=NOW, produced_artifact_type="general_research_reasoning",
        )
    )
    assert len(recovered.results) == 1
    assert len(recovered.artifacts) == 1
    assert recovered.work_units[0].status.value == "completed"
    assert recovered.capability_calls[0].status.value == "succeeded"
    assert catalog_artifact is not None
    assert recovered_message is None
    settled_again, _catalog_artifact, _dispatches, settled_message = (
        ResearchOrchestrationRepository._task_with_completed_result(
            recovered, fence=recovery_fence, result=result, artifact_contract="progress_result_v1",
            artifact_schema_digest="a" * 64, content_sha256="b" * 64,
            storage_type="local", storage_scope=task.task_id,
            storage_ref="durable/research-dispatch/dispatch-progress-a1/result.json",
            now=NOW, produced_artifact_type="general_research_reasoning",
        )
    )
    assert len(settled_again.results) == 1
    assert len(settled_again.artifacts) == 1
    assert settled_again.capability_calls[0].status.value == "succeeded"
    assert settled_message is None


def test_general_fallback_progress_stops_after_one_bounded_handoff() -> None:
    task, fence = _running_progress_task_and_fence()
    payload = task.model_dump(mode="json", by_alias=True)
    payload["executionDecisions"][0].update({"strategy": "general_research_agent", "fallbackInvoked": True, "fallbackReasonCode": "no_completion_coverage_preferred_candidate"})
    for row in (payload["executionDecisions"][0]["assignments"][0], payload["workUnits"][0]):
        row.update({"responsibleActorRef": {"actorType": "agent", "actorId": "general_research_agent", "definitionVersion": 1}, "executorRef": {"executorType": "agent", "executorId": "general_research_agent", "definitionVersion": 1}})
    updated, _catalog_artifact, dispatches, _agent_message = (
        ResearchOrchestrationRepository._task_with_completed_result(
            ResearchTaskV12.model_validate(payload), fence=fence,
            result=DispatchSubmissionCompleted(title="Bounded fallback handoff", mime_type="application/json", content='{"result":"formal completion capability is unavailable"}', provider_id="openai_primary", adapter="huiyan_chat_completions"),
            artifact_contract="general_research_reasoning_artifact_v1", artifact_schema_digest="a" * 64,
            content_sha256="b" * 64, storage_type="local", storage_scope=task.task_id,
            storage_ref="durable/research-dispatch/dispatch-progress-fallback/result.json",
            now=NOW, produced_artifact_type="general_research_reasoning",
        )
    )
    assert updated.execution_decisions[0].outcome.value == "progressed"
    assert updated.artifacts[0].artifact_state.value == "usable"
    assert updated.substage_runtime_states[0].status.value == "blocked_recoverable"
    assert updated.substage_runtime_states[0].status_reason_code == "progress_capability_exhausted_without_completion_contract"
    assert dispatches == ()


def test_completion_leaf_report_empty_content_materializes_degraded_artifact() -> None:
    task, fence = _running_progress_task_and_fence()
    payload = task.model_dump(mode="json", by_alias=True)
    decision = payload["executionDecisions"][0]
    completion = _fixture()["executionDecisions"][0]
    decision.update(
        {
            "purpose": "completion",
            "completionPolicy": completion["completionPolicy"],
            "progressPolicy": None,
            "candidateEvaluations": completion["candidateEvaluations"],
            "assignments": deepcopy(completion["assignments"]),
        }
    )
    decision["assignments"][0]["expectedArtifactTypes"] = ["research_report"]
    decision["assignments"][0]["completionCoverage"] = "completion_capable"
    decision["resultAssemblyPolicy"]["finalArtifactRequirements"] = [
        {
            "artifactType": "research_report",
            "minimumCount": 1,
            "sourceAssignmentIds": [decision["assignments"][0]["assignmentId"]],
        }
    ]
    payload["workUnits"][0].update(
        {
            "substageDefinitionId": "sdr:report_generation",
            "expectedArtifactTypes": ["research_report"],
            "inputArtifactRefs": ["artifact_upstream_key_literature"],
            "inputArtifactSnapshots": [
                {
                    "artifactId": "artifact_upstream_key_literature",
                    "artifactType": "key_literature_output",
                    "version": 1,
                    "contentSha256": "d" * 64,
                }
            ],
        }
    )
    payload["capabilityCalls"][0]["outputArtifactRefs"] = [
        "artifact_upstream_key_literature"
    ]
    payload["artifacts"] = [
        {
            "artifactId": "artifact_upstream_key_literature",
            "workUnitId": payload["workUnits"][0]["workUnitId"],
            "substageInstanceId": payload["workUnits"][0]["substageInstanceId"],
            "sourceCapabilityCallId": payload["capabilityCalls"][0]["capabilityCallId"],
            "artifactType": "key_literature_output",
            "artifactState": "usable",
            "version": 1,
            "sourceRefs": [],
            "validatorResult": {"contentSha256": "d" * 64},
            "lineageKind": "capability_call_output",
        }
    ]
    payload["researchDesignVersions"][0]["instances"][0].update(
        {
            "substageDefinitionId": "sdr:report_generation",
            "stageKey": "insight_generation",
            "expectedArtifactTypes": ["research_report"],
        }
    )
    task = ResearchTaskV12.model_validate(payload)

    updated, catalog_artifact, _dispatches, _message = (
        ResearchOrchestrationRepository._task_with_completed_result(
            task,
            fence=fence,
            result=DispatchSubmissionCompleted(
                title="Empty report",
                mime_type="application/json",
                content="{}",
                provider_id="openai_primary",
                adapter="huiyan_chat_completions",
            ),
            artifact_contract="agent_report_v1",
            artifact_schema_digest="a" * 64,
            content_sha256="b" * 64,
            storage_type="local",
            storage_scope=task.task_id,
            storage_ref="durable/research-dispatch/dispatch-progress-a1/result.json",
            now=NOW,
        )
    )

    assert updated.work_units[0].status.value == "completed"
    assert updated.capability_calls[0].output_artifact_refs
    assert catalog_artifact is not None
    assert catalog_artifact["format"] == "markdown"
    assert catalog_artifact.get("contentOverride")
    report = next(
        row for row in updated.artifacts if row.artifact_type == "research_report"
    )
    produced_result = next(row for row in updated.results if report.artifact_id in row.artifact_refs)
    assert report.source_result_id == produced_result.result_id
    assert produced_result.check_status.value == "failed"
    assert produced_result.check_result["reasonCode"] == "result_content_not_materializable"
    assert report.artifact_state.value == "usable"
    assert report.validator_result.get("artifactQuality") == "degraded"
    assert updated.execution_decisions[0].outcome.value == "succeeded"
    assert updated.substage_runtime_states[0].status.value == "ready"
    assert (
        updated.substage_runtime_states[0].status_reason_code
        == "acceptance_criteria_unresolved"
    )
    assert updated.status.value != "awaiting_confirmation"


def test_t1_progress_dispatch_persists_result_evidence_and_capability_artifact_blob(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task, fence = _running_progress_task_and_fence()
    stored: dict[str, bytes] = {}
    completed_kwargs: list[dict] = []

    class Repository:
        def load(self, **_scope):
            return task

        def record_dispatch_completed_result(self, _fence, **kwargs):
            completed_kwargs.append(kwargs)
            return True

        def prepare_execution_continuation(self, **_kwargs):
            return ExecutionContinuationDirective(
                action="none",
                expected_task_revision=5,
                next_instance_id=None,
                selection_request_id=None,
                reason_code=None,
            )

        def finalize_dispatch_completed_result(self, _fence, **_kwargs):
            return True

    monkeypatch.setattr(dispatch_service_module.settings, "artifact_storage", "local")
    monkeypatch.setattr(
        dispatch_service_module,
        "validate_completed_dispatch_result",
        lambda *_args, **_kwargs: SimpleNamespace(
            artifactContract="progress_result_v1",
            artifactSchemaDigest="a" * 64,
            orchestrationPlan={
                "assignmentRole": "primary",
                "artifactKind": "general_research_reasoning",
            },
        ),
    )
    monkeypatch.setattr(
        dispatch_service_module.artifact_write_service,
        "store_durable_bytes",
        lambda _task_id, storage_ref, data, _content_type: (
            stored.__setitem__(storage_ref, data)
            or SimpleNamespace(storage_type="local", storage_ref=storage_ref)
        ),
    )
    monkeypatch.setattr(
        dispatch_service_module.artifact_write_service,
        "read_durable_bytes",
        lambda _task_id, _storage_type, storage_ref: stored.get(storage_ref),
    )
    evidence = (
        {
            "adapter": "huiyan_chat_completions",
            "outcome": "succeeded",
            "evidenceRef": "provider-receipt:a1",
            "contentSha256": "c" * 64,
        },
    )

    assert ResearchDispatchService(Repository()).record_completed_result(  # type: ignore[arg-type]
        fence,
        owner_id="owner_1",
        workspace_id="workspace_1",
        result=DispatchSubmissionCompleted(
            title="Progress result",
            mime_type="application/json",
            content='{"result":"progress"}',
            provider_id="openai_primary",
            adapter="huiyan_chat_completions",
            external_action_evidence=evidence,
        ),
    )

    result_ref = f"durable/research-dispatch/{fence.dispatch_id}/result.json"
    artifact_ref = f"durable/research-dispatch/{fence.dispatch_id}/artifact.json"
    assert result_ref in stored
    assert artifact_ref in stored
    envelope = json.loads(stored[result_ref])
    assert envelope["externalActionEvidence"] == [dict(evidence[0])]
    assert completed_kwargs[0]["artifact_storage_type"] == "local"
    assert completed_kwargs[0]["artifact_storage_ref"] == artifact_ref
    assert completed_kwargs[0]["produced_artifact_type"] == "general_research_reasoning"


def test_t2_progress_supplement_creates_one_canonical_revision() -> None:
    task = _progress_task()
    original = task.execution_decisions[0].model_dump(mode="json", by_alias=True)
    service, repository, _commands, command = _service_harness(task)

    committed = service.progress_controller_command(
        task.task_id,
        command["command_id"],
        "owner_1",
        "workspace_1",
        5,
    )

    assert committed.task.revision == 6
    assert repository.commits == 1
    assert len(repository.task.execution_decisions) == 2
    assert len(repository.task.work_units) == 2
    new_decision = repository.task.execution_decisions[-1]
    assert new_decision.selection_request_id == "controller-command:cmd_a1:research-revision"
    assert new_decision.supersedes_execution_decision_id == "decision_contract_full"
    assert new_decision.revision_reason_code == "research_revision"
    assert repository.task.execution_decisions[0].model_dump(mode="json", by_alias=True) == original


def test_t2_supplement_reentry_keeps_unmet_professional_requirement_outstanding() -> None:
    task = _progress_task()
    payload = task.model_dump(mode="json", by_alias=True)
    payload["scientificStates"] = [{
        "scientificStateVersion": 1,
        "researchDesignVersion": 1,
        "scientificFacts": {},
        "executionState": {
            "terminalSubstages": False,
            "validatedArtifactRefs": [],
            "substageCompletion": [{
                "substageInstanceId": "sub_contract_full",
                "instanceRevision": 1,
                "criteria": [{
                    "criterionId": "criterion_contract_full",
                    "status": "unmet",
                    "requiredArtifactType": "evidence_summary",
                    "judgmentRequirement": "domain_professional",
                    "evaluatorType": "agent",
                    "evaluatorRef": "reviewer_contract_full",
                }],
                "completionCriteriaSatisfied": False,
                "unresolvedCriterionIds": [],
                "unmetCriterionIds": ["criterion_contract_full"],
                "gaps": ["criterion:criterion_contract_full"],
                "uncertainty": [],
                "needsAction": False,
                "runtimeStatus": "blocked_recoverable",
                "hasExecutionDegradation": True,
            }],
        },
        "sourceRefs": [],
        "createdAt": "2026-07-28T00:01:00Z",
    }]
    task = ResearchTaskV12.model_validate(payload)
    service, repository, _commands, command = _service_harness(task)

    service.progress_controller_command(
        task.task_id,
        command["command_id"],
        "owner_1",
        "workspace_1",
        5,
    )

    new_decision = repository.task.execution_decisions[-1]
    assert new_decision.revision_reason_code == "research_revision"
    # General fallback cannot silently become completion authority when the
    # current State says a professional criterion is still unmet.
    assert new_decision.purpose.value == "progress"
    assert repository.task.work_units[-1].expected_artifact_types == []


def test_pre_fix_revision_completion_recovers_intermediate_skill_result_without_reviewer() -> None:
    task, fence = _running_progress_task_and_fence()
    payload = task.model_dump(mode="json", by_alias=True)
    decision = payload["executionDecisions"][0]
    producer = decision["assignments"][0]
    producer["completionCoverage"] = "completion_capable"
    reviewer = deepcopy(producer)
    reviewer_id = "assignment_pre_fix_reviewer"
    reviewer.update({
        "assignmentId": reviewer_id,
        "role": "reviewer",
        "objective": "Independently review the completion artifact.",
        "responsibleActorRef": {
            "actorType": "agent",
            "actorId": "result_reliability_audit",
            "definitionVersion": 1,
        },
        "executorRef": {
            "executorType": "agent",
            "executorId": "result_reliability_audit",
            "definitionVersion": 1,
        },
        "allowedSkillRefs": [],
        "sourceBindingRefs": [],
        "inputRefs": list(producer["inputRefs"]),
        "expectedArtifactTypes": [],
        "acceptanceCriteria": [],
        "dependencyAssignmentIds": [producer["assignmentId"]],
    })
    decision.update({
        "purpose": "completion",
        "progressPolicy": None,
        "completionPolicy": {
            "requiredAssignmentIds": [producer["assignmentId"], reviewer_id],
            "successMode": "all_required",
            "reviewerAssignmentIds": [reviewer_id],
            "reviewRequirement": "all_reviewers_approve",
            "partialSuccessAllowed": False,
            "onReviewerReject": "new_revision_decision",
        },
        "resultAssemblyPolicy": {
            "mode": "primary_output",
            "assemblerAssignmentId": None,
            "finalArtifactRequirements": [],
        },
        "revisionReasonCode": "research_revision",
        "supersedesExecutionDecisionId": "decision_before_fix",
        "assignments": [producer, reviewer],
    })
    producer["expectedArtifactTypes"] = []
    payload["workUnits"][0].update({
        "expectedArtifactTypes": [],
        "status": "external_result_unknown",
        "terminalReasonCode": "producer_artifact_type_ambiguous",
    })
    payload["capabilityCalls"][0].update({
        "status": "external_result_unknown",
        "finishedAt": NOW.isoformat(),
    })
    task = ResearchTaskV12.model_validate(payload)
    fence = AuthorizedDispatch(
        **{**fence.__dict__, "result_recovery_from_unknown": True}
    )

    updated, catalog_artifact, pending_dispatches, _agent_message = (
        ResearchOrchestrationRepository._task_with_completed_result(
            task,
            fence=fence,
            result=DispatchSubmissionCompleted(
                title="Recovered historical intermediate Skill result",
                mime_type="application/json",
                content='{"repository":"https://example.invalid/repo","revision":"abc123"}',
                provider_id="direct_skill_runtime",
                adapter="durable_skill_runtime",
            ),
            artifact_contract="direct_skill:cs_reproduction_input_structuring:v1",
            artifact_schema_digest="a" * 64,
            content_sha256="b" * 64,
            storage_type="external",
            storage_scope="minio+http://minio:9000/openair-artifacts",
            storage_ref="durable/research-dispatch/dispatch-progress-a1/result.json",
            now=NOW,
            produced_artifact_type="cs_reproduction_execution_inputs",
        )
    )

    assert updated.results[-1].work_unit_id == fence.work_unit_id
    assert updated.artifacts[-1].artifact_type == "cs_reproduction_execution_inputs"
    assert updated.artifacts[-1].artifact_state.value == "usable"
    assert catalog_artifact is not None
    assert catalog_artifact["metadata"]["artifactType"] == "cs_reproduction_execution_inputs"
    # Historical truth is immutable: the malformed WorkUnit remains frozen with
    # no Design-owned output contract; recovery only preserves the producer type.
    assert updated.work_units[0].expected_artifact_types == []
    assert updated.work_units[0].status.value == "completed"
    assert updated.capability_calls[0].status.value == "succeeded"
    assert updated.execution_decisions[0].status.value == "closed"
    assert updated.execution_decisions[0].outcome.value == "blocked"
    runtime = updated.substage_runtime_states[0]
    assert runtime.status.value == "blocked_recoverable"
    assert runtime.active_execution_decision_id is None
    # The malformed completion reviewer is never materialized: this recovered
    # intermediate Artifact has no authority to satisfy the final target.
    assert len(updated.work_units) == 1
    assert pending_dispatches == ()
    assert len(updated.scientific_states) == 1


def test_pre_fix_revision_completion_without_producer_type_still_fails_closed() -> None:
    task, fence = _running_progress_task_and_fence()
    payload = task.model_dump(mode="json", by_alias=True)
    decision = payload["executionDecisions"][0]
    assignment = decision["assignments"][0]
    assignment["completionCoverage"] = "completion_capable"
    decision.update({
        "purpose": "completion",
        "progressPolicy": None,
        "completionPolicy": {
            "requiredAssignmentIds": [assignment["assignmentId"]],
            "successMode": "all_required",
            "reviewerAssignmentIds": [],
            "reviewRequirement": "none",
            "partialSuccessAllowed": False,
            "onReviewerReject": "new_revision_decision",
        },
        "resultAssemblyPolicy": {
            "mode": "primary_output",
            "assemblerAssignmentId": None,
            "finalArtifactRequirements": [],
        },
        "revisionReasonCode": "research_revision",
        "supersedesExecutionDecisionId": "decision_before_fix",
    })
    assignment["expectedArtifactTypes"] = []
    payload["workUnits"][0]["expectedArtifactTypes"] = []
    task = ResearchTaskV12.model_validate(payload)

    with pytest.raises(
        ResearchOrchestrationPersistenceError,
        match="producer_artifact_type_ambiguous",
    ):
        ResearchOrchestrationRepository._task_with_completed_result(
            task,
            fence=fence,
            result=DispatchSubmissionCompleted(
                title="Ambiguous historical provider result",
                mime_type="application/json",
                content='{"result":"durable provider output"}',
                provider_id="direct_skill_runtime",
                adapter="durable_skill_runtime",
            ),
            artifact_contract="direct_skill:unknown:v1",
            artifact_schema_digest="a" * 64,
            content_sha256="b" * 64,
            storage_type="external",
            storage_scope=task.task_id,
            storage_ref="durable/research-dispatch/ambiguous/result.json",
            now=NOW,
            produced_artifact_type=None,
        )


def test_t3_review_needs_revision_reuses_same_progression_and_preserves_history() -> None:
    task = _review_needs_revision_task()
    artifact_before = task.artifacts[0].model_dump(mode="json", by_alias=True)
    review_before = task.agent_interaction_records[0].model_dump(mode="json", by_alias=True)
    decision_before = task.execution_decisions[0].model_dump(mode="json", by_alias=True)
    service, repository, _commands, command = _service_harness(task, "cmd_review")

    service.progress_controller_command(
        task.task_id,
        command["command_id"],
        "owner_1",
        "workspace_1",
        5,
    )

    new_decision = repository.task.execution_decisions[-1]
    assert new_decision.supersedes_execution_decision_id == "decision_contract_full"
    assert new_decision.revision_reason_code == "research_revision"
    assert repository.task.execution_decisions[0].model_dump(mode="json", by_alias=True) == decision_before
    assert repository.task.artifacts[0].model_dump(mode="json", by_alias=True) == artifact_before
    assert repository.task.agent_interaction_records[0].model_dump(mode="json", by_alias=True) == review_before
    assert repository.task.work_units[-1].status.value == "queued"


def test_t4_needs_input_progression_survives_reload() -> None:
    task = _progress_task(outcome="needs_input", runtime_status="needs_input")
    service, repository, _commands, command = _service_harness(task, "cmd_needs_input")

    service.progress_controller_command(
        task.task_id,
        command["command_id"],
        "owner_1",
        "workspace_1",
        5,
    )
    reloaded = repository.load(
        task_id=task.task_id,
        owner_id="owner_1",
        workspace_id="workspace_1",
    )

    assert reloaded.revision == 6
    assert len(reloaded.execution_decisions) == 2
    assert len(reloaded.work_units) == 2




class _CanonicalReconciliationRepository:
    _task_with_reconciliation = ResearchOrchestrationRepository._task_with_reconciliation
    _task_with_outcome = ResearchOrchestrationRepository._task_with_outcome

    def __init__(self) -> None:
        self.reducer_inputs: list[ResearchTaskV12] = []

    def _reconcile_task_lifecycle_with_frozen_snapshot(
        self,
        task: ResearchTaskV12,
        *,
        occurred_at: datetime,
    ) -> ResearchTaskV12:
        self.reducer_inputs.append(task)
        return reconcile_task_lifecycle_from_runtime(task, occurred_at=occurred_at)


class _ReducerOwnerProbe(_CanonicalReconciliationRepository):
    def _reconcile_task_lifecycle_with_frozen_snapshot(
        self,
        task: ResearchTaskV12,
        *,
        occurred_at: datetime,
    ) -> ResearchTaskV12:
        del occurred_at
        self.reducer_inputs.append(task)
        return task.model_copy(
            update={
                "status": TaskStatus.CANCELLED,
                "substage_runtime_states": [
                    row.model_copy(update={"status": SubstageRuntimeStatus.CANCELLED})
                    for row in task.substage_runtime_states
                ],
            }
        )


def _reconcile_command(
    task: ResearchTaskV12,
    fence: AuthorizedDispatch,
    request_id: str,
    resolution: ReconcileResolution = ReconcileResolution.QUERY_PROVIDER,
) -> ReconcileExternalResultCommand:
    return ReconcileExternalResultCommand(
        task_id=task.task_id,
        work_unit_id=fence.work_unit_id,
        dispatch_id=fence.dispatch_id,
        execution_decision_id=fence.execution_decision_id,
        capability_call_id=dispatch_capability_call_id(fence),
        attempt_number=1,
        resolution=resolution,
        evidence_refs=(f"evidence_{request_id}",),
        command_request_id=request_id,
        canonical_payload_fingerprint="d" * 64,
        expected_task_revision=task.revision,
        owner_id="owner_1",
        workspace_id="workspace_1",
        actor_id="system_recovery",
    )


@pytest.mark.parametrize(
    ("case", "external_state", "expected_work", "expected_call"),
    (
        (
            "running",
            ExternalExecutionState.RUNNING,
            "running",
            "running",
        ),
        (
            "completed",
            ExternalExecutionState.COMPLETED,
            "reviewing",
            "succeeded",
        ),
        (
            "unknown",
            ExternalExecutionState.UNKNOWN,
            "external_result_unknown",
            "external_result_unknown",
        ),
        (
            "never_submitted",
            ExternalExecutionState.NEVER_SUBMITTED,
            "blocked",
            "failed",
        ),
        (
            "known_failed",
            ExternalExecutionState.KNOWN_FAILED,
            "blocked",
            "failed",
        ),
    ),
)
def test_t3a_every_reconciliation_outcome_flows_through_canonical_reducer(
    case: str,
    external_state: ExternalExecutionState,
    expected_work: str,
    expected_call: str,
) -> None:
    task, fence = _running_progress_task_and_fence()
    repository = _ReducerOwnerProbe()

    reconciled = repository._task_with_reconciliation(
        task,
        _reconcile_command(task, fence, f"canonical_owner_{case}"),
        ExternalQueryObservation(
            external_state=external_state,
            remote_run_id=None if external_state is ExternalExecutionState.NEVER_SUBMITTED else f"remote_{case}",
            error_code=(
                "provider_query_result_unknown"
                if external_state is ExternalExecutionState.UNKNOWN
                else "provider_known_failure"
                if external_state is ExternalExecutionState.KNOWN_FAILED
                else None
            ),
            result_evidence_refs=(f"evidence_canonical_owner_{case}",)
            if external_state is ExternalExecutionState.COMPLETED
            else (),
        ),
        NOW,
    )

    assert len(repository.reducer_inputs) == 1
    reducer_input = repository.reducer_inputs[0]
    assert reducer_input.work_units[0].status.value == expected_work
    assert reducer_input.capability_calls[0].status.value == expected_call
    assert reducer_input.status.value == "running"
    assert reconciled.status is TaskStatus.CANCELLED
    assert reconciled.substage_runtime_states[0].status is SubstageRuntimeStatus.CANCELLED


def test_t3a_unknown_runtime_fact_is_not_promoted_to_failure() -> None:
    task, fence = _running_progress_task_and_fence()
    repository = _CanonicalReconciliationRepository()

    reconciled = repository._task_with_reconciliation(
        task,
        _reconcile_command(task, fence, "unknown_runtime"),
        ExternalQueryObservation(
            external_state=ExternalExecutionState.UNKNOWN,
            error_code="provider_query_result_unknown",
        ),
        NOW,
    )

    assert len(repository.reducer_inputs) == 1
    assert reconciled.work_units[0].status.value == "external_result_unknown"
    assert reconciled.capability_calls[0].status.value == "external_result_unknown"
    assert reconciled.status.value == "running"
    assert reconciled.substage_runtime_states[0].status.value == "running"


def test_t3a_cancelled_task_keeps_late_completed_fact_without_reopening() -> None:
    running, fence = _running_progress_task_and_fence()
    payload = running.model_dump(mode="json", by_alias=True)
    payload.update({"status": "cancelled", "finishedAt": NOW.isoformat()})
    payload["substageRuntimeStates"][0].update(
        {"status": "cancelled", "activeExecutionDecisionId": None}
    )
    cancelled = ResearchTaskV12.model_validate(payload)
    repository = _CanonicalReconciliationRepository()

    reconciled = repository._task_with_reconciliation(
        cancelled,
        _reconcile_command(
            cancelled,
            fence,
            "late_completed",
        ),
        ExternalQueryObservation(
            external_state=ExternalExecutionState.COMPLETED,
            remote_run_id="remote_late_completed",
            result_evidence_refs=("evidence_late_completed",),
        ),
        NOW,
    )

    assert len(repository.reducer_inputs) == 1
    assert reconciled.capability_calls[0].status.value == "succeeded"
    assert reconciled.work_units[0].status.value == "reviewing"
    assert reconciled.status.value == "cancelled"
    assert reconciled.substage_runtime_states[0].status.value == "cancelled"


def test_a2_retired_abandon_resolution_is_absent() -> None:
    assert tuple(ReconcileResolution) == (ReconcileResolution.QUERY_PROVIDER,)


@pytest.mark.parametrize("attempt_number", (1, 2))
def test_semantic_material_gap_continues_without_inventing_scientific_facts(attempt_number: int) -> None:
    task, fence = _running_progress_task_and_fence()
    if attempt_number == 2:
        fence = replace(fence, external_attempt=ExternalAttemptIdentity("call-retry-isolated-2", 2))
    payload = task.model_dump(mode="json", by_alias=True)
    decision = payload["executionDecisions"][0]
    completion = _fixture()["executionDecisions"][0]
    decision.update(
        {
            "purpose": "completion",
            "completionPolicy": completion["completionPolicy"],
            "progressPolicy": None,
            "candidateEvaluations": completion["candidateEvaluations"],
            "resultAssemblyPolicy": completion["resultAssemblyPolicy"],
        }
    )
    decision["assignments"] = deepcopy(completion["assignments"])
    expected_types = list(decision["assignments"][0]["expectedArtifactTypes"])
    assert expected_types
    payload["workUnits"][0].update(
        {
            "expectedArtifactTypes": expected_types,
            "status": "running",
            "finishedAt": None,
        }
    )
    payload["capabilityCalls"][0].update(
        {
            "status": "running",
            "capabilityCallId": fence.external_attempt.capability_call_id,
            "attemptNumber": attempt_number,
            "outputArtifactRefs": [],
            "finishedAt": None,
        }
    )
    payload["executionMode"] = "automatic_research"
    task = ResearchTaskV12.model_validate(payload)

    repository = ResearchOrchestrationRepository.__new__(ResearchOrchestrationRepository)
    repository.load_registry_snapshot = lambda *_args: {}
    repository._reconcile_task_lifecycle_with_frozen_snapshot = lambda task, **_kwargs: task
    import hashlib

    submitted = DispatchSubmissionCompleted(
                title="Evidence extraction",
                mime_type="text/markdown",
                content=(
                    "Result\n"
                    "状态：requires_existing_inputs\n"
                    "当前仅有检索元数据，没有全文、方法、结果或图表；"
                    "不能抽取离子电导率或界面稳定性证据。"
                ),
                provider_id="openai_primary",
                adapter="huiyan_chat_completions",
            )
    submitted_sha = hashlib.sha256(submitted.content.encode("utf-8")).hexdigest()
    updated, catalog_artifact, dispatches, _message = (
        repository._task_with_completed_result(
            task,
            fence=fence,
            result=submitted,
            artifact_contract="direct_skill:evidence_extraction:v1",
            artifact_schema_digest="a" * 64,
            content_sha256=submitted_sha,
            storage_type="local",
            storage_scope=task.task_id,
            storage_ref="durable/research-dispatch/dispatch-progress-a1/result.json",
            scientific_state_fact_projections={
                "experimental_system_context": {
                    "contract": "pvd-experimental-system-context/v1"
                }
            },
            scientific_state_projection_declaration={
                "contract_version": 2,
                "materializer_ref": "pvd:experimental-system-context:v1",
                "facts": [
                    {
                        "fact_key": "experimental_system_context",
                        "source_field": "experimental_system_context",
                    }
                ],
            },
            now=NOW,
        )
    )

    assert updated.capability_calls[0].status.value == "succeeded"
    assert updated.work_units[0].status.value == "completed"
    assert updated.work_units[0].terminal_reason_code is None
    assert len(updated.results) == 1
    failed_result = updated.results[0]
    assert failed_result.result_id == "result:dispatch-progress-a1"
    assert failed_result.check_status.value == "passed"
    assert failed_result.scientific_state_versions
    assert "semantic_material_insufficient" not in failed_result.check_result["warnings"]
    produced = next(
        row for row in updated.artifacts
        if row.work_unit_id == updated.work_units[0].work_unit_id
    )
    assert any(produced.artifact_id in state.source_refs for state in updated.scientific_states)
    assert produced.artifact_state.value == "usable"
    assert produced.source_result_id == failed_result.result_id
    assert "semanticReadiness" not in produced.validator_result
    assert failed_result.storage_ref == "durable/research-dispatch/dispatch-progress-a1/result.json"
    assert failed_result.content_sha256 == submitted_sha
    assert catalog_artifact.get("contentOverride", submitted.content) == submitted.content
    assert "contentOverride" not in catalog_artifact
    assert "requires_existing_inputs" in submitted.content
    assert "不能抽取离子电导率或界面稳定性证据" in submitted.content
    assert updated.execution_decisions[0].status.value == "closed"
    assert updated.execution_decisions[0].outcome.value == "succeeded"
    assert updated.status.value == "running"
    assert updated.final_result_authority is None
    assert dispatches == ()
    assert catalog_artifact is not None
    assert catalog_artifact["metadata"]["artifactState"] == "usable"


def test_passed_execution_result_traces_accepted_pvd_scientific_state() -> None:
    task, fence = _running_progress_task_and_fence()
    payload = task.model_dump(mode="json", by_alias=True)
    decision = payload["executionDecisions"][0]
    completion = _fixture()["executionDecisions"][0]
    decision.update(
        {
            "purpose": "completion",
            "completionPolicy": completion["completionPolicy"],
            "progressPolicy": None,
            "candidateEvaluations": completion["candidateEvaluations"],
            "assignments": deepcopy(completion["assignments"]),
        }
    )
    expected_types = list(decision["assignments"][0]["expectedArtifactTypes"])
    payload["workUnits"][0].update(
        {
            "expectedArtifactTypes": expected_types,
            "status": "running",
            "finishedAt": None,
        }
    )
    payload["capabilityCalls"][0].update(
        {"status": "running", "outputArtifactRefs": [], "finishedAt": None}
    )
    payload["scientificStates"] = []
    task = ResearchTaskV12.model_validate(payload)
    declaration = {
        "contract_version": 2,
        "materializer_ref": "pvd:experimental-system-context:v1",
        "facts": [
            {
                "fact_key": "experimental_system_context",
                "source_field": "experimental_system_context",
            }
        ],
    }
    context = {"contract": "pvd-experimental-system-context/v1"}
    content = json.dumps({"experimental_system_context": context})

    updated, _catalog_artifact, _dispatches, _message = (
        ResearchOrchestrationRepository._task_with_completed_result(
            task,
            fence=fence,
            result=DispatchSubmissionCompleted(
                title="PVD experimental context",
                mime_type="application/json",
                content=content,
                provider_id="openai_primary",
                adapter="huiyan_chat_completions",
            ),
            artifact_contract="pvd_context_v1",
            artifact_schema_digest="a" * 64,
            content_sha256="b" * 64,
            storage_type="local",
            storage_scope=task.task_id,
            storage_ref="durable/research-dispatch/dispatch-progress-a1/result.json",
            scientific_state_fact_projections={
                "experimental_system_context": context
            },
            scientific_state_projection_declaration=declaration,
            now=NOW,
        )
    )

    produced_result = updated.results[0]
    produced_artifact = next(
        row for row in updated.artifacts if row.source_result_id == produced_result.result_id
    )
    assert produced_result.check_status.value == "passed"
    assert produced_result.artifact_refs == [produced_artifact.artifact_id]
    assert produced_result.scientific_state_versions == [1]
    accepted_state = next(
        state for state in updated.scientific_states
        if state.scientific_state_version == produced_result.scientific_state_versions[0]
    )
    assert accepted_state.source_refs == [produced_artifact.artifact_id]
    assert accepted_state.scientific_facts[
        "experimental_system_contexts"
    ][0]["experimental_system_context"] == context
