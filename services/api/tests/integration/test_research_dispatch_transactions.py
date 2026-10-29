"""PostgreSQL claim, authorization, suppression, recovery, and dispatch-control transactions."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
from threading import Barrier
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
from app.modules.external_execution_checkpoint import ExternalExecutionCheckpointRepository
from app.modules.research_dispatch_service import ResearchDispatchService
from app.execution.research_dispatch_provider import (
    DispatchSubmissionCompleted,
    STRUCTURED_OUTPUT_CORRECTION_EXHAUSTED,
    STRUCTURED_OUTPUT_EXPLICIT_RETRY_REASON,
)
from app.models.research_external_result import ExplicitRetryCommand

from tests.support.research_dispatch_transactions import (
    postgres_environment,
    clean_tables,
    repository,
    _replace_fixture_identifiers,
    _replace_registry_snapshot_refs,
    _canonical_test_registry_snapshot_ref,
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

def test_two_concurrent_claimants_have_one_winner_and_one_fence(repository, postgres_environment) -> None:
    task, _dispatch = _seed(repository, postgres_environment, "claim_race")
    barrier = Barrier(2)

    def attempt(worker: str):
        barrier.wait()
        return _claim(repository, task.task_id, worker)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, ("worker-a", "worker-b")))

    winners = [row for row in results if row is not None]
    assert len(winners) == 1
    assert winners[0].attempt_count == 1
    assert winners[0].lease_owner in {"worker-a", "worker-b"}


def test_active_lease_is_not_stolen_and_expired_unautorized_claim_reclaims_with_new_epoch(
    repository, postgres_environment
) -> None:
    task, dispatch = _seed(repository, postgres_environment, "reclaim")
    first = _claim(repository, task.task_id, "worker-a")
    assert first is not None
    assert _claim(repository, task.task_id, "worker-b") is None

    with _namespaced_dispatch_mutation_connection(postgres_environment) as connection:
        connection.execute(
            "UPDATE openair_dispatch SET lease_expires_at = clock_timestamp() - interval '1 second' WHERE dispatch_id = %s",
            (dispatch.dispatch_id,),
        )
    second = _claim(repository, task.task_id, "worker-b")

    assert second is not None
    assert second.attempt_count == 2
    assert second.lease_owner == "worker-b"
    assert repository.renew_dispatch(first, lease_seconds=60) is False
    assert repository.authorize_dispatch_submission(
        first, owner_id="owner", workspace_id="workspace", expected_task_revision=1
    ) is None


def test_late_authorized_outcome_ignores_expiry_but_stale_fence_is_zero_write(
    repository, postgres_environment
) -> None:
    task, dispatch = _seed(repository, postgres_environment, "late_outcome")
    fence = _claim(repository, task.task_id, "worker-a")
    assert fence is not None
    authorized = repository.authorize_dispatch_submission(
        fence, owner_id="owner", workspace_id="workspace", expected_task_revision=1
    )
    assert authorized is not None
    authorized = _start(repository, authorized)
    with _namespaced_dispatch_mutation_connection(postgres_environment) as connection:
        connection.execute(
            "UPDATE openair_dispatch SET lease_expires_at = clock_timestamp() - interval '1 second' WHERE dispatch_id = %s",
            (dispatch.dispatch_id,),
        )

    assert repository.record_dispatch_outcome(
        authorized,
        owner_id="owner",
        workspace_id="workspace",
        outcome="accepted",
        remote_run_id="receipt-late",
    ) is True
    revision = _task_row(postgres_environment, task.task_id)["revision"]
    assert repository.record_dispatch_outcome(
        replace(authorized, attempt_count=authorized.attempt_count + 1),
        owner_id="owner",
        workspace_id="workspace",
        outcome="unknown",
        error_code="stale",
    ) is False
    row = _dispatch_row(postgres_environment, dispatch.dispatch_id)
    assert (row["status"], row["remote_run_id"]) == ("submitted", "receipt-late")
    assert _task_row(postgres_environment, task.task_id)["revision"] == revision


def test_known_rejection_persists_safe_runtime_failure_detail(
    repository, postgres_environment
) -> None:
    task, dispatch = _seed(repository, postgres_environment, "safe_failure_detail")
    authorized = _authorize(repository, task.task_id)
    authorized = _start(repository, authorized)

    assert repository.record_dispatch_outcome(
        authorized,
        owner_id="owner",
        workspace_id="workspace",
        outcome="known_rejected",
        error_code="provider_response_contract_invalid",
        failure_detail={
            "failedStage": "runtime_guard_failed",
            "firstMismatch": "P07C-R03",
        },
    ) is True

    task_row = _task_row(postgres_environment, task.task_id)
    dispatch_row = _dispatch_row(postgres_environment, dispatch.dispatch_id)
    assert dispatch_row["status"] == "failed"
    assert task_row["payload"]["dispatchFailureDetail"] == {
        "failedStage": "runtime_guard_failed",
        "firstMismatch": "P07C-R03",
    }


def test_authorized_recovery_active_lease_is_not_stolen_and_expired_takeover_has_one_winner(
    repository, postgres_environment
) -> None:
    task, dispatch = _seed(repository, postgres_environment, "authorized_takeover")
    original = _authorize(repository, task.task_id)
    claim_recovery = getattr(repository, "claim_authorized_recovery", None)
    assert callable(claim_recovery), "submission_authorized requires a dedicated recovery claim"

    assert claim_recovery(
        lease_owner="recovery-early",
        lease_seconds=60,
        task_id=task.task_id,
        owner_id="owner",
        workspace_id="workspace",
    ) == []
    with _namespaced_dispatch_mutation_connection(postgres_environment) as connection:
        connection.execute(
            "UPDATE openair_dispatch SET lease_expires_at = clock_timestamp() - interval '1 second' "
            "WHERE dispatch_id = %s",
            (dispatch.dispatch_id,),
        )

    barrier = Barrier(2)

    def attempt(worker: str):
        barrier.wait()
        return claim_recovery(
            lease_owner=worker,
            lease_seconds=60,
            task_id=task.task_id,
            owner_id="owner",
            workspace_id="workspace",
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, ("recovery-a", "recovery-b")))

    winners = [rows[0] for rows in results if rows]
    assert len(winners) == 1
    recovered = winners[0]
    assert isinstance(recovered, AuthorizedDispatch)
    assert recovered.attempt_count == original.attempt_count + 1
    assert recovered.authorization_task_revision == original.authorization_task_revision
    assert recovered.idempotency_key == original.idempotency_key
    assert recovered.work_unit_payload == original.work_unit_payload
    assert _dispatch_row(postgres_environment, dispatch.dispatch_id)["lease_owner"] == recovered.lease_owner


def test_single_attempt_authorization_persists_action_start_before_no_replay_recovery(
    repository, postgres_environment
) -> None:
    task, dispatch = _seed(repository, postgres_environment, "single_attempt_start")
    fence = _claim(repository, task.task_id, "worker-single")
    assert fence is not None
    authorized = repository.authorize_dispatch_submission(
        fence,
        owner_id="owner",
        workspace_id="workspace",
        expected_task_revision=1,
        submission_mode="single_attempt_no_replay",
    )
    assert authorized is not None
    assert authorized.submission_mode == "single_attempt_no_replay"
    assert authorized.external_action_started_at is None
    row = _dispatch_row(postgres_environment, dispatch.dispatch_id)
    assert row["submission_mode"] == "single_attempt_no_replay"
    assert row["external_action_started_at"] is None

    authorized = _start(repository, authorized)
    assert repository.mark_external_action_started(
        authorized,
        provider_id="openai_primary",
        adapter="huiyan_chat_completions",
    ) is None
    dispatch_after_start = _dispatch_row(postgres_environment, dispatch.dispatch_id)
    task_after_start = _task_row(postgres_environment, task.task_id)
    started_at = dispatch_after_start["external_action_started_at"]
    assert started_at is not None
    assert authorized.authorization_task_revision == 1
    assert task_after_start["revision"] == authorized.task_revision_after_action_started == 2
    assert dispatch_after_start["authorization_task_revision"] == 1
    calls = task_after_start["payload"]["capabilityCalls"]
    assert len(calls) == 1
    assert calls[0]["capabilityCallId"] == dispatch_capability_call_id(authorized)
    assert calls[0]["attemptNumber"] == 1
    assert calls[0]["status"] == "running"
    assert calls[0]["finishedAt"] is None

    with _namespaced_dispatch_mutation_connection(postgres_environment) as connection:
        connection.execute(
            "UPDATE openair_dispatch SET lease_expires_at = clock_timestamp() - interval '1 second' "
            "WHERE dispatch_id = %s",
            (dispatch.dispatch_id,),
        )
    recovered = repository.claim_authorized_recovery(
        lease_owner="recovery-single",
        lease_seconds=60,
        task_id=task.task_id,
        owner_id="owner",
        workspace_id="workspace",
    )[0]

    assert recovered.submission_mode == "single_attempt_no_replay"
    assert recovered.external_action_started_at == started_at


def test_reserve_dispatch_result_storage_is_idempotent_for_matching_reservation(
    repository, postgres_environment
) -> None:
    task, dispatch = _seed(repository, postgres_environment, "result_storage_idempotent")
    authorized = _start(
        repository,
        repository.authorize_dispatch_submission(
            _claim(repository, task.task_id, "worker-reserve"),
            owner_id="owner",
            workspace_id="workspace",
            expected_task_revision=1,
            submission_mode="single_attempt_no_replay",
            lease_seconds=60,
        ),
    )
    assert authorized is not None
    storage_ref = f"durable/research-dispatch/{dispatch.dispatch_id}/result.json"
    assert repository.reserve_dispatch_result_storage(
        authorized,
        storage_type="local",
        storage_scope=authorized.task_id,
        storage_ref=storage_ref,
    ) is True
    stale_fence = replace(authorized, attempt_count=authorized.attempt_count + 99)
    assert repository.reserve_dispatch_result_storage(
        stale_fence,
        storage_type="local",
        storage_scope=authorized.task_id,
        storage_ref=storage_ref,
    ) is True


def test_providerless_recovery_claim_waits_without_result_storage(
    repository, postgres_environment
) -> None:
    task, dispatch = _seed(repository, postgres_environment, "providerless_quiet")
    fence = _claim(repository, task.task_id, "worker-literature")
    assert fence is not None
    authorized = repository.authorize_dispatch_submission(
        fence,
        owner_id="owner",
        workspace_id="workspace",
        expected_task_revision=1,
        submission_mode="single_attempt_no_replay",
        lease_seconds=30,
    )
    assert authorized is not None
    started = _start(repository, authorized)
    with _namespaced_dispatch_mutation_connection(postgres_environment) as connection:
        connection.execute(
            "UPDATE openair_dispatch SET lease_expires_at = clock_timestamp() - interval '5 seconds' "
            "WHERE dispatch_id = %s",
            (dispatch.dispatch_id,),
        )
    assert repository.claim_authorized_recovery(
        lease_owner="recovery-early",
        lease_seconds=60,
        task_id=task.task_id,
        owner_id="owner",
        workspace_id="workspace",
    ) == []
    with _namespaced_dispatch_mutation_connection(postgres_environment) as connection:
        connection.execute(
            "UPDATE openair_dispatch SET lease_expires_at = clock_timestamp() - interval '130 seconds' "
            "WHERE dispatch_id = %s",
            (dispatch.dispatch_id,),
        )
    recovered = repository.claim_authorized_recovery(
        lease_owner="recovery-late",
        lease_seconds=60,
        task_id=task.task_id,
        owner_id="owner",
        workspace_id="workspace",
    )
    assert len(recovered) == 1
    assert recovered[0].dispatch_id == dispatch.dispatch_id
    assert recovered[0].lease_owner == "recovery-late"
    assert recovered[0].attempt_count == started.attempt_count


def test_authorize_and_action_start_refresh_lease_expires_at(
    repository, postgres_environment
) -> None:
    task, dispatch = _seed(repository, postgres_environment, "lease_refresh")
    fence = _claim(repository, task.task_id, "worker-lease-refresh")
    assert fence is not None
    with _namespaced_dispatch_mutation_connection(postgres_environment) as connection:
        connection.execute(
            "UPDATE openair_dispatch SET lease_expires_at = clock_timestamp() + interval '2 seconds' "
            "WHERE dispatch_id = %s",
            (dispatch.dispatch_id,),
        )
    before = _dispatch_row(postgres_environment, dispatch.dispatch_id)["lease_expires_at"]
    authorized = repository.authorize_dispatch_submission(
        fence,
        owner_id="owner",
        workspace_id="workspace",
        expected_task_revision=1,
        submission_mode="single_attempt_no_replay",
        lease_seconds=60,
    )
    assert authorized is not None
    after_authorize = _dispatch_row(postgres_environment, dispatch.dispatch_id)["lease_expires_at"]
    assert after_authorize > before

    started = repository.mark_external_action_started(
        authorized,
        provider_id="openai_primary",
        adapter="huiyan_chat_completions",
        lease_seconds=60,
    )
    assert isinstance(started, AuthorizedDispatch)
    after_start = _dispatch_row(postgres_environment, dispatch.dispatch_id)["lease_expires_at"]
    assert after_start >= after_authorize


def test_claim_authorized_recovery_reconciles_zombie_in_flight_after_expired_lease(
    repository, postgres_environment
) -> None:
    task, dispatch = _seed(repository, postgres_environment, "inflight_no_takeover")
    fence = _claim(repository, task.task_id, "worker-inflight")
    assert fence is not None
    authorized = repository.authorize_dispatch_submission(
        fence,
        owner_id="owner",
        workspace_id="workspace",
        expected_task_revision=1,
        submission_mode="single_attempt_no_replay",
        lease_seconds=60,
    )
    assert authorized is not None
    started = _start(repository, authorized)
    call_id = dispatch_capability_call_id(started)
    attempt_id = f"pat-{dispatch.dispatch_id}"
    with _namespaced_dispatch_mutation_connection(postgres_environment) as connection:
        connection.execute(
            """INSERT INTO openair_provider_http_attempts (
                   attempt_id, capability_call_id, attempt_number, request_hash,
                   provider_endpoint_identity, evidence_kind, transport_outcome,
                   execution_state, io_started
               ) VALUES (
                   %s, %s, 1, %s, 'provider://test', 'provider_submission', 'in_flight',
                   'running', TRUE
               )""",
            (
                attempt_id,
                call_id,
                "a" * 64,
            ),
        )
        connection.execute(
            "UPDATE openair_dispatch SET lease_expires_at = clock_timestamp() - interval '1 second' "
            "WHERE dispatch_id = %s",
            (dispatch.dispatch_id,),
        )

    recovered = repository.claim_authorized_recovery(
        lease_owner="recovery-inflight",
        lease_seconds=60,
        task_id=task.task_id,
        owner_id="owner",
        workspace_id="workspace",
    )
    assert len(recovered) == 1
    assert recovered[0].dispatch_id == dispatch.dispatch_id
    assert recovered[0].lease_owner == "recovery-inflight"
    assert repository.renew_authorized_dispatch(started, lease_seconds=60) is False
    row = _dispatch_row(postgres_environment, dispatch.dispatch_id)
    assert row["lease_owner"] == "recovery-inflight"
    assert int(row["attempt_count"]) == int(started.attempt_count)
    assert row["status"] == "submission_authorized"
    with _namespaced_dispatch_mutation_connection(postgres_environment) as connection:
        attempt = connection.execute(
            """SELECT transport_outcome, execution_state, execution_reason, finished_at
                 FROM openair_provider_http_attempts
                WHERE attempt_id = %s""",
            (attempt_id,),
        ).fetchone()
    assert attempt["transport_outcome"] == "transport_uncertain"
    assert attempt["execution_state"] == "unknown"
    assert attempt["execution_reason"] == "transport_uncertain"
    assert attempt["finished_at"] is not None


def test_authorized_recovery_renew_and_takeover_stale_the_old_fence(
    repository, postgres_environment
) -> None:
    task, dispatch = _seed(repository, postgres_environment, "authorized_renew")
    original = _authorize(repository, task.task_id)
    claim_recovery = getattr(repository, "claim_authorized_recovery", None)
    renew_recovery = getattr(repository, "renew_authorized_dispatch", None)
    assert callable(claim_recovery) and callable(renew_recovery)
    with _namespaced_dispatch_mutation_connection(postgres_environment) as connection:
        connection.execute(
            "UPDATE openair_dispatch SET lease_expires_at = clock_timestamp() - interval '1 second' "
            "WHERE dispatch_id = %s",
            (dispatch.dispatch_id,),
        )
    recovered = claim_recovery(
        lease_owner="recovery-new",
        lease_seconds=60,
        task_id=task.task_id,
        owner_id="owner",
        workspace_id="workspace",
    )[0]

    assert renew_recovery(original, lease_seconds=60) is False
    assert renew_recovery(recovered, lease_seconds=60) is True
    revision = _task_row(postgres_environment, task.task_id)["revision"]
    assert repository.record_dispatch_outcome(
        original,
        owner_id="owner",
        workspace_id="workspace",
        outcome="accepted",
        remote_run_id="stale-receipt",
    ) is False
    assert _task_row(postgres_environment, task.task_id)["revision"] == revision
    assert repository.record_dispatch_outcome(
        recovered,
        owner_id="owner",
        workspace_id="workspace",
        outcome="accepted",
        remote_run_id="winner-receipt",
    ) is True


def test_authorized_callback_before_recovery_takeover_wins_and_prevents_recovery(
    repository, postgres_environment
) -> None:
    task, dispatch = _seed(repository, postgres_environment, "callback_before_takeover")
    authorized = _authorize(repository, task.task_id)
    with _namespaced_dispatch_mutation_connection(postgres_environment) as connection:
        connection.execute(
            "UPDATE openair_dispatch SET lease_expires_at = clock_timestamp() - interval '1 second' "
            "WHERE dispatch_id = %s",
            (dispatch.dispatch_id,),
        )
    assert repository.record_dispatch_outcome(
        authorized,
        owner_id="owner",
        workspace_id="workspace",
        outcome="accepted",
        remote_run_id="callback-winner",
        provider_id="openai_primary",
        adapter="huiyan_chat_completions",
    ) is True
    assert repository.claim_authorized_recovery(
        lease_owner="recovery-loser",
        lease_seconds=60,
        task_id=task.task_id,
        owner_id="owner",
        workspace_id="workspace",
    ) == []


def test_stop_receipt_replays_for_original_and_committed_revision_without_new_write(
    repository, postgres_environment
) -> None:
    task, _dispatch = _seed(repository, postgres_environment, "stop_receipt_replay")
    first = repository.suppress_dispatches_with_receipt(
        task_id=task.task_id,
        owner_id="owner",
        workspace_id="workspace",
        expected_task_revision=1,
        reason_code="user_stop_requested",
    )
    assert first["status"] == "stopped"
    assert first["controlAction"] == "stop"
    assert first["controlGeneration"] == 1
    assert first["taskStatus"] == "cancelled"
    assert first["revision"] == 2
    assert _task_row(postgres_environment, task.task_id)["revision"] == 2

    replay_original = repository.suppress_dispatches_with_receipt(
        task_id=task.task_id,
        owner_id="owner",
        workspace_id="workspace",
        expected_task_revision=1,
        reason_code="user_stop_requested",
    )
    replay_current = repository.suppress_dispatches_with_receipt(
        task_id=task.task_id,
        owner_id="owner",
        workspace_id="workspace",
        expected_task_revision=2,
        reason_code="user_stop_requested",
    )
    assert replay_original == first
    assert replay_current == first
    assert _task_row(postgres_environment, task.task_id)["revision"] == 2


def test_resume_supersedes_stop_generation_and_only_releases_never_submitted_dispatch(
    repository, postgres_environment, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(repository, "load_registry_snapshot", lambda _hash: {})
    task, dispatch = _seed(repository, postgres_environment, "resume_never_submitted")
    stopped = repository.suppress_dispatches_with_receipt(
        task_id=task.task_id,
        owner_id="owner",
        workspace_id="workspace",
        expected_task_revision=1,
        reason_code="user_stop_requested",
    )
    assert _dispatch_row(postgres_environment, dispatch.dispatch_id)["status"] == "suppressed"

    resumed = repository.resume_suppressed_dispatches_with_receipt(
        task_id=task.task_id,
        owner_id="owner",
        workspace_id="workspace",
        expected_task_revision=stopped["revision"],
    )

    assert resumed["controlAction"] == "resume"
    assert resumed["controlGeneration"] == stopped["controlGeneration"] + 1
    assert resumed["supersedesControlGeneration"] == stopped["controlGeneration"]
    assert resumed["resumedDispatchCount"] == 1
    row = _dispatch_row(postgres_environment, dispatch.dispatch_id)
    assert row["status"] == "pending"
    assert row["external_action_started_at"] is None


def test_suppress_requires_exact_revision_cas_and_revision_zero_is_valid(
    repository, postgres_environment
) -> None:
    task, dispatch = _seed(repository, postgres_environment, "stop_revision")
    before_task = deepcopy(_task_row(postgres_environment, task.task_id))
    before_dispatch = deepcopy(_dispatch_row(postgres_environment, dispatch.dispatch_id))

    with pytest.raises(TaskRevisionConflictError, match="task_revision_conflict"):
        repository.suppress_dispatches(
            task_id=task.task_id,
            owner_id="owner",
            workspace_id="workspace",
            expected_task_revision=0,
        )
    assert _task_row(postgres_environment, task.task_id) == before_task
    assert _dispatch_row(postgres_environment, dispatch.dispatch_id) == before_dispatch

    baseline, _candidate, _dispatch = _transaction_fixture("stop_revision_zero")
    _insert_task(postgres_environment, baseline)
    assert repository.suppress_dispatches(
        task_id=baseline.task_id,
        owner_id="owner",
        workspace_id="workspace",
        expected_task_revision=0,
    ) == 0
    zero_row = _task_row(postgres_environment, baseline.task_id)
    assert zero_row["revision"] == 1


@pytest.mark.parametrize(
    ("outcome", "kwargs", "dispatch_status", "call_status"),
    (
        ("accepted", {"remote_run_id": "accepted-after-stop"}, "submitted", "running"),
        ("known_rejected", {"error_code": "provider_http_400"}, "failed", "failed"),
        ("unknown", {"error_code": "provider_result_unknown"}, "submission_unknown", "external_result_unknown"),
    ),
)
def test_suppressed_dispatch_still_records_external_outcome_truth_atomically(
    repository,
    postgres_environment,
    outcome,
    kwargs,
    dispatch_status,
    call_status,
) -> None:
    task, dispatch = _seed(repository, postgres_environment, f"cancelled_truth_{outcome}")
    authorized = _authorize(repository, task.task_id)
    authorized = _start(repository, authorized)
    assert repository.suppress_dispatches(
        task_id=task.task_id,
        owner_id="owner",
        workspace_id="workspace",
        expected_task_revision=authorized.task_revision_after_action_started,
    ) == 0
    stopped = _task_row(postgres_environment, task.task_id)
    stopped_revision = stopped["revision"]

    assert repository.record_dispatch_outcome(
        authorized,
        owner_id="owner",
        workspace_id="workspace",
        outcome=outcome,
        **kwargs,
    ) is True

    task_row = _task_row(postgres_environment, task.task_id)
    dispatch_row = _dispatch_row(postgres_environment, dispatch.dispatch_id)
    assert task_row["revision"] == stopped_revision + 1
    assert dispatch_row["status"] == dispatch_status
    assert len(task_row["payload"]["capabilityCalls"]) == 1
    call = task_row["payload"]["capabilityCalls"][0]
    assert call["capabilityCallId"] == dispatch_capability_call_id(authorized)
    assert call["status"] == call_status
    assert (call["finishedAt"] is None) is (call_status == "running")
    assert task_row["payload"]["agentInteractionRecords"] == []


def test_outcome_requires_authorized_fence_and_exact_authorization_revision(
    repository, postgres_environment
) -> None:
    task, dispatch = _seed(repository, postgres_environment, "outcome_auth_revision")
    authorized = _authorize(repository, task.task_id)
    plain = DispatchFence(**{field: getattr(authorized, field) for field in DispatchFence.__dataclass_fields__})

    with pytest.raises(TypeError, match="AuthorizedDispatch"):
        repository.record_dispatch_outcome(
            plain,
            owner_id="owner",
            workspace_id="workspace",
            outcome="accepted",
            remote_run_id="plain-fence",
        )
    before_task = deepcopy(_task_row(postgres_environment, task.task_id))
    before_dispatch = deepcopy(_dispatch_row(postgres_environment, dispatch.dispatch_id))
    assert repository.record_dispatch_outcome(
        replace(authorized, authorization_task_revision=authorized.authorization_task_revision + 1),
        owner_id="owner",
        workspace_id="workspace",
        outcome="accepted",
        remote_run_id="wrong-auth-revision",
    ) is False
    assert _task_row(postgres_environment, task.task_id) == before_task
    assert _dispatch_row(postgres_environment, dispatch.dispatch_id) == before_dispatch


def test_outcome_sql_predicate_independently_fences_authorization_revision(
    repository,
    postgres_environment,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task, dispatch = _seed(repository, postgres_environment, "outcome_sql_auth_revision")
    authorized = _authorize(repository, task.task_id)
    before_task = deepcopy(_task_row(postgres_environment, task.task_id))
    before_dispatch = deepcopy(_dispatch_row(postgres_environment, dispatch.dispatch_id))
    monkeypatch.setattr(repository, "_matches_authorized_fence", lambda *_args, **_kwargs: True)

    with pytest.raises(ResearchOrchestrationPersistenceError, match="dispatch_outcome_race"):
        repository.record_dispatch_outcome(
            replace(authorized, authorization_task_revision=authorized.authorization_task_revision + 1),
            owner_id="owner",
            workspace_id="workspace",
            outcome="accepted",
            remote_run_id="wrong-sql-auth-revision",
        )

    assert _task_row(postgres_environment, task.task_id) == before_task
    assert _dispatch_row(postgres_environment, dispatch.dispatch_id) == before_dispatch


def test_cancelled_task_outcome_rolls_back_task_when_dispatch_write_fails(
    repository, postgres_environment
) -> None:
    task, dispatch = _seed(repository, postgres_environment, "cancelled_truth_rollback")
    authorized = _authorize(repository, task.task_id)
    authorized = _start(repository, authorized)
    repository.suppress_dispatches(
        task_id=task.task_id,
        owner_id="owner",
        workspace_id="workspace",
        expected_task_revision=authorized.task_revision_after_action_started,
    )
    before_task = deepcopy(_task_row(postgres_environment, task.task_id))
    before_dispatch = deepcopy(_dispatch_row(postgres_environment, dispatch.dispatch_id))
    with postgres_environment() as connection:
        connection.execute(
            """CREATE FUNCTION t04_r1_reject_submitted() RETURNS trigger LANGUAGE plpgsql AS $$
                   BEGIN
                       IF NEW.status = 'submitted' THEN
                           RAISE EXCEPTION 't04 forced dispatch write failure';
                       END IF;
                       RETURN NEW;
                   END
               $$"""
        )
        connection.execute(
            """CREATE TRIGGER t04_r1_reject_submitted
                   BEFORE UPDATE ON openair_dispatch
                   FOR EACH ROW EXECUTE FUNCTION t04_r1_reject_submitted()"""
        )
    try:
        with pytest.raises(psycopg.DatabaseError, match="t04 forced dispatch write failure"):
            repository.record_dispatch_outcome(
                authorized,
                owner_id="owner",
                workspace_id="workspace",
                outcome="accepted",
                remote_run_id="rollback-receipt",
            )
    finally:
        with postgres_environment() as connection:
            connection.execute("DROP TRIGGER IF EXISTS t04_r1_reject_submitted ON openair_dispatch")
            connection.execute("DROP FUNCTION IF EXISTS t04_r1_reject_submitted()")

    assert _task_row(postgres_environment, task.task_id) == before_task
    assert _dispatch_row(postgres_environment, dispatch.dispatch_id) == before_dispatch


def test_stop_suppresses_authorized_dispatch_before_recovery_can_restore_authority(
    repository, postgres_environment
) -> None:
    task, dispatch = _seed(repository, postgres_environment, "recovery_after_stop")
    authorized = _authorize(repository, task.task_id)
    assert repository.suppress_dispatches(
        task_id=task.task_id,
        owner_id="owner",
        workspace_id="workspace",
        expected_task_revision=1,
    ) == 1
    assert repository.mark_external_action_started(
        authorized,
        provider_id="openai_primary",
        adapter="huiyan_chat_completions",
    ) is None
    assert repository.claim_authorized_recovery(
        lease_owner="recovery-after-stop",
        lease_seconds=60,
        task_id=task.task_id,
        owner_id="owner",
        workspace_id="workspace",
    ) == []
    dispatch_row = _dispatch_row(postgres_environment, dispatch.dispatch_id)
    assert dispatch_row["status"] == "suppressed"
    assert dispatch_row["last_error_code"] == "task_stopped"


def test_suppression_committed_before_authorization_prevents_submission(
    repository, postgres_environment
) -> None:
    task, dispatch = _seed(repository, postgres_environment, "stop_first")
    fence = _claim(repository, task.task_id, "worker-a")
    assert fence is not None

    assert repository.suppress_dispatches(
        task_id=task.task_id,
        owner_id="owner",
        workspace_id="workspace",
        expected_task_revision=1,
    ) == 1
    assert repository.authorize_dispatch_submission(
        fence, owner_id="owner", workspace_id="workspace", expected_task_revision=1
    ) is None
    assert _dispatch_row(postgres_environment, dispatch.dispatch_id)["status"] == "suppressed"


def test_stop_after_authorization_suppresses_before_external_action_start(
    repository, postgres_environment
) -> None:
    task, dispatch = _seed(repository, postgres_environment, "stop_after_authorize")
    authorized = _authorize(repository, task.task_id)

    assert repository.suppress_dispatches(
        task_id=task.task_id,
        owner_id="owner",
        workspace_id="workspace",
        expected_task_revision=1,
    ) == 1
    assert repository.mark_external_action_started(
        authorized,
        provider_id="openai_primary",
        adapter="huiyan_chat_completions",
    ) is None
    dispatch_row = _dispatch_row(postgres_environment, dispatch.dispatch_id)
    assert dispatch_row["status"] == "suppressed"
    assert dispatch_row["external_action_started_at"] is None


@pytest.mark.parametrize(
    ("outcome", "kwargs", "expected_status"),
    (
        ("accepted", {"remote_run_id": "receipt-after-stop"}, "submitted"),
        ("unknown", {"error_code": "provider_response_lost"}, "submission_unknown"),
    ),
)
def test_external_action_started_before_suppression_preserves_late_truth(
    repository, postgres_environment, outcome, kwargs, expected_status
) -> None:
    task, dispatch = _seed(repository, postgres_environment, f"auth_first_{outcome}")
    fence = _claim(repository, task.task_id, "worker-a")
    assert fence is not None
    authorized = repository.authorize_dispatch_submission(
        fence, owner_id="owner", workspace_id="workspace", expected_task_revision=1
    )
    assert authorized is not None
    authorized = _start(repository, authorized)

    assert repository.suppress_dispatches(
        task_id=task.task_id,
        owner_id="owner",
        workspace_id="workspace",
        expected_task_revision=authorized.task_revision_after_action_started,
    ) == 0
    assert repository.record_dispatch_outcome(
        authorized,
        owner_id="owner",
        workspace_id="workspace",
        outcome=outcome,
        **kwargs,
    ) is True

    assert _dispatch_row(postgres_environment, dispatch.dispatch_id)["status"] == expected_status


def test_late_completed_result_after_stop_does_not_release_reviewer_dispatch(
    repository, postgres_environment, monkeypatch: pytest.MonkeyPatch
) -> None:
    import hashlib

    monkeypatch.setattr(repository, "load_registry_snapshot", lambda _hash: {})
    task, producer_dispatch, reviewer_assignment_id = _seed_reviewed_dispatch(
        repository, postgres_environment, "completed_after_stop"
    )
    authorized = _authorize(repository, task.task_id)
    authorized = _start(repository, authorized)
    assert repository.suppress_dispatches(
        task_id=task.task_id,
        owner_id="owner",
        workspace_id="workspace",
        expected_task_revision=authorized.task_revision_after_action_started,
    ) == 0
    content = '{"result":"validated after stop"}'

    result_storage_ref = (
        f"durable/research-dispatch/{producer_dispatch.dispatch_id}/result.json"
    )
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
            title="Late validated result",
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
            f"durable/research-dispatch/{producer_dispatch.dispatch_id}/artifact.json"
        ),
    ) is True
    task_row = _task_row(postgres_environment, task.task_id)
    assert task_row["status"] == task_row["payload"]["status"] == "cancelled"
    stop_receipt = task_row["payload"].get("stopReceipt")
    assert isinstance(stop_receipt, dict)
    assert stop_receipt["status"] == "stopped"
    assert stop_receipt["active"] is True
    assert _work_unit_status(task_row, producer_dispatch.work_unit_id)[0] == "completed"
    assert all(
        row["assignmentId"] != reviewer_assignment_id
        for row in task_row["payload"]["workUnits"]
    )
    with postgres_environment() as connection:
        rows = connection.execute(
            "SELECT status FROM openair_dispatch WHERE task_id = %s ORDER BY dispatch_id",
            (task.task_id,),
        ).fetchall()
        continuation = connection.execute(
            "SELECT status, last_error_code FROM openair_dispatch_continuation WHERE dispatch_id = %s",
            (producer_dispatch.dispatch_id,),
        ).fetchone()
    assert [row["status"] for row in rows] == ["submitted"]
    assert continuation == {"status": "pending", "last_error_code": None}

    service = ResearchDispatchService(repository)
    assert service.process_next_dispatch_continuation(
        lease_owner="continuation-after-stop",
        lease_seconds=30,
    ) is True
    with postgres_environment() as connection:
        stopped_continuation = connection.execute(
            "SELECT status, last_error_code FROM openair_dispatch_continuation WHERE dispatch_id = %s",
            (producer_dispatch.dispatch_id,),
        ).fetchone()
    assert stopped_continuation == {
        "status": "completed",
        "last_error_code": "task_stopped",
    }

    receipt = repository.resume_suppressed_dispatches_with_receipt(
        task_id=task.task_id,
        owner_id="owner",
        workspace_id="workspace",
        expected_task_revision=task_row["revision"],
    )
    assert receipt["resumedContinuationCount"] == 1
    resumed_task = _task_row(postgres_environment, task.task_id)
    assert resumed_task["status"] == resumed_task["payload"]["status"] == "running"
    with postgres_environment() as connection:
        resumed_continuation = connection.execute(
            "SELECT status FROM openair_dispatch_continuation WHERE dispatch_id = %s",
            (producer_dispatch.dispatch_id,),
        ).fetchone()
        source_counts = connection.execute(
            """SELECT
                   count(*) FILTER (WHERE dispatch_id = %s) AS dispatch_count,
                   count(DISTINCT work_unit_id) FILTER (WHERE dispatch_id = %s) AS work_unit_count
                 FROM openair_dispatch
                WHERE task_id = %s""",
            (
                producer_dispatch.dispatch_id,
                producer_dispatch.dispatch_id,
                task.task_id,
            ),
        ).fetchone()
    assert resumed_continuation["status"] == "pending"
    assert source_counts == {"dispatch_count": 1, "work_unit_count": 1}


def test_wrong_revision_and_dispatch_lineage_block_authorization(repository, postgres_environment) -> None:
    task, dispatch = _seed(repository, postgres_environment, "bad_auth")
    fence = _claim(repository, task.task_id, "worker-a")
    assert fence is not None
    with pytest.raises(DispatchAuthorizationError, match="task_revision_mismatch"):
        repository.authorize_dispatch_submission(
            fence, owner_id="owner", workspace_id="workspace", expected_task_revision=99
        )

    wrong = replace(fence, execution_decision_id="wrong-decision")
    assert repository.authorize_dispatch_submission(
        wrong, owner_id="owner", workspace_id="workspace", expected_task_revision=1
    ) is None
    assert _dispatch_row(postgres_environment, dispatch.dispatch_id)["status"] == "claimed"


@pytest.mark.parametrize("design_status", (ResearchDesignStatus.DRAFT, ResearchDesignStatus.SUPERSEDED))
def test_draft_or_superseded_design_and_wrong_instance_block_authorization_fixture(
    design_status,
) -> None:
    _baseline, task, dispatch = _transaction_fixture(f"auth_guard_{design_status.value}")
    row = dispatch.model_dump(mode="python")
    design = task.research_design_versions[0].model_copy(update={"status": design_status})
    invalid_design_task = task.model_copy(update={"research_design_versions": [design]})

    with pytest.raises(DispatchAuthorizationError, match="design_not_active_confirmed"):
        ResearchOrchestrationRepository._validate_authorization(invalid_design_task, row)

    decision = task.execution_decisions[0].model_copy(update={"substage_instance_id": "wrong-instance"})
    wrong_instance_task = task.model_copy(update={"execution_decisions": [decision]})
    with pytest.raises(DispatchAuthorizationError, match="instance_lineage_mismatch"):
        ResearchOrchestrationRepository._validate_authorization(wrong_instance_task, row)


def test_duplicate_active_selection_identity_cannot_authorize_or_create_pending_dispatch() -> None:
    _baseline, task, dispatch = _transaction_fixture("duplicate_selection")
    decision = task.execution_decisions[0]
    duplicate = decision.model_copy(
        update={
            "execution_decision_id": f"{decision.execution_decision_id}-duplicate",
            "decision_sequence": decision.decision_sequence + 1,
        }
    )
    ambiguous = task.model_copy(update={"execution_decisions": [decision, duplicate]})
    row = dispatch.model_dump(mode="python")

    with pytest.raises(DispatchAuthorizationError, match="decision_not_unique_active"):
        ResearchOrchestrationRepository._validate_authorization(ambiguous, row)
    with pytest.raises(ValueError, match="one active Decision per selection identity"):
        ResearchOrchestrationRepository._validate_pending_dispatches(ambiguous, [dispatch])


def test_unique_dispatch_conflict_rolls_back_task_cas_and_outbox(repository, postgres_environment) -> None:
    _task_a, dispatch_a = _seed(repository, postgres_environment, "unique_a")
    baseline_b, candidate_b, dispatch_b = _transaction_fixture("unique_b")
    payload_b = candidate_b.model_dump(mode="json", by_alias=True)
    payload_b["workUnits"][0]["idempotencyKey"] = dispatch_a.idempotency_key
    candidate_b = ResearchTaskV12.model_validate(payload_b)
    dispatch_b = DispatchRecord.model_validate(
        {**dispatch_b.model_dump(mode="json", by_alias=True), "idempotencyKey": dispatch_a.idempotency_key}
    )
    _insert_task(postgres_environment, baseline_b)

    with pytest.raises(DispatchIdentityConflictError):
        repository.save(
            candidate_b,
            owner_id="owner",
            workspace_id="workspace",
            expected_task_revision=0,
            pending_dispatches=[dispatch_b],
            automatic_dispatch=True,
        )

    row = _task_row(postgres_environment, baseline_b.task_id)
    assert row["revision"] == 0
    assert row["payload"]["executionDecisions"] == []
    assert _dispatch_row(postgres_environment, dispatch_b.dispatch_id) is None


def test_only_live_claimed_fence_can_fail_before_submission(repository, postgres_environment) -> None:
    task, dispatch = _seed(repository, postgres_environment, "preflight_failed")
    fence = _claim(repository, task.task_id, "worker-a")
    assert fence is not None

    assert repository.fail_unsubmitted_dispatch(
        fence,
        owner_id="owner",
        workspace_id="workspace",
        error_code="provider_preflight_failed",
    ) is True
    revision = _task_row(postgres_environment, task.task_id)["revision"]
    assert repository.fail_unsubmitted_dispatch(
        replace(fence, attempt_count=fence.attempt_count + 1),
        owner_id="owner",
        workspace_id="workspace",
        error_code="stale_failure",
    ) is False
    row = _dispatch_row(postgres_environment, dispatch.dispatch_id)
    assert row["status"] == "failed"
    assert row["last_error_code"] == "provider_preflight_failed"
    assert _task_row(postgres_environment, task.task_id)["revision"] == revision


def test_user_input_blocker_survives_reload_with_reason_projection(
    repository, postgres_environment
) -> None:
    baseline, _candidate, _dispatch = _transaction_fixture("user_input_blocker")
    baseline = ResearchTaskV12.model_validate(
        _replace_registry_snapshot_refs(
            baseline.model_dump(mode="json", by_alias=True),
            _canonical_test_registry_snapshot_ref(postgres_environment),
        )
    )
    _insert_task(postgres_environment, baseline)

    updated = repository.record_needs_input(
        task_id=baseline.task_id,
        owner_id="owner",
        workspace_id="workspace",
        expected_task_revision=0,
        substage_instance_id=baseline.substage_runtime_states[0].substage_instance_id,
        reason_code="required_input_missing",
        selection_request_id="selection-user-input-blocker",
        selection_command_hash="sha256:" + hashlib.sha256(b"selection-user-input-command").hexdigest(),
        blocker_facts={"inputRefs": ["material_missing"]},
    )
    reloaded = repository.load(
        task_id=baseline.task_id,
        owner_id="owner",
        workspace_id="workspace",
    )

    assert updated.revision == 1
    assert reloaded.revision == 1
    assert reloaded.substage_runtime_states[0].status.value == "needs_input"
    assert reloaded.substage_runtime_states[0].status_reason_code == "required_input_missing"
    assert reloaded.execution_decisions == []
    assert reloaded.work_units == []
    with postgres_environment() as connection:
        attempt = connection.execute(
            """SELECT operation, idempotency_key_sha256, outcome, canonical_run_id
               FROM openair_execution_start_attempts
               WHERE task_id = %s AND owner_id = %s AND workspace_id = %s""",
            (baseline.task_id, "owner", "workspace"),
        ).fetchone()
    assert attempt["operation"] == "research_execution_selection"
    assert attempt["idempotency_key_sha256"] == hashlib.sha256(
        b"selection-user-input-blocker"
    ).hexdigest()
    blocker = json.loads(attempt["outcome"].removeprefix("blocked:"))
    assert blocker == {
        "facts": {"inputRefs": ["material_missing"], "taskRevision": 1},
        "reasonCode": "required_input_missing",
        "selectionCommandHash": "sha256:" + hashlib.sha256(b"selection-user-input-command").hexdigest(),
        "substageInstanceId": baseline.substage_runtime_states[0].substage_instance_id,
    }
    assert attempt["canonical_run_id"] is None

    same = repository.lookup_selection_replay(
        task_id=baseline.task_id,
        owner_id="owner",
        workspace_id="workspace",
        selection_request_id="selection-user-input-blocker",
        selection_command_hash="sha256:" + hashlib.sha256(b"selection-user-input-command").hexdigest(),
    )
    assert same.reason_code == "required_input_missing"
    assert same.facts == {"inputRefs": ["material_missing"], "taskRevision": 1}

    route_replay = repository.lookup_selection_replay_evidence(
        task_id=baseline.task_id,
        owner_id="owner",
        workspace_id="workspace",
        selection_request_id="selection-user-input-blocker",
    )
    assert route_replay is not None
    assert route_replay.instance_id == baseline.substage_runtime_states[0].substage_instance_id
    assert route_replay.selection_command_hash == (
        "sha256:" + hashlib.sha256(b"selection-user-input-command").hexdigest()
    )
    assert route_replay.replay.reason_code == "required_input_missing"

    assert repository.lookup_selection_replay(
        task_id=baseline.task_id,
        owner_id="owner",
        workspace_id="workspace",
        selection_request_id="selection-brand-new",
        selection_command_hash="sha256:" + hashlib.sha256(b"brand-new-command").hexdigest(),
    ) is None


def test_concurrent_same_user_input_selection_replays_under_final_task_lock(
    repository, postgres_environment
) -> None:
    from app.modules.research_execution_decision_service import BlockedSelectionReplay

    baseline, _candidate, _dispatch = _transaction_fixture("concurrent_user_input_blocker")
    baseline = ResearchTaskV12.model_validate(
        _replace_registry_snapshot_refs(
            baseline.model_dump(mode="json", by_alias=True),
            _canonical_test_registry_snapshot_ref(postgres_environment),
        )
    )
    _insert_task(postgres_environment, baseline)
    selection_request_id = "selection-concurrent-user-input"
    selection_command_hash = "sha256:" + hashlib.sha256(
        b"selection-concurrent-user-input-command"
    ).hexdigest()
    instance_id = baseline.substage_runtime_states[0].substage_instance_id
    barrier = Barrier(2)

    def commit_blocker():
        barrier.wait(timeout=10)
        return repository.record_needs_input(
            task_id=baseline.task_id,
            owner_id="owner",
            workspace_id="workspace",
            expected_task_revision=0,
            substage_instance_id=instance_id,
            reason_code="required_input_missing",
            selection_request_id=selection_request_id,
            selection_command_hash=selection_command_hash,
            blocker_facts={"inputRefs": ["material_missing"]},
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = [future.result(timeout=20) for future in [
            executor.submit(commit_blocker),
            executor.submit(commit_blocker),
        ]]

    reloaded = repository.load(
        task_id=baseline.task_id,
        owner_id="owner",
        workspace_id="workspace",
    )
    assert reloaded.revision == 1
    assert reloaded.substage_runtime_states[0].status.value == "needs_input"
    assert reloaded.substage_runtime_states[0].status_reason_code == "required_input_missing"
    assert sum(isinstance(outcome, BlockedSelectionReplay) for outcome in outcomes) == 1
    replay = next(outcome for outcome in outcomes if isinstance(outcome, BlockedSelectionReplay))
    assert replay.reason_code == "required_input_missing"
    assert replay.facts == {"inputRefs": ["material_missing"], "taskRevision": 1}

    with postgres_environment() as connection:
        rows = connection.execute(
            """SELECT outcome
               FROM openair_execution_start_attempts
               WHERE task_id = %s AND owner_id = %s AND workspace_id = %s
                 AND operation = 'research_execution_selection'
                 AND idempotency_key_sha256 = %s""",
            (
                baseline.task_id,
                "owner",
                "workspace",
                hashlib.sha256(selection_request_id.encode("utf-8")).hexdigest(),
            ),
        ).fetchall()
    assert len(rows) == 1
    parsed = json.loads(rows[0]["outcome"].removeprefix("blocked:"))
    assert parsed["selectionCommandHash"] == selection_command_hash
    assert parsed["facts"] == {"inputRefs": ["material_missing"], "taskRevision": 1}


def test_retryable_pre_submission_failure_is_bounded_and_durable(
    repository, postgres_environment
) -> None:
    task, dispatch = _seed(repository, postgres_environment, "bounded_retry")
    first = _claim(repository, task.task_id, "worker-a")
    assert first is not None

    assert repository.record_dispatch_retryable_failure(
        first,
        owner_id="owner",
        workspace_id="workspace",
        error_code="provider_temporarily_unavailable",
        max_attempts=2,
        retry_delay_seconds=30,
    ) == "retry_scheduled"
    row = _dispatch_row(postgres_environment, dispatch.dispatch_id)
    assert row["status"] == "pending"
    assert row["attempt_count"] == 1
    assert row["next_attempt_at"] is not None
    assert row["last_failure_at"] is not None
    assert row["last_error_code"] == "provider_temporarily_unavailable"
    assert _claim(repository, task.task_id, "worker-too-early") is None

    with _namespaced_dispatch_mutation_connection(postgres_environment) as connection:
        connection.execute(
            "UPDATE openair_dispatch SET next_attempt_at = clock_timestamp() WHERE dispatch_id = %s",
            (dispatch.dispatch_id,),
        )
    second = _claim(repository, task.task_id, "worker-b")
    assert second is not None
    assert second.attempt_count == 2
    assert repository.record_dispatch_retryable_failure(
        second,
        owner_id="owner",
        workspace_id="workspace",
        error_code="provider_still_unavailable",
        max_attempts=2,
        retry_delay_seconds=30,
    ) == "failed"

    row = _dispatch_row(postgres_environment, dispatch.dispatch_id)
    assert row["status"] == "failed"
    assert row["next_attempt_at"] is None
    task_row = _task_row(postgres_environment, task.task_id)
    assert _work_unit_status(task_row, dispatch.work_unit_id) == (
        "blocked",
        "provider_still_unavailable",
    )


def test_retry_settlement_never_replays_after_external_action_started(
    repository, postgres_environment
) -> None:
    task, dispatch = _seed(repository, postgres_environment, "retry_no_replay")
    authorized = _authorize(repository, task.task_id)
    started = _start(repository, authorized)

    assert repository.record_dispatch_retryable_failure(
        started,
        owner_id="owner",
        workspace_id="workspace",
        error_code="late_worker_error",
        max_attempts=5,
        retry_delay_seconds=30,
    ) == "submission_unknown"

    row = _dispatch_row(postgres_environment, dispatch.dispatch_id)
    assert row["status"] == "submission_unknown"
    assert row["external_action_started_at"] is not None
    assert row["next_attempt_at"] is None
    assert row["lease_owner"] is None
    assert repository.claim_authorized_recovery(
        lease_owner="worker-recovery",
        lease_seconds=60,
        task_id=task.task_id,
        owner_id="owner",
        workspace_id="workspace",
    ) == []
    task_row = _task_row(postgres_environment, task.task_id)
    assert _work_unit_status(task_row, dispatch.work_unit_id) == (
        "external_result_unknown",
        "late_worker_error",
    )
    with postgres_environment() as connection:
        evidence_count = connection.execute(
            """SELECT count(*) AS count FROM openair_external_evidence
                WHERE dispatch_id = %s
                  AND normalized_finish_reason = 'late_worker_error'""",
            (dispatch.dispatch_id,),
        ).fetchone()["count"]
        continuation = connection.execute(
            """SELECT status FROM openair_dispatch_continuation
                WHERE dispatch_id = %s""",
            (dispatch.dispatch_id,),
        ).fetchone()
    assert evidence_count == 1
    assert continuation == {"status": "pending"}


@pytest.mark.parametrize(
    ("outcome", "expected_status"),
    (
        ("known_rejected", "failed"),
        ("unknown", "submission_unknown"),
    ),
)
def test_nonaccepted_dispatch_outcome_types_nullable_postgres_parameters(
    repository, postgres_environment, monkeypatch: pytest.MonkeyPatch, outcome, expected_status
) -> None:
    """Regression: nullable outcome params must have explicit PostgreSQL types."""
    task, dispatch = _seed(repository, postgres_environment, f"typed_nonaccepted_{outcome}")
    authorized = _start(repository, _authorize(repository, task.task_id))
    monkeypatch.setattr(
        repository,
        "_reconcile_task_lifecycle_with_frozen_snapshot",
        lambda task_value, **_kwargs: task_value,
    )

    assert repository.record_dispatch_outcome(
        authorized,
        owner_id="owner",
        workspace_id="workspace",
        outcome=outcome,
        remote_run_id=None,
        error_code="single_attempt_result_unknown",
    ) is True

    row = _dispatch_row(postgres_environment, dispatch.dispatch_id)
    assert row["status"] == expected_status
    assert row["remote_run_id"] is None
    assert row["last_error_code"] == "single_attempt_result_unknown"
    assert row["lease_owner"] is None
    assert row["lease_expires_at"] is None
    assert row["next_attempt_at"] is None


@pytest.mark.parametrize(
    ("outcome", "error_code", "expected_dispatch", "expected_external", "expected_work", "expected_call"),
    (
        ("unknown", "provider_query_result_unknown", "submission_unknown", "unknown", "external_result_unknown", "external_result_unknown"),
        ("running", None, "submitted", "running", "running", "running"),
        ("completed", "external_execution_result_storage_failed", "submitted", "completed", "reviewing", "succeeded"),
    ),
)
def test_recovery_facts_preserve_provider_truth_through_canonical_reducer(
    repository,
    postgres_environment,
    monkeypatch: pytest.MonkeyPatch,
    outcome,
    error_code,
    expected_dispatch,
    expected_external,
    expected_work,
    expected_call,
) -> None:
    task, dispatch = _seed(repository, postgres_environment, f"recovery_truth_{outcome}")
    authorized = _start(repository, _authorize(repository, task.task_id))
    reducer_calls = []

    def reconcile(task_value, **kwargs):
        reducer_calls.append((task_value, kwargs))
        # The shared transaction fixture intentionally has no Registry snapshot;
        # T3A owns reducer behavior. This verifies T2 hands facts to that sole sink.
        return task_value

    monkeypatch.setattr(
        repository,
        "_reconcile_task_lifecycle_with_frozen_snapshot",
        reconcile,
    )

    assert repository.record_dispatch_outcome(
        authorized,
        owner_id="owner",
        workspace_id="workspace",
        outcome=outcome,
        remote_run_id="physical-attempt-1",
        error_code=error_code,
    ) is True

    dispatch_row = _dispatch_row(postgres_environment, dispatch.dispatch_id)
    task_row = _task_row(postgres_environment, task.task_id)
    work = next(
        row for row in task_row["payload"]["workUnits"]
        if row["workUnitId"] == dispatch.work_unit_id
    )
    call = next(
        row for row in task_row["payload"]["capabilityCalls"]
        if row["workUnitId"] == dispatch.work_unit_id
    )
    with postgres_environment() as connection:
        external = connection.execute(
            "SELECT external_state, remote_run_id, failure_code "
            "FROM openair_external_execution WHERE dispatch_id = %s",
            (dispatch.dispatch_id,),
        ).fetchone()

    assert len(reducer_calls) == 1
    assert dispatch_row["status"] == expected_dispatch
    assert external["external_state"] == expected_external
    assert external["remote_run_id"] == "physical-attempt-1"
    assert external["failure_code"] == error_code
    assert work["status"] == expected_work
    assert call["status"] == expected_call
    assert work["status"] != "failed"


def test_started_provider_unavailable_recovery_settles_once_and_is_not_reclaimed(
    repository, postgres_environment, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Real PostgreSQL proof for the reviewer action-start recovery failure.

    The physical provider action is modelled as having been invoked exactly once
    before the worker crashed. Recovery loses provider authority, must settle to
    submission_unknown, and subsequent polls must not claim or submit again.
    """
    from app.modules.research_dispatch_service import ResearchDispatchService
    from worker import entrypoint

    task, dispatch = _seed(repository, postgres_environment, "started_provider_lost")
    authorized = _authorize(repository, task.task_id)
    started = _start(repository, authorized)
    with _namespaced_dispatch_mutation_connection(postgres_environment) as connection:
        connection.execute(
            "UPDATE openair_dispatch SET lease_expires_at = clock_timestamp() - interval '1 second' "
            "WHERE dispatch_id = %s",
            (dispatch.dispatch_id,),
        )

    binding = entrypoint.DispatchProviderBinding(
        provider_id="provider-lost-after-start",
        dispatch_id=started.dispatch_id,
        task_id=started.task_id,
        execution_decision_id=started.execution_decision_id,
        work_unit_id=started.work_unit_id,
        registry_snapshot_ref={"registryId": "providers", "version": 1},
        executor_ref={"executorType": "agent", "executorId": "executor-1"},
        responsible_actor_ref={"actorType": "agent", "actorId": "actor-1"},
        owner_id="owner",
        workspace_id="workspace",
        expected_task_revision=started.authorization_task_revision,
    )

    class Resolver:
        def resolve(self):
            return binding

    class UnavailableFactory:
        create_calls = 0
        submit_calls = 1  # the one action that occurred before the simulated crash

        def create(self, _provider_id):
            self.create_calls += 1
            return None

    factory = UnavailableFactory()
    service = ResearchDispatchService(repository=repository)
    monkeypatch.setattr(
        repository,
        "_reconcile_task_lifecycle_with_frozen_snapshot",
        lambda task_value, **_kwargs: task_value,
    )
    monkeypatch.setattr(entrypoint.settings, "research_orchestration_v1", True)
    monkeypatch.setattr(entrypoint.settings, "persistence_backend", "postgres")

    assert entrypoint._claim_and_run_research_dispatch(
        binding_resolver=Resolver(), provider_factory=factory, service=service
    ) is True
    assert entrypoint._claim_and_run_research_dispatch(
        binding_resolver=Resolver(), provider_factory=factory, service=service
    ) is False
    assert entrypoint._claim_and_run_research_dispatch(
        binding_resolver=Resolver(), provider_factory=factory, service=service
    ) is False

    row = _dispatch_row(postgres_environment, dispatch.dispatch_id)
    assert row["status"] == "submission_unknown"
    assert row["external_action_started_at"] is not None
    assert row["last_error_code"] == "dispatch_provider_unavailable"
    assert row["lease_owner"] is None
    assert row["next_attempt_at"] is None
    assert factory.create_calls == 1
    assert factory.submit_calls == 1


def test_callback_authority_is_durable_and_epoch_fenced(postgres_environment) -> None:
    from app.execution.worker_callback_authority import (
        WorkerCallbackAuthority,
        WorkerCallbackAuthorityRepository,
    )

    first_replica = WorkerCallbackAuthorityRepository(
        connection_factory=postgres_environment
    )
    second_replica = WorkerCallbackAuthorityRepository(
        connection_factory=postgres_environment
    )
    authority = WorkerCallbackAuthority(
        execution_id="exec-durable-1",
        task_id="task-1",
        owner_id="owner-1",
        execution_epoch="epoch-1",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
    )

    first_replica.issue(authority)
    assert second_replica.load_active(authority.execution_id) == authority

    with pytest.raises(RuntimeError, match="callback_authority_identity_conflict"):
        second_replica.issue(
            replace(authority, execution_epoch="epoch-stale-reuse")
        )

    second_replica.revoke(authority.execution_id, authority.execution_epoch)
    assert first_replica.load_active(authority.execution_id) is None


def test_submitted_unknown_and_suppressed_are_terminal_against_failed_overwrite(
    repository, postgres_environment
) -> None:
    for suffix, outcome, kwargs, expected in (
        ("terminal_submitted", "accepted", {"remote_run_id": "receipt"}, "submitted"),
        ("terminal_unknown", "unknown", {"error_code": "lost_response"}, "submission_unknown"),
        ("terminal_failed", "known_rejected", {"error_code": "provider_http_400"}, "failed"),
    ):
        task, dispatch = _seed(repository, postgres_environment, suffix)
        fence = _claim(repository, task.task_id, "worker-a")
        authorized = repository.authorize_dispatch_submission(
            fence, owner_id="owner", workspace_id="workspace", expected_task_revision=1
        )
        assert authorized is not None
        authorized = _start(repository, authorized)
        assert repository.record_dispatch_outcome(
            authorized, owner_id="owner", workspace_id="workspace", outcome=outcome, **kwargs
        ) is True
        assert repository.record_dispatch_outcome(
            authorized,
            owner_id="owner",
            workspace_id="workspace",
            outcome="known_rejected",
            error_code="late_failure",
        ) is False
        assert _dispatch_row(postgres_environment, dispatch.dispatch_id)["status"] == expected

    task, dispatch = _seed(repository, postgres_environment, "terminal_suppressed")
    assert repository.suppress_dispatches(
        task_id=task.task_id,
        owner_id="owner",
        workspace_id="workspace",
        expected_task_revision=1,
    ) == 1
    row = _dispatch_row(postgres_environment, dispatch.dispatch_id)
    assert row["status"] == "suppressed"
    assert row["last_error_code"] == "task_stopped"
    stale_claim = repository.claim_dispatches(
        lease_owner="worker-late",
        lease_seconds=60,
        task_id=task.task_id,
        owner_id="owner",
        workspace_id="workspace",
    )
    assert stale_claim == []




def test_started_resolver_failure_settles_unknown_and_never_reenters_candidate_window(
    repository, postgres_environment, monkeypatch
) -> None:
    """Real PostgreSQL gate for resolver failure after external action-start.

    A prior worker submitted exactly once and crashed.  If frozen binding/design
    authority is unavailable during resolver polling, the row must become an
    absorbing unknown result and never be offered for claim or submission again.
    """
    from psycopg.conninfo import conninfo_to_dict
    from worker import entrypoint as worker_entrypoint

    params = conninfo_to_dict(os.environ["OPENAIR_T04_POSTGRES_DSN"])
    monkeypatch.setattr(worker_entrypoint.settings, "postgres_host", params["host"])
    monkeypatch.setattr(worker_entrypoint.settings, "postgres_port", int(params["port"]))
    monkeypatch.setattr(worker_entrypoint.settings, "postgres_database", params["dbname"])
    monkeypatch.setattr(worker_entrypoint.settings, "postgres_user", params["user"])
    monkeypatch.setattr(worker_entrypoint.settings, "postgres_password", params["password"])

    task, dispatch = _seed(repository, postgres_environment, "started_resolver_lost")
    authorized = _authorize(repository, task.task_id)
    started = _start(repository, authorized)
    with _namespaced_dispatch_mutation_connection(postgres_environment) as connection:
        connection.execute(
            "UPDATE openair_dispatch SET lease_expires_at = clock_timestamp() - interval '1 second' "
            "WHERE dispatch_id = %s",
            (dispatch.dispatch_id,),
        )
        current_revision = connection.execute(
            "SELECT revision FROM openair_tasks WHERE task_id = %s",
            (task.task_id,),
        ).fetchone()["revision"]

    provider_submit_calls = 1
    assert repository.record_dispatch_resolution_failure(
        dispatch_id=started.dispatch_id,
        task_id=started.task_id,
        execution_decision_id=started.execution_decision_id,
        work_unit_id=started.work_unit_id,
        idempotency_key=started.idempotency_key,
        owner_id="owner",
        workspace_id="workspace",
        expected_task_revision=int(current_revision),
        error_code="dispatch_binding_drift",
        retryable=False,
        max_attempts=2,
        retry_delay_seconds=1,
    ) == "submission_unknown"

    row = _dispatch_row(postgres_environment, dispatch.dispatch_id)
    assert row["status"] == "submission_unknown"
    assert row["external_action_started_at"] is not None
    assert row["lease_owner"] is None
    assert row["lease_expires_at"] is None
    assert row["next_attempt_at"] is None
    task_row = _task_row(postgres_environment, task.task_id)
    assert _work_unit_status(task_row, dispatch.work_unit_id) == (
        "external_result_unknown",
        "dispatch_binding_drift",
    )

    resolver = worker_entrypoint.PostgresDispatchProviderBindingResolver(repository=repository)
    assert resolver.resolve() is None
    assert resolver.resolve() is None
    assert repository.claim_authorized_recovery(
        lease_owner="worker-recovery",
        lease_seconds=60,
        task_id=task.task_id,
        owner_id="owner",
        workspace_id="workspace",
    ) == []
    assert provider_submit_calls == 1


def test_missing_work_unit_poison_dispatch_is_quarantined_and_valid_dispatch_remains_claimable(
    repository, postgres_environment
) -> None:
    poison_task, poison_dispatch = _seed(
        repository, postgres_environment, "missing_work_unit_poison"
    )
    valid_task, valid_dispatch = _seed(
        repository, postgres_environment, "missing_work_unit_valid_after_poison"
    )
    missing_work_unit_id = f"{poison_dispatch.work_unit_id}-missing"
    with _namespaced_dispatch_mutation_connection(postgres_environment) as connection:
        connection.execute(
            "UPDATE openair_dispatch SET work_unit_id = %s WHERE dispatch_id = %s",
            (missing_work_unit_id, poison_dispatch.dispatch_id),
        )
        current_revision = int(
            connection.execute(
                "SELECT revision FROM openair_tasks WHERE task_id = %s",
                (poison_task.task_id,),
            ).fetchone()["revision"]
        )

    assert repository.record_dispatch_resolution_failure(
        dispatch_id=poison_dispatch.dispatch_id,
        task_id=poison_task.task_id,
        execution_decision_id=poison_dispatch.execution_decision_id,
        work_unit_id=missing_work_unit_id,
        idempotency_key=poison_dispatch.idempotency_key,
        owner_id="owner",
        workspace_id="workspace",
        expected_task_revision=current_revision,
        error_code="dispatch_contract_mismatch",
        retryable=False,
        max_attempts=2,
        retry_delay_seconds=1,
    ) == "failed"

    poison_row = _dispatch_row(postgres_environment, poison_dispatch.dispatch_id)
    assert poison_row["status"] == "failed"
    assert poison_row["last_error_code"] == "dispatch_work_unit_missing"
    assert poison_row["lease_owner"] is None
    assert poison_row["lease_expires_at"] is None
    assert poison_row["next_attempt_at"] is None
    blocker = _integrity_blocker(
        _task_row(postgres_environment, poison_task.task_id),
        poison_dispatch.dispatch_id,
    )
    assert blocker["reasonCode"] == "dispatch_work_unit_missing"
    assert blocker["status"] == "blocked_recoverable"
    assert blocker["allowedActions"] == ["rebuild_execution_assignment"]

    assert poison_dispatch.dispatch_id not in _due_dispatch_ids(postgres_environment)
    assert repository.claim_dispatches(
        lease_owner="worker-poison-recheck",
        lease_seconds=60,
        task_id=poison_task.task_id,
        owner_id="owner",
        workspace_id="workspace",
    ) == []
    valid_claim = _claim(repository, valid_task.task_id, "worker-valid-after-poison")
    assert valid_claim is not None
    assert valid_claim.dispatch_id == valid_dispatch.dispatch_id


def test_durable_run_authority_is_task_owner_worker_and_epoch_fenced(
    repository, postgres_environment
) -> None:
    """Real PostgreSQL authority used before durable callback/upload mutations."""
    task, _dispatch = _seed(repository, postgres_environment, "durable_run_token_authority")
    run_id = "run-durable-token-authority"
    with postgres_environment() as connection:
        connection.execute(
            """INSERT INTO openair_execution_runs(
                   run_id, task_id, owner_id, workspace_id, task_revision, run_number,
                   run_kind, idempotency_key, request_fingerprint, input_snapshot,
                   target_substage, source_mode, capability_call_id, state,
                   lease_owner, lease_epoch, lease_expires_at
               ) VALUES (%s,%s,'owner','workspace',1,1,'initial_start',%s,%s,%s::jsonb,
                         'execution_validation','live',%s,'leased','worker-1',7,
                         clock_timestamp() + interval '5 minutes')""",
            (
                run_id,
                task.task_id,
                "durable-token-key",
                "d" * 64,
                '{"question":"q"}',
                "call-durable-token-authority",
            ),
        )

    durable = DurableExecutionRepository()
    durable._connect = postgres_environment  # type: ignore[method-assign]
    authority = durable.load_active_worker_authority(run_id, "worker-1", 7)
    assert authority is not None
    assert authority == {
        "run_id": run_id,
        "task_id": task.task_id,
        "owner_id": "owner",
        "worker_id": "worker-1",
        "lease_epoch": 7,
        "lease_expires_at": authority["lease_expires_at"],
    }
    assert durable.load_active_worker_authority(run_id, "worker-attacker", 7) is None
    assert durable.load_active_worker_authority(run_id, "worker-1", 6) is None

    with postgres_environment() as connection:
        connection.execute(
            "UPDATE openair_execution_runs SET lease_expires_at = clock_timestamp() - interval '1 second' "
            "WHERE run_id = %s",
            (run_id,),
        )
    assert durable.load_active_worker_authority(run_id, "worker-1", 7) is None


def test_explicit_retry_requeues_dispatch_with_matching_external_execution_identity(
    repository,
    postgres_environment,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from psycopg.conninfo import conninfo_to_dict

    checkpoint_database = conninfo_to_dict(os.environ["OPENAIR_T04_POSTGRES_DSN"])
    monkeypatch.setattr(settings, "postgres_host", checkpoint_database["host"])
    monkeypatch.setattr(settings, "postgres_port", int(checkpoint_database["port"]))
    monkeypatch.setattr(settings, "postgres_user", checkpoint_database["user"])
    monkeypatch.setattr(settings, "postgres_password", checkpoint_database["password"])
    monkeypatch.setattr(settings, "postgres_database", checkpoint_database["dbname"])
    task, dispatch = _seed(repository, postgres_environment, "retry_external_identity")
    authorized = _authorize(repository, task.task_id, "worker-retry-identity")
    started = _start(repository, authorized)
    assert repository.record_dispatch_outcome(
        started,
        owner_id="owner",
        workspace_id="workspace",
        outcome="known_failed",
        error_code=STRUCTURED_OUTPUT_CORRECTION_EXHAUSTED,
        provider_id="openai_primary",
        adapter="huiyan_chat_completions",
    )
    latest = repository.load(
        task_id=task.task_id,
        owner_id="owner",
        workspace_id="workspace",
    )
    assert latest is not None
    work_unit = latest.work_units[0]
    fingerprint = ResearchOrchestrationRepository._canonical_fingerprint(
        work_unit.model_dump(mode="json", by_alias=True)
    )
    prior_call_id = started.external_attempt.capability_call_id
    command = ExplicitRetryCommand(
        task_id=task.task_id,
        work_unit_id=dispatch.work_unit_id,
        dispatch_id=dispatch.dispatch_id,
        execution_decision_id=dispatch.execution_decision_id,
        prior_capability_call_id=prior_call_id,
        prior_attempt_number=1,
        command_request_id="retry-external-identity-1",
        canonical_payload_fingerprint=fingerprint,
        expected_task_revision=latest.revision,
        owner_id="owner",
        workspace_id="workspace",
        actor_id="owner",
        reason_code=STRUCTURED_OUTPUT_EXPLICIT_RETRY_REASON,
    )
    retry_result = repository.create_retry_attempt(command)
    assert retry_result["replayed"] is False
    assert retry_result["attemptNumber"] == 2

    # Deliberately make the Task projection point at attempt 1. Execution must
    # still use the current durable attempt bound by the retry command.
    with postgres_environment() as connection:
        task_row = connection.execute(
            "SELECT payload FROM openair_tasks WHERE task_id = %s",
            (task.task_id,),
        ).fetchone()
        conflicting_payload = dict(task_row["payload"])
        for call in conflicting_payload["capabilityCalls"]:
            if call["capabilityCallId"] == prior_call_id:
                call["status"] = "queued"
                call["finishedAt"] = None
            elif call["capabilityCallId"] == retry_result["capabilityCallId"]:
                call["status"] = "failed"
                call["finishedAt"] = call["startedAt"]
        connection.execute(
            "UPDATE openair_tasks SET payload = %s::jsonb WHERE task_id = %s",
            (json.dumps(conflicting_payload), task.task_id),
        )

    after_retry = repository.load(
        task_id=task.task_id,
        owner_id="owner",
        workspace_id="workspace",
    )
    assert after_retry is not None
    retried_work = next(
        row for row in after_retry.work_units if row.work_unit_id == dispatch.work_unit_id
    )
    assert retried_work.status.value == "queued"
    retried_decision = next(
        row
        for row in after_retry.execution_decisions
        if row.execution_decision_id == dispatch.execution_decision_id
    )
    assert retried_decision.status.value == "dispatched"
    assert retried_decision.outcome is None

    reclaimed = _claim(repository, task.task_id, "worker-retry-identity-2")
    assert reclaimed is not None
    reauthorized = repository.authorize_dispatch_submission(
        reclaimed,
        owner_id="owner",
        workspace_id="workspace",
        expected_task_revision=retry_result["taskRevision"],
    )
    assert reauthorized is not None
    assert reauthorized.external_attempt.capability_call_id == retry_result["capabilityCallId"]
    assert reauthorized.external_attempt.attempt_number == 2
    restarted = repository.mark_external_action_started(
        reauthorized,
        provider_id="openai_primary",
        adapter="huiyan_chat_completions",
    )
    assert isinstance(restarted, AuthorizedDispatch)
    assert restarted.external_attempt == reauthorized.external_attempt

    with postgres_environment() as connection:
        external = connection.execute(
            """SELECT capability_call_id, attempt_number, external_state
                 FROM openair_external_execution
                WHERE dispatch_id = %s""",
            (dispatch.dispatch_id,),
        ).fetchone()
    assert external == {
        "capability_call_id": retry_result["capabilityCallId"],
        "attempt_number": 2,
        "external_state": "unknown",
    }

    dispatch_service = ResearchDispatchService(repository=repository)
    dispatch_service.prepare_external_execution(
        reauthorized,
        owner_id="owner",
        workspace_id="workspace",
        execution_kind="provider_http",
        provider_id="openai_primary",
        adapter="huiyan_chat_completions",
        request_body=b"{}",
    )
    retry_checkpoint = ExternalExecutionCheckpointRepository().get(
        capability_call_id=retry_result["capabilityCallId"],
    )
    assert retry_checkpoint is not None
    assert retry_checkpoint.capability_call_id == retry_result["capabilityCallId"]
    assert retry_checkpoint.capability_call_id.startswith("call-retry-")
    assert retry_checkpoint.capability_call_id != dispatch_capability_call_id(reauthorized)
