"""Hard-fail API proof for the P01 Recovery authority.

Requires IB01_TEST_DATABASE_URL to name an isolated PostgreSQL test/qa
database.  This suite deliberately resets only that database's public schema;
it never uses SQLite, a fallback DSN, or a repository mock.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import os
from threading import Barrier, BrokenBarrierError
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.modules.artifact_repository import ArtifactRepository
from app.modules.durable_execution import DurableExecutionError, DurableExecutionRepository
from app.modules.recovery_repository import RecoveryRepository
from app.modules.task_repository import TaskRepository


DSN_ENV = "IB01_TEST_DATABASE_URL"
OWNER = "p01-recovery-owner"
WORKSPACE = "p01-recovery-workspace"
OTHER_OWNER = "p01-recovery-other-owner"
OTHER_WORKSPACE = "p01-recovery-other-workspace"
PLAN_FINGERPRINT = "a" * 64


def _configure_isolated_postgres() -> str:
    dsn = os.environ.get(DSN_ENV, "").strip()
    if not dsn:
        pytest.fail(f"{DSN_ENV} is required; Recovery hard-fail tests refuse SQLite and mocks")
    parsed = urlparse(dsn)
    database = parsed.path.lstrip("/").lower()
    if parsed.scheme not in {"postgres", "postgresql"} or not database or not any(
        tag in database for tag in ("test", "qa")
    ):
        pytest.fail(f"{DSN_ENV} must identify an isolated PostgreSQL test or qa database")
    if any(tag in database for tag in ("prod", "production", "development", "dev")):
        pytest.fail("Recovery hard-fail tests refuse development and production databases")
    settings.postgres_host = parsed.hostname or "localhost"
    settings.postgres_port = parsed.port or 5432
    settings.postgres_user = parsed.username or "postgres"
    settings.postgres_password = parsed.password or ""
    settings.postgres_database = database
    settings.persistence_backend = "postgres"
    return dsn


@pytest.fixture(scope="module", autouse=True)
def postgres_recovery_authority() -> str:
    dsn = _configure_isolated_postgres()
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE")
        conn.execute("CREATE SCHEMA public")
    TaskRepository().bootstrap()
    ArtifactRepository().bootstrap()
    DurableExecutionRepository().bootstrap(True)
    return dsn


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


def _headers(owner_id: str = OWNER, workspace_id: str = WORKSPACE) -> dict[str, str]:
    return {
        "X-OpenAIR-Owner-Id": owner_id,
        "X-OpenAIR-Workspace-Id": workspace_id,
        "X-OpenAIR-Created-By": owner_id,
    }


def _query(dsn: str, statement: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    with psycopg.connect(dsn, row_factory=psycopg.rows.dict_row) as conn:
        return list(conn.execute(statement, params).fetchall())


def _seed_durable_failure(
    dsn: str,
    *,
    owner_id: str = OWNER,
    workspace_id: str = WORKSPACE,
    state: str = "failed",
) -> dict[str, Any]:
    token = uuid4().hex
    task_id = f"p01-recovery-task-{token}"
    source_run_id = f"p01-recovery-source-{token}"
    command_id = f"p01-recovery-command-{token}"
    source_revision = 6
    current_revision = source_revision + 1
    with psycopg.connect(dsn) as conn:
        conn.execute(
            """INSERT INTO openair_tasks(
                   task_id, owner_id, workspace_id, status, current_stage, title,
                   created_at, updated_at, payload, revision
               ) VALUES (%s,%s,%s,'failed','execution_validation',%s,
                         clock_timestamp(),clock_timestamp(),%s::jsonb,%s)""",
            (task_id, owner_id, workspace_id, task_id, '{"capabilityCalls":[]}', current_revision),
        )
        conn.execute(
            """INSERT INTO openair_execution_runs(
                   run_id, task_id, owner_id, workspace_id, task_revision, run_number,
                   run_kind, idempotency_key, request_fingerprint, input_snapshot,
                   target_substage, source_mode, capability_call_id, state
               ) VALUES (%s,%s,%s,%s,%s,1,'initial_start',%s,%s,%s::jsonb,
                         'execution_validation','live',%s,%s)""",
            (
                source_run_id,
                task_id,
                owner_id,
                workspace_id,
                source_revision,
                f"source-key-{token}",
                "b" * 64,
                '{"durableFailure":"preserved"}',
                f"source-call-{token}",
                state,
            ),
        )
        conn.execute(
            """INSERT INTO openair_execution_run_events(run_id, sequence, type, payload)
               VALUES (%s,1,'failed',%s::jsonb)""",
            (source_run_id, '{"result":"source-terminal-result"}'),
        )
        conn.execute("UPDATE openair_execution_runs SET next_event_sequence=2 WHERE run_id=%s", (source_run_id,))
        conn.execute(
            """INSERT INTO openair_controller_commands(
                   command_id, task_id, owner_id, workspace_id, kind, payload, state,
                   idempotency_key, request_fingerprint, user_message
               ) VALUES (%s,%s,%s,%s,'supplement_input',%s::jsonb,'received',%s,%s,%s)""",
            (command_id, task_id, owner_id, workspace_id, '{"text":"retry with preserved evidence"}', f"command-key-{token}", "c" * 64, "retry with preserved evidence"),
        )
        conn.execute(
            """INSERT INTO openair_artifacts(
                   artifact_id, owner_id, workspace_id, created_by, kind, title,
                   metadata, source_task_id, source_execution_run_id, source_mode
               ) VALUES (%s,%s,%s,%s,'report','source evidence',%s::jsonb,%s,%s,'live')""",
            (f"p01-source-artifact-{token}", owner_id, workspace_id, owner_id, '{"result":"source-terminal-result"}', task_id, source_run_id),
        )
    return {
        "taskId": task_id,
        "sourceRunId": source_run_id,
        "commandId": command_id,
        "sourceRevision": source_revision,
        "currentRevision": current_revision,
    }


def _body(fact: dict[str, Any], *, kind: str, key: str, message_ids: list[str] | None = None, revision: int | None = None) -> dict[str, Any]:
    return {
        "sourceRunId": fact["sourceRunId"],
        "recoveryKind": kind,
        "expectedTaskRevision": fact["currentRevision"] if revision is None else revision,
        "messageIds": message_ids or [fact["commandId"]],
        "idempotencyKey": key,
        "planFingerprint": PLAN_FINGERPRINT,
    }


def _assert_error(response: Any, status_code: int, code: str) -> None:
    assert response.status_code == status_code, response.text
    assert response.json() == {"detail": {"code": code}}


def _recovery_counts(dsn: str, task_id: str) -> dict[str, int]:
    return _query(
        dsn,
        """SELECT
               (SELECT COUNT(*) FROM openair_task_recovery_requests WHERE task_id=%s) AS requests,
               (SELECT COUNT(*) FROM openair_recovery_messages WHERE task_id=%s) AS messages,
               (SELECT COUNT(*) FROM openair_recovery_request_messages WHERE task_id=%s) AS links,
               (SELECT COUNT(*) FROM openair_execution_runs WHERE task_id=%s AND run_kind LIKE 'recovery_%%') AS successors,
               (SELECT COUNT(*) FROM openair_execution_capability_calls c JOIN openair_execution_runs r ON r.run_id=c.run_id WHERE r.task_id=%s AND r.run_kind LIKE 'recovery_%%') AS calls,
               (SELECT COUNT(*) FROM openair_execution_run_events e JOIN openair_execution_runs r ON r.run_id=e.run_id WHERE r.task_id=%s AND r.run_kind LIKE 'recovery_%%' AND e.type='start_requested') AS starts,
               (SELECT COUNT(*) FROM openair_recovery_execution_projections WHERE task_id=%s) AS projections""",
        (task_id,) * 7,
    )[0]


def test_retry_confirms_once_replays_and_rejects_late_source_callback(client: TestClient, postgres_recovery_authority: str) -> None:
    fact = _seed_durable_failure(postgres_recovery_authority)
    path = f"/api/tasks/{fact['taskId']}/recovery-requests"
    created = client.post(path, headers=_headers(), json=_body(fact, kind="retry", key="retry-key"))
    assert created.status_code == 201, created.text
    payload = created.json()
    assert payload["state"] == "confirmed"
    assert payload["replayed"] is False
    assert payload["allowedActions"] == []
    assert payload["taskRevision"] == fact["currentRevision"] + 1
    assert payload["successorRunId"]
    assert _recovery_counts(postgres_recovery_authority, fact["taskId"]) == {
        "requests": 1, "messages": 1, "links": 1, "successors": 1, "calls": 1, "starts": 1, "projections": 1,
    }

    replay = client.post(path, headers=_headers(), json=_body(fact, kind="retry", key="retry-key"))
    assert replay.status_code == 201, replay.text
    assert replay.json()["recoveryRequestId"] == payload["recoveryRequestId"]
    assert replay.json()["successorRunId"] == payload["successorRunId"]
    assert replay.json()["replayed"] is True
    confirmed_again = client.post(
        f"{path}/{payload['recoveryRequestId']}/confirm", headers=_headers(), json={}
    )
    assert confirmed_again.status_code == 200, confirmed_again.text
    assert confirmed_again.json()["successorRunId"] == payload["successorRunId"]
    assert confirmed_again.json()["replayed"] is True

    before_callback = _query(
        postgres_recovery_authority,
        """SELECT source.state AS source_state, successor.state AS successor_state,
                  successor.next_event_sequence AS successor_sequence,
                  projection.source_lease_epoch_fence, projection.source_next_event_sequence_fence,
                  (SELECT COUNT(*) FROM openair_artifacts WHERE source_execution_run_id=successor.run_id) AS successor_artifact_count,
                  successor.input_snapshot
             FROM openair_execution_runs source
             JOIN openair_execution_runs successor ON successor.run_id=%s
             JOIN openair_recovery_execution_projections projection ON projection.successor_run_id=successor.run_id
            WHERE source.run_id=%s""",
        (payload["successorRunId"], fact["sourceRunId"]),
    )[0]
    with pytest.raises(DurableExecutionError) as rejected:
        DurableExecutionRepository().callback(
            fact["sourceRunId"], "late-source-worker", 1, "failed", {"failureClassification": "late"}
        )
    assert rejected.value.status_code == 409
    assert rejected.value.detail == "terminal_run"
    assert _query(
        postgres_recovery_authority,
        "SELECT reason,worker_id,lease_epoch FROM openair_execution_callback_rejections WHERE run_id=%s",
        (fact["sourceRunId"],),
    ) == [{"reason": "terminal_run", "worker_id": "late-source-worker", "lease_epoch": 1}]
    after_callback = _query(
        postgres_recovery_authority,
        """SELECT source.state AS source_state, successor.state AS successor_state,
                  successor.next_event_sequence AS successor_sequence,
                  projection.source_lease_epoch_fence, projection.source_next_event_sequence_fence,
                  (SELECT COUNT(*) FROM openair_artifacts WHERE source_execution_run_id=successor.run_id) AS successor_artifact_count,
                  successor.input_snapshot
             FROM openair_execution_runs source
             JOIN openair_execution_runs successor ON successor.run_id=%s
             JOIN openair_recovery_execution_projections projection ON projection.successor_run_id=successor.run_id
            WHERE source.run_id=%s""",
        (payload["successorRunId"], fact["sourceRunId"]),
    )[0]
    assert after_callback == before_callback
    assert before_callback["source_state"] == "failed"
    assert before_callback["successor_state"] == "queued"
    assert before_callback["source_next_event_sequence_fence"] == 2
    assert before_callback["successor_artifact_count"] == 0
    snapshot = before_callback["input_snapshot"]
    assert snapshot["taskId"] == fact["taskId"]
    assert snapshot["durableFailure"] == "preserved"
    assert snapshot["controllerInputContext"] == ["retry with preserved evidence"]
    assert snapshot["recovery"] == {
        "requestId": payload["recoveryRequestId"], "sourceRunId": fact["sourceRunId"], "sourceRunNumber": 1,
        "expectedTaskRevision": fact["currentRevision"], "recoveryKind": "retry",
        "recoveryMessageIds": snapshot["recovery"]["recoveryMessageIds"],
    }
    assert len(snapshot["recovery"]["recoveryMessageIds"]) == 1
def test_replan_requires_confirmation_then_get_recovers_response_loss(client: TestClient, postgres_recovery_authority: str) -> None:
    fact = _seed_durable_failure(postgres_recovery_authority)
    path = f"/api/tasks/{fact['taskId']}/recovery-requests"
    request_body = _body(fact, kind="replan", key="replan-key")
    lost_response = client.post(path, headers=_headers(), json=request_body)
    assert lost_response.status_code == 201, lost_response.text
    # The caller discards this successful body.  Replaying the same command is
    # the only way it learns the durable request ID.
    requested = client.post(path, headers=_headers(), json=request_body)
    assert requested.status_code == 201, requested.text
    requested_payload = requested.json()
    assert requested_payload["replayed"] is True
    assert requested_payload["state"] == "requested"
    assert requested_payload["successorRunId"] is None
    assert requested_payload["allowedActions"] == ["confirm"]
    assert _recovery_counts(postgres_recovery_authority, fact["taskId"])["successors"] == 0

    confirmed = client.post(
        f"{path}/{requested_payload['recoveryRequestId']}/confirm", headers=_headers(), json={}
    )
    assert confirmed.status_code == 200, confirmed.text
    payload = confirmed.json()
    assert payload["state"] == "confirmed" and payload["successorRunId"]
    replayed_confirm = client.post(
        f"{path}/{payload['recoveryRequestId']}/confirm", headers=_headers(), json={}
    )
    assert replayed_confirm.status_code == 200
    assert replayed_confirm.json()["successorRunId"] == payload["successorRunId"]
    assert replayed_confirm.json()["replayed"] is True
    # Simulated response loss: no second mutation, only GET observes the durable fact.
    recovered = client.get(f"{path}/{payload['recoveryRequestId']}", headers=_headers())
    assert recovered.status_code == 200, recovered.text
    assert {key: recovered.json()[key] for key in ("recoveryRequestId", "state", "successorRunId")} == {
        key: payload[key] for key in ("recoveryRequestId", "state", "successorRunId")
    }
    assert _recovery_counts(postgres_recovery_authority, fact["taskId"])["successors"] == 1


def test_two_independent_clients_concurrently_enforce_one_recovery_mutex(
    postgres_recovery_authority: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    fact = _seed_durable_failure(postgres_recovery_authority)
    path = f"/api/tasks/{fact['taskId']}/recovery-requests"
    lock_start = Barrier(2)
    original_lock = RecoveryRepository._lock_task

    def synchronize_before_task_lock(cur: Any, task_id: str) -> None:
        try:
            lock_start.wait(timeout=10)
        except BrokenBarrierError as exc:
            raise AssertionError("both independent requests must reach the PostgreSQL lock") from exc
        original_lock(cur, task_id)

    monkeypatch.setattr(RecoveryRepository, "_lock_task", staticmethod(synchronize_before_task_lock))

    def post_from_independent_client(kind: str, key: str):
        independent_client = TestClient(app, raise_server_exceptions=False)
        try:
            return independent_client.post(path, headers=_headers(), json=_body(fact, kind=kind, key=key))
        finally:
            independent_client.close()

    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="recovery") as pool:
        retry = pool.submit(post_from_independent_client, "retry", "concurrent-retry")
        replan = pool.submit(post_from_independent_client, "replan", "concurrent-replan")
        responses = [retry.result(timeout=15), replan.result(timeout=15)]

    successful = [response for response in responses if response.status_code == 201]
    conflicts = [response for response in responses if response.status_code == 409]
    assert len(successful) == 1, [response.text for response in responses]
    assert len(conflicts) == 1, [response.text for response in responses]
    assert conflicts[0].json() in (
        {"detail": {"code": "recovery_mutex_conflict"}},
        {"detail": {"code": "recovery_successor_exists"}},
        {"detail": {"code": "recovery_stale"}},
    )
    counts = _recovery_counts(postgres_recovery_authority, fact["taskId"])
    assert counts["requests"] == 1
    assert counts["successors"] <= 1
    confirmed_successors = _query(
        postgres_recovery_authority,
        "SELECT COUNT(*) AS count FROM openair_task_recovery_requests "
        "WHERE task_id=%s AND state='confirmed' AND successor_run_id IS NOT NULL",
        (fact["taskId"],),
    )
    assert confirmed_successors[0]["count"] <= 1


def test_hard_fail_preflight_mutex_scope_and_structured_codes(client: TestClient, postgres_recovery_authority: str) -> None:
    stale = _seed_durable_failure(postgres_recovery_authority)
    stale_response = client.post(
        f"/api/tasks/{stale['taskId']}/recovery-requests", headers=_headers(),
        json=_body(stale, kind="retry", key="stale-key", revision=stale["sourceRevision"]),
    )
    _assert_error(stale_response, 409, "recovery_stale")
    assert _recovery_counts(postgres_recovery_authority, stale["taskId"]) == {
        "requests": 0, "messages": 0, "links": 0, "successors": 0, "calls": 0, "starts": 0, "projections": 0,
    }

    nonfailed = _seed_durable_failure(postgres_recovery_authority, state="queued")
    _assert_error(
        client.post(f"/api/tasks/{nonfailed['taskId']}/recovery-requests", headers=_headers(), json=_body(nonfailed, kind="retry", key="queued-key")),
        409,
        "recovery_preflight_failed",
    )
    assert _recovery_counts(postgres_recovery_authority, nonfailed["taskId"])["requests"] == 0
    missing_message = _seed_durable_failure(postgres_recovery_authority)
    _assert_error(
        client.post(f"/api/tasks/{missing_message['taskId']}/recovery-requests", headers=_headers(), json=_body(missing_message, kind="retry", key="missing-message", message_ids=["missing-command"])),
        409,
        "recovery_preflight_failed",
    )
    other_task = _seed_durable_failure(postgres_recovery_authority)
    _assert_error(
        client.post(f"/api/tasks/{missing_message['taskId']}/recovery-requests", headers=_headers(), json=_body(missing_message, kind="retry", key="cross-task-message", message_ids=[other_task["commandId"]])),
        409,
        "recovery_preflight_failed",
    )
    assert _recovery_counts(postgres_recovery_authority, missing_message["taskId"])["requests"] == 0

    mutex = _seed_durable_failure(postgres_recovery_authority)
    mutex_path = f"/api/tasks/{mutex['taskId']}/recovery-requests"
    assert client.post(mutex_path, headers=_headers(), json=_body(mutex, kind="replan", key="mutex-replan")).status_code == 201
    _assert_error(
        client.post(mutex_path, headers=_headers(), json=_body(mutex, kind="retry", key="mutex-retry")),
        409,
        "recovery_mutex_conflict",
    )
    assert _recovery_counts(postgres_recovery_authority, mutex["taskId"]) == {
        "requests": 1, "messages": 1, "links": 1, "successors": 0, "calls": 0, "starts": 0, "projections": 0,
    }

    idem = _seed_durable_failure(postgres_recovery_authority)
    idem_path = f"/api/tasks/{idem['taskId']}/recovery-requests"
    assert client.post(idem_path, headers=_headers(), json=_body(idem, kind="replan", key="same-key")).status_code == 201
    _assert_error(
        client.post(idem_path, headers=_headers(), json=_body(idem, kind="retry", key="same-key")),
        409,
        "idempotency_payload_conflict",
    )
    assert _recovery_counts(postgres_recovery_authority, idem["taskId"])["requests"] == 1

    foreign_owner = _seed_durable_failure(postgres_recovery_authority, owner_id=OTHER_OWNER)
    _assert_error(
        client.post(f"/api/tasks/{foreign_owner['taskId']}/recovery-requests", headers=_headers(), json=_body(foreign_owner, kind="retry", key="foreign-owner-key")),
        404,
        "recovery_not_found",
    )
    foreign_workspace = _seed_durable_failure(postgres_recovery_authority, workspace_id=OTHER_WORKSPACE)
    _assert_error(
        client.post(f"/api/tasks/{foreign_workspace['taskId']}/recovery-requests", headers=_headers(), json=_body(foreign_workspace, kind="retry", key="foreign-workspace-key")),
        404,
        "recovery_not_found",
    )
    _assert_error(
        client.post(f"/api/tasks/{missing_message['taskId']}/recovery-requests", headers=_headers(), json=_body({**missing_message, "sourceRunId": foreign_owner["sourceRunId"]}, kind="retry", key="cross-task-source")),
        404,
        "recovery_not_found",
    )
    _assert_error(
        client.get(f"/api/tasks/{foreign_owner['taskId']}/recovery-requests/not-visible", headers=_headers()),
        404,
        "recovery_not_found",
    )
    assert _recovery_counts(postgres_recovery_authority, foreign_owner["taskId"])["requests"] == 0
    assert _recovery_counts(postgres_recovery_authority, foreign_workspace["taskId"])["requests"] == 0


def test_failure_after_successor_write_rolls_back_every_recovery_fact(
    client: TestClient, postgres_recovery_authority: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    fact = _seed_durable_failure(postgres_recovery_authority)
    original_event = RecoveryRepository._event

    def fail_after_successor_event(self: RecoveryRepository, cur: Any, run: dict[str, Any], event_type: str, payload: dict[str, Any]) -> None:
        original_event(cur, run, event_type, payload)
        raise RuntimeError("forced failure after successor write")

    monkeypatch.setattr(RecoveryRepository, "_event", fail_after_successor_event)
    response = client.post(
        f"/api/tasks/{fact['taskId']}/recovery-requests", headers=_headers(), json=_body(fact, kind="retry", key="rollback-key")
    )
    assert response.status_code == 500, response.text
    assert _recovery_counts(postgres_recovery_authority, fact["taskId"]) == {
        "requests": 0, "messages": 0, "links": 0, "successors": 0, "calls": 0, "starts": 0, "projections": 0,
    }
    assert _query(
        postgres_recovery_authority, "SELECT revision,status FROM openair_tasks WHERE task_id=%s", (fact["taskId"],)
    ) == [{"revision": fact["currentRevision"], "status": "failed"}]
