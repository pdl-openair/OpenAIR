"""PostgreSQL dispatch result persistence, message, artifact, and review transactions."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
import math
import os
from pathlib import Path
from threading import Barrier
from time import perf_counter, sleep
from datetime import datetime, timedelta, timezone

import psycopg
from psycopg.rows import dict_row
import pytest
from fastapi import HTTPException

from app.config import settings
from app.models.research_orchestration import (
    DispatchRecord,
    ResearchDesignStatus,
    ResearchTaskV12,
)
from app.modules.collaboration_repository import CollaborationRepository
from app.modules.continuation_responsibility import (
    ContinuationOwnerKind,
    ContinuationResponsibilityKind,
    ContinuationResponsibilityProof,
)
from app.modules.durable_execution import DurableExecutionRepository
from app.modules.artifact_write import artifact_write_service
from app.modules.registry_snapshot_repository import (
    RegistrySnapshotRepository,
    registry_envelope_ref,
)
from app.modules.research_orchestration_resolver import build_registry_snapshot
from app.modules.research_orchestration_resolver import ResolutionResult
from app.modules.research_execution_decision_service import (
    ExecutionMaterialization,
    PreparedSelection,
)
from app.modules.research_dispatch_service import (
    DispatchContinuationRetryableError,
    ResearchDispatchService,
)
from app.modules.research_orchestration_repository import (
    AuthorizedDispatch,
    DispatchAuthorizationError,
    DispatchIdentityConflictError,
    DispatchFence,
    ResearchOrchestrationPersistenceError,
    ResearchOrchestrationRepository,
    ResearchTaskNotFoundError,
    SelectionRequestPayloadConflictError,
    TaskRevisionConflictError,
    _provider_result_message_text,
    dispatch_capability_call_id,
)
from app.execution.research_dispatch_provider import DispatchSubmissionCompleted

from tests.support.research_dispatch_transactions import (
    postgres_environment,
    clean_tables,
    repository,
    _replace_fixture_identifiers,
    _transaction_fixture,
    _insert_task,
    _seed,
    _namespaced_dispatch_mutation_connection,
    _record_completed_and_finalize,
    _seed_reviewed_dispatch,
    _release_ready_reviewer_assignment,
    _claim,
    _dispatch_row,
    _task_row,
    _task_message_rows,
    _work_unit_status,
    _integrity_blocker,
    _due_dispatch_ids,
    _authorize,
    _start,
)


def _replace_registry_snapshot_refs(value: object, snapshot_ref: dict[str, object]) -> object:
    if isinstance(value, dict):
        return {
            key: deepcopy(snapshot_ref)
            if key == "registrySnapshotRef"
            else _replace_registry_snapshot_refs(item, snapshot_ref)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_replace_registry_snapshot_refs(item, snapshot_ref) for item in value]
    return value


def _seed_with_archived_registry(repository, connect, suffix: str):
    snapshot = build_registry_snapshot()
    RegistrySnapshotRepository(connect).put(snapshot)
    snapshot_ref = registry_envelope_ref(snapshot.envelope)
    baseline, candidate, dispatch = _transaction_fixture(suffix)
    baseline = ResearchTaskV12.model_validate(
        _replace_registry_snapshot_refs(
            baseline.model_dump(mode="json", by_alias=True), snapshot_ref
        )
    )
    candidate = ResearchTaskV12.model_validate(
        _replace_registry_snapshot_refs(
            candidate.model_dump(mode="json", by_alias=True), snapshot_ref
        )
    )
    _insert_task(connect, baseline)
    return (
        repository.save(
            candidate,
            owner_id="owner",
            workspace_id="workspace",
            expected_task_revision=0,
            pending_dispatches=[dispatch],
            automatic_dispatch=True,
        ),
        dispatch,
    )


def _persist_completed_dispatch_for_continuation(
    repository,
    connect,
    suffix: str,
    *,
    store_artifact_bytes: bool = False,
):
    snapshot = build_registry_snapshot()
    RegistrySnapshotRepository(connect).put(snapshot)
    snapshot_ref = registry_envelope_ref(snapshot.envelope)
    task, dispatch, _reviewer_id = _seed_reviewed_dispatch(
        repository,
        connect,
        suffix,
        registry_snapshot_ref=snapshot_ref,
    )
    authorized = _start(repository, _authorize(repository, task.task_id))
    content = '{"result":"durable continuation input"}'
    result_storage_ref = f"durable/research-dispatch/{dispatch.dispatch_id}/result.json"
    artifact_storage_ref = (
        f"durable/research-dispatch/{dispatch.dispatch_id}/artifact.json"
    )
    if store_artifact_bytes:
        stored = artifact_write_service.store_durable_bytes(
            task.task_id,
            artifact_storage_ref,
            content.encode("utf-8"),
            "application/json",
        )
        artifact_storage_type = stored.storage_type
        artifact_storage_scope = stored.storage_scope
        artifact_storage_ref = stored.storage_ref
    else:
        artifact_storage_type = "local"
        artifact_storage_scope = authorized.task_id
    assert repository.reserve_dispatch_result_storage(
        authorized,
        storage_type="local",
        storage_scope=authorized.task_id,
        storage_ref=result_storage_ref,
    ) is True
    assert repository.record_dispatch_completed_result(
        authorized,
        owner_id="owner",
        workspace_id="workspace",
        result=DispatchSubmissionCompleted(
            title="Durable continuation input",
            mime_type="text/plain",
            content=content,
            provider_id="openai_primary",
            adapter="huiyan_chat_completions",
        ),
        artifact_contract="test_artifact_v1",
        artifact_schema_digest="a" * 64,
        storage_type="local",
        storage_scope=authorized.task_id,
        storage_ref=result_storage_ref,
        content_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        artifact_storage_type=artifact_storage_type,
        artifact_storage_scope=artifact_storage_scope,
        artifact_storage_ref=artifact_storage_ref,
    ) is True
    return task, dispatch


def _insert_unrelated_dispatch_witness(
    connect,
    *,
    task_id: str,
    suffix: str,
    status: str = "pending",
) -> str:
    """Insert a same-task Dispatch that is deliberately outside source lineage.

    This is test setup for the historical task-scoped EXISTS false positive.  The
    IDs do not appear in the canonical Task execution graph.
    """
    dispatch_id = f"dispatch-unrelated-{suffix}"
    with connect() as connection:
        connection.execute(
            """INSERT INTO openair_dispatch (
                   dispatch_id, task_id, execution_decision_id, work_unit_id,
                   idempotency_key, execution_namespace, status, attempt_count,
                   lease_owner, lease_expires_at, authorization_task_revision,
                   remote_run_id, next_attempt_at, last_error_code, created_at, updated_at
               ) VALUES (
                   %s, %s, %s, %s, %s, %s, %s, 0,
                   NULL, NULL, NULL, NULL, clock_timestamp(), NULL,
                   clock_timestamp(), clock_timestamp()
               )""",
            (
                dispatch_id,
                task_id,
                f"decision-unrelated-{suffix}",
                f"work-unit-unrelated-{suffix}",
                f"idempotency-unrelated-{suffix}",
                settings.execution_namespace,
                status,
            ),
        )
    return dispatch_id


def _commit_cross_decision_successor(
    repository,
    postgres_environment,
    suffix: str,
    *,
    materialize_live_work: bool = True,
):
    task, source_dispatch = _persist_completed_dispatch_for_continuation(
        repository,
        postgres_environment,
        suffix,
    )
    source_fence = repository.claim_dispatch_continuation(
        lease_owner=f"continuation-cross-{suffix}",
        lease_seconds=300,
        retry_delay_seconds=1,
    )
    assert source_fence is not None
    current = repository.load(
        task_id=task.task_id,
        owner_id="owner",
        workspace_id="workspace",
    )

    def close_source(payload):
        payload["executionDecisions"][0].update(
            {
                "status": "closed",
                "outcome": "succeeded",
                "closedAt": datetime.now(timezone.utc).isoformat(),
            }
        )
        payload["substageRuntimeStates"][0].update(
            {
                "status": "ready",
                "statusReasonCode": None,
                "activeExecutionDecisionId": None,
            }
        )

    baseline = _save_responsibility_fixture_state(repository, current, close_source)
    payload = baseline.model_dump(mode="json", by_alias=True)
    source_decision = payload["executionDecisions"][0]
    source_assignment = source_decision["assignments"][0]
    source_work = payload["workUnits"][0]
    successor_decision_id = f"decision-cross-{suffix}"
    successor_assignment_id = f"assignment-cross-{suffix}"
    successor_work_unit_id = f"work-cross-{suffix}"
    successor_dispatch_id = f"dispatch-cross-{suffix}"
    selection_request_id = f"continuation:{source_fence.execution_decision_id}"

    successor_decision = deepcopy(source_decision)
    successor_decision.update(
        {
            "executionDecisionId": successor_decision_id,
            "selectionRequestId": selection_request_id,
            "decisionSequence": int(source_decision["decisionSequence"]) + 1,
            "strategy": "direct_skill",
            "multiAgentRecipeRef": None,
            "status": "dispatched",
            "outcome": None,
            "closedAt": None,
        }
    )
    successor_assignment = deepcopy(source_assignment)
    successor_assignment.update(
        {
            "assignmentId": successor_assignment_id,
            "dependencyAssignmentIds": [],
            "recipeTemplateKey": None,
        }
    )
    successor_decision["assignments"] = [successor_assignment]
    successor_decision["completionPolicy"].update(
        {
            "requiredAssignmentIds": [successor_assignment_id],
            "reviewerAssignmentIds": [],
            "reviewRequirement": "none",
        }
    )
    successor_result_policy = successor_decision["resultAssemblyPolicy"]
    if successor_result_policy.get("assemblerAssignmentId") is not None:
        successor_result_policy["assemblerAssignmentId"] = successor_assignment_id
    for requirement in successor_result_policy.get("finalArtifactRequirements") or []:
        requirement["sourceAssignmentIds"] = [successor_assignment_id]
    successor_work = deepcopy(source_work)
    successor_work.update(
        {
            "workUnitId": successor_work_unit_id,
            "assignmentId": successor_assignment_id,
            "executionDecisionId": successor_decision_id,
            "idempotencyKey": f"idempotency-cross-{suffix}",
            "status": "queued",
            "terminalReasonCode": None,
            "finishedAt": None,
        }
    )
    payload["executionDecisions"].append(successor_decision)
    if materialize_live_work:
        payload["workUnits"].append(successor_work)
        payload["substageRuntimeStates"][0].update(
            {
                "status": "queued",
                "activeExecutionDecisionId": successor_decision_id,
                "workUnitIds": [successor_work_unit_id],
                "statusReasonCode": None,
            }
        )
    candidate = ResearchTaskV12.model_validate(payload)
    dispatch_payload = source_dispatch.model_dump(mode="json", by_alias=True)
    dispatch_payload.update(
        {
            "dispatchId": successor_dispatch_id,
            "taskId": task.task_id,
            "executionDecisionId": successor_decision_id,
            "workUnitId": successor_work_unit_id,
            "idempotencyKey": f"idempotency-cross-{suffix}",
            "status": "pending",
            "attemptCount": 0,
            "leaseOwner": None,
            "leaseExpiresAt": None,
            "authorizationTaskRevision": None,
            "remoteRunId": None,
            "nextAttemptAt": None,
            "lastErrorCode": None,
        }
    )
    successor_dispatch = DispatchRecord.model_validate(dispatch_payload)
    successor_model = candidate.execution_decisions[-1]
    instance = candidate.research_design_versions[0].instances[0]
    prepared = PreparedSelection(
        task=baseline,
        instance=instance,
        resolution=ResolutionResult(
            designRef=candidate.active_research_design_ref,
            instanceRef={
                "substageInstanceId": instance.substage_instance_id,
                "instanceRevision": instance.instance_revision,
            },
            registrySnapshotRef=candidate.registry_snapshot_ref,
            candidateEvaluations=successor_model.candidate_evaluations,
            preferredCandidateEvaluationRefs=[],
        ),
        snapshot_envelope={},
        runtime_readiness={},
        selection_request_id=selection_request_id,
        selection_command_hash=(
            "sha256:" + hashlib.sha256(f"command:{suffix}".encode()).hexdigest()
        ),
        selection_payload_hash=(
            "sha256:" + hashlib.sha256(f"payload:{suffix}".encode()).hexdigest()
        ),
        expected_task_revision=baseline.revision,
        owner_id="owner",
        workspace_id="workspace",
        final_tie_candidate_ids=("candidate:cross-decision",),
        fairness=None,
        decided_at=successor_model.decided_at,
    )

    def materialize(_locked_task, winner: str):
        return ExecutionMaterialization(
            task=candidate,
            decision=successor_model,
            work_units=((candidate.work_units[-1],) if materialize_live_work else ()),
            dispatches=((successor_dispatch,) if materialize_live_work else ()),
            selected_candidate_identity=winner,
        )

    committed = repository.commit_prepared_selection(prepared, materialize)
    return task, source_dispatch, source_fence, committed, successor_dispatch


def test_synchronous_completed_result_atomically_persists_artifact_and_lineage(
    repository, postgres_environment
) -> None:
    import hashlib

    task, dispatch = _seed_with_archived_registry(
        repository, postgres_environment, "completed_result"
    )
    fence = _claim(repository, task.task_id, "worker-result")
    assert fence is not None
    authorized = repository.authorize_dispatch_submission(
        fence,
        owner_id="owner",
        workspace_id="workspace",
        expected_task_revision=1,
        submission_mode="single_attempt_no_replay",
    )
    assert authorized is not None
    authorized = _start(repository, authorized)
    content = '{"reasoning_summary":"The supplied evidence supports the bounded research direction."}'
    result_storage_ref = f"durable/research-dispatch/{dispatch.dispatch_id}/result.json"
    assert repository.reserve_dispatch_result_storage(
        authorized,
        storage_type="local",
        storage_scope=authorized.task_id,
        storage_ref=result_storage_ref,
    ) is True

    assert repository.record_dispatch_completed_result(
        authorized,
        owner_id="owner",
        workspace_id="workspace",
        result=DispatchSubmissionCompleted(
            title="Validated result",
            mime_type="text/plain",
            content=content,
            provider_id="openai_primary",
            adapter="huiyan_chat_completions",

            warnings=(
                "intent_ambiguities",
                "missing_information",
                "research_risks",
            ),
            result_protocol={
                "status": "degraded",
                "accepted_items": [{"path": "queries", "count": 7}],
                "quarantined_items": [{"path": "queries[7]", "reason": "invalid_query"}],
                "repaired_items": [],
                "warnings": ["one query was skipped"],
                "coverage": {"accepted_query_count": 7, "input_query_count": 8},
            },
            warnings=("one query was skipped",),
        ),
        artifact_contract="test_artifact_v1",
        artifact_schema_digest="a" * 64,
        storage_type="local",
        storage_scope=authorized.task_id,
        storage_ref=result_storage_ref,
        content_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        artifact_storage_type="local",
        artifact_storage_scope=authorized.task_id,
        artifact_storage_ref=f"durable/research-dispatch/{dispatch.dispatch_id}/artifact.json",
    ) is True

    persisted = _dispatch_row(postgres_environment, dispatch.dispatch_id)
    assert persisted["status"] == "submitted"
    assert persisted["result_storage_ref"].endswith("/result.json")
    task_row = _task_row(postgres_environment, task.task_id)
    artifact = task_row["payload"]["artifacts"][0]
    result = task_row["payload"]["results"][0]
    assert artifact["artifactState"] == "usable"
    assert result["checkStatus"] == "passed"
    assert artifact["validatorResult"]["resultProtocol"]["status"] == "degraded"
    assert artifact["validatorResult"]["artifactQuality"] == "degraded"
    assert artifact["validatorResult"]["warnings"] == [
        "one query was skipped",
        "intent_ambiguities",
        "missing_information",
        "research_risks",
    ]
    assert "formalGate" not in artifact["validatorResult"]
    assert result["checkResult"]["resultProtocol"]["status"] == "degraded"
    assert result["checkResult"]["artifactQuality"] == "degraded"
    assert task_row["payload"].get("executionIntegrityBlockers", []) == []
    assert all(
        row["status"] not in {"external_result_unknown", "blocked_recoverable"}
        for row in task_row["payload"]["workUnits"]
    )
    with postgres_environment() as connection:
        continuation = connection.execute(
            """SELECT status, attempt_count, next_attempt_at
                 FROM openair_dispatch_continuation WHERE dispatch_id = %s""",
            (dispatch.dispatch_id,),
        ).fetchone()
    assert continuation["status"] == "pending"
    assert continuation["attempt_count"] == 0
    assert continuation["next_attempt_at"] is not None

    row = _dispatch_row(postgres_environment, dispatch.dispatch_id)
    assert row["status"] == "submitted"
    assert row["result_storage_type"] == "local"
    assert row["result_content_sha256"] == hashlib.sha256(content.encode("utf-8")).hexdigest()
    task_row = _task_row(postgres_environment, task.task_id)
    work = task_row["payload"]["workUnits"][0]
    assert work["status"] == "completed"
    assert len(task_row["payload"]["capabilityCalls"]) == 1
    call = task_row["payload"]["capabilityCalls"][0]
    assert call["capabilityCallId"] == dispatch_capability_call_id(authorized)
    assert call["attemptNumber"] == 1
    assert call["status"] == "succeeded"
    assert call["finishedAt"] is not None
    assert len(task_row["payload"]["results"]) == 1
    formal_result = task_row["payload"]["results"][0]
    assert formal_result["resultId"] == f"result:{dispatch.dispatch_id}"
    assert formal_result["workUnitId"] == work["workUnitId"]
    assert formal_result["capabilityCallId"] == call["capabilityCallId"]
    assert formal_result["storageRef"] == result_storage_ref
    assert formal_result["contentSha256"] == hashlib.sha256(
        content.encode("utf-8")
    ).hexdigest()
    assert formal_result["checkStatus"] == "passed"
    assert len(task_row["payload"]["artifacts"]) == 1
    artifact = task_row["payload"]["artifacts"][0]
    artifact_id = artifact["artifactId"]
    assert artifact["sourceResultId"] == formal_result["resultId"]
    assert formal_result["artifactRefs"] == [artifact_id]
    assert formal_result["resultId"] != artifact_id
    messages = _task_message_rows(postgres_environment, task.task_id)
    assert len(messages) == 1
    message = messages[0]
    assert message["role"] == "assistant"
    assert message["content_text"] == "The supplied evidence supports the bounded research direction."
    assert message["content_text"] != content
    assert "openai_primary" not in message["content_text"]
    assert message["capability_call_id"] == call["capabilityCallId"]
    metadata = message["metadata"]
    assert metadata["workUnitId"] == work["workUnitId"]
    assert metadata["artifactRefs"] == [artifact_id]
    assert metadata["authorLabel"] == task_row["payload"]["executionDecisions"][0]["assignments"][0]["executorRef"]["executorId"]
    assert metadata["userFacingNarrative"]["detailCollapsedByDefault"] is True
    assert metadata["userFacingNarrative"]["sourceType"] == "capability_call"
    assert "本次工作：" in metadata["userFacingNarrative"]["detailText"]
    assert "openai_primary" not in metadata["userFacingNarrative"]["detailText"]
    assert task_row["revision"] == task_row["payload"]["revision"]
    assert repository.record_dispatch_completed_result(
        authorized,
        owner_id="owner",
        workspace_id="workspace",
        result=DispatchSubmissionCompleted(
            title="Validated result",
            mime_type="text/plain",
            content=content,
            provider_id="openai_primary",
            adapter="huiyan_chat_completions",

            warnings=(
                "intent_ambiguities",
                "missing_information",
                "research_risks",
            ),
            result_protocol={
                "status": "degraded",
                "accepted_items": [{"path": "queries", "count": 7}],
                "quarantined_items": [{"path": "queries[7]", "reason": "invalid_query"}],
                "repaired_items": [],
                "warnings": ["one query was skipped"],
                "coverage": {"accepted_query_count": 7, "input_query_count": 8},
            },
            warnings=("one query was skipped",),
        ),
        artifact_contract="test_artifact_v1",
        artifact_schema_digest="a" * 64,
        storage_type="local",
        storage_scope=authorized.task_id,
        storage_ref=f"durable/research-dispatch/{dispatch.dispatch_id}/result.json",
        content_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        artifact_storage_type="local",
        artifact_storage_scope=authorized.task_id,
        artifact_storage_ref=f"durable/research-dispatch/{dispatch.dispatch_id}/artifact.json",
    ) is False
    assert len(_task_message_rows(postgres_environment, task.task_id)) == 1
    assert len(_task_row(postgres_environment, task.task_id)["payload"]["results"]) == 1
    with pytest.raises(
        ResearchOrchestrationPersistenceError,
        match="dispatch_completed_result_identity_conflict",
    ):
        repository.record_dispatch_completed_result(
            authorized,
            owner_id="owner",
            workspace_id="workspace",
            result=DispatchSubmissionCompleted(
                title="Validated result",
                mime_type="text/plain",
                content=content,
                provider_id="openai_primary",
                adapter="huiyan_chat_completions",
                result_protocol={"status": "ok"},
            ),
            artifact_contract="test_artifact_v1",
            artifact_schema_digest="a" * 64,
            storage_type="local",
            storage_scope=authorized.task_id,
            storage_ref=f"durable/research-dispatch/{dispatch.dispatch_id}/result.json",
            content_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
            artifact_storage_type="local",
            artifact_storage_scope=authorized.task_id,
            artifact_storage_ref=f"durable/research-dispatch/{dispatch.dispatch_id}/artifact.json",
        )
    with postgres_environment() as connection:
        artifact_row = connection.execute(
            "SELECT content_sha256, storage_ref, metadata FROM openair_artifacts WHERE artifact_id = %s",
            (artifact_id,),
        ).fetchone()
        assert artifact_row["content_sha256"] == row["result_content_sha256"]
        assert artifact_row["storage_ref"].endswith("/artifact.json")
        assert artifact_row["metadata"]["resultProtocol"]["status"] == "degraded"
        assert artifact_row["metadata"]["resultProtocol"]["quarantined_items"] == [
            {"path": "queries[7]", "reason": "invalid_query"}
        ]
        assert artifact_row["metadata"]["resultProtocol"]["coverage"] == {
            "accepted_query_count": 7,
            "input_query_count": 8,
        }
        assert artifact_row["metadata"]["artifactState"] == "usable"
        assert artifact_row["metadata"]["artifactQuality"] == "degraded"
        assert artifact_row["metadata"]["warnings"] == [
            "one query was skipped",
            "intent_ambiguities",
            "missing_information",
            "research_risks",
        ]
        assert connection.execute(
            "SELECT count(*) AS count FROM openair_capability_call_lineage WHERE task_id = %s",
            (task.task_id,),
        ).fetchone()["count"] == 1


def test_dispatch_continuation_renew_and_settlement_are_epoch_fenced(
    repository, postgres_environment
) -> None:
    task, dispatch = _seed(repository, postgres_environment, "continuation_lease_fence")
    with postgres_environment() as connection:
        connection.execute(
            """INSERT INTO openair_dispatch_continuation (
                   dispatch_id, task_id, execution_decision_id, execution_namespace
               ) VALUES (%s, %s, %s, %s)""",
            (
                dispatch.dispatch_id,
                task.task_id,
                dispatch.execution_decision_id,
                settings.execution_namespace,
            ),
        )

    first = repository.claim_dispatch_continuation(
        lease_owner="continuation-a",
        lease_seconds=30,
        retry_delay_seconds=1,
    )
    assert first is not None
    assert repository.renew_dispatch_continuation(first, lease_seconds=30) is True

    with postgres_environment() as connection:
        connection.execute(
            """UPDATE openair_dispatch_continuation
                  SET lease_expires_at = clock_timestamp() - interval '1 second'
                WHERE dispatch_id = %s""",
            (dispatch.dispatch_id,),
        )

    assert repository.renew_dispatch_continuation(first, lease_seconds=30) is False
    assert repository.claim_dispatch_continuation(
        lease_owner="continuation-b",
        lease_seconds=30,
        retry_delay_seconds=1,
    ) is None
    with postgres_environment() as connection:
        connection.execute(
            """UPDATE openair_dispatch_continuation
                  SET next_attempt_at = clock_timestamp()
                WHERE dispatch_id = %s""",
            (dispatch.dispatch_id,),
        )

    second = repository.claim_dispatch_continuation(
        lease_owner="continuation-b",
        lease_seconds=30,
        retry_delay_seconds=1,
    )
    assert second is not None
    assert second.attempt_count == first.attempt_count + 1
    assert repository.complete_dispatch_continuation(first) is False
    assert repository.defer_dispatch_continuation(
        first,
        error_code="stale_holder",
        retry_delay_seconds=30,
    ) is False
    assert repository.complete_dispatch_continuation(second) is False
    assert repository.defer_dispatch_continuation(
        second,
        error_code="dispatch_continuation_not_completed",
        retry_delay_seconds=30,
    ) is True
    with postgres_environment() as connection:
        continuation = connection.execute(
            """SELECT status, next_attempt_at > clock_timestamp() AS future_retry
                 FROM openair_dispatch_continuation WHERE dispatch_id = %s""",
            (dispatch.dispatch_id,),
        ).fetchone()
    assert continuation == {"status": "pending", "future_retry": True}
    assert _task_row(postgres_environment, task.task_id)["status"] == "running"


def test_dispatch_continuation_retry_is_future_dated_and_epoch_fenced(
    repository, postgres_environment
) -> None:
    task, dispatch = _seed_with_archived_registry(
        repository, postgres_environment, "continuation_retry"
    )
    authorized = _start(repository, _authorize(repository, task.task_id))
    content = '{"result":"validated"}'
    result_storage_ref = f"durable/research-dispatch/{dispatch.dispatch_id}/result.json"
    assert repository.reserve_dispatch_result_storage(
        authorized,
        storage_type="local",
        storage_scope=authorized.task_id,
        storage_ref=result_storage_ref,
    ) is True
    assert repository.record_dispatch_completed_result(
        authorized,
        owner_id="owner",
        workspace_id="workspace",
        result=DispatchSubmissionCompleted(
            title="Validated result",
            mime_type="text/plain",
            content=content,
            provider_id="openai_primary",
            adapter="huiyan_chat_completions",
        ),
        artifact_contract="test_artifact_v1",
        artifact_schema_digest="a" * 64,
        storage_type="local",
        storage_scope=authorized.task_id,
        storage_ref=result_storage_ref,
        content_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        artifact_storage_type="local",
        artifact_storage_scope=authorized.task_id,
        artifact_storage_ref=(
            f"durable/research-dispatch/{dispatch.dispatch_id}/artifact.json"
        ),
    ) is True

    first = repository.claim_dispatch_continuation(
        lease_owner="continuation-a",
        lease_seconds=30,
        retry_delay_seconds=5,
    )
    assert first is not None
    assert repository.renew_dispatch_continuation(first, lease_seconds=30) is True
    assert repository.claim_dispatch_continuation(
        lease_owner="continuation-b",
        lease_seconds=30,
        retry_delay_seconds=5,
    ) is None
    assert repository.defer_dispatch_continuation(
        first,
        error_code="injected_continuation_failure",
        retry_delay_seconds=30,
    ) is True
    assert repository.complete_dispatch_continuation(first) is False
    assert repository.renew_dispatch_continuation(first, lease_seconds=30) is False
    assert repository.claim_dispatch_continuation(
        lease_owner="continuation-b",
        lease_seconds=30,
        retry_delay_seconds=5,
    ) is None

    fresh_task, _fresh_dispatch = _seed(
        repository,
        postgres_environment,
        "fresh_during_continuation_backoff",
    )
    assert _claim(repository, fresh_task.task_id, "fresh-worker") is not None
    with postgres_environment() as connection:
        row = connection.execute(
            """SELECT status, attempt_count,
                      next_attempt_at > clock_timestamp() AS future_retry
                 FROM openair_dispatch_continuation WHERE dispatch_id = %s""",
            (dispatch.dispatch_id,),
        ).fetchone()
    assert row == {
        "status": "pending",
        "attempt_count": 1,
        "future_retry": True,
    }

    with postgres_environment() as connection:
        connection.execute(
            "UPDATE openair_dispatch_continuation SET next_attempt_at = clock_timestamp() "
            "WHERE dispatch_id = %s",
            (dispatch.dispatch_id,),
        )
    second = repository.claim_dispatch_continuation(
        lease_owner="continuation-b",
        lease_seconds=30,
        retry_delay_seconds=5,
    )
    assert second is not None
    assert second.attempt_count == 2
    assert repository.renew_dispatch_continuation(second, lease_seconds=30) is True
    assert repository.complete_dispatch_continuation(first) is False
    assert repository.defer_dispatch_continuation(
        first,
        error_code="stale_holder",
        retry_delay_seconds=30,
    ) is False
    # State value alone is not responsibility proof.  Even if this fixture is
    # projected as awaiting_confirmation, the continuation remains owned until
    # a named user/successor/recovery proof is supplied.
    assert repository.complete_dispatch_continuation(second) is False
    assert repository.defer_dispatch_continuation(
        second,
        error_code="dispatch_continuation_no_responsibility_proof",
        retry_delay_seconds=30,
    ) is True


def test_restarted_continuation_worker_reclaims_durable_owner_after_backoff(
    repository, postgres_environment,
) -> None:
    _task, dispatch = _persist_completed_dispatch_for_continuation(
        repository,
        postgres_environment,
        "continuation_worker_restart_reclaim",
    )
    first = repository.claim_dispatch_continuation(
        lease_owner="continuation-before-restart",
        lease_seconds=30,
        retry_delay_seconds=1,
    )
    assert first is not None
    assert repository.defer_dispatch_continuation(
        first,
        error_code="worker_restart_injected",
        retry_delay_seconds=1,
    ) is True
    with postgres_environment() as connection:
        pending = connection.execute(
            """SELECT status, completed_at,
                      next_attempt_at > clock_timestamp() AS future_retry
                 FROM openair_dispatch_continuation WHERE dispatch_id = %s""",
            (dispatch.dispatch_id,),
        ).fetchone()
    assert pending == {
        "status": "pending",
        "completed_at": None,
        "future_retry": True,
    }

    sleep(1.1)
    restarted_repository = ResearchOrchestrationRepository(
        connection_factory=postgres_environment,
        runtime_settings=settings,
    )
    second = restarted_repository.claim_dispatch_continuation(
        lease_owner="continuation-after-restart",
        lease_seconds=30,
        retry_delay_seconds=1,
    )
    assert second is not None
    assert second.dispatch_id == dispatch.dispatch_id
    assert second.attempt_count == first.attempt_count + 1
    assert repository.complete_dispatch_continuation(first) is False
    assert restarted_repository.defer_dispatch_continuation(
        second,
        error_code="dispatch_continuation_no_responsibility_proof",
        retry_delay_seconds=30,
    ) is True
    with postgres_environment() as connection:
        retained = connection.execute(
            """SELECT status, lease_owner, completed_at,
                      next_attempt_at > clock_timestamp() AS future_retry
                 FROM openair_dispatch_continuation WHERE dispatch_id = %s""",
            (dispatch.dispatch_id,),
        ).fetchone()
    assert retained == {
        "status": "pending",
        "lease_owner": None,
        "completed_at": None,
        "future_retry": True,
    }


def test_exact_same_decision_successor_owner_can_complete_source_continuation(
    repository,
    postgres_environment,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from app.execution import execution_context

    monkeypatch.setattr(settings, "artifact_storage", "local")
    monkeypatch.setattr(settings, "workspace_root", tmp_path)
    monkeypatch.setattr(
        execution_context,
        "get_task_workspace_dir",
        lambda _task_id: tmp_path,
    )
    task, dispatch = _persist_completed_dispatch_for_continuation(
        repository,
        postgres_environment,
        "exact_same_decision_successor",
        store_artifact_bytes=True,
    )
    fence = repository.claim_dispatch_continuation(
        lease_owner="continuation-successor",
        lease_seconds=30,
        retry_delay_seconds=1,
    )
    assert fence is not None

    proof = ResearchDispatchService(repository).process_continuation(fence)

    assert proof.kind is ContinuationResponsibilityKind.SUCCESSOR_EXECUTION
    assert proof.decision_lineage == (fence.execution_decision_id,)
    assert proof.successor_execution_decision_id == fence.execution_decision_id
    assert proof.successor_work_unit_id != fence.work_unit_id
    assert proof.owner_kind is ContinuationOwnerKind.DISPATCH
    assert repository.complete_dispatch_continuation(fence, proof=proof) is True
    with postgres_environment() as connection:
        source = connection.execute(
            """SELECT status, completed_at IS NOT NULL AS completed
                 FROM openair_dispatch_continuation WHERE dispatch_id = %s""",
            (dispatch.dispatch_id,),
        ).fetchone()
    assert source == {"status": "completed", "completed": True}


def test_exact_cross_decision_dispatch_owner_can_complete_source_continuation(
    repository, postgres_environment,
) -> None:
    task, source_dispatch, source_fence, committed, successor_dispatch = (
        _commit_cross_decision_successor(
            repository, postgres_environment, "cross_dispatch_owner"
        )
    )
    proof = ResearchDispatchService(repository)._successor_execution_proof_from_task(
        source_fence,
        task=committed.task,
        successor_execution_decision_id=committed.decision.execution_decision_id,
        selection_request_id=f"continuation:{source_fence.execution_decision_id}",
    )

    assert proof.kind is ContinuationResponsibilityKind.SUCCESSOR_EXECUTION
    assert proof.decision_lineage == (
        source_fence.execution_decision_id,
        committed.decision.execution_decision_id,
    )
    assert proof.owner_kind is ContinuationOwnerKind.DISPATCH
    assert proof.owner_id == successor_dispatch.dispatch_id
    assert repository.complete_dispatch_continuation(source_fence, proof=proof) is True
    with postgres_environment() as connection:
        row = connection.execute(
            "SELECT status FROM openair_dispatch_continuation WHERE dispatch_id = %s",
            (source_dispatch.dispatch_id,),
        ).fetchone()
    assert row["status"] == "completed"


def test_exact_cross_decision_continuation_owner_can_complete_source_continuation(
    repository, postgres_environment,
) -> None:
    task, source_dispatch, source_fence, committed, successor_dispatch = (
        _commit_cross_decision_successor(
            repository, postgres_environment, "cross_continuation_owner"
        )
    )
    successor_fence = _claim(repository, task.task_id, "successor-worker")
    assert successor_fence is not None
    assert successor_fence.dispatch_id == successor_dispatch.dispatch_id
    authorized = repository.authorize_dispatch_submission(
        successor_fence,
        owner_id="owner",
        workspace_id="workspace",
        expected_task_revision=committed.task.revision,
    )
    assert authorized is not None
    authorized = repository.mark_external_action_started(
        authorized,
        provider_id="openai_primary",
        adapter="huiyan_chat_completions",
    )
    assert authorized is not None
    content = '{"result":"cross decision successor durable"}'
    result_storage_ref = (
        f"durable/research-dispatch/{successor_dispatch.dispatch_id}/result.json"
    )
    assert repository.reserve_dispatch_result_storage(
        authorized,
        storage_type="local",
        storage_scope=task.task_id,
        storage_ref=result_storage_ref,
    ) is True
    assert repository.record_dispatch_completed_result(
        authorized,
        owner_id="owner",
        workspace_id="workspace",
        result=DispatchSubmissionCompleted(
            title="Cross decision successor",
            mime_type="text/plain",
            content=content,
            provider_id="openai_primary",
            adapter="huiyan_chat_completions",
        ),
        artifact_contract="test_artifact_v1",
        artifact_schema_digest="a" * 64,
        storage_type="local",
        storage_scope=task.task_id,
        storage_ref=result_storage_ref,
        content_sha256=hashlib.sha256(content.encode()).hexdigest(),
        artifact_storage_type="local",
        artifact_storage_scope=task.task_id,
        artifact_storage_ref=(
            f"durable/research-dispatch/{successor_dispatch.dispatch_id}/artifact.json"
        ),
    ) is True
    durable = repository.load(
        task_id=task.task_id,
        owner_id="owner",
        workspace_id="workspace",
    )
    proof = ResearchDispatchService(repository)._successor_execution_proof_from_task(
        source_fence,
        task=durable,
        successor_execution_decision_id=committed.decision.execution_decision_id,
        selection_request_id=f"continuation:{source_fence.execution_decision_id}",
    )

    assert proof.kind is ContinuationResponsibilityKind.SUCCESSOR_EXECUTION
    assert proof.owner_kind is ContinuationOwnerKind.CONTINUATION
    assert proof.owner_id == successor_dispatch.dispatch_id
    assert repository.complete_dispatch_continuation(source_fence, proof=proof) is True
    with postgres_environment() as connection:
        rows = connection.execute(
            """SELECT dispatch_id, status
                 FROM openair_dispatch_continuation
                WHERE dispatch_id IN (%s, %s)""",
            (source_dispatch.dispatch_id, successor_dispatch.dispatch_id),
        ).fetchall()
    assert {row["dispatch_id"]: row["status"] for row in rows} == {
        source_dispatch.dispatch_id: "completed",
        successor_dispatch.dispatch_id: "pending",
    }


def test_cross_decision_without_live_work_unit_cannot_complete_source_continuation(
    repository, postgres_environment,
) -> None:
    _task, source_dispatch, source_fence, committed, _successor_dispatch = (
        _commit_cross_decision_successor(
            repository,
            postgres_environment,
            "cross_decision_without_live_work",
            materialize_live_work=False,
        )
    )
    proof = ResearchDispatchService(repository)._successor_execution_proof_from_task(
        source_fence,
        task=committed.task,
        successor_execution_decision_id=committed.decision.execution_decision_id,
        selection_request_id=f"continuation:{source_fence.execution_decision_id}",
    )
    assert proof.kind is ContinuationResponsibilityKind.NO_PROOF
    assert repository.complete_dispatch_continuation(source_fence, proof=proof) is False
    with postgres_environment() as connection:
        source = connection.execute(
            """SELECT status, completed_at
                 FROM openair_dispatch_continuation WHERE dispatch_id = %s""",
            (source_dispatch.dispatch_id,),
        ).fetchone()
    assert source == {"status": "claimed", "completed_at": None}


def test_historical_cross_decision_without_live_owner_cannot_complete_source_continuation(
    repository, postgres_environment,
) -> None:
    task, source_dispatch, source_fence, committed, successor_dispatch = (
        _commit_cross_decision_successor(
            repository, postgres_environment, "cross_decision_historical_owner"
        )
    )
    successor_fence = _claim(repository, task.task_id, "historical-successor-worker")
    assert successor_fence is not None
    assert successor_fence.dispatch_id == successor_dispatch.dispatch_id
    authorized = repository.authorize_dispatch_submission(
        successor_fence,
        owner_id="owner",
        workspace_id="workspace",
        expected_task_revision=committed.task.revision,
    )
    assert authorized is not None
    assert repository.record_dispatch_outcome(
        authorized,
        owner_id="owner",
        workspace_id="workspace",
        outcome="known_failed",
        error_code="historical_successor_failed",
        provider_id="openai_primary",
        adapter="huiyan_chat_completions",
    )
    latest = repository.load(
        task_id=task.task_id,
        owner_id="owner",
        workspace_id="workspace",
    )
    proof = ResearchDispatchService(repository)._successor_execution_proof_from_task(
        source_fence,
        task=latest,
        successor_execution_decision_id=committed.decision.execution_decision_id,
        selection_request_id=f"continuation:{source_fence.execution_decision_id}",
    )
    assert proof.kind is ContinuationResponsibilityKind.NO_PROOF
    assert repository.complete_dispatch_continuation(source_fence, proof=proof) is False
    with postgres_environment() as connection:
        source = connection.execute(
            """SELECT status, completed_at
                 FROM openair_dispatch_continuation WHERE dispatch_id = %s""",
            (source_dispatch.dispatch_id,),
        ).fetchone()
    assert source == {"status": "claimed", "completed_at": None}


def _save_responsibility_fixture_state(repository, task, mutate):
    current = repository.load(
        task_id=task.task_id,
        owner_id="owner",
        workspace_id="workspace",
    )
    payload = current.model_dump(mode="json", by_alias=True)
    mutate(payload)
    candidate = ResearchTaskV12.model_validate(payload)
    return repository.save(
        candidate,
        owner_id="owner",
        workspace_id="workspace",
        expected_task_revision=current.revision,
    )


@pytest.mark.parametrize(
    ("reason_code", "execution_mode"),
    (
        ("stage_confirmation_required", "stage_confirmation"),
        ("manual_step_confirmation_required", "manual_step"),
    ),
)
def test_explicit_user_gate_is_transactionally_proven_in_postgres(
    repository,
    postgres_environment,
    reason_code: str,
    execution_mode: str,
) -> None:
    task, dispatch = _persist_completed_dispatch_for_continuation(
        repository,
        postgres_environment,
        f"user_gate_{execution_mode}",
    )

    def mutate(payload):
        payload["status"] = "awaiting_confirmation"
        payload["executionMode"] = execution_mode
        payload["substageRuntimeStates"][0].update(
            {
                "status": "ready",
                "statusReasonCode": reason_code,
                "activeExecutionDecisionId": None,
            }
        )

    gated = _save_responsibility_fixture_state(repository, task, mutate)
    fence = repository.claim_dispatch_continuation(
        lease_owner=f"continuation-{execution_mode}",
        lease_seconds=30,
        retry_delay_seconds=1,
    )
    assert fence is not None
    proof = ResearchDispatchService(repository)._user_responsibility_proof(
        fence,
        reason_code=reason_code,
        target_instance_id=gated.substage_runtime_states[0].substage_instance_id,
    )

    assert proof.kind is ContinuationResponsibilityKind.USER_RESPONSIBILITY
    assert proof.user_action
    assert proof.resume_path
    assert repository.complete_dispatch_continuation(fence, proof=proof) is True
    with postgres_environment() as connection:
        row = connection.execute(
            "SELECT status FROM openair_dispatch_continuation WHERE dispatch_id = %s",
            (dispatch.dispatch_id,),
        ).fetchone()
    assert row["status"] == "completed"


def test_research_design_confirmation_is_transactionally_proven_in_postgres(
    repository, postgres_environment,
) -> None:
    task, dispatch = _persist_completed_dispatch_for_continuation(
        repository,
        postgres_environment,
        "user_gate_research_design",
    )

    def add_draft(payload):
        payload["status"] = "awaiting_confirmation"
        draft = deepcopy(payload["researchDesignVersions"][-1])
        design_id = draft["researchDesignId"]
        prior_version = int(draft["version"])
        draft_version = prior_version + 1

        def advance_design_ref(value):
            if isinstance(value, dict):
                replaced = {
                    key: advance_design_ref(item) for key, item in value.items()
                }
                if (
                    replaced.get("researchDesignId") == design_id
                    and replaced.get("version") == prior_version
                ):
                    replaced["version"] = draft_version
                return replaced
            if isinstance(value, list):
                return [advance_design_ref(item) for item in value]
            return value

        draft = advance_design_ref(draft)
        draft["version"] = draft_version
        draft["status"] = "draft"
        payload["researchDesignVersions"].append(draft)

    gated = _save_responsibility_fixture_state(repository, task, add_draft)
    fence = repository.claim_dispatch_continuation(
        lease_owner="continuation-research-design",
        lease_seconds=30,
        retry_delay_seconds=1,
    )
    assert fence is not None
    proof = ResearchDispatchService(repository)._user_responsibility_proof(
        fence,
        reason_code="research_design_confirmation_required",
    )

    assert proof.kind is ContinuationResponsibilityKind.USER_RESPONSIBILITY
    assert proof.target_id == gated.research_design_versions[-1].research_design_id
    assert proof.target_revision == gated.research_design_versions[-1].version
    assert repository.complete_dispatch_continuation(fence, proof=proof) is True
    with postgres_environment() as connection:
        row = connection.execute(
            "SELECT status FROM openair_dispatch_continuation WHERE dispatch_id = %s",
            (dispatch.dispatch_id,),
        ).fetchone()
    assert row["status"] == "completed"


def _prepare_formal_needs_input_proof(repository, postgres_environment, suffix: str):
    task, dispatch = _persist_completed_dispatch_for_continuation(
        repository,
        postgres_environment,
        suffix,
    )
    current = repository.load(
        task_id=task.task_id,
        owner_id="owner",
        workspace_id="workspace",
    )
    def ready_after_closed_source(payload):
        payload["executionDecisions"][0].update(
            {
                "status": "closed",
                "outcome": "succeeded",
                "closedAt": datetime.now(timezone.utc).isoformat(),
            }
        )
        payload["substageRuntimeStates"][0].update(
            {
                "status": "ready",
                "statusReasonCode": None,
                "activeExecutionDecisionId": None,
            }
        )

    current = _save_responsibility_fixture_state(
        repository,
        current,
        ready_after_closed_source,
    )
    fence = repository.claim_dispatch_continuation(
        lease_owner="continuation-needs-input",
        lease_seconds=30,
        retry_delay_seconds=1,
    )
    assert fence is not None
    selection_request_id = f"continuation:{dispatch.execution_decision_id}"
    blocked = repository.record_needs_input(
        task_id=task.task_id,
        owner_id="owner",
        workspace_id="workspace",
        expected_task_revision=current.revision,
        substage_instance_id=current.substage_runtime_states[0].substage_instance_id,
        reason_code="required_input_missing",
        selection_request_id=selection_request_id,
        selection_command_hash=(
            "sha256:" + hashlib.sha256(b"needs-input-command").hexdigest()
        ),
        blocker_facts={"inputRefs": ["missing_material"]},
    )
    proof = ResearchDispatchService(repository)._user_responsibility_proof(
        fence,
        reason_code="required_input_missing",
        target_instance_id=blocked.substage_runtime_states[0].substage_instance_id,
        selection_request_id=selection_request_id,
    )
    return task, dispatch, fence, proof


def test_formal_needs_input_is_transactionally_proven_in_postgres(
    repository, postgres_environment,
) -> None:
    task, dispatch, fence, proof = _prepare_formal_needs_input_proof(
        repository,
        postgres_environment,
        "user_gate_needs_input",
    )

    assert proof.kind is ContinuationResponsibilityKind.USER_RESPONSIBILITY
    durable = repository.load(
        task_id=task.task_id,
        owner_id="owner",
        workspace_id="workspace",
    )
    assert durable.status.value == "awaiting_confirmation"
    assert durable.substage_runtime_states[0].status.value == "needs_input"
    assert proof.target_revision == durable.substage_runtime_states[0].instance_revision
    assert repository.complete_dispatch_continuation(fence, proof=proof) is True
    with postgres_environment() as connection:
        row = connection.execute(
            "SELECT status FROM openair_dispatch_continuation WHERE dispatch_id = %s",
            (dispatch.dispatch_id,),
        ).fetchone()
    assert row["status"] == "completed"


def test_stale_user_responsibility_proof_is_rejected_transactionally_in_postgres(
    repository, postgres_environment,
) -> None:
    task, dispatch, fence, proof = _prepare_formal_needs_input_proof(
        repository,
        postgres_environment,
        "user_gate_stale_proof",
    )
    current = repository.load(
        task_id=task.task_id,
        owner_id="owner",
        workspace_id="workspace",
    )
    advanced = repository.save(
        current,
        owner_id="owner",
        workspace_id="workspace",
        expected_task_revision=current.revision,
    )
    assert advanced.revision == current.revision + 1

    assert repository.complete_dispatch_continuation(fence, proof=proof) is False
    with postgres_environment() as connection:
        row = connection.execute(
            "SELECT status, completed_at FROM openair_dispatch_continuation WHERE dispatch_id = %s",
            (dispatch.dispatch_id,),
        ).fetchone()
    assert row == {"status": "claimed", "completed_at": None}


@pytest.mark.parametrize(
    "negative_kind",
    ("bare_awaiting_confirmation", "system_blocker", "incomplete_proof"),
)
def test_unproven_user_state_cannot_complete_continuation_in_postgres(
    repository, postgres_environment, negative_kind: str,
) -> None:
    task, dispatch = _persist_completed_dispatch_for_continuation(
        repository,
        postgres_environment,
        f"user_gate_negative_{negative_kind}",
    )

    def mutate(payload):
        runtime = payload["substageRuntimeStates"][0]
        if negative_kind == "bare_awaiting_confirmation":
            payload["status"] = "awaiting_confirmation"
            runtime["statusReasonCode"] = None
        elif negative_kind == "system_blocker":
            runtime.update(
                {
                    "status": "blocked_recoverable",
                    "statusReasonCode": "dispatch_continuation_internal_error_exhausted",
                    "activeExecutionDecisionId": None,
                }
            )

    _save_responsibility_fixture_state(repository, task, mutate)
    fence = repository.claim_dispatch_continuation(
        lease_owner=f"continuation-negative-{negative_kind}",
        lease_seconds=30,
        retry_delay_seconds=1,
    )
    assert fence is not None
    if negative_kind == "incomplete_proof":
        proof = ContinuationResponsibilityProof(
            kind=ContinuationResponsibilityKind.USER_RESPONSIBILITY,
            source_execution_decision_id=fence.execution_decision_id,
            reason_code="stage_confirmation_required",
            target_type="substage_instance",
        )
    else:
        proof = ResearchDispatchService(repository)._user_responsibility_proof(
            fence,
            reason_code="stage_confirmation_required",
        )
        assert proof.kind is ContinuationResponsibilityKind.NO_PROOF
    assert repository.complete_dispatch_continuation(fence, proof=proof) is False
    assert repository.defer_dispatch_continuation(
        fence,
        error_code="dispatch_continuation_no_responsibility_proof",
        retry_delay_seconds=30,
    ) is True
    with postgres_environment() as connection:
        row = connection.execute(
            """SELECT status, completed_at,
                      next_attempt_at > clock_timestamp() AS future_retry
                 FROM openair_dispatch_continuation WHERE dispatch_id = %s""",
            (dispatch.dispatch_id,),
        ).fetchone()
    assert row == {
        "status": "pending",
        "completed_at": None,
        "future_retry": True,
    }


@pytest.mark.parametrize(
    "weak_witness",
    ("selection_ledger", "unrelated_active_dispatch", "unrelated_pending_continuation"),
)
def test_weak_task_scoped_witnesses_cannot_complete_continuation(
    repository, postgres_environment, weak_witness: str,
) -> None:
    task, dispatch = _persist_completed_dispatch_for_continuation(
        repository,
        postgres_environment,
        f"weak_witness_{weak_witness}",
    )
    fence = repository.claim_dispatch_continuation(
        lease_owner=f"continuation-{weak_witness}",
        lease_seconds=30,
        retry_delay_seconds=1,
    )
    assert fence is not None

    if weak_witness == "selection_ledger":
        selection_request_id = f"continuation:{fence.execution_decision_id}"
        with postgres_environment() as connection:
            connection.execute(
                """INSERT INTO openair_execution_selection_request (
                       task_id, owner_id, workspace_id, selection_request_id,
                       command_hash, payload_hash, execution_decision_id
                   ) VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                (
                    task.task_id,
                    "owner",
                    "workspace",
                    selection_request_id,
                    "sha256:" + hashlib.sha256(b"orphan-selection-command").hexdigest(),
                    "sha256:" + hashlib.sha256(b"orphan-selection-payload").hexdigest(),
                    fence.execution_decision_id,
                ),
            )
    elif weak_witness == "unrelated_active_dispatch":
        _insert_unrelated_dispatch_witness(
            postgres_environment,
            task_id=task.task_id,
            suffix=f"active-{dispatch.dispatch_id}",
            status="pending",
        )
    else:
        unrelated_dispatch_id = _insert_unrelated_dispatch_witness(
            postgres_environment,
            task_id=task.task_id,
            suffix=f"continuation-{dispatch.dispatch_id}",
            status="submitted",
        )
        with postgres_environment() as connection:
            connection.execute(
                """INSERT INTO openair_dispatch_continuation (
                       dispatch_id, task_id, execution_decision_id, execution_namespace,
                       status, next_attempt_at
                   ) VALUES (%s, %s, %s, %s, 'pending', clock_timestamp())""",
                (
                    unrelated_dispatch_id,
                    task.task_id,
                    f"decision-unrelated-continuation-{dispatch.dispatch_id}",
                    settings.execution_namespace,
                ),
            )

    # None of these facts is a named responsibility proof.  The source
    # continuation therefore remains owned by this exact fence.
    assert repository.complete_dispatch_continuation(fence) is False
    with postgres_environment() as connection:
        source = connection.execute(
            """SELECT status, completed_at
                 FROM openair_dispatch_continuation WHERE dispatch_id = %s""",
            (dispatch.dispatch_id,),
        ).fetchone()
    assert source == {"status": "claimed", "completed_at": None}


def test_continuation_completed_marker_does_not_project_business_completion(
    repository, postgres_environment,
) -> None:
    task, dispatch = _persist_completed_dispatch_for_continuation(
        repository,
        postgres_environment,
        "continuation_business_state_isolation",
    )
    before = deepcopy(_task_row(postgres_environment, task.task_id))

    # Fixture-only fault injection: a continuation row is an outbox/recovery
    # record.  Even if its local marker says completed, it is not allowed to
    # mutate Task/WorkUnit/Result/Artifact business authority by itself.
    with postgres_environment() as connection:
        connection.execute(
            """UPDATE openair_dispatch_continuation
                  SET status = 'completed', completed_at = clock_timestamp(),
                      updated_at = clock_timestamp()
                WHERE dispatch_id = %s""",
            (dispatch.dispatch_id,),
        )

    after = _task_row(postgres_environment, task.task_id)
    assert after == before
    assert after["status"] != "completed"


def test_stable_continuation_internal_error_converges_without_losing_completed_facts(
    repository, postgres_environment, monkeypatch: pytest.MonkeyPatch,
) -> None:
    task, dispatch = _persist_completed_dispatch_for_continuation(
        repository,
        postgres_environment,
        "continuation_stable_internal_error",
    )
    before = _task_row(postgres_environment, task.task_id)["payload"]
    before_facts = {
        "results": deepcopy(before["results"]),
        "artifacts": deepcopy(before["artifacts"]),
        "workUnits": deepcopy(before["workUnits"]),
        "revision": before["revision"],
    }
    with postgres_environment() as connection:
        provider_submit_facts_before = connection.execute(
            "SELECT count(*) AS count FROM openair_capability_call_lineage WHERE task_id = %s",
            (task.task_id,),
        ).fetchone()["count"]

    monkeypatch.setattr(
        settings,
        "research_dispatch_continuation_internal_error_max_attempts",
        3,
    )
    monkeypatch.setattr(settings, "research_dispatch_retry_base_seconds", 1)
    monkeypatch.setattr(settings, "research_dispatch_retry_max_seconds", 1)
    service = ResearchDispatchService(repository)
    service.process_continuation = lambda _fence: (_ for _ in ()).throw(  # type: ignore[method-assign]
        RuntimeError("stable continuation invariant failure")
    )

    for attempt in range(1, 4):
        assert service.process_next_dispatch_continuation(
            lease_owner=f"continuation-worker-{attempt}",
            lease_seconds=30,
        ) is True
        with postgres_environment() as connection:
            row = connection.execute(
                """SELECT status, attempt_count, last_error_code, completed_at,
                          next_attempt_at > clock_timestamp() AS future_retry
                     FROM openair_dispatch_continuation WHERE dispatch_id = %s""",
                (dispatch.dispatch_id,),
            ).fetchone()
            if attempt < 3:
                assert row["status"] == "pending"
                assert row["last_error_code"] == "dispatch_continuation_internal_error"
                connection.execute(
                    """UPDATE openair_dispatch_continuation
                          SET next_attempt_at = clock_timestamp()
                        WHERE dispatch_id = %s""",
                    (dispatch.dispatch_id,),
                )

    assert row == {
        "status": "pending",
        "attempt_count": 3,
        "last_error_code": "dispatch_continuation_internal_error_exhausted",
        "completed_at": None,
        "future_retry": True,
    }
    assert service.process_next_dispatch_continuation(
        lease_owner="continuation-worker-after-terminal",
        lease_seconds=30,
    ) is False
    with postgres_environment() as connection:
        terminal = connection.execute(
            """SELECT status, attempt_count, last_error_code
                 FROM openair_dispatch_continuation WHERE dispatch_id = %s""",
            (dispatch.dispatch_id,),
        ).fetchone()
        provider_submit_facts_after = connection.execute(
            "SELECT count(*) AS count FROM openair_capability_call_lineage WHERE task_id = %s",
            (task.task_id,),
        ).fetchone()["count"]
    assert terminal == {
        "status": "pending",
        "attempt_count": 3,
        "last_error_code": "dispatch_continuation_internal_error_exhausted",
    }
    after_row = _task_row(postgres_environment, task.task_id)
    after = after_row["payload"]
    assert after["results"] == before_facts["results"]
    assert after["artifacts"] == before_facts["artifacts"]
    assert after["workUnits"] == before_facts["workUnits"]
    assert after["revision"] == before_facts["revision"] + 1
    blocked_work = next(
        row for row in after["workUnits"]
        if row["workUnitId"] == dispatch.work_unit_id
    )
    blocked_runtime = next(
        row for row in after["substageRuntimeStates"]
        if row["substageInstanceId"] == blocked_work["substageInstanceId"]
    )
    assert blocked_runtime["status"] == "blocked_recoverable"
    assert (
        blocked_runtime["statusReasonCode"]
        == "dispatch_continuation_internal_error_exhausted"
    )
    assert after["status"] == "running"
    public_runtime = next(
        row for row in after["progressProjection"]["substageProgress"]
        if row["substageInstanceId"] == blocked_work["substageInstanceId"]
    )
    assert public_runtime["status"] == "blocked_recoverable"
    assert (
        public_runtime["statusReasonCode"]
        == "dispatch_continuation_internal_error_exhausted"
    )
    assert provider_submit_facts_after == provider_submit_facts_before == 1


def test_continuation_recovery_owner_and_canonical_blocker_are_one_transaction(
    repository, postgres_environment, monkeypatch: pytest.MonkeyPatch,
) -> None:
    task, dispatch = _persist_completed_dispatch_for_continuation(
        repository,
        postgres_environment,
        "continuation_terminal_blocker_atomicity",
    )
    before = _task_row(postgres_environment, task.task_id)
    fence = repository.claim_dispatch_continuation(
        lease_owner="continuation-atomicity",
        lease_seconds=30,
        retry_delay_seconds=1,
    )
    assert fence is not None
    original_write = repository._write_locked_task

    def injected_failure(*_args, **_kwargs):
        raise RuntimeError("injected canonical blocker write failure")

    monkeypatch.setattr(repository, "_write_locked_task", injected_failure)
    with pytest.raises(RuntimeError, match="injected canonical blocker write failure"):
        repository.fail_dispatch_continuation(
            fence,
            error_code="dispatch_continuation_internal_error_exhausted",
            retry_delay_seconds=60,
        )

    with postgres_environment() as connection:
        rolled_back = connection.execute(
            """SELECT status, attempt_count, last_error_code, completed_at
                 FROM openair_dispatch_continuation WHERE dispatch_id = %s""",
            (dispatch.dispatch_id,),
        ).fetchone()
    assert rolled_back == {
        "status": "claimed",
        "attempt_count": 1,
        "last_error_code": None,
        "completed_at": None,
    }
    assert _task_row(postgres_environment, task.task_id) == before

    monkeypatch.setattr(repository, "_write_locked_task", original_write)
    assert repository.fail_dispatch_continuation(
        fence,
        error_code="dispatch_continuation_internal_error_exhausted",
        retry_delay_seconds=60,
    ) is True
    with postgres_environment() as connection:
        recovery_owner = connection.execute(
            """SELECT status, completed_at,
                      next_attempt_at > clock_timestamp() AS future_retry
                 FROM openair_dispatch_continuation WHERE dispatch_id = %s""",
            (dispatch.dispatch_id,),
        ).fetchone()
    assert recovery_owner == {
        "status": "pending",
        "completed_at": None,
        "future_retry": True,
    }
    after = _task_row(postgres_environment, task.task_id)
    blocked_work = next(
        row for row in after["payload"]["workUnits"]
        if row["workUnitId"] == dispatch.work_unit_id
    )
    blocked_runtime = next(
        row for row in after["payload"]["substageRuntimeStates"]
        if row["substageInstanceId"] == blocked_work["substageInstanceId"]
    )
    assert blocked_runtime["status"] == "blocked_recoverable"
    assert (
        blocked_runtime["statusReasonCode"]
        == "dispatch_continuation_internal_error_exhausted"
    )
    assert after["revision"] == before["revision"] + 1
    assert repository.fail_dispatch_continuation(
        fence,
        error_code="dispatch_continuation_internal_error_exhausted",
        retry_delay_seconds=60,
    ) is False
    assert _task_row(postgres_environment, task.task_id) == after


def test_retryable_continuation_error_defers_then_retains_owner_without_successor(
    repository, postgres_environment,
) -> None:
    _task, dispatch = _persist_completed_dispatch_for_continuation(
        repository,
        postgres_environment,
        "continuation_retryable_then_complete",
    )
    calls = 0
    service = ResearchDispatchService(repository)

    def process_continuation(_fence):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise DispatchContinuationRetryableError("temporary continuation conflict")
        return ContinuationResponsibilityProof.no_proof(
            source_execution_decision_id=_fence.execution_decision_id,
            reason_code="dispatch_continuation_not_completed",
        )

    service.process_continuation = process_continuation  # type: ignore[method-assign]
    assert service.process_next_dispatch_continuation(
        lease_owner="continuation-transient-1",
        lease_seconds=30,
    ) is True
    with postgres_environment() as connection:
        first = connection.execute(
            """SELECT status, attempt_count, last_error_code
                 FROM openair_dispatch_continuation WHERE dispatch_id = %s""",
            (dispatch.dispatch_id,),
        ).fetchone()
        connection.execute(
            """UPDATE openair_dispatch_continuation SET next_attempt_at = clock_timestamp()
                 WHERE dispatch_id = %s""",
            (dispatch.dispatch_id,),
        )
    assert first == {
        "status": "pending",
        "attempt_count": 1,
        "last_error_code": "temporary continuation conflict",
    }
    assert service.process_next_dispatch_continuation(
        lease_owner="continuation-transient-2",
        lease_seconds=30,
    ) is True
    with postgres_environment() as connection:
        pending = connection.execute(
            """SELECT status, attempt_count, last_error_code,
                      next_attempt_at > clock_timestamp() AS future_retry
                 FROM openair_dispatch_continuation WHERE dispatch_id = %s""",
            (dispatch.dispatch_id,),
        ).fetchone()
    assert pending == {
        "status": "pending",
        "attempt_count": 2,
        "last_error_code": "dispatch_continuation_not_completed",
        "future_retry": True,
    }
    assert calls == 2


def test_completed_dispatch_persists_one_formal_result_on_replay(
    repository, postgres_environment
) -> None:
    import hashlib

    task, dispatch = _seed(repository, postgres_environment, "formal_result_replay")
    fence = _claim(repository, task.task_id, "worker-formal-result")
    assert fence is not None
    authorized = repository.authorize_dispatch_submission(
        fence,
        owner_id="owner",
        workspace_id="workspace",
        expected_task_revision=1,
        submission_mode="single_attempt_no_replay",
    )
    assert authorized is not None
    authorized = _start(repository, authorized)
    content = '{"reasoning_summary":"One durable execution result."}'
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    result_storage_ref = f"durable/research-dispatch/{dispatch.dispatch_id}/result.json"
    assert repository.reserve_dispatch_result_storage(
        authorized,
        storage_type="local",
        storage_scope=authorized.task_id,
        storage_ref=result_storage_ref,
    ) is True
    kwargs = dict(
        owner_id="owner",
        workspace_id="workspace",
        result=DispatchSubmissionCompleted(
            title="Formal execution result",
            mime_type="application/json",
            content=content,
            provider_id="openai_primary",
            adapter="huiyan_chat_completions",
        ),
        artifact_contract="test_artifact_v1",
        artifact_schema_digest="a" * 64,
        storage_type="local",
        storage_scope=authorized.task_id,
        storage_ref=result_storage_ref,
        content_sha256=digest,
        artifact_storage_type="local",
        artifact_storage_scope=authorized.task_id,
        artifact_storage_ref=(
            f"durable/research-dispatch/{dispatch.dispatch_id}/artifact.json"
        ),
    )

    assert repository.record_dispatch_completed_result(authorized, **kwargs) is True
    first_payload = _task_row(postgres_environment, task.task_id)["payload"]
    assert len(first_payload["results"]) == 1
    formal_result = first_payload["results"][0]
    artifact = first_payload["artifacts"][0]
    assert formal_result["resultId"] == f"result:{dispatch.dispatch_id}"
    assert formal_result["workUnitId"] == authorized.work_unit_id
    assert formal_result["capabilityCallId"] == dispatch_capability_call_id(authorized)
    assert formal_result["storageRef"] == result_storage_ref
    assert formal_result["contentSha256"] == digest
    assert formal_result["checkStatus"] == "passed"
    assert formal_result["artifactRefs"] == [artifact["artifactId"]]
    assert artifact["sourceResultId"] == formal_result["resultId"]
    assert formal_result["resultId"] != artifact["artifactId"]

    assert repository.record_dispatch_completed_result(authorized, **kwargs) is False
    replay_payload = _task_row(postgres_environment, task.task_id)["payload"]
    assert replay_payload["results"] == first_payload["results"]
    assert len(replay_payload["results"]) == 1
    assert replay_payload["revision"] == first_payload["revision"]


def test_pre_stored_dispatch_result_digest_still_materializes_canonical_artifact_and_lineage(
    repository, postgres_environment
) -> None:
    task, dispatch = _seed(repository, postgres_environment, "pre_stored_result_replay")
    fence = _claim(repository, task.task_id, "worker-pre-stored-result")
    assert fence is not None
    authorized = repository.authorize_dispatch_submission(
        fence,
        owner_id="owner",
        workspace_id="workspace",
        expected_task_revision=1,
        submission_mode="single_attempt_no_replay",
    )
    assert authorized is not None
    authorized = _start(repository, authorized)
    content = '{"reasoning_summary":"Crash replay must materialize the canonical result."}'
    content_sha256 = hashlib.sha256(content.encode("utf-8")).hexdigest()
    result_storage_ref = f"durable/research-dispatch/{dispatch.dispatch_id}/result.json"
    artifact_storage_ref = f"durable/research-dispatch/{dispatch.dispatch_id}/artifact.json"
    assert repository.reserve_dispatch_result_storage(
        authorized,
        storage_type="local",
        storage_scope=authorized.task_id,
        storage_ref=result_storage_ref,
    ) is True
    with _namespaced_dispatch_mutation_connection(postgres_environment) as connection:
        connection.execute(
            """UPDATE openair_dispatch
                  SET result_content_sha256 = %s
                WHERE dispatch_id = %s""",
            (content_sha256, dispatch.dispatch_id),
        )

    kwargs = dict(
        owner_id="owner",
        workspace_id="workspace",
        result=DispatchSubmissionCompleted(
            title="Crash replay result",
            mime_type="text/plain",
            content=content,
            provider_id="openai_primary",
            adapter="huiyan_chat_completions",
        ),
        artifact_contract="test_artifact_v1",
        artifact_schema_digest="a" * 64,
        storage_type="local",
        storage_scope=authorized.task_id,
        storage_ref=result_storage_ref,
        content_sha256=content_sha256,
        artifact_storage_type="local",
        artifact_storage_scope=authorized.task_id,
        artifact_storage_ref=artifact_storage_ref,
    )
    assert repository.record_dispatch_completed_result(authorized, **kwargs) is True
    assert repository.record_dispatch_completed_result(authorized, **kwargs) is False

    with postgres_environment() as connection:
        assert connection.execute(
            "SELECT count(*) AS count FROM openair_artifacts WHERE source_task_id = %s",
            (task.task_id,),
        ).fetchone()["count"] == 1
        assert connection.execute(
            "SELECT count(*) AS count FROM openair_artifact_lineage WHERE task_id = %s",
            (task.task_id,),
        ).fetchone()["count"] == 1
        assert connection.execute(
            "SELECT count(*) AS count FROM openair_capability_call_lineage WHERE task_id = %s",
            (task.task_id,),
        ).fetchone()["count"] == 1
        assert connection.execute(
            "SELECT count(*) AS count FROM openair_work_unit_lineage WHERE task_id = %s",
            (task.task_id,),
        ).fetchone()["count"] == 1
    payload = _task_row(postgres_environment, task.task_id)["payload"]
    assert len(_task_message_rows(postgres_environment, task.task_id)) == 1
    assert len(payload["results"]) == 1
    assert payload["results"][0]["resultId"] == f"result:{dispatch.dispatch_id}"
    assert payload["capabilityCalls"][0]["capabilityCallId"] == dispatch_capability_call_id(authorized)
    assert payload["workUnits"][0]["status"] == "completed"


def test_direct_skill_markdown_completion_persists_markdown_artifact_format(
    repository, postgres_environment
) -> None:
    import base64
    import hashlib

    from app.execution import research_dispatch_skill_provider as skill_provider_module

    task, dispatch = _seed(repository, postgres_environment, "direct_skill_markdown_format")
    fence = _claim(repository, task.task_id, "worker-skill-markdown")
    assert fence is not None
    authorized = repository.authorize_dispatch_submission(
        fence,
        owner_id="owner",
        workspace_id="workspace",
        expected_task_revision=1,
        submission_mode="single_attempt_no_replay",
    )
    assert authorized is not None
    authorized = _start(repository, authorized)

    content = "# Validated direct Skill artifact\n\nAuthoritative Markdown body."
    raw = content.encode("utf-8")
    frozen = skill_provider_module.FrozenDirectSkillDispatch(
        skill_id="evidence_synthesis_skill",
        definition_version=1,
        artifactContract="direct_skill:evidence_synthesis_skill:v1",
        artifactSchemaDigest="sha256:" + "2" * 64,
        artifactMimeType="text/markdown",
        logicalProviderId=None,
        modelProfile=None,
        resolvedExecutionPolicy=None,
        runtimeKind="structured_runtime",
        requiresExternalAction=False,
        adapterId="durable_skill_runtime",
        evidenceContractVersion="external-action-evidence/v1",
        orchestrationPlan={"assignmentRole": "primary"},
    )
    completed = skill_provider_module._completed_submission(
        {
            "state": "succeeded",
            "relatedSkillId": frozen.skill_id,
            "artifactPayload": {
                "contentBase64": base64.b64encode(raw).decode("ascii"),
                "contentType": "text/markdown",
                "presentation": {
                    "title": "Validated Markdown",
                    "mimeType": "text/markdown",
                    "contentSha256": hashlib.sha256(raw).hexdigest(),
                    "contentSize": len(raw),
                },
            },
        },
        frozen=frozen,
    )
    assert completed.mime_type == frozen.artifactMimeType
    result_storage_ref = f"durable/research-dispatch/{dispatch.dispatch_id}/result.json"
    assert repository.reserve_dispatch_result_storage(
        authorized,
        storage_type="local",
        storage_scope=authorized.task_id,
        storage_ref=result_storage_ref,
    ) is True

    assert repository.record_dispatch_completed_result(
        authorized,
        owner_id="owner",
        workspace_id="workspace",
        result=completed,
        artifact_contract=frozen.artifactContract,
        artifact_schema_digest=frozen.artifactSchemaDigest,
        storage_type="local",
        storage_scope=authorized.task_id,
        storage_ref=result_storage_ref,
        content_sha256=hashlib.sha256(raw).hexdigest(),
        artifact_storage_type="local",
        artifact_storage_scope=authorized.task_id,
        artifact_storage_ref=f"durable/research-dispatch/{dispatch.dispatch_id}/artifact.md",
    ) is True

    task_row = _task_row(postgres_environment, task.task_id)
    assert len(task_row["payload"]["artifacts"]) == 1
    artifact = task_row["payload"]["artifacts"][0]
    assert artifact["artifactType"] == "evidence_summary"
    with postgres_environment() as connection:
        catalog = connection.execute(
            "SELECT format, mime_type, content_sha256, metadata FROM openair_artifacts WHERE artifact_id = %s",
            (artifact["artifactId"],),
        ).fetchone()
    assert catalog["format"] == "markdown"
    assert catalog["mime_type"] == "text/markdown"
    assert catalog["content_sha256"] == hashlib.sha256(raw).hexdigest()
    initial_id = f"v_initial_{artifact['artifactId']}"
    assert catalog["metadata"]["currentVersionId"] == initial_id
    assert len(catalog["metadata"]["versions"]) == 1
    initial = catalog["metadata"]["versions"][0]
    assert initial["versionId"] == initial_id
    assert initial["versionNumber"] == 1
    assert initial["source"] == "initial"
    assert initial["contentSha256"] == hashlib.sha256(raw).hexdigest()
    assert initial["contentSize"] == len(raw)
    assert initial["storageType"] == "local"
    assert initial["path"] == f"durable/research-dispatch/{dispatch.dispatch_id}/artifact.md"


def test_agent_structured_text_plain_completion_preserves_json_artifact_format(
    repository, postgres_environment
) -> None:
    import hashlib

    baseline, candidate, dispatch = _transaction_fixture("agent_structured_json_format")
    payload = candidate.model_dump(mode="json", by_alias=True)
    decision = payload["executionDecisions"][0]
    assignment = decision["assignments"][0]
    agent_ref = {
        "executorType": "agent",
        "executorId": "structured_research_agent",
        "definitionVersion": 1,
    }
    actor_ref = {
        "actorType": "agent",
        "actorId": "structured_research_agent",
        "definitionVersion": 1,
    }
    decision["strategy"] = "specialist_agent"
    assignment["executorRef"] = agent_ref
    assignment["responsibleActorRef"] = actor_ref
    work = payload["workUnits"][0]
    work["executorRef"] = agent_ref
    work["responsibleActorRef"] = actor_ref
    candidate = ResearchTaskV12.model_validate(payload)

    _insert_task(postgres_environment, baseline)
    task = repository.save(
        candidate,
        owner_id="owner",
        workspace_id="workspace",
        expected_task_revision=0,
        pending_dispatches=[dispatch],
        automatic_dispatch=True,
    )
    fence = _claim(repository, task.task_id, "worker-agent-json")
    assert fence is not None
    authorized = repository.authorize_dispatch_submission(
        fence,
        owner_id="owner",
        workspace_id="workspace",
        expected_task_revision=1,
        submission_mode="single_attempt_no_replay",
    )
    assert authorized is not None
    authorized = _start(repository, authorized)
    content = '{"reasoning_summary":"Structured Agent result remains canonical JSON."}'
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    result_storage_ref = f"durable/research-dispatch/{dispatch.dispatch_id}/result.json"
    assert repository.reserve_dispatch_result_storage(
        authorized,
        storage_type="local",
        storage_scope=authorized.task_id,
        storage_ref=result_storage_ref,
    ) is True

    assert repository.record_dispatch_completed_result(
        authorized,
        owner_id="owner",
        workspace_id="workspace",
        result=DispatchSubmissionCompleted(
            title="Structured Agent result",
            mime_type="text/plain",
            content=content,
            provider_id="openai_primary",
            adapter="huiyan_chat_completions",
        ),
        artifact_contract="structured_agent_artifact_v1",
        artifact_schema_digest="a" * 64,
        storage_type="local",
        storage_scope=authorized.task_id,
        storage_ref=result_storage_ref,
        content_sha256=digest,
        artifact_storage_type="local",
        artifact_storage_scope=authorized.task_id,
        artifact_storage_ref=f"durable/research-dispatch/{dispatch.dispatch_id}/artifact.json",
    ) is True

    task_row = _task_row(postgres_environment, task.task_id)
    assert len(task_row["payload"]["artifacts"]) == 1
    artifact = task_row["payload"]["artifacts"][0]
    with postgres_environment() as connection:
        catalog = connection.execute(
            "SELECT format, mime_type, content_sha256 FROM openair_artifacts WHERE artifact_id = %s",
            (artifact["artifactId"],),
        ).fetchone()
    assert catalog["format"] == "json"
    assert catalog["mime_type"] == "text/plain"
    assert catalog["content_sha256"] == digest


def test_postgres_agent_messages_get_reads_authoritative_payload_and_scopes(monkeypatch) -> None:
    from app.routes import agent as agent_routes

    message_a = {
        "id": "msg_dispatch_a", "role": "assistant", "text": "Authoritative result A.",
        "at": "2026-08-03T00:00:00+00:00", "runtime": None,
        "messageType": "research_dispatch_result", "authorLabel": "agent_a",
        "capabilityCallId": "call_a", "artifactRefs": ["artifact_a"],
        "confirmationRequests": [], "userFacingNarrative": None,
    }
    tasks = {
        "task-a": {"taskId": "task-a", "ownerId": "owner-a", "workspaceId": "workspace-a", "createdBy": "owner-a", "agentMessages": [message_a], "capabilityCalls": []},
        "task-b": {"taskId": "task-b", "ownerId": "owner-b", "workspaceId": "workspace-b", "createdBy": "owner-b", "agentMessages": [{**message_a, "id": "msg_dispatch_b", "text": "Other task result."}], "capabilityCalls": []},
    }

    class Repository:
        def get_task(self, task_id: str):
            return tasks.get(task_id)

    class MessageRepository:
        def list_messages(
            self,
            *,
            task_id: str,
            owner_id: str,
            workspace_id: str,
            before_seq=None,
            limit: int = 50,
        ):
            assert owner_id == "owner-a"
            assert workspace_id == "workspace-a"
            assert before_seq is None
            assert limit == 50
            if task_id == "task-a":
                return ([{**message_a, "seq": 1}], False, 1)
            return ([], False, 0)

    monkeypatch.setattr(settings, "persistence_backend", "postgres")
    monkeypatch.setattr(agent_routes, "TaskRepository", Repository)
    monkeypatch.setattr(agent_routes, "TaskMessageRepository", MessageRepository)
    monkeypatch.setattr(agent_routes.store, "get_task", lambda _task_id: (_ for _ in ()).throw(AssertionError("TaskStore must not be read")))
    identity = {"ownerId": "owner-a", "workspaceId": "workspace-a", "createdBy": "owner-a"}
    response = asyncio.run(agent_routes.list_agent_messages("task-a", identity=identity))
    assert [item.id for item in response.messages] == ["msg_dispatch_a"]
    assert response.messages[0].text == "Authoritative result A."
    with pytest.raises(HTTPException):
        asyncio.run(agent_routes.list_agent_messages("task-b", identity=identity))


def test_provider_result_message_text_rejects_raw_or_internal_payloads() -> None:
    assert _provider_result_message_text(
        '{"reasoning_summary":"A source-bounded research conclusion."}'
    ) == "A source-bounded research conclusion."
    assert _provider_result_message_text(
        '{"result":{"status":"succeeded"},"providerId":"openai_primary"}'
    ) is None
    assert _provider_result_message_text(
        '{"providerId":"openai_primary","adapter":"huiyan_chat_completions"}'
    ) is None
    assert _provider_result_message_text('{"verdict":"external_result_unknown"}') is None
    assert _provider_result_message_text(
        '{"status":"failed","reasoning_summary":"Do not publish this."}'
    ) is None
def test_canonical_dispatch_applies_pending_mode_at_completed_result_boundary(
    repository, postgres_environment
) -> None:
    import hashlib

    task, dispatch = _seed(repository, postgres_environment, "policy_boundary")
    authorized = _authorize(repository, task.task_id)
    started = _start(repository, authorized)

    durable = DurableExecutionRepository()
    durable._connect = postgres_environment  # type: ignore[method-assign]
    policy_repository = CollaborationRepository(durable)
    pending = policy_repository.set_task_execution_policy(
        task_id=task.task_id,
        owner_id="owner",
        workspace_id="workspace",
        mode="automatic_research",
        changed_by="owner",
    )

    assert pending["currentMode"] == "stage_confirmation"
    assert pending["pendingMode"] == "automatic_research"
    pending_task = _task_row(postgres_environment, task.task_id)
    # revision 1 seed → 2 action-started → 3 pending policy
    assert pending_task["revision"] == pending_task["payload"]["revision"] == 3
    assert pending_task["payload"]["executionMode"] == "stage_confirmation"
    assert pending_task["payload"]["pendingExecutionMode"] == "automatic_research"
    assert (
        pending_task["payload"]["executionPolicyVersion"]
        == "execution-policy.v1.stage_confirmation"
    )

    repeated = policy_repository.set_task_execution_policy(
        task_id=task.task_id,
        owner_id="owner",
        workspace_id="workspace",
        mode="automatic_research",
        changed_by="owner",
    )
    assert repeated["pendingMode"] == "automatic_research"
    assert _task_row(postgres_environment, task.task_id)["revision"] == 3

    content = '{"result":"validated"}'
    assert _record_completed_and_finalize(
        repository,
        started,
        owner_id="owner",
        workspace_id="workspace",
        result=DispatchSubmissionCompleted(
            title="Validated result",
            mime_type="text/plain",
            content=content,
            provider_id="openai_primary",
            adapter="huiyan_chat_completions",
        ),
        artifact_contract="test_artifact_v1",
        artifact_schema_digest="a" * 64,
        storage_type="local",
        storage_ref=f"durable/research-dispatch/{dispatch.dispatch_id}/result.json",
        content_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        artifact_storage_type="local",
        artifact_storage_ref=(
            f"durable/research-dispatch/{dispatch.dispatch_id}/artifact.json"
        ),
    ) is True

    applied = policy_repository.get_task_execution_policy(
        task_id=task.task_id,
        owner_id="owner",
        workspace_id="workspace",
    )
    assert applied["currentMode"] == "automatic_research"
    assert applied["pendingMode"] is None
    completed = _task_row(postgres_environment, task.task_id)
    # record_completed applies pending mode (3→4); prepare_execution_continuation
    # then reconciles lifecycle and may persist another revision (4→5).
    assert completed["revision"] == completed["payload"]["revision"] == 5
    assert completed["payload"]["executionMode"] == "automatic_research"
    assert completed["payload"]["pendingExecutionMode"] is None
    assert (
        completed["payload"]["executionPolicyVersion"]
        == "execution-policy.v2.automatic_research"
    )


def test_usable_review_closes_decision_and_updates_scientific_state(
    repository,
    postgres_environment,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import hashlib
    from datetime import datetime, timedelta

    from app.modules import research_orchestration_repository as repository_module

    monkeypatch.setattr(settings, "artifact_storage", "local")
    monkeypatch.setattr(settings, "workspace_root", tmp_path)
    task, producer_dispatch, reviewer_assignment_id = _seed_reviewed_dispatch(
        repository, postgres_environment, "reviewed_result"
    )
    producer_fence = _claim(repository, task.task_id, "worker-producer")
    assert producer_fence is not None
    producer = repository.authorize_dispatch_submission(
        producer_fence,
        owner_id="owner",
        workspace_id="workspace",
        expected_task_revision=1,
        submission_mode="single_attempt_no_replay",
    )
    assert producer is not None
    producer = _start(repository, producer)
    producer_content = '{"result":"validated"}'
    producer_digest = hashlib.sha256(producer_content.encode("utf-8")).hexdigest()
    artifact_storage_ref = (
        f"durable/research-dispatch/{producer_dispatch.dispatch_id}/artifact.json"
    )
    stored = artifact_write_service.store_durable_bytes(
        task.task_id,
        artifact_storage_ref,
        producer_content.encode("utf-8"),
        "application/json",
    )
    pvd_context = {"contract": "pvd-experimental-system-context/v1"}
    pvd_projection = {
        "contract_version": 2,
        "materializer_ref": "pvd:experimental-system-context:v1",
        "facts": [
            {
                "fact_key": "experimental_system_context",
                "source_field": "experimental_system_context",
            }
        ],
    }
    real_datetime = repository_module.datetime

    class ApiClockAhead(datetime):
        @classmethod
        def now(cls, tz=None):
            return real_datetime.now(tz) + timedelta(minutes=1)

    with monkeypatch.context() as clock_patch:
        clock_patch.setattr(repository_module, "datetime", ApiClockAhead)
        assert _record_completed_and_finalize(
            repository,
            producer,
            owner_id="owner",
            workspace_id="workspace",
            result=DispatchSubmissionCompleted(
                title="Validated result",
                mime_type="text/plain",
                content=producer_content,
                provider_id="openai_primary",
                adapter="huiyan_chat_completions",
            ),
            artifact_contract="test_artifact_v1",
            artifact_schema_digest="a" * 64,
            storage_type="local",
            storage_ref=f"durable/research-dispatch/{producer_dispatch.dispatch_id}/result.json",
            content_sha256=producer_digest,
            artifact_storage_type=stored.storage_type,
            artifact_storage_ref=stored.storage_ref,
            scientific_state_fact_projections={
                "experimental_system_context": pvd_context
            },
            scientific_state_projection_declaration=pvd_projection,
        ) is True

    after_producer = _task_row(postgres_environment, task.task_id)
    _release_ready_reviewer_assignment(
        repository,
        task_id=task.task_id,
        expected_task_revision=after_producer["revision"],
        execution_decision_id=producer.execution_decision_id,
        monkeypatch=monkeypatch,
    )
    after_producer = _task_row(postgres_environment, task.task_id)
    reviewer_work = next(
        row for row in after_producer["payload"]["workUnits"]
        if row["assignmentId"] == reviewer_assignment_id
    )
    assert reviewer_work["status"] == "queued"
    producer_artifact_id = after_producer["payload"]["artifacts"][0]["artifactId"]
    assert after_producer["payload"]["artifacts"][0]["artifactState"] == "needs_review"
    producer_result = next(
        row for row in after_producer["payload"]["results"]
        if row["workUnitId"] != reviewer_work["workUnitId"]
    )
    assert producer_result["checkStatus"] == "pending"
    assert producer_result["scientificStateVersions"] == []
    assert after_producer["payload"]["scientificStates"] == []
    assert reviewer_work["inputArtifactRefs"] == [producer_artifact_id]
    with postgres_environment() as connection:
        catalog_before_review = connection.execute(
            "SELECT metadata FROM openair_artifacts WHERE artifact_id = %s",
            (producer_artifact_id,),
        ).fetchone()
        pending_reviewer = connection.execute(
            """SELECT *, clock_timestamp() AS database_now
                 FROM openair_dispatch
                WHERE task_id = %s AND work_unit_id = %s""",
            (task.task_id, reviewer_work["workUnitId"]),
        ).fetchone()
    assert catalog_before_review["metadata"]["artifactState"] == "needs_review"
    assert pending_reviewer["status"] == "pending"
    assert pending_reviewer["created_at"] <= pending_reviewer["database_now"]
    assert pending_reviewer["updated_at"] >= pending_reviewer["created_at"]
    reviewer_fence = _claim(repository, task.task_id, "worker-reviewer")
    assert reviewer_fence is not None
    assert reviewer_fence.work_unit_id == reviewer_work["workUnitId"]
    reviewer_dispatch = _dispatch_row(postgres_environment, reviewer_fence.dispatch_id)
    assert reviewer_dispatch["status"] == "claimed"
    assert reviewer_dispatch["attempt_count"] == 1
    assert reviewer_dispatch["lease_owner"] == "worker-reviewer"
    assert reviewer_dispatch["lease_expires_at"] is not None
    assert reviewer_dispatch["authorization_task_revision"] is None
    assert reviewer_dispatch["external_action_started_at"] is None
    assert reviewer_dispatch["updated_at"] >= reviewer_dispatch["created_at"]
    reviewer = repository.authorize_dispatch_submission(
        reviewer_fence,
        owner_id="owner",
        workspace_id="workspace",
        expected_task_revision=after_producer["revision"],
        submission_mode="single_attempt_no_replay",
    )
    assert reviewer is not None
    reviewer = _start(repository, reviewer)
    review_content = json.dumps(
        {
            "verdict": "usable",
            "basis": "The producer artifact satisfies the frozen contract.",
            "issues": [],
            "missingInputs": [],
        },
        separators=(",", ":"),
    )
    assert _record_completed_and_finalize(
        repository,
        reviewer,
        owner_id="owner",
        workspace_id="workspace",
        result=DispatchSubmissionCompleted(
            title="Independent review",
            mime_type="text/plain",
            content=review_content,
            provider_id="openai_primary",
            adapter="huiyan_chat_completions",
        ),
        artifact_contract="independent_result_review_v1",
        artifact_schema_digest="b" * 64,
        storage_type="local",
        storage_ref=f"durable/research-dispatch/{reviewer.dispatch_id}/result.json",
        content_sha256=hashlib.sha256(review_content.encode("utf-8")).hexdigest(),
    ) is True

    completed = _task_row(postgres_environment, task.task_id)
    decision = completed["payload"]["executionDecisions"][0]
    assert (decision["status"], decision["outcome"]) == ("closed", "succeeded")
    assert completed["status"] == completed["payload"]["status"] == "completed"
    assert completed["payload"]["progressProjection"]["taskProgress"]["status"] == "completed"
    assert len(completed["payload"]["artifacts"]) == 1
    assert completed["payload"]["artifacts"][0]["artifactState"] == "usable"
    assert len(completed["payload"]["results"]) == 2
    checked_producer_result = next(
        row for row in completed["payload"]["results"]
        if row["resultId"] == producer_result["resultId"]
    )
    reviewer_result = next(
        row for row in completed["payload"]["results"]
        if row["resultId"] != producer_result["resultId"]
    )
    assert checked_producer_result["checkStatus"] == "passed"
    assert checked_producer_result["checkResult"]["reviewResultId"] == reviewer_result[
        "resultId"
    ]
    assert checked_producer_result["scientificStateVersions"] == [1]
    assert reviewer_result["workUnitId"] == reviewer_work["workUnitId"]
    assert reviewer_result["checkStatus"] == "passed"
    assert completed["payload"]["scientificStates"][-1]["sourceRefs"] == [
        producer_artifact_id
    ]
    assert len(completed["payload"]["capabilityCalls"]) == 2
    interactions = completed["payload"]["agentInteractionRecords"]
    assert len(interactions) == 2
    review_record = next(
        row for row in interactions if row["interactionType"] == "review_submitted"
    )
    acceptance_record = next(
        row for row in interactions if row["interactionType"] == "acceptance_recorded"
    )
    assert review_record["inputArtifactRefs"] == [producer_artifact_id]
    assert review_record["verdict"] == {
        "verdictType": "review",
        "decision": "approved",
        "rationaleCode": "review_usable",
    }
    assert acceptance_record["inputArtifactRefs"] == [producer_artifact_id]
    assert acceptance_record["sourceWorkUnitId"] == reviewer_work["workUnitId"]
    assert acceptance_record["targetWorkUnitId"] == producer_result["workUnitId"]
    assert acceptance_record["verdict"] == {
        "verdictType": "acceptance",
        "decision": "approved",
        "rationaleCode": "review_usable",
    }
    with postgres_environment() as connection:
        catalog_after_review = connection.execute(
            "SELECT metadata FROM openair_artifacts WHERE artifact_id = %s",
            (producer_artifact_id,),
        ).fetchone()
        assert catalog_after_review["metadata"]["artifactState"] == "usable"
        assert connection.execute(
            "SELECT count(*) AS count FROM openair_artifacts WHERE source_task_id = %s",
            (task.task_id,),
        ).fetchone()["count"] == 1
        assert connection.execute(
            "SELECT count(*) AS count FROM openair_artifact_lineage WHERE task_id = %s",
            (task.task_id,),
        ).fetchone()["count"] == 1


def test_insufficient_review_preserves_authority_and_restarts_same_instance(
    repository,
    postgres_environment,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import hashlib

    monkeypatch.setattr(settings, "artifact_storage", "local")
    monkeypatch.setattr(settings, "workspace_root", tmp_path)
    task, producer_dispatch, reviewer_assignment_id = _seed_reviewed_dispatch(
        repository,
        postgres_environment,
        "review_insufficient",
        execution_mode="automatic_research",
    )
    producer_fence = _claim(repository, task.task_id, "worker-producer")
    assert producer_fence is not None
    producer = repository.authorize_dispatch_submission(
        producer_fence,
        owner_id="owner",
        workspace_id="workspace",
        expected_task_revision=1,
        submission_mode="single_attempt_no_replay",
    )
    assert producer is not None
    producer = _start(repository, producer)
    producer_content = '{"result":"validated"}'
    producer_digest = hashlib.sha256(producer_content.encode("utf-8")).hexdigest()
    artifact_storage_ref = (
        f"durable/research-dispatch/{producer_dispatch.dispatch_id}/artifact.json"
    )
    stored = artifact_write_service.store_durable_bytes(
        task.task_id,
        artifact_storage_ref,
        producer_content.encode("utf-8"),
        "application/json",
    )
    assert _record_completed_and_finalize(
        repository,
        producer,
        owner_id="owner",
        workspace_id="workspace",
        result=DispatchSubmissionCompleted(
            title="Validated result",
            mime_type="text/plain",
            content=producer_content,
            provider_id="openai_primary",
            adapter="huiyan_chat_completions",
        ),
        artifact_contract="test_artifact_v1",
        artifact_schema_digest="a" * 64,
        storage_type="local",
        storage_ref=f"durable/research-dispatch/{producer_dispatch.dispatch_id}/result.json",
        content_sha256=producer_digest,
        artifact_storage_type=stored.storage_type,
        artifact_storage_ref=stored.storage_ref,
    ) is True

    after_producer = _task_row(postgres_environment, task.task_id)
    _release_ready_reviewer_assignment(
        repository,
        task_id=task.task_id,
        expected_task_revision=after_producer["revision"],
        execution_decision_id=producer.execution_decision_id,
        monkeypatch=monkeypatch,
    )
    after_producer = _task_row(postgres_environment, task.task_id)
    reviewer_work = next(
        row
        for row in after_producer["payload"]["workUnits"]
        if row["assignmentId"] == reviewer_assignment_id
    )
    producer_artifact_id = after_producer["payload"]["artifacts"][0]["artifactId"]
    assert reviewer_work["inputArtifactRefs"] == [producer_artifact_id]
    reviewer_fence = _claim(repository, task.task_id, "worker-reviewer")
    assert reviewer_fence is not None
    assert reviewer_fence.work_unit_id == reviewer_work["workUnitId"]
    reviewer = repository.authorize_dispatch_submission(
        reviewer_fence,
        owner_id="owner",
        workspace_id="workspace",
        expected_task_revision=after_producer["revision"],
        submission_mode="single_attempt_no_replay",
    )
    assert reviewer is not None
    reviewer = _start(repository, reviewer)
    review_content = json.dumps(
        {
            "verdict": "insufficient_materials",
            "basis": "The supplied artifact does not contain enough evidence for approval.",
            "issues": ["Evidence is incomplete."],
            "missingInputs": ["A verifiable source."],
        },
        separators=(",", ":"),
    )
    reviewer_storage_ref = (
        f"durable/research-dispatch/{reviewer.dispatch_id}/result.json"
    )
    assert repository.reserve_dispatch_result_storage(
        reviewer,
        storage_type="local",
        storage_scope=reviewer.task_id,
        storage_ref=reviewer_storage_ref,
    ) is True
    assert repository.record_dispatch_completed_result(
        reviewer,
        owner_id="owner",
        workspace_id="workspace",
        result=DispatchSubmissionCompleted(
            title="Independent review",
            mime_type="text/plain",
            content=review_content,
            provider_id="openai_primary",
            adapter="huiyan_chat_completions",
        ),
        artifact_contract="independent_result_review_v1",
        artifact_schema_digest="b" * 64,
        storage_type="local",
        storage_scope=reviewer.task_id,
        storage_ref=reviewer_storage_ref,
        content_sha256=hashlib.sha256(review_content.encode("utf-8")).hexdigest(),
    ) is True
    directive = repository.prepare_execution_continuation(
        task_id=reviewer.task_id,
        owner_id="owner",
        workspace_id="workspace",
        completed_execution_decision_id=reviewer.execution_decision_id,
        changed_by="owner",
    )
    assert directive.action == "start"
    assert directive.next_instance_id == reviewer_work["substageInstanceId"]
    assert directive.selection_request_id is not None
    completed = _task_row(postgres_environment, task.task_id)
    payload = completed["payload"]
    projection = payload["progressProjection"]
    assert completed["revision"] == payload["revision"] == projection["revision"]
    assert completed["revision"] > reviewer.task_revision_after_action_started
    assert completed["status"] == payload["status"] == "running"
    assert projection["taskProgress"]["status"] == "running"
    decision = payload["executionDecisions"][0]
    assert decision["status"] == "closed"
    assert decision["outcome"] == "blocked"

    artifact = payload["artifacts"][0]
    assert artifact["artifactState"] == "insufficient_materials"
    validator = artifact["validatorResult"]
    assert validator["reviewVerdict"] == "insufficient_materials"
    assert validator["reviewDecision"] == "insufficient_evidence"
    assert validator["reviewMissingInputs"] == ["A verifiable source."]
    assert "continueAllowed" not in validator
    assert "qualityAdvisory" not in validator

    producer_result = next(
        row for row in payload["results"]
        if row.get("artifactRefs") == [producer_artifact_id]
    )
    assert producer_result["checkStatus"] == "failed"
    assert producer_result["checkResult"]["reviewVerdict"] == "insufficient_materials"
    assert producer_result["checkResult"]["reviewDecision"] == "insufficient_evidence"

    runtime = next(
        row for row in payload["substageRuntimeStates"]
        if row["substageInstanceId"] == reviewer_work["substageInstanceId"]
    )
    assert runtime["status"] == "ready"
    assert runtime["activeExecutionDecisionId"] is None
    assert runtime["statusReasonCode"] == "review_insufficient_materials"

    interaction = payload["agentInteractionRecords"][0]
    assert interaction["inputArtifactRefs"] == [producer_artifact_id]
    assert interaction["verdict"] == {
        "verdictType": "review",
        "decision": "insufficient_evidence",
        "rationaleCode": "review_insufficient_materials",
    }

    with postgres_environment() as connection:
        catalog_after_review = connection.execute(
            "SELECT metadata FROM openair_artifacts WHERE artifact_id = %s",
            (producer_artifact_id,),
        ).fetchone()
    assert catalog_after_review["metadata"]["artifactState"] == "insufficient_materials"
    assert catalog_after_review["metadata"]["validatorResult"] == validator

    # A rejected research Artifact stays durable history but cannot be hydrated
    # as accepted material for the next scientific action.
    with pytest.raises(
        ResearchOrchestrationPersistenceError,
        match="execution_input_artifact_domain_scope_invalid",
    ):
        repository.read_direct_skill_input_context_for_dispatch(
            task_id=task.task_id,
            owner_id="owner",
            workspace_id="workspace",
            execution_decision_id=reviewer.execution_decision_id,
            work_unit_id=reviewer.work_unit_id,
            _expected_executor_type="agent",
        )


def test_reviewer_reads_only_verified_canonical_artifact_content(
    repository,
    postgres_environment,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import hashlib

    monkeypatch.setattr(settings, "artifact_storage", "local")
    monkeypatch.setattr(settings, "workspace_root", tmp_path)
    task, producer_dispatch, reviewer_assignment_id = _seed_reviewed_dispatch(
        repository,
        postgres_environment,
        "review_materials",
    )
    producer_fence = _claim(repository, task.task_id, "worker-producer")
    assert producer_fence is not None
    producer = repository.authorize_dispatch_submission(
        producer_fence,
        owner_id="owner",
        workspace_id="workspace",
        expected_task_revision=1,
        submission_mode="single_attempt_no_replay",
    )
    assert producer is not None
    producer = _start(repository, producer)
    content = '{"result":"validated"}'
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    artifact_storage_ref = (
        f"durable/research-dispatch/{producer_dispatch.dispatch_id}/artifact.json"
    )
    stored = artifact_write_service.store_durable_bytes(
        task.task_id,
        artifact_storage_ref,
        content.encode("utf-8"),
        "application/json",
    )
    assert _record_completed_and_finalize(
        repository,
        producer,
        owner_id="owner",
        workspace_id="workspace",
        result=DispatchSubmissionCompleted(
            title="Validated result",
            mime_type="text/plain",
            content=content,
            provider_id="openai_primary",
            adapter="huiyan_chat_completions",
        ),
        artifact_contract="test_artifact_v1",
        artifact_schema_digest="a" * 64,
        storage_type="local",
        storage_ref=f"durable/research-dispatch/{producer_dispatch.dispatch_id}/result.json",
        content_sha256=digest,
        artifact_storage_type=stored.storage_type,
        artifact_storage_ref=stored.storage_ref,
    ) is True

    after_producer = _task_row(postgres_environment, task.task_id)
    _release_ready_reviewer_assignment(
        repository,
        task_id=task.task_id,
        expected_task_revision=after_producer["revision"],
        execution_decision_id=producer.execution_decision_id,
        monkeypatch=monkeypatch,
    )
    persisted = repository.load(
        task_id=task.task_id,
        owner_id="owner",
        workspace_id="workspace",
    )
    reviewer_work = next(
        row for row in persisted.work_units if row.assignment_id == reviewer_assignment_id
    )
    reviewer_assignment = next(
        row
        for row in persisted.execution_decisions[0].assignments
        if row.assignment_id == reviewer_assignment_id
    )
    assert persisted.artifacts[0].artifact_state.value == "needs_review"
    assert reviewer_work.input_artifact_refs
    assert reviewer_work.input_artifact_refs[0] not in reviewer_assignment.input_refs
    materials = repository.read_review_materials_for_dispatch(
        task_id=task.task_id,
        owner_id="owner",
        workspace_id="workspace",
        execution_decision_id=reviewer_work.execution_decision_id,
        reviewer_work_unit_id=reviewer_work.work_unit_id,
    )
    assert materials == [
        {
            "artifactId": reviewer_work.input_artifact_refs[0],
            "title": "Validated result",
            "mimeType": "text/plain",
            "content": content,
            "truncated": False,
            "contentSha256": digest,
        }
    ]

    with postgres_environment() as connection:
        connection.execute(
            """UPDATE openair_artifacts
                  SET metadata = jsonb_set(
                      metadata,
                      '{validatorResult,contentSha256}',
                      to_jsonb(%s::text)
                  )
                WHERE artifact_id = %s""",
            ("0" * 64, reviewer_work.input_artifact_refs[0]),
        )
    with pytest.raises(
        ResearchOrchestrationPersistenceError,
        match="review_artifact_catalog_verification_failed",
    ):
        repository.read_review_materials_for_dispatch(
            task_id=task.task_id,
            owner_id="owner",
            workspace_id="workspace",
            execution_decision_id=reviewer_work.execution_decision_id,
            reviewer_work_unit_id=reviewer_work.work_unit_id,
        )

    with pytest.raises(ResearchTaskNotFoundError):
        repository.read_review_materials_for_dispatch(
            task_id=task.task_id,
            owner_id="owner",
            workspace_id="other-workspace",
            execution_decision_id=reviewer_work.execution_decision_id,
            reviewer_work_unit_id=reviewer_work.work_unit_id,
        )


def test_claim_dispatch_continuation_deprioritizes_no_responsibility_zombies(
    repository, postgres_environment,
) -> None:
    _zombie_task, zombie_dispatch = _persist_completed_dispatch_for_continuation(
        repository,
        postgres_environment,
        "continuation_claim_zombie",
    )
    fresh_task, fresh_dispatch = _persist_completed_dispatch_for_continuation(
        repository,
        postgres_environment,
        "continuation_claim_fresh",
    )
    with postgres_environment() as connection:
        connection.execute(
            """UPDATE openair_dispatch_continuation
                  SET attempt_count = 44,
                      last_error_code = 'dispatch_continuation_no_responsibility_proof',
                      next_attempt_at = clock_timestamp()
                WHERE dispatch_id = %s""",
            (zombie_dispatch.dispatch_id,),
        )
        connection.execute(
            """UPDATE openair_dispatch_continuation
                  SET next_attempt_at = clock_timestamp()
                WHERE dispatch_id = %s""",
            (fresh_dispatch.dispatch_id,),
        )

    claimed = repository.claim_dispatch_continuation(
        lease_owner="continuation-priority-worker",
        lease_seconds=30,
        retry_delay_seconds=1,
    )
    assert claimed is not None
    assert claimed.dispatch_id == fresh_dispatch.dispatch_id
    assert claimed.task_id == fresh_task.task_id


def test_terminalize_dispatch_continuation_persists_completed_with_error_code(
    repository, postgres_environment,
) -> None:
    task, dispatch = _persist_completed_dispatch_for_continuation(
        repository,
        postgres_environment,
        "continuation_terminalize",
    )
    fence = repository.claim_dispatch_continuation(
        lease_owner="continuation-terminalize",
        lease_seconds=30,
        retry_delay_seconds=1,
    )
    assert fence is not None
    assert repository.terminalize_dispatch_continuation(
        fence,
        error_code="dispatch_continuation_no_responsibility_proof_exhausted",
    ) is True
    with postgres_environment() as connection:
        row = connection.execute(
            """SELECT status, last_error_code, completed_at IS NOT NULL AS completed
                 FROM openair_dispatch_continuation
                WHERE dispatch_id = %s AND task_id = %s""",
            (dispatch.dispatch_id, task.task_id),
        ).fetchone()
    assert row == {
        "status": "completed",
        "last_error_code": "dispatch_continuation_no_responsibility_proof_exhausted",
        "completed": True,
    }
