"""Physical Provider execution truth across primary and child calls.

These tests protect the durable identity/state invariants that distinguish a
physical Provider submission from logical retries and local processing.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import psycopg
import pytest
from psycopg.rows import dict_row

from app.config import settings
from app.modules.artifact_repository import ArtifactRepository
from app.modules.provider_transport_evidence import request_sha256
from app.modules.collaboration_repository import CollaborationRepository
from app.modules.durable_execution import DurableExecutionRepository
from app.modules.provider_transport_evidence import (
    DuplicateProviderAttemptError,
    ProviderAttemptEvidenceError,
    ProviderHttpAttemptRepository,
)
from app.modules.task_repository import TaskRepository
from tests.integration._production_postgres_bootstrap import bootstrap_production_postgres_schema


OWNER = "multi-owner"
WORKSPACE = "multi-workspace"
WORKER = "multi-worker"


def _configure_isolated_database() -> str:
    dsn = os.environ.get("C1_TRANSPORT_TEST_DATABASE_URL", "").strip()
    if not dsn:
        pytest.fail("C1_TRANSPORT_TEST_DATABASE_URL is required; multi-attempt refuses an implicit database")
    parsed = urlparse(dsn)
    database = parsed.path.lstrip("/").lower()
    if (
        parsed.scheme not in {"postgres", "postgresql"}
        or "c1" not in database
        or not any(marker in database for marker in ("test", "qa"))
        or any(marker in database for marker in ("prod", "production", "development", "dev"))
    ):
        pytest.fail("multi-attempt tests require a disposable c1 test/qa PostgreSQL database")
    settings.postgres_host = parsed.hostname or "localhost"
    settings.postgres_port = parsed.port or 5432
    settings.postgres_user = parsed.username or "postgres"
    settings.postgres_password = parsed.password or ""
    settings.postgres_database = database
    settings.persistence_backend = "postgres"
    return dsn


@pytest.fixture(autouse=True)
def isolated_database(monkeypatch: pytest.MonkeyPatch) -> None:
    dsn = _configure_isolated_database()
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE")
        conn.execute("CREATE SCHEMA public")
        with conn.cursor() as cur:
            bootstrap_production_postgres_schema(cur)
    ArtifactRepository().bootstrap()
    DurableExecutionRepository().bootstrap(auto_apply_migrations=False)


def _query(sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    with psycopg.connect(os.environ["C1_TRANSPORT_TEST_DATABASE_URL"], row_factory=dict_row) as conn:
        return list(conn.execute(sql, params).fetchall())


def _started_call(label: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    task_id = f"task-{label}"
    TaskRepository().insert_task({
        "taskId": task_id,
        "ownerId": OWNER,
        "workspaceId": WORKSPACE,
        "createdBy": OWNER,
        "title": "Multi attempt evidence",
        "domain": "research_task",
        "currentStage": "evidence",
        "status": "not_started",
        "creationStatus": "confirmed",
        "resultSource": "none",
        "executionMode": "stage_confirmation",
        "stages": ["evidence"],
        "stageCatalog": [],
        "stageDetails": [{"stageKey": "evidence", "status": "not_started", "subStages": []}],
        "artifacts": [],
        "materials": [],
        "memoryRecords": [],
        "confirmations": [],
        "capabilityCalls": [],
    })
    durable = DurableExecutionRepository()
    run, _ = durable.start_initial(
        task_id=task_id,
        owner_id=OWNER,
        workspace_id=WORKSPACE,
        idempotency_key=f"start-{label}",
        source_mode="live",
    )
    claim = durable.claim(WORKER, 60)
    assert claim and claim["run_id"] == run["run_id"]
    collaboration = CollaborationRepository()
    unit = collaboration.create_work_unit(
        run_id=run["run_id"],
        owner_id=OWNER,
        workspace_id=WORKSPACE,
        worker_id=WORKER,
        fence_epoch=claim["lease_epoch"],
        agent_id="magnet_mvp",
        kind="research",
        title=label,
        unit_key=f"unit-{label}",
        objective=label,
    )
    collaboration.transition_work_unit(
        work_unit_id=unit["work_unit_id"],
        owner_id=OWNER,
        workspace_id=WORKSPACE,
        worker_id=WORKER,
        fence_epoch=claim["lease_epoch"],
        state="running",
    )
    call = collaboration.create_capability_invocation(
        run_id=run["run_id"],
        owner_id=OWNER,
        workspace_id=WORKSPACE,
        worker_id=WORKER,
        fence_epoch=claim["lease_epoch"],
        work_unit_id=unit["work_unit_id"],
        requesting_agent_id="magnet_mvp",
        invocation_kind="agent_brain",
        idempotency_key=f"call-{label}",
        request_fingerprint=f"fingerprint-{label}",
    )
    begun, replay = collaboration.begin_capability_invocation(
        call_id=call["call_id"],
        owner_id=OWNER,
        workspace_id=WORKSPACE,
        worker_id=WORKER,
        fence_epoch=claim["lease_epoch"],
        idempotency_key=f"call-{label}",
        request_fingerprint=f"fingerprint-{label}",
    )
    assert replay is False and begun["attempt_number"] == 1
    return run, claim, {**call, "work_unit_id": unit["work_unit_id"]}


def test_multiple_mcp_sub_attempts_under_one_capability_call() -> None:
    _run, _claim, call = _started_call("multi-mcp")
    call_id = call["call_id"]
    repo = ProviderHttpAttemptRepository()

    payloads = [
        ("diffcsp-generate", b'{"tool":"generate_structures","args":{"formula":"Nd2Fe14B"}}', 200, b'{"cif":"..."}', "response_received"),
        ("mattersim-relax", b'{"tool":"relax","args":{"cif":"..."}}', 200, b'{"energy":-1.2}', "response_received"),
        ("alignn_prediction", b'{"tool":"predict","args":{"cif":"..."}}', 500, b'{"error":"boom"}', "provider_http_error"),
        ("mattergen-evaluate", b'{"tool":"eval","args":{}}', 200, b'{"job_id":"j_1"}', "response_received"),
    ]

    inserted: list[dict[str, Any]] = []
    for adapter_name, req, status, resp, outcome in payloads:
        attempt = repo.record_sub_attempt(
            capability_call_id=call_id,
            evidence_kind="mcp_call",
            adapter_name=adapter_name,
            request_body=req,
            endpoint=f"http://materials-mcp.internal/mcp/{adapter_name}/mcp",
            adapter="huiyan_chat_completions",
            outcome=outcome,
            http_status=status,
            response_body=resp,
            response_headers=None,
        )
        assert attempt is not None
        inserted.append({
            "attempt_id": attempt.attempt_id,
            "attempt_number": attempt.attempt_number,
            "adapter_name": adapter_name,
        })

    assert [row["attempt_number"] for row in inserted] == [1, 2, 3, 4]

    rows = _query(
        """SELECT attempt_number, evidence_kind, adapter_name, http_status, transport_outcome
             FROM openair_provider_http_attempts
            WHERE capability_call_id = %s
            ORDER BY attempt_number""",
        (call_id,),
    )
    assert len(rows) == 4
    assert all(row["evidence_kind"] == "mcp_call" for row in rows)
    assert [row["adapter_name"] for row in rows] == [
        "diffcsp-generate",
        "mattersim-relax",
        "alignn_prediction",
        "mattergen-evaluate",
    ]
    assert [row["http_status"] for row in rows] == [200, 200, 500, 200]
    assert rows[2]["transport_outcome"] == "provider_http_error"

    listed = repo.list_attempts(capability_call_id=call_id)
    assert [row["attempt_number"] for row in listed] == [1, 2, 3, 4]
    assert listed[0]["adapter_name"] == "diffcsp-generate"


def test_completed_provider_submission_cannot_be_replayed_for_same_operation() -> None:
    """Completed physical execution is permanent for the same operation."""
    _run, _claim, call = _started_call("c1-invariant")
    call_id = call["call_id"]
    repo = ProviderHttpAttemptRepository()

    first = repo.begin(
        capability_call_id=call_id,
        attempt_number=1,
        request_body=b'{"frozen":"request"}',
        endpoint="http://provider.internal/v1/chat/completions",
        adapter="huiyan_chat_completions",
    )
    assert first.evidence_kind == "provider_submission"
    assert first.attempt_number == 1
    repo.finish(
        first,
        request_body=b'{"frozen":"request"}',
        outcome="response_received",
        http_status=200,
        response_body=b'{"id":"r1"}',
        response_headers={"X-Request-Id": "req-1"},
    )

    with pytest.raises(DuplicateProviderAttemptError, match="terminal_replay_forbidden"):
        repo.begin(
            capability_call_id=call_id,
            attempt_number=1,
            request_body=b'{"frozen":"request-2"}',
            endpoint="http://provider.internal/v1/chat/completions",
            adapter="huiyan_chat_completions",
        )

    rows = _query(
        """SELECT evidence_kind, attempt_number FROM openair_provider_http_attempts
            WHERE capability_call_id = %s""",
        (call_id,),
    )
    assert rows == [{"evidence_kind": "provider_submission", "attempt_number": 1}]


def test_transport_uncertain_stays_unknown_and_cannot_be_silently_replayed() -> None:
    _run, _claim, call = _started_call("unknown-no-replay")
    repo = ProviderHttpAttemptRepository()
    attempt = repo.begin(
        capability_call_id=call["call_id"],
        attempt_number=1,
        request_body=b'{"request":"uncertain"}',
        endpoint="http://provider.internal/v1/chat/completions",
        adapter="huiyan_chat_completions",
        operation_identity="provider_submission:unknown",
    )
    repo.mark_io_started(attempt)
    repo.finish(
        attempt,
        request_body=b'{"request":"uncertain"}',
        outcome="transport_uncertain",
        http_status=None,
        response_body=None,
        response_headers=None,
        stage="response_observation",
    )

    observed = repo.observe_latest(
        parent_kind="capability_call",
        parent_id=call["call_id"],
        operation_identity="provider_submission:unknown",
    )
    assert observed is not None
    assert observed.state == "unknown"
    assert observed.reason == "transport_uncertain"
    assert observed.stage == "response_observation"
    assert observed.submission_proven is None

    with pytest.raises(DuplicateProviderAttemptError, match="unknown_replay_forbidden"):
        repo.begin(
            capability_call_id=call["call_id"],
            attempt_number=2,
            request_body=b'{"request":"retry"}',
            endpoint="http://provider.internal/v1/chat/completions",
            adapter="huiyan_chat_completions",
            operation_identity="provider_submission:unknown",
        )


def test_cancelled_stays_cancelled_and_cannot_be_rewritten_failed() -> None:
    _run, _claim, call = _started_call("cancelled-permanent")
    repo = ProviderHttpAttemptRepository()
    body = b'{"request":"cancel"}'
    attempt = repo.begin(
        capability_call_id=call["call_id"],
        attempt_number=1,
        request_body=body,
        endpoint="http://provider.internal/v1/chat/completions",
        adapter="huiyan_chat_completions",
        operation_identity="provider_submission:cancelled",
    )
    repo.mark_io_started(attempt)
    repo.finish(
        attempt,
        request_body=body,
        outcome="provider_cancelled",
        http_status=None,
        response_body=None,
        response_headers=None,
    )
    with pytest.raises(ProviderAttemptEvidenceError, match="finish_conflict"):
        repo.finish(
            attempt,
            request_body=body,
            outcome="provider_http_error",
            http_status=500,
            response_body=b'{"error":"late"}',
            response_headers=None,
        )
    observed = repo.observe_attempt(attempt_id=attempt.attempt_id)
    assert observed is not None
    assert observed.state == "cancelled"


def test_completed_execution_remains_completed_after_late_local_failure_mapping() -> None:
    _run, _claim, call = _started_call("completed-permanent")
    repo = ProviderHttpAttemptRepository()
    body = b'{"request":"complete"}'
    attempt = repo.begin(
        capability_call_id=call["call_id"],
        attempt_number=1,
        request_body=body,
        endpoint="http://provider.internal/v1/chat/completions",
        adapter="huiyan_chat_completions",
        operation_identity="provider_submission:completed",
    )
    repo.mark_io_started(attempt)
    repo.finish(
        attempt,
        request_body=body,
        outcome="response_received",
        http_status=200,
        response_body=b'{"id":"provider-result"}',
        response_headers={"X-Request-Id": "req-complete"},
    )
    # A later staging/parsing/storage failure belongs to processing truth and
    # must never mutate the already completed physical Provider execution.
    with pytest.raises(ProviderAttemptEvidenceError, match="finish_conflict"):
        repo.finish(
            attempt,
            request_body=body,
            outcome="transport_uncertain",
            http_status=None,
            response_body=None,
            response_headers=None,
            reason="local_result_processing",
            stage="result_staging",
        )
    observed = repo.observe_attempt(attempt_id=attempt.attempt_id)
    assert observed is not None
    assert observed.state == "completed"
    assert observed.provider_response_id == "provider-result"


def test_proven_not_submitted_retry_gets_new_physical_attempt_identity() -> None:
    _run, _claim, call = _started_call("not-submitted-retry")
    repo = ProviderHttpAttemptRepository()
    first_body = b'{"request":"first"}'
    first = repo.begin(
        capability_call_id=call["call_id"],
        attempt_number=1,
        request_body=first_body,
        endpoint="http://provider.internal/v1/chat/completions",
        adapter="huiyan_chat_completions",
        operation_identity="provider_submission:retryable",
    )
    repo.mark_io_started(first)
    repo.finish(
        first,
        request_body=first_body,
        outcome="transport_not_submitted",
        http_status=None,
        response_body=None,
        response_headers=None,
        stage="connect",
    )
    second_body = b'{"request":"second"}'
    second = repo.begin(
        capability_call_id=call["call_id"],
        attempt_number=1,
        request_body=second_body,
        endpoint="http://provider.internal/v1/chat/completions",
        adapter="huiyan_chat_completions",
        operation_identity="provider_submission:retryable",
    )

    assert second.attempt_id != first.attempt_id
    assert second.attempt_number == first.attempt_number + 1
    assert second.logical_attempt_ordinal == first.logical_attempt_ordinal == 1
    first_observed = repo.observe_attempt(attempt_id=first.attempt_id)
    assert first_observed is not None
    assert first_observed.state == "failed"
    assert first_observed.reason == "transport_not_submitted"
    assert first_observed.submission_proven is False


def test_pre_io_process_crash_recovers_with_new_physical_attempt() -> None:
    _run, _claim, call = _started_call("pre-io-crash-recovery")
    repo = ProviderHttpAttemptRepository()
    operation = "provider_submission:pre-io-crash"
    first = repo.begin(
        capability_call_id=call["call_id"],
        attempt_number=1,
        request_body=b'{"request":"reserved-before-crash"}',
        endpoint="http://provider.internal/v1/chat/completions",
        adapter="huiyan_chat_completions",
        operation_identity=operation,
    )

    before_recovery = repo.observe_attempt(attempt_id=first.attempt_id)
    assert before_recovery is not None
    assert before_recovery.state == "running"
    assert before_recovery.io_started is False
    assert before_recovery.submission_proven is None

    second = repo.begin(
        capability_call_id=call["call_id"],
        attempt_number=1,
        request_body=b'{"request":"after-process-restart"}',
        endpoint="http://provider.internal/v1/chat/completions",
        adapter="huiyan_chat_completions",
        operation_identity=operation,
    )

    assert second.attempt_id != first.attempt_id
    assert second.attempt_number == first.attempt_number + 1
    assert second.logical_attempt_ordinal == first.logical_attempt_ordinal
    preserved = repo.observe_attempt(attempt_id=first.attempt_id)
    recovered = repo.observe_attempt(attempt_id=second.attempt_id)
    assert preserved is not None
    assert preserved.state == "failed"
    assert preserved.reason == "transport_not_submitted"
    assert preserved.stage == "pre_io_recovery"
    assert preserved.io_started is False
    assert preserved.submission_proven is False
    assert recovered is not None
    assert recovered.state == "running"
    assert recovered.io_started is False
    assert recovered.submission_proven is None


def test_historical_in_flight_migrates_to_unknown_and_rolls_back_losslessly() -> None:
    _run, _claim, call = _started_call("historical-in-flight")
    migrations = Path(__file__).resolve().parents[2] / "migrations"
    up_path = migrations / "20260928_01_physical_execution_truth.sql"
    down_path = migrations / "20260928_01_physical_execution_truth_down.sql"
    dsn = os.environ["C1_TRANSPORT_TEST_DATABASE_URL"]

    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        conn.execute(down_path.read_text(encoding="utf-8"))
        conn.execute(
            """INSERT INTO openair_provider_http_attempts (
                   attempt_id, capability_call_id, attempt_number, request_hash,
                   provider_endpoint_identity, evidence_kind, transport_outcome
               ) VALUES (%s, %s, 1, %s, %s, 'provider_submission', 'in_flight')""",
            (
                "pat_historical_in_flight",
                call["call_id"],
                "a" * 64,
                "provider.test/v1/chat/completions#huiyan_chat_completions",
            ),
        )
        conn.execute(up_path.read_text(encoding="utf-8"))
        conn.execute(
            "INSERT INTO openair_schema_migrations(version) VALUES (%s)",
            (up_path.name,),
        )
        migrated = conn.execute(
            """SELECT execution_state, execution_reason, execution_stage,
                      io_started, submission_proven, transport_outcome
                 FROM openair_provider_http_attempts
                WHERE attempt_id = 'pat_historical_in_flight'"""
        ).fetchone()
        assert migrated == {
            "execution_state": "unknown",
            "execution_reason": "historical_in_flight_without_terminal_evidence",
            "execution_stage": None,
            "io_started": None,
            "submission_proven": None,
            "transport_outcome": "in_flight",
        }

        conn.execute(down_path.read_text(encoding="utf-8"))
        rolled_back = conn.execute(
            """SELECT attempt_id, capability_call_id, attempt_number, transport_outcome
                 FROM openair_provider_http_attempts
                WHERE attempt_id = 'pat_historical_in_flight'"""
        ).fetchone()
        assert rolled_back == {
            "attempt_id": "pat_historical_in_flight",
            "capability_call_id": call["call_id"],
            "attempt_number": 1,
            "transport_outcome": "in_flight",
        }
        assert conn.execute(
            """SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = 'openair_provider_http_attempts'
                  AND column_name = 'execution_state'"""
        ).fetchone() is None


def test_known_provider_failure_retry_requires_new_logical_and_physical_attempt() -> None:
    _run, _claim, call = _started_call("known-failure-retry")
    repo = ProviderHttpAttemptRepository()
    first_body = b'{"request":"first"}'
    first = repo.begin(
        capability_call_id=call["call_id"],
        attempt_number=1,
        request_body=first_body,
        endpoint="http://provider.internal/v1/chat/completions",
        adapter="huiyan_chat_completions",
        operation_identity="provider_submission:known-failure",
    )
    repo.finish(
        first,
        request_body=first_body,
        outcome="provider_http_error",
        http_status=503,
        response_body=b'{"error":"busy"}',
        response_headers=None,
        reason="known_provider_failure",
        stage="response_observation",
    )
    with pytest.raises(DuplicateProviderAttemptError, match="logical_retry_identity_required"):
        repo.begin(
            capability_call_id=call["call_id"],
            attempt_number=1,
            request_body=b'{"request":"same-logical"}',
            endpoint="http://provider.internal/v1/chat/completions",
            adapter="huiyan_chat_completions",
            operation_identity="provider_submission:known-failure",
        )
    second = repo.begin(
        capability_call_id=call["call_id"],
        attempt_number=2,
        request_body=b'{"request":"logical-two"}',
        endpoint="http://provider.internal/v1/chat/completions",
        adapter="huiyan_chat_completions",
        operation_identity="provider_submission:known-failure",
    )
    assert second.attempt_id != first.attempt_id
    assert second.attempt_number == first.attempt_number + 1
    assert second.logical_attempt_ordinal == 2


def test_failure_provenance_survives_durable_mapping() -> None:
    _run, _claim, call = _started_call("failure-provenance")
    repo = ProviderHttpAttemptRepository()
    body = b'{"request":"failure"}'
    attempt = repo.begin(
        capability_call_id=call["call_id"],
        attempt_number=1,
        request_body=body,
        endpoint="http://provider.internal/v1/chat/completions",
        adapter="huiyan_chat_completions",
        operation_identity="provider_submission:provenance",
    )
    repo.finish(
        attempt,
        request_body=body,
        outcome="provider_http_error",
        http_status=422,
        response_body=b'{"error":{"code":"schema"}}',
        response_headers={"X-Request-Id": "req-prov"},
        reason="schema_contract",
        stage="provider_response_contract",
    )
    observed = repo.observe_attempt(attempt_id=attempt.attempt_id)
    assert observed is not None
    assert observed.state == "failed"
    assert observed.reason == "schema_contract"
    assert observed.stage == "provider_response_contract"
    assert observed.originating_authority == "capability_call_runtime"
    assert observed.io_started is True
    assert observed.submission_proven is True


def test_continuation_child_failure_does_not_rewrite_completed_provider_submission() -> None:
    _run, _claim, call = _started_call("continuation-separation")
    repo = ProviderHttpAttemptRepository()
    main_body = b'{"request":"main"}'
    main = repo.begin(
        capability_call_id=call["call_id"],
        attempt_number=1,
        request_body=main_body,
        endpoint="http://provider.internal/v1/chat/completions",
        adapter="huiyan_chat_completions",
        operation_identity="provider_submission:dispatch-1",
    )
    repo.finish(
        main,
        request_body=main_body,
        outcome="response_received",
        http_status=200,
        response_body=b'{"id":"main-result"}',
        response_headers=None,
    )
    continuation_body = b'{"request":"next-task"}'
    continuation = repo.begin(
        capability_call_id=call["call_id"],
        attempt_number=1,
        request_body=continuation_body,
        endpoint="http://provider.internal/v1/chat/completions",
        adapter="huiyan_chat_completions",
        operation_identity="next_task_selection",
    )
    repo.finish(
        continuation,
        request_body=continuation_body,
        outcome="provider_http_error",
        http_status=503,
        response_body=b'{"error":"unavailable"}',
        response_headers=None,
    )

    assert continuation.attempt_id != main.attempt_id
    assert continuation.attempt_number == main.attempt_number + 1
    main_observed = repo.observe_attempt(attempt_id=main.attempt_id)
    continuation_observed = repo.observe_attempt(attempt_id=continuation.attempt_id)
    assert main_observed is not None and main_observed.state == "completed"
    assert continuation_observed is not None and continuation_observed.state == "failed"


def test_record_sub_attempt_refuses_provider_submission_kind() -> None:
    """provider_submission kind is reserved for the C1 begin()/finish() path."""
    _run, _claim, call = _started_call("kind-reserved")
    call_id = call["call_id"]
    repo = ProviderHttpAttemptRepository()
    with pytest.raises(ProviderAttemptEvidenceError, match="reserved"):
        repo.record_sub_attempt(
            capability_call_id=call_id,
            evidence_kind="provider_submission",
            adapter_name="should-not-be-used",
            request_body=b"{}",
            endpoint="http://provider.internal/",
            adapter="huiyan_chat_completions",
            outcome="response_received",
            http_status=200,
            response_body=b"{}",
        )


def test_record_sub_attempt_unknown_capability_call_returns_none() -> None:
    """Best-effort: sub-attempts without a parent call have no evidentiary value."""
    repo = ProviderHttpAttemptRepository()
    attempt = repo.record_sub_attempt(
        capability_call_id="call-does-not-exist",
        evidence_kind="mcp_call",
        adapter_name="diffcsp-generate",
        request_body=b"{}",
        endpoint="http://materials-mcp.internal/mcp/diffcsp-generate/mcp",
        adapter="huiyan_chat_completions",
        outcome="response_received",
        http_status=200,
        response_body=b"{}",
    )
    assert attempt is None


def test_mcp_sub_attempts_and_provider_submission_coexist_under_one_call() -> None:
    """One capability_call can host both a C1 provider_submission and N MCP rows."""
    _run, _claim, call = _started_call("coexist")
    call_id = call["call_id"]
    repo = ProviderHttpAttemptRepository()

    provider = repo.begin(
        capability_call_id=call_id,
        attempt_number=1,
        request_body=b'{"frozen":"provider"}',
        endpoint="http://provider.internal/v1/chat/completions",
        adapter="huiyan_chat_completions",
    )
    repo.finish(
        provider,
        request_body=b'{"frozen":"provider"}',
        outcome="response_received",
        http_status=200,
        response_body=b'{"id":"r1"}',
        response_headers=None,
    )

    for i, name in enumerate(("diffcsp-generate", "mattersim-relax")):
        attempt = repo.record_sub_attempt(
            capability_call_id=call_id,
            evidence_kind="mcp_call",
            adapter_name=name,
            request_body=f'{{"i":{i}}}'.encode("utf-8"),
            endpoint=f"http://materials-mcp.internal/mcp/{name}/mcp",
            adapter="huiyan_chat_completions",
            outcome="response_received",
            http_status=200,
            response_body=b"{}",
        )
        assert attempt is not None
        # Sub-attempts pick attempt_number = MAX + 1 (provider_submission was 1).
        assert attempt.attempt_number == 2 + i

    listed = repo.list_attempts(capability_call_id=call_id)
    assert len(listed) == 3
    assert listed[0]["evidence_kind"] == "provider_submission"
    assert listed[0]["adapter_name"] is None
    assert [row["evidence_kind"] for row in listed[1:]] == ["mcp_call", "mcp_call"]
    assert [row["adapter_name"] for row in listed[1:]] == ["diffcsp-generate", "mattersim-relax"]


def test_parallel_sub_attempt_reservations_get_unique_monotonic_ordinals() -> None:
    _run, _claim, call = _started_call("parallel-sub-attempts")
    call_id = call["call_id"]
    repo = ProviderHttpAttemptRepository()

    primary = repo.begin(
        capability_call_id=call_id,
        attempt_number=1,
        request_body=b'{"frozen":"provider"}',
        endpoint="http://provider.internal/v1/chat/completions",
        adapter="huiyan_chat_completions",
    )
    repo.finish(
        primary,
        request_body=b'{"frozen":"provider"}',
        outcome="response_received",
        http_status=200,
        response_body=b'{"id":"r1"}',
        response_headers=None,
    )

    def reserve(index: int) -> int:
        request_body = f'{{"index":{index}}}'.encode("utf-8")
        if index % 2:
            attempt = repo.record_sub_attempt(
                capability_call_id=call_id,
                evidence_kind="mcp_call",
                adapter_name=f"parallel-record-{index}",
                request_body=request_body,
                endpoint="http://provider.internal/mcp",
                adapter="huiyan_chat_completions",
                outcome="response_received",
                http_status=200,
                response_body=f'{{"id":"r-{index}"}}'.encode("utf-8"),
            )
            assert attempt is not None
            return attempt.attempt_number
        attempt = repo.begin_sub_attempt(
            capability_call_id=call_id,
            evidence_kind="sub_call",
            adapter_name=f"parallel-begin-{index}",
            request_body=request_body,
            endpoint="http://provider.internal/v1/chat/completions",
            adapter="huiyan_chat_completions",
        )
        repo.finish(
            attempt,
            request_body=request_body,
            outcome="response_received",
            http_status=200,
            response_body=f'{{"id":"r-{index}"}}'.encode("utf-8"),
            response_headers=None,
        )
        return attempt.attempt_number

    with ThreadPoolExecutor(max_workers=4) as executor:
        numbers = list(executor.map(reserve, range(8)))

    assert sorted(numbers) == list(range(2, 10))
    listed = repo.list_attempts(capability_call_id=call_id)
    assert [row["attempt_number"] for row in listed] == list(range(1, 10))


def test_concurrent_same_adapter_sub_attempts_do_not_fail_each_other() -> None:
    """Siblings sharing one adapter name (parallel per-record deep verification)
    reserve distinct operations because the runtime folds the request hash into
    the sub_call operation identity; without it the pre-IO takeover path marks
    the earlier running sibling as transport_not_submitted."""
    _run, _claim, call = _started_call("same-adapter-siblings")
    call_id = call["call_id"]
    repo = ProviderHttpAttemptRepository()

    primary = repo.begin(
        capability_call_id=call_id,
        attempt_number=1,
        request_body=b'{"frozen":"provider"}',
        endpoint="http://provider.internal/v1/chat/completions",
        adapter="huiyan_chat_completions",
    )
    repo.finish(
        primary,
        request_body=b'{"frozen":"provider"}',
        outcome="response_received",
        http_status=200,
        response_body=b'{"id":"r1"}',
        response_headers=None,
    )

    def reserve(index: int) -> str:
        request_body = f'{{"record":{index}}}'.encode()
        # the runtime's sub_call identity: adapter name + request hash
        operation = (
            "skill:example_skill:sub:same_adapter_sibling:"
            + request_sha256(request_body)[:16]
        )
        attempt = repo.begin_sub_attempt(
            capability_call_id=call_id,
            evidence_kind="sub_call",
            adapter_name="same_adapter_sibling",
            request_body=request_body,
            endpoint="http://provider.internal/v1/chat/completions",
            adapter="huiyan_chat_completions",
            operation_identity=operation,
        )
        return attempt.attempt_id

    with ThreadPoolExecutor(max_workers=4) as executor:
        attempt_ids = list(executor.map(reserve, range(4)))

    listed = repo.list_attempts(capability_call_id=call_id)
    siblings = [row for row in listed if row["adapter_name"] == "same_adapter_sibling"]
    assert len(siblings) == 4
    # every sibling survives the concurrent reservation; none is marked failed
    assert {row["execution_state"] for row in siblings} == {"running"}
    assert set(attempt_ids) == {row["attempt_id"] for row in siblings}
