"""Shared PostgreSQL fixtures and deterministic helpers for research dispatch transaction tests."""

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
from time import perf_counter
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
from app.modules.durable_execution import DurableExecutionRepository
from app.modules.artifact_write import artifact_write_service
from app.modules.registry_snapshot_repository import (
    RegistrySnapshotRepository,
    registry_envelope_ref,
)
from app.modules.research_orchestration_resolver import build_registry_snapshot
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


DSN_ENV = "OPENAIR_T04_POSTGRES_DSN"
SHARED_FIXTURE = (
    Path(__file__).resolve().parents[4]
    / "packages"
    / "shared"
    / "test-fixtures"
    / "research-orchestration-v1.2-valid.json"
)
_TEST_REGISTRY_SNAPSHOT_REF: dict[str, object] | None = None


@pytest.fixture(scope="session")
def postgres_environment():
    dsn = os.environ.get(DSN_ENV, "").strip()
    if not dsn:
        pytest.fail(f"{DSN_ENV} is required")
    from tests.integration._production_postgres_bootstrap import bootstrap_production_postgres_schema

    with psycopg.connect(dsn, row_factory=dict_row) as connection:
        with connection.cursor() as cursor:
            bootstrap_production_postgres_schema(cursor)

    @contextmanager
    def connect():
        with psycopg.connect(dsn, row_factory=dict_row) as connection:
            yield connection

    yield connect



@pytest.fixture(autouse=True)
def clean_tables(postgres_environment):
    connect = postgres_environment
    with connect() as connection:
        connection.execute("DELETE FROM openair_worker_callback_authority")
        connection.execute("DELETE FROM openair_artifact_lineage_audit")
        connection.execute("DELETE FROM openair_artifact_lineage")
        connection.execute("DELETE FROM openair_capability_call_lineage")
        connection.execute("DELETE FROM openair_work_unit_lineage")
        connection.execute("DELETE FROM openair_artifact_storage_references")
        connection.execute("DELETE FROM openair_artifacts")
        # Artifact DELETE enqueues cleanup outbox rows that fence storage identity
        # as retired; clear them so fixture suffixes can reuse local storage refs.
        connection.execute("DELETE FROM openair_artifact_cleanup_outbox")
        connection.execute("DELETE FROM openair_dispatch_continuation")
        connection.execute("DELETE FROM openair_external_retry_attempt")
        connection.execute("DELETE FROM openair_external_reconciliation")
        connection.execute("DELETE FROM openair_external_evidence")
        connection.execute("DELETE FROM openair_external_execution")
        connection.execute("DELETE FROM openair_dispatch")
        connection.execute("DELETE FROM openair_execution_start_attempts")
        connection.execute("DELETE FROM openair_execution_selection_request")
        connection.execute("DELETE FROM openair_tasks")
    yield
    with connect() as connection:
        connection.execute("DELETE FROM openair_worker_callback_authority")
        connection.execute("DELETE FROM openair_artifact_lineage_audit")
        connection.execute("DELETE FROM openair_artifact_lineage")
        connection.execute("DELETE FROM openair_capability_call_lineage")
        connection.execute("DELETE FROM openair_work_unit_lineage")
        connection.execute("DELETE FROM openair_artifact_storage_references")
        connection.execute("DELETE FROM openair_artifacts")
        connection.execute("DELETE FROM openair_artifact_cleanup_outbox")
        connection.execute("DELETE FROM openair_dispatch_continuation")
        connection.execute("DELETE FROM openair_external_retry_attempt")
        connection.execute("DELETE FROM openair_external_reconciliation")
        connection.execute("DELETE FROM openair_external_evidence")
        connection.execute("DELETE FROM openair_external_execution")
        connection.execute("DELETE FROM openair_dispatch")
        connection.execute("DELETE FROM openair_execution_start_attempts")
        connection.execute("DELETE FROM openair_execution_selection_request")
        connection.execute("DELETE FROM openair_tasks")


@pytest.fixture()
def repository(monkeypatch: pytest.MonkeyPatch, postgres_environment):
    monkeypatch.setattr(settings, "research_orchestration_v1", True)
    monkeypatch.setattr(settings, "persistence_backend", "postgres")
    return ResearchOrchestrationRepository(
        connection_factory=postgres_environment,
        runtime_settings=settings,
    )


def _replace_fixture_identifiers(value: object, suffix: str) -> object:
    identifiers = (
        "task_contract_full", "planning_contract_full", "request_contract_full",
        "provider_call_contract_full", "rd_contract_full", "sub_contract_full",
        "criterion_contract_full", "target_contract_full", "coverage_contract_full",
        "confirmation_design_contract_full", "selection_contract_full",
        "decision_contract_full", "candidate_contract_full", "assignment_contract_full",
        "wu_contract_full", "call_contract_full", "artifact_contract_full",
        "interaction_contract_full", "dispatch_contract_full",
    )
    if isinstance(value, dict):
        return {key: _replace_fixture_identifiers(item, suffix) for key, item in value.items()}
    if isinstance(value, list):
        return [_replace_fixture_identifiers(item, suffix) for item in value]
    if isinstance(value, str):
        for identifier in identifiers:
            value = value.replace(identifier, f"{identifier}_{suffix}")
    return value


def _replace_registry_snapshot_refs(
    value: object,
    snapshot_ref: dict[str, object],
) -> object:
    if isinstance(value, dict):
        return {
            key: deepcopy(snapshot_ref)
            if key == "registrySnapshotRef"
            else _replace_registry_snapshot_refs(item, snapshot_ref)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [
            _replace_registry_snapshot_refs(item, snapshot_ref)
            for item in value
        ]
    return value


def _canonical_test_registry_snapshot_ref(connect) -> dict[str, object]:
    global _TEST_REGISTRY_SNAPSHOT_REF
    if _TEST_REGISTRY_SNAPSHOT_REF is None:
        snapshot = build_registry_snapshot()
        RegistrySnapshotRepository(connect).put(snapshot)
        _TEST_REGISTRY_SNAPSHOT_REF = registry_envelope_ref(snapshot.envelope)
    return deepcopy(_TEST_REGISTRY_SNAPSHOT_REF)


def _transaction_fixture(suffix: str) -> tuple[ResearchTaskV12, ResearchTaskV12, DispatchRecord]:
    fixture = json.loads(SHARED_FIXTURE.read_text(encoding="utf-8"))
    transformed = _replace_fixture_identifiers(fixture, suffix)
    task_payload = transformed["researchTask"]
    task_payload["revision"] = 0
    task_payload["substageRuntimeStates"][0].update(
        {
            "status": "queued",
            "activeExecutionDecisionId": task_payload["executionDecisions"][0]["executionDecisionId"],
            "artifactRefs": [],
        }
    )
    task_payload["workUnits"][0].update(
        {"status": "queued", "terminalReasonCode": None, "finishedAt": None}
    )
    task_payload["workUnits"][0]["budgetSnapshot"].update(
        {"consumedToolCalls": 0, "consumedCostAmount": 0.0}
    )
    task_payload["capabilityCalls"] = []
    task_payload["artifacts"] = []
    task_payload["agentInteractionRecords"] = []
    candidate = ResearchTaskV12.model_validate(task_payload)
    baseline_payload = candidate.model_dump(mode="json", by_alias=True)
    baseline_payload["executionDecisions"] = []
    baseline_payload["workUnits"] = []
    baseline_payload["substageRuntimeStates"][0].update(
        {"status": "ready", "activeExecutionDecisionId": None, "workUnitIds": []}
    )
    dispatch_payload = transformed["dispatchRecord"]
    # Pending inserts require a namespaced pristine DispatchRecord; shared
    # fixtures predate the fence and omit executionNamespace.
    if not dispatch_payload.get("executionNamespace"):
        dispatch_payload["executionNamespace"] = (
            getattr(settings, "execution_namespace", None) or "local-development"
        )
    return (
        ResearchTaskV12.model_validate(baseline_payload),
        candidate,
        DispatchRecord.model_validate(dispatch_payload),
    )


def _insert_task(connect, task: ResearchTaskV12, owner="owner", workspace="workspace") -> None:
    payload = task.model_dump(mode="json", by_alias=True)
    payload["ownerId"] = owner
    payload["workspaceId"] = workspace
    with connect() as connection:
        connection.execute(
            """INSERT INTO openair_tasks (
                   task_id, owner_id, workspace_id, status, current_stage, title,
                   domain, created_at, updated_at, payload, revision
               ) VALUES (%s, %s, %s, %s, 'solution_design', %s, 'research_task',
                         NOW(), NOW(), %s::jsonb, %s)""",
            (task.task_id, owner, workspace, task.status.value, task.task_id, json.dumps(payload), task.revision),
        )


def _seed(repository, connect, suffix: str, owner="owner", workspace="workspace"):
    baseline, candidate, dispatch = _transaction_fixture(suffix)
    snapshot_ref = _canonical_test_registry_snapshot_ref(connect)
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
    _insert_task(connect, baseline, owner, workspace)
    saved = repository.save(
        candidate,
        owner_id=owner,
        workspace_id=workspace,
        expected_task_revision=0,
        pending_dispatches=[dispatch],
        automatic_dispatch=True,
    )
    return saved, dispatch


@contextmanager
def _namespaced_dispatch_mutation_connection(connect):
    """Authorize test-only direct mutations of canonical Dispatch rows."""
    with connect() as connection:
        connection.execute(
            "SELECT set_config('openair.execution_namespace', %s, true)",
            (settings.execution_namespace,),
        )
        yield connection


def _record_completed_and_finalize(
    repository,
    fence,
    *,
    owner_id: str,
    workspace_id: str,
    **kwargs,
):
    # Match research_dispatch_service local-storage contract (Line C storage_scope).
    kwargs.setdefault("storage_scope", fence.task_id)
    if kwargs.get("artifact_storage_ref") is not None:
        kwargs.setdefault("artifact_storage_scope", fence.task_id)
    assert repository.reserve_dispatch_result_storage(
        fence,
        storage_type=kwargs["storage_type"],
        storage_scope=kwargs["storage_scope"],
        storage_ref=kwargs["storage_ref"],
    ) is True
    assert repository.record_dispatch_completed_result(
        fence,
        owner_id=owner_id,
        workspace_id=workspace_id,
        **kwargs,
    ) is True
    # This helper advances the independent continuation explicitly for tests
    # that assert downstream dependency or lifecycle effects.
    repository.prepare_execution_continuation(
        task_id=fence.task_id,
        owner_id=owner_id,
        workspace_id=workspace_id,
        completed_execution_decision_id=fence.execution_decision_id,
        changed_by=owner_id,
    )
    return True


def _seed_reviewed_dispatch(
    repository,
    connect,
    suffix: str,
    *,
    execution_mode: str | None = None,
    registry_snapshot_ref: dict[str, object] | None = None,
):
    baseline, candidate, dispatch = _transaction_fixture(suffix)
    if registry_snapshot_ref is None:
        registry_snapshot_ref = _canonical_test_registry_snapshot_ref(connect)
    if registry_snapshot_ref is not None:
        baseline = ResearchTaskV12.model_validate(
            _replace_registry_snapshot_refs(
                baseline.model_dump(mode="json", by_alias=True),
                registry_snapshot_ref,
            )
        )
        candidate = ResearchTaskV12.model_validate(
            _replace_registry_snapshot_refs(
                candidate.model_dump(mode="json", by_alias=True),
                registry_snapshot_ref,
            )
        )
    payload = candidate.model_dump(mode="json", by_alias=True)
    if execution_mode is not None:
        payload["executionMode"] = execution_mode
    decision = payload["executionDecisions"][0]
    producer = decision["assignments"][0]
    producer["recipeTemplateKey"] = "producer"
    reviewer = deepcopy(producer)
    reviewer_id = f"assignment_reviewer_{suffix}"
    reviewer.update(
        {
            "assignmentId": reviewer_id,
            "recipeTemplateKey": "reviewer",
            "role": "reviewer",
            "objective": "Independently review the producer artifact.",
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
            # A production recipe cannot name an Artifact before the producer
            # call exists. Reviewer release inherits verified dependency outputs
            # that are still awaiting scientific acceptance.
            "inputRefs": list(producer["inputRefs"]),
            "expectedArtifactTypes": [],
            "acceptanceCriteria": [],
            "dependencyAssignmentIds": [producer["assignmentId"]],
        }
    )
    decision.update(
        {
            "strategy": "multi_agent",
            "multiAgentRecipeRef": {
                "recipeId": "recipe_producer_reviewer",
                "recipeVersion": 1,
            },
            "assignments": [producer, reviewer],
        }
    )
    decision["completionPolicy"].update(
        {
            "requiredAssignmentIds": [producer["assignmentId"], reviewer_id],
            "reviewerAssignmentIds": [reviewer_id],
            "reviewRequirement": "all_reviewers_approve",
        }
    )
    decision["runtimeReadiness"].append(
        {
            "capabilityRef": {
                "agentId": "result_reliability_audit",
                "definitionVersion": 1,
            },
            "ready": True,
            "runtimeBindingId": "registry:result-reliability-audit:1",
            "checkedAt": "2026-07-31T00:00:00Z",
            "expiresAt": "2099-07-31T00:00:00Z",
            "reason": None,
        }
    )
    candidate = ResearchTaskV12.model_validate(payload)
    baseline_payload = baseline.model_dump(mode="json", by_alias=True)
    _insert_task(connect, ResearchTaskV12.model_validate(baseline_payload))
    saved = repository.save(
        candidate,
        owner_id="owner",
        workspace_id="workspace",
        expected_task_revision=0,
        pending_dispatches=[dispatch],
        automatic_dispatch=True,
    )
    return saved, dispatch, reviewer_id


def _release_ready_reviewer_assignment(
    repository,
    *,
    task_id: str,
    expected_task_revision: int,
    execution_decision_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.modules.research_execution_decision_service import (
        ResearchExecutionDecisionService,
    )

    _ = monkeypatch
    service = ResearchExecutionDecisionService(
        repository=repository,
        resolver=None,
        facts_source=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("dependency release must not resolve a new decision")
        ),
    )
    service.release_ready(
        task_id=task_id,
        execution_decision_id=execution_decision_id,
        owner_id="owner",
        workspace_id="workspace",
        expected_task_revision=expected_task_revision,
    )


def _claim(repository, task_id: str, worker: str):
    claims = repository.claim_dispatches(
        lease_owner=worker,
        lease_seconds=60,
        batch_size=1,
        task_id=task_id,
        owner_id="owner",
        workspace_id="workspace",
    )
    return claims[0] if claims else None


def _dispatch_row(connect, dispatch_id: str):
    with connect() as connection:
        return connection.execute(
            "SELECT * FROM openair_dispatch WHERE dispatch_id = %s", (dispatch_id,)
        ).fetchone()


def _task_row(connect, task_id: str):
    with connect() as connection:
        return connection.execute(
            "SELECT payload, revision, status FROM openair_tasks WHERE task_id = %s", (task_id,)
        ).fetchone()


def _task_message_rows(connect, task_id: str):
    with connect() as connection:
        return connection.execute(
            """SELECT role, content_text, capability_call_id, metadata
                 FROM openair_task_messages
                WHERE task_id = %s
                ORDER BY seq""",
            (task_id,),
        ).fetchall()


def _work_unit_status(task_row, work_unit_id: str) -> tuple[str, str | None]:
    work_unit = next(
        row for row in task_row["payload"]["workUnits"] if row["workUnitId"] == work_unit_id
    )
    return work_unit["status"], work_unit["terminalReasonCode"]


def _integrity_blocker(task_row, dispatch_id: str) -> dict:
    return next(
        row
        for row in task_row["payload"].get("executionIntegrityBlockers", [])
        if row.get("dispatchId") == dispatch_id
    )


def _due_dispatch_ids(connect) -> list[str]:
    with connect() as connection:
        rows = connection.execute(
            """SELECT dispatch_id
                 FROM openair_dispatch
                WHERE (
                       (status = 'pending'
                        AND (next_attempt_at IS NULL OR next_attempt_at <= clock_timestamp()))
                    OR (status = 'claimed' AND lease_expires_at <= clock_timestamp())
                    OR (status = 'submission_authorized'
                        AND lease_expires_at <= clock_timestamp())
                )
                ORDER BY dispatch_id"""
        ).fetchall()
    return [str(row["dispatch_id"]) for row in rows]


def _authorize(repository, task_id: str, worker: str = "worker-a") -> AuthorizedDispatch:
    fence = _claim(repository, task_id, worker)
    assert fence is not None
    authorized = repository.authorize_dispatch_submission(
        fence,
        owner_id="owner",
        workspace_id="workspace",
        expected_task_revision=1,
    )
    assert authorized is not None
    return authorized


def _start(repository, authorized: AuthorizedDispatch) -> AuthorizedDispatch:
    started = repository.mark_external_action_started(
        authorized,
        provider_id="openai_primary",
        adapter="huiyan_chat_completions",
    )
    assert isinstance(started, AuthorizedDispatch)
    return started
