"""IB-01A.1 repository evidence against a disposable PostgreSQL + MinIO pair.

This module intentionally fails fast without the explicitly supplied isolated
services.  It does not use the JSON store, SQLite, mocks, or FastAPI TestClient.
"""

from __future__ import annotations

import hashlib
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from urllib.parse import urlparse
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import psycopg
import pytest
from psycopg import sql

from app.config import settings
from app.modules.durable_execution import DurableExecutionError, DurableExecutionRepository
from app.modules.minio_client import ensure_minio_bucket, upload_bytes
from app.modules.artifact_repository import ArtifactRepository
from app.modules.task_repository import TaskRepository


OWNER = "ib01-owner"
WORKSPACE = "ib01-workspace"


def _record_scenario(name: str, *, task_id: str | None, run_id: str | None, call_id: str | None, artifact_id: str | None, null_reason: str | None = None) -> None:
    target = os.environ.get("IB01_A1_SCENARIO_MANIFEST")
    if not target:
        return
    Path(target).parent.mkdir(parents=True, exist_ok=True)
    with Path(target).open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"scenario": name, "taskId": task_id, "runId": run_id, "callId": call_id, "artifactId": artifact_id, "nullReason": null_reason}) + "\n")


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        pytest.fail(f"{name} is required; A1 refuses implicit or shared databases")
    return value


def _configure_isolated_services() -> str:
    dsn = _required_env("IB01_TEST_DATABASE_URL")
    parsed = urlparse(dsn)
    database = parsed.path.lstrip("/").lower()
    if parsed.scheme not in {"postgres", "postgresql"} or not database or not any(tag in database for tag in ("test", "qa")):
        pytest.fail("IB01_TEST_DATABASE_URL must be a PostgreSQL database whose name contains test or qa")
    if any(tag in database for tag in ("prod", "production", "development", " dev")):
        pytest.fail("IB01_TEST_DATABASE_URL must not target a development or production database")
    settings.postgres_host = parsed.hostname or "localhost"
    settings.postgres_port = parsed.port or 5432
    settings.postgres_user = parsed.username or "postgres"
    settings.postgres_password = parsed.password or ""
    settings.postgres_database = database
    settings.persistence_backend = "postgres"
    settings.artifact_storage = "minio"
    settings.minio_endpoint = _required_env("IB01_TEST_MINIO_ENDPOINT")
    settings.minio_access_key = _required_env("IB01_TEST_MINIO_ACCESS_KEY")
    settings.minio_secret_key = _required_env("IB01_TEST_MINIO_SECRET_KEY")
    settings.minio_bucket = _required_env("IB01_TEST_MINIO_BUCKET")
    return dsn


@pytest.fixture(autouse=True)
def isolated_database():
    dsn = _configure_isolated_services()
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE")
        conn.execute("CREATE SCHEMA public")
    TaskRepository().bootstrap()
    ArtifactRepository().bootstrap()
    DurableExecutionRepository().bootstrap()
    assert _query("SELECT version FROM openair_schema_migrations WHERE version = %s", ("20260720_01_ib01_durable_execution.sql",))
    import app.modules.minio_client as minio_client

    minio_client._client = None
    ensure_minio_bucket()
    yield


def _task(task_id: str, *, stage: str = "problem_modeling") -> dict:
    return {
        "taskId": task_id,
        "ownerId": OWNER,
        "workspaceId": WORKSPACE,
        "createdBy": OWNER,
        "title": "IB01 transaction task",
        "domain": "research_task",
        "currentStage": stage,
        "status": "not_started",
        "creationStatus": "confirmed",
        "resultSource": "none",
        "executionMode": "stage_confirmation",
        "stages": ["problem_modeling"],
        "stageCatalog": [],
        "stageDetails": [{"stageKey": stage, "status": "not_started", "subStages": []}],
        "artifacts": [],
        "materials": [],
        "memoryRecords": [],
        "confirmations": [],
        "capabilityCalls": [],
    }


def _seed(task_id: str) -> None:
    TaskRepository().insert_task(_task(task_id))


def _query(sql: str, params: tuple = ()) -> list[dict]:
    dsn = os.environ["IB01_TEST_DATABASE_URL"]
    with psycopg.connect(dsn, row_factory=psycopg.rows.dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()


def _start_and_claim(task_id: str, worker: str = "W1") -> tuple[dict, dict]:
    repo = DurableExecutionRepository()
    run, _ = repo.start_initial(task_id=task_id, owner_id=OWNER, workspace_id=WORKSPACE, idempotency_key="key-1", source_mode="replay")
    claimed = repo.claim(worker, 30)
    assert claimed and claimed["run_id"] == run["run_id"]
    repo.record_orchestration_plan(
        run["run_id"],
        worker,
        claimed["lease_epoch"],
        {
            "context": {"runId": run["run_id"], "taskId": task_id, "ownerId": OWNER, "workspaceId": WORKSPACE},
            "selectedProvider": "openai_primary",
            "selectedAgentId": "agent_test",
            "selectedSkillIds": [],
            "providerPolicy": {"allowedProviders": ["openai_primary"]},
        },
    )
    return run, claimed


def _presentation(content: bytes) -> dict:
    return {
        "title": "Isolated artifact",
        "mimeType": "text/plain",
        "metadata": {"providerId": "openai_primary", "adapter": "huiyan_chat_completions", "contentLength": len(content.decode("utf-8"))},
        "contentSha256": hashlib.sha256(content).hexdigest(),
        "contentSize": len(content),
    }


def test_start_idempotency_and_fingerprint_conflict() -> None:
    _seed("task-idem")
    repo = DurableExecutionRepository()
    first, replayed = repo.start_initial(task_id="task-idem", owner_id=OWNER, workspace_id=WORKSPACE, idempotency_key="same", source_mode="replay")
    again, duplicate = repo.start_initial(task_id="task-idem", owner_id=OWNER, workspace_id=WORKSPACE, idempotency_key="same", source_mode="replay")
    assert not replayed and duplicate and again["run_id"] == first["run_id"]
    with psycopg.connect(os.environ["IB01_TEST_DATABASE_URL"], autocommit=True) as conn:
        conn.execute("UPDATE openair_tasks SET current_stage = 'solution_design', payload = jsonb_set(payload, '{currentStage}', '\"solution_design\"') WHERE task_id = 'task-idem'")
    with pytest.raises(DurableExecutionError, match="fingerprint"):
        repo.start_initial(task_id="task-idem", owner_id=OWNER, workspace_id=WORKSPACE, idempotency_key="same", source_mode="replay")
    assert len(_query("SELECT * FROM openair_execution_runs")) == 1
    assert len(_query("SELECT * FROM openair_execution_idempotency")) == 1
    attempts = _query(
        "SELECT outcome, canonical_run_id FROM openair_execution_start_attempts WHERE task_id = %s ORDER BY occurred_at",
        ("task-idem",),
    )
    assert [(row["outcome"], row["canonical_run_id"]) for row in attempts] == [
        ("accepted", first["run_id"]), ("idempotent_replay", first["run_id"]),
    ]
    _record_scenario("idempotency_fingerprint", task_id="task-idem", run_id=first["run_id"], call_id=first["capability_call_id"], artifact_id=None, null_reason="start has no artifact")


def test_two_different_keys_concurrently_create_only_one_initial_run() -> None:
    _seed("task-race")
    repo = DurableExecutionRepository()

    def attempt(key: str) -> str:
        try:
            return repo.start_initial(task_id="task-race", owner_id=OWNER, workspace_id=WORKSPACE, idempotency_key=key, source_mode="replay")[0]["run_id"]
        except DurableExecutionError as exc:
            return f"error:{exc.status_code}:{exc.detail}"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, ["a", "b"]))
    assert sum(not value.startswith("error:") for value in results) == 1
    assert any("initial_execution_already_exists" in value for value in results)
    assert len(_query("SELECT * FROM openair_execution_runs WHERE task_id = 'task-race'")) == 1
    assert len(_query("SELECT * FROM openair_execution_run_events")) == 1
    row = _query("SELECT run_id, capability_call_id FROM openair_execution_runs WHERE task_id = 'task-race'")[0]
    _record_scenario("concurrent_start", task_id="task-race", run_id=row["run_id"], call_id=row["capability_call_id"], artifact_id=None, null_reason="start has no artifact")


def test_revision_conflict_does_not_leave_run_or_event() -> None:
    _seed("task-revision")
    with psycopg.connect(os.environ["IB01_TEST_DATABASE_URL"], autocommit=True) as conn:
        conn.execute("CREATE FUNCTION ib01_reject_projection() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RETURN NULL; END $$")
        conn.execute("CREATE TRIGGER ib01_reject_projection BEFORE UPDATE ON openair_tasks FOR EACH ROW EXECUTE FUNCTION ib01_reject_projection()")
    with pytest.raises(DurableExecutionError, match="revision_conflict"):
        DurableExecutionRepository().start_initial(
            task_id="task-revision", owner_id=OWNER, workspace_id=WORKSPACE, idempotency_key="conflict", source_mode="replay",
        )
    assert _query("SELECT * FROM openair_execution_runs WHERE task_id = 'task-revision'") == []
    assert _query("SELECT * FROM openair_execution_run_events") == []
    _record_scenario("revision_rollback", task_id="task-revision", run_id=None, call_id=None, artifact_id=None, null_reason="forced revision conflict rolled back all durable rows")


def test_gets_are_read_only_for_running_and_terminal_records() -> None:
    _seed("task-readonly")
    run, claimed = _start_and_claim("task-readonly")
    repo = DurableExecutionRepository()
    before = json.dumps(_query("SELECT * FROM openair_execution_runs ORDER BY run_id") + _query("SELECT * FROM openair_execution_run_events ORDER BY sequence"), default=str, sort_keys=True)
    repo.get_run(run["run_id"], OWNER, WORKSPACE)
    repo.list_events(run["run_id"], OWNER, WORKSPACE, 0)
    repo.get_run(run["run_id"], OWNER, WORKSPACE)
    after = json.dumps(_query("SELECT * FROM openair_execution_runs ORDER BY run_id") + _query("SELECT * FROM openair_execution_run_events ORDER BY sequence"), default=str, sort_keys=True)
    assert hashlib.sha256(before.encode()).digest() == hashlib.sha256(after.encode()).digest()
    repo.callback(run["run_id"], "W1", claimed["lease_epoch"], "failed", {})
    terminal_before = json.dumps(_query("SELECT * FROM openair_execution_runs") + _query("SELECT * FROM openair_execution_run_events ORDER BY sequence"), default=str, sort_keys=True)
    repo.get_run(run["run_id"], OWNER, WORKSPACE)
    assert terminal_before == json.dumps(_query("SELECT * FROM openair_execution_runs") + _query("SELECT * FROM openair_execution_run_events ORDER BY sequence"), default=str, sort_keys=True)
    _record_scenario("get_read_only", task_id="task-readonly", run_id=run["run_id"], call_id=run["capability_call_id"], artifact_id=None, null_reason="read-only scenario has no artifact")


def test_stale_lease_completion_is_zero_effect_and_audited() -> None:
    _seed("task-stale")
    run, first = _start_and_claim("task-stale")
    with psycopg.connect(os.environ["IB01_TEST_DATABASE_URL"], autocommit=True) as conn:
        conn.execute("UPDATE openair_execution_runs SET lease_expires_at = NOW() - INTERVAL '1 second' WHERE run_id = %s", (run["run_id"],))
    second = DurableExecutionRepository().claim("W2", 30)
    assert second and second["lease_epoch"] > first["lease_epoch"] and second["run_number"] == first["run_number"]
    with pytest.raises(DurableExecutionError) as exc:
        DurableExecutionRepository().callback(run["run_id"], "W1", first["lease_epoch"], "succeeded", {})
    assert exc.value.status_code == 409
    row = _query("SELECT state, lease_owner, lease_epoch FROM openair_execution_runs WHERE run_id = %s", (run["run_id"],))[0]
    assert row == {"state": "leased", "lease_owner": "W2", "lease_epoch": second["lease_epoch"]}
    assert _query("SELECT reason FROM openair_execution_callback_rejections WHERE run_id = %s", (run["run_id"],))[-1]["reason"] == "stale_or_expired_lease"
    _record_scenario("stale_lease_audit", task_id="task-stale", run_id=run["run_id"], call_id=run["capability_call_id"], artifact_id=None, null_reason="stale completion cannot attach artifact")


def test_execution_started_retry_is_exactly_once_and_old_fences_remain_rejected() -> None:
    _seed("task-started-retry")
    run, claimed = _start_and_claim("task-started-retry")
    repo = DurableExecutionRepository()
    first = repo.callback(run["run_id"], "W1", claimed["lease_epoch"], "execution_started", {})
    task_before = _query("SELECT revision, payload FROM openair_tasks WHERE task_id = %s", ("task-started-retry",))[0]
    replay = repo.callback(run["run_id"], "W1", claimed["lease_epoch"], "execution_started", {})
    task_after = _query("SELECT revision, payload FROM openair_tasks WHERE task_id = %s", ("task-started-retry",))[0]
    assert first["external_action_started"] is True
    assert replay["idempotent_replay"] is True
    assert task_after == task_before
    assert _query("SELECT count(*) AS count FROM openair_execution_run_events WHERE run_id = %s AND type = 'execution_started'", (run["run_id"],))[0]["count"] == 1
    with pytest.raises(DurableExecutionError, match="unknown_run"):
        repo.callback("run_missing", "W1", claimed["lease_epoch"], "execution_started", {})
    repo.callback(run["run_id"], "W1", claimed["lease_epoch"], "failed", {})
    with pytest.raises(DurableExecutionError, match="terminal_run"):
        repo.callback(run["run_id"], "W1", claimed["lease_epoch"], "execution_started", {})
    reasons = {row["reason"] for row in _query("SELECT reason FROM openair_execution_callback_rejections")}
    assert {"unknown_run", "terminal_run"}.issubset(reasons)


def test_api_execution_started_response_loss_retries_as_one_postgres_fact() -> None:
    _seed("task-api-started-retry")
    run, claimed = _start_and_claim("task-api-started-retry")
    parsed = urlparse(os.environ["IB01_TEST_DATABASE_URL"])
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    env = os.environ.copy()
    env.update({
        "OPENAIR_ENV": "test", "OPENAIR_PERSISTENCE_BACKEND": "postgres", "OPENAIR_DEV_SEED_FIXTURES": "0",
        "POSTGRES_HOST": parsed.hostname or "127.0.0.1", "POSTGRES_PORT": str(parsed.port or 5432),
        "POSTGRES_USER": parsed.username or "postgres", "POSTGRES_PASSWORD": parsed.password or "", "POSTGRES_DATABASE": parsed.path.lstrip("/"),
        "OPENAIR_WORKER_AUTH_TOKEN": "ib01-api-retry-token",
    })
    api_dir = Path(__file__).resolve().parents[2]
    process = subprocess.Popen([str(api_dir / ".venv" / "Scripts" / "uvicorn.exe"), "app.main:app", "--host", "127.0.0.1", "--port", str(port)], cwd=api_dir, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    url = f"http://127.0.0.1:{port}/internal/durable-runs/{run['run_id']}/callback"
    payload = json.dumps({"workerId": "W1", "leaseEpoch": claimed["lease_epoch"], "type": "execution_started", "payload": {}}).encode()
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            try:
                if urlopen(f"http://127.0.0.1:{port}/health", timeout=1).status == 200:  # nosec B310: local isolated API
                    break
            except OSError:
                time.sleep(0.1)
        else:
            pytest.fail("durable callback API did not start")
        # The first request is delivered, but its response is deliberately not
        # read.  PostgreSQL confirms the server committed before the client retry.
        client = socket.create_connection(("127.0.0.1", port), timeout=5)
        client.sendall((f"POST /internal/durable-runs/{run['run_id']}/callback HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nContent-Type: application/json\r\nX-OpenAIR-Worker-Token: ib01-api-retry-token\r\nContent-Length: {len(payload)}\r\nConnection: close\r\n\r\n").encode() + payload)
        client.close()
        while time.monotonic() < deadline and _query("SELECT count(*) AS count FROM openair_execution_run_events WHERE run_id = %s AND type = 'execution_started'", (run["run_id"],))[0]["count"] != 1:
            time.sleep(0.1)
        response = urlopen(Request(url, data=payload, method="POST", headers={"Content-Type": "application/json", "X-OpenAIR-Worker-Token": "ib01-api-retry-token"}), timeout=10)  # nosec B310: local isolated API
        body = json.loads(response.read().decode())
        assert response.status == 200 and body["idempotentReplay"] is True
        assert _query("SELECT count(*) AS count FROM openair_execution_run_events WHERE run_id = %s AND type = 'execution_started'", (run["run_id"],))[0]["count"] == 1
    finally:
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def test_postgres_task_lock_serializes_reconcile_callback_confirm_and_stop_without_deadlock() -> None:
    """Exercise the real PostgreSQL lock graph repeatedly, not a mock lock."""
    for attempt in range(3):
        task_id = f"task-lock-race-{attempt}"
        _seed(task_id)
        run, claimed = _start_and_claim(task_id)
        repo = DurableExecutionRepository()
        repo.callback(run["run_id"], "W1", claimed["lease_epoch"], "execution_started", {})
        payload = f"race-{attempt}".encode()
        presentation = _presentation(payload)
        repo.record_provider_presentation(run["run_id"], "W1", claimed["lease_epoch"], presentation)
        upload = repo.request_upload(run["run_id"], "W1", claimed["lease_epoch"], None, len(payload), hashlib.sha256(payload).hexdigest(), run["capability_call_id"], presentation)
        upload_bytes(task_id, f"race-{attempt}.bin", payload, object_key=upload["object_key"])
        with psycopg.connect(os.environ["IB01_TEST_DATABASE_URL"], autocommit=True) as conn:
            conn.execute("UPDATE openair_execution_runs SET lease_expires_at = NOW() - INTERVAL '1 second' WHERE run_id = %s", (run["run_id"],))

        def compete(operation: str) -> str:
            try:
                if operation == "claim":
                    DurableExecutionRepository().claim("W2", 30)
                elif operation == "callback":
                    DurableExecutionRepository().callback(run["run_id"], "W1", claimed["lease_epoch"], "failed", {})
                elif operation == "confirm":
                    DurableExecutionRepository().confirm_upload(run["run_id"], upload["upload_id"], "W1", claimed["lease_epoch"])
                else:
                    DurableExecutionRepository().stop_current(task_id, OWNER, WORKSPACE)
                return "ok"
            except DurableExecutionError as exc:
                assert exc.status_code < 500
                return f"durable:{exc.status_code}"
            except psycopg.Error as exc:
                return f"postgres:{exc}"

        with ThreadPoolExecutor(max_workers=4) as pool:
            outcomes = list(pool.map(compete, ("claim", "callback", "confirm", "stop")))
        assert not any("deadlock detected" in outcome.lower() for outcome in outcomes)
        state = _query("SELECT state FROM openair_execution_runs WHERE run_id = %s", (run["run_id"],))[0]["state"]
        assert state in {"abandoned", "cancelled"}
        assert _query("SELECT count(*) AS count FROM openair_execution_run_events WHERE run_id = %s AND type = 'abandoned'", (run["run_id"],))[0]["count"] <= 1


def test_artifact_success_is_atomic_and_failures_never_succeed() -> None:
    _seed("task-artifact")
    run, claimed = _start_and_claim("task-artifact")
    repo = DurableExecutionRepository()
    repo.callback(run["run_id"], "W1", claimed["lease_epoch"], "execution_started", {})
    data = b"verified artifact"
    digest = hashlib.sha256(data).hexdigest()
    presentation = _presentation(data)
    repo.record_provider_presentation(run["run_id"], "W1", claimed["lease_epoch"], presentation)
    upload = repo.request_upload(run["run_id"], "W1", claimed["lease_epoch"], "ib01/task-artifact/success.bin", len(data), digest, run["capability_call_id"], presentation)
    upload_bytes("task-artifact", "success.bin", data, object_key=upload["object_key"])
    confirmed = repo.confirm_upload(run["run_id"], upload["upload_id"], "W1", claimed["lease_epoch"])
    assert confirmed["artifact_id"]
    assert len(_query("SELECT * FROM openair_execution_artifact_associations")) == 1
    repo.callback(run["run_id"], "W1", claimed["lease_epoch"], "succeeded", {})
    assert _query("SELECT state FROM openair_execution_runs WHERE run_id = %s", (run["run_id"],))[0]["state"] == "succeeded"

    _seed("task-unreadable")
    bad_run, bad_claim = _start_and_claim("task-unreadable")
    repo.callback(bad_run["run_id"], "W1", bad_claim["lease_epoch"], "execution_started", {})
    bad_presentation = _presentation(b"x")
    repo.record_provider_presentation(bad_run["run_id"], "W1", bad_claim["lease_epoch"], bad_presentation)
    bad_upload = repo.request_upload(bad_run["run_id"], "W1", bad_claim["lease_epoch"], "ib01/task-unreadable/missing.bin", 1, hashlib.sha256(b"x").hexdigest(), bad_run["capability_call_id"], bad_presentation)
    with pytest.raises(DurableExecutionError, match="not_readable"):
        repo.confirm_upload(bad_run["run_id"], bad_upload["upload_id"], "W1", bad_claim["lease_epoch"])
    with pytest.raises(DurableExecutionError, match="barrier"):
        repo.callback(bad_run["run_id"], "W1", bad_claim["lease_epoch"], "succeeded", {})
    assert _query("SELECT state FROM openair_artifact_uploads WHERE upload_id = %s", (bad_upload["upload_id"],))[0]["state"] == "failed"

    _seed("task-association-failure")
    failed_run, failed_claim = _start_and_claim("task-association-failure")
    payload = b"will rollback association"
    repo.callback(failed_run["run_id"], "W1", failed_claim["lease_epoch"], "execution_started", {})
    failed_presentation = _presentation(payload)
    repo.record_provider_presentation(failed_run["run_id"], "W1", failed_claim["lease_epoch"], failed_presentation)
    failed_upload = repo.request_upload(
        failed_run["run_id"], "W1", failed_claim["lease_epoch"], "ib01/task-association-failure/output.bin",
        len(payload), hashlib.sha256(payload).hexdigest(), failed_run["capability_call_id"], failed_presentation,
    )
    upload_bytes("task-association-failure", "output.bin", payload, object_key=failed_upload["object_key"])
    with psycopg.connect(os.environ["IB01_TEST_DATABASE_URL"], autocommit=True) as conn:
        conn.execute("CREATE FUNCTION ib01_fail_artifact_insert() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'forced association failure'; END $$")
        conn.execute("CREATE TRIGGER ib01_fail_artifact_insert BEFORE INSERT ON openair_artifacts FOR EACH ROW EXECUTE FUNCTION ib01_fail_artifact_insert()")
    with pytest.raises(psycopg.Error):
        repo.confirm_upload(failed_run["run_id"], failed_upload["upload_id"], "W1", failed_claim["lease_epoch"])
    assert _query("SELECT state FROM openair_artifact_uploads WHERE upload_id = %s", (failed_upload["upload_id"],))[0]["state"] == "pending"
    assert _query("SELECT * FROM openair_execution_artifact_associations WHERE run_id = %s", (failed_run["run_id"],)) == []
    with pytest.raises(DurableExecutionError, match="barrier"):
        repo.callback(failed_run["run_id"], "W1", failed_claim["lease_epoch"], "succeeded", {})
    _record_scenario("artifact_success_and_failures", task_id="task-artifact", run_id=run["run_id"], call_id=run["capability_call_id"], artifact_id=confirmed["artifact_id"])


def test_api_owned_presentation_is_bound_before_upload_and_worker_cannot_replace_it() -> None:
    _seed("task-presentation-bound")
    private_prompt_marker = "private-frozen-prompt-marker"
    with psycopg.connect(os.environ["IB01_TEST_DATABASE_URL"], autocommit=True) as conn:
        conn.execute(
            "UPDATE openair_tasks SET payload = jsonb_set(payload, '{privatePrompt}', to_jsonb(%s::text)) WHERE task_id = %s",
            (private_prompt_marker, "task-presentation-bound"),
        )
    run, claimed = _start_and_claim("task-presentation-bound")
    repo = DurableExecutionRepository()
    content = b"bound durable artifact"
    accepted = {
        "title": "Provider-approved title",
        "mimeType": "text/markdown",
        "metadata": {"providerId": "openai_primary", "adapter": "huiyan_chat_completions", "contentLength": len(content.decode("utf-8"))},
        "contentSha256": hashlib.sha256(content).hexdigest(),
        "contentSize": len(content),
    }
    repo.callback(run["run_id"], "W1", claimed["lease_epoch"], "execution_started", {})
    repo.record_provider_presentation(run["run_id"], "W1", claimed["lease_epoch"], accepted)
    replacement = {**accepted, "title": "Worker replacement title"}
    with pytest.raises(DurableExecutionError, match="api_owned"):
        repo.request_upload(
            run["run_id"], "W1", claimed["lease_epoch"], None, len(content), hashlib.sha256(content).hexdigest(),
            run["capability_call_id"], replacement,
        )
    upload = repo.request_upload(
        run["run_id"], "W1", claimed["lease_epoch"], None, len(content), hashlib.sha256(content).hexdigest(),
        run["capability_call_id"], accepted,
    )
    upload_bytes("task-presentation-bound", "result.md", content, object_key=upload["object_key"])
    repo.confirm_upload(run["run_id"], upload["upload_id"], "W1", claimed["lease_epoch"])
    repo.callback(run["run_id"], "W1", claimed["lease_epoch"], "succeeded", {})
    artifact = _query(
        "SELECT title, format, mime_type, metadata, source_mode, source_execution_run_id, source_capability_call_id FROM openair_artifacts"
    )[0]
    assert artifact["title"] == accepted["title"]
    assert artifact["format"] == "md" and artifact["mime_type"] == accepted["mimeType"]
    assert artifact["metadata"] == {
        "taskId": "task-presentation-bound",
        "artifactState": "needs_review",
        "sourceMode": "replay",
    }
    assert artifact["source_mode"] == "replay"
    assert artifact["source_execution_run_id"] == run["run_id"]
    assert artifact["source_capability_call_id"] == run["capability_call_id"]
    assert _query(
        "SELECT payload FROM openair_execution_run_events WHERE run_id = %s AND type = 'provider_presentation_accepted'",
        (run["run_id"],),
    )[0]["payload"]["presentation"] == accepted
    evidence = json.dumps(_query("SELECT type, payload FROM openair_execution_run_events WHERE run_id = %s", (run["run_id"],)))
    assert private_prompt_marker not in evidence
    assert "bound durable artifact" not in evidence
    assert "contentBase64" not in evidence
    assert "test-only-not-a-live-key" not in evidence


def test_api_owned_integrity_rejects_replaced_hash_size_or_same_length_content() -> None:
    _seed("task-integrity-bound")
    run, claimed = _start_and_claim("task-integrity-bound")
    repo = DurableExecutionRepository()
    accepted_content = b"integrity-one"
    replacement_content = b"integrity-two"
    assert len(accepted_content) == len(replacement_content)
    accepted = _presentation(accepted_content)
    repo.callback(run["run_id"], "W1", claimed["lease_epoch"], "execution_started", {})
    repo.record_provider_presentation(run["run_id"], "W1", claimed["lease_epoch"], accepted)
    with pytest.raises(DurableExecutionError, match="api_owned"):
        repo.request_upload(
            run["run_id"], "W1", claimed["lease_epoch"], None, len(accepted_content) + 1,
            accepted["contentSha256"], run["capability_call_id"], accepted,
        )
    with pytest.raises(DurableExecutionError, match="api_owned"):
        repo.request_upload(
            run["run_id"], "W1", claimed["lease_epoch"], None, len(accepted_content),
            hashlib.sha256(replacement_content).hexdigest(), run["capability_call_id"], accepted,
        )
    upload = repo.request_upload(
        run["run_id"], "W1", claimed["lease_epoch"], None, len(accepted_content),
        accepted["contentSha256"], run["capability_call_id"], accepted,
    )
    upload_bytes("task-integrity-bound", "result.txt", replacement_content, object_key=upload["object_key"])
    with pytest.raises(DurableExecutionError, match="not_readable"):
        repo.confirm_upload(run["run_id"], upload["upload_id"], "W1", claimed["lease_epoch"])
    with pytest.raises(DurableExecutionError, match="barrier"):
        repo.callback(run["run_id"], "W1", claimed["lease_epoch"], "succeeded", {})
    assert _query("SELECT count(*) AS count FROM openair_artifacts")[0]["count"] == 0
    assert _query("SELECT count(*) AS count FROM openair_execution_run_events WHERE type = 'succeeded'")[0]["count"] == 0


def test_stop_terminal_barrier_and_legacy_run_marker_recovery() -> None:
    _seed("task-stop")
    run, claimed = _start_and_claim("task-stop")
    repo = DurableExecutionRepository()
    stopped = repo.stop_current("task-stop", OWNER, WORKSPACE)
    assert stopped["status"] == "cancelled"
    assert next(call for call in stopped["capabilityCalls"] if call["id"] == run["capability_call_id"])["status"] == "failed"
    assert repo.get_latest_run_for_task("task-stop", OWNER, WORKSPACE) is None
    with pytest.raises(DurableExecutionError) as exc:
        repo.callback(run["run_id"], "W1", claimed["lease_epoch"], "succeeded", {})
    assert exc.value.status_code == 409
    assert _query("SELECT state FROM openair_execution_runs WHERE run_id = %s", (run["run_id"],))[0]["state"] == "cancelled"

    _seed("task-complete")
    complete, complete_claim = _start_and_claim("task-complete")
    with pytest.raises(DurableExecutionError, match="artifact_completion_barrier_not_met"):
        repo.callback(complete["run_id"], "W1", complete_claim["lease_epoch"], "succeeded", {})
    repo.callback(
        complete["run_id"], "W1", complete_claim["lease_epoch"], "failed",
        {"failureClassification": "artifact_completion_barrier_not_met"},
    )
    with pytest.raises(DurableExecutionError, match="already_terminal"):
        repo.stop_current("task-complete", OWNER, WORKSPACE)

    _seed("task-crash")
    crash, crash_claim = _start_and_claim("task-crash")
    repo.callback(crash["run_id"], "W1", crash_claim["lease_epoch"], "execution_started", {})
    with psycopg.connect(os.environ["IB01_TEST_DATABASE_URL"], autocommit=True) as conn:
        conn.execute("UPDATE openair_execution_runs SET lease_expires_at = NOW() - INTERVAL '1 second' WHERE run_id = %s", (crash["run_id"],))
    reclaimed = repo.claim("W2", 30)
    assert reclaimed and reclaimed["run_id"] == crash["run_id"]
    assert reclaimed["lease_epoch"] == crash_claim["lease_epoch"] + 1
    repo.callback(
        crash["run_id"], "W2", reclaimed["lease_epoch"], "failed",
        {"failureClassification": "legacy_run_marker_without_call"},
    )
    assert not _query("SELECT * FROM openair_execution_run_events WHERE run_id = %s AND type = 'abandoned'", (crash["run_id"],))
    assert not _query("SELECT * FROM openair_artifacts WHERE source_execution_run_id = %s", (crash["run_id"],))
    _record_scenario("stop_terminal_barrier_and_legacy_recovery", task_id="task-stop", run_id=run["run_id"], call_id=run["capability_call_id"], artifact_id=None, null_reason="stop path forbids successful artifact")


def test_durable_orchestration_plan_api_records_only_a_fenced_plan_event() -> None:
    task = _task("task-orchestration-plan", stage="solution_design")
    task["skillPack"] = "sdr"
    task["creationPlan"] = {
        "orchestratorDecision": {
            "primaryAgent": "validation_design",
            "selectedEntryAgent": "validation_design",
        },
        "selectedCapabilities": [{
            "kind": "agent",
            "id": "validation_design",
            "skillIds": ["variable-identification"],
        }],
    }
    task["stageDetails"] = [{
        "stageKey": "solution_design",
        "status": "not_started",
        "subStages": [{"subStage": "variable_definition", "status": "not_started"}],
    }]
    TaskRepository().insert_task(task)
    run, claimed = DurableExecutionRepository().start_initial(
        task_id=task["taskId"], owner_id=OWNER, workspace_id=WORKSPACE,
        idempotency_key="orchestration-plan", source_mode="replay",
    )
    claimed = DurableExecutionRepository().claim("W1", 30)
    assert claimed and claimed["run_id"] == run["run_id"]

    parsed = urlparse(os.environ["IB01_TEST_DATABASE_URL"])
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    env = os.environ.copy()
    env.update({
        "OPENAIR_ENV": "test", "OPENAIR_PERSISTENCE_BACKEND": "postgres", "OPENAIR_DEV_SEED_FIXTURES": "0",
        "POSTGRES_HOST": parsed.hostname or "127.0.0.1", "POSTGRES_PORT": str(parsed.port or 5432),
        "POSTGRES_USER": parsed.username or "postgres", "POSTGRES_PASSWORD": parsed.password or "", "POSTGRES_DATABASE": parsed.path.lstrip("/"),
        "OPENAIR_WORKER_AUTH_TOKEN": "ib01-orchestration-plan-token",
    })
    api_dir = Path(__file__).resolve().parents[2]
    process = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)], cwd=api_dir, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    url = f"http://127.0.0.1:{port}/internal/durable-runs/{run['run_id']}/orchestration-plan"

    def request_plan(epoch: int) -> tuple[int, dict]:
        payload = json.dumps({"workerId": "W1", "leaseEpoch": epoch}).encode()
        request = Request(url, data=payload, method="POST", headers={"Content-Type": "application/json", "X-OpenAIR-Worker-Token": "ib01-orchestration-plan-token"})
        try:
            response = urlopen(request, timeout=10)  # nosec B310: local isolated API
            return response.status, json.loads(response.read().decode())
        except HTTPError as exc:
            return exc.code, json.loads(exc.read().decode())

    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            try:
                if urlopen(f"http://127.0.0.1:{port}/health", timeout=1).status == 200:  # nosec B310: local isolated API
                    break
            except OSError:
                time.sleep(0.1)
        else:
            pytest.fail("durable orchestration plan API did not start")

        before = _query(
            "SELECT state, external_action_started FROM openair_execution_runs WHERE run_id = %s",
            (run["run_id"],),
        )[0]
        status, response = request_plan(claimed["lease_epoch"])
        assert status == 200 and response["replayed"] is False
        plan = response["plan"]
        assert plan["context"] == {
            "runId": run["run_id"], "taskId": task["taskId"], "ownerId": OWNER, "workspaceId": WORKSPACE,
            "runNumber": 1, "leaseEpoch": claimed["lease_epoch"], "sourceMode": "replay",
            "currentStage": "solution_design", "targetSubstage": "variable_definition",
        }
        assert plan["selectedAgentId"] == "validation_design"
        assert plan["selectedSkillIds"] == ["variable-identification"]
        assert plan["selectedProvider"] == "openai_primary"
        assert plan["providerPolicy"]["toolRequirements"] == {"requiresFilesystem": False, "requiresNetwork": False}
        assert "inputSnapshot" not in plan
        assert _query("SELECT state, external_action_started FROM openair_execution_runs WHERE run_id = %s", (run["run_id"],))[0] == before
        assert _query("SELECT state FROM openair_execution_capability_calls WHERE run_id = %s", (run["run_id"],))[0]["state"] == "queued"
        assert _query("SELECT * FROM openair_artifacts WHERE source_execution_run_id = %s", (run["run_id"],)) == []
        assert _query("SELECT count(*) AS count FROM openair_execution_run_events WHERE run_id = %s AND type = 'orchestration_plan_created'", (run["run_id"],))[0]["count"] == 1

        replay_status, replay = request_plan(claimed["lease_epoch"])
        assert replay_status == 200 and replay["replayed"] is True and replay["plan"] == plan
        assert _query("SELECT count(*) AS count FROM openair_execution_run_events WHERE run_id = %s AND type = 'orchestration_plan_created'", (run["run_id"],))[0]["count"] == 1

        stale_status, _ = request_plan(claimed["lease_epoch"] + 1)
        assert stale_status == 409
        assert _query("SELECT reason FROM openair_execution_callback_rejections WHERE run_id = %s ORDER BY occurred_at DESC", (run["run_id"],))[0]["reason"] == "stale_or_expired_lease"

        stopped_task = _task("task-orchestration-plan-stopped")
        TaskRepository().insert_task(stopped_task)
        stopped_run, stopped_claim = _start_and_claim(stopped_task["taskId"])
        DurableExecutionRepository().stop_current(stopped_task["taskId"], OWNER, WORKSPACE)
        stopped_url = url.replace(run["run_id"], stopped_run["run_id"])
        payload = json.dumps({"workerId": "W1", "leaseEpoch": stopped_claim["lease_epoch"]}).encode()
        request = Request(stopped_url, data=payload, method="POST", headers={"Content-Type": "application/json", "X-OpenAIR-Worker-Token": "ib01-orchestration-plan-token"})
        with pytest.raises(HTTPError) as stopped_error:
            urlopen(request, timeout=10)  # nosec B310: local isolated API
        assert stopped_error.value.code == 409
    finally:
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _ib01b_api_env(*, fake_log: Path, fake_delay: float = 0, lease_seconds: int = 30) -> dict[str, str]:
    parsed = urlparse(os.environ["IB01_TEST_DATABASE_URL"])
    env = os.environ.copy()
    env.update({
        "OPENAIR_ENV": "test", "OPENAIR_PERSISTENCE_BACKEND": "postgres", "OPENAIR_DEV_SEED_FIXTURES": "0",
        "POSTGRES_HOST": parsed.hostname or "127.0.0.1", "POSTGRES_PORT": str(parsed.port or 5432),
        "POSTGRES_USER": parsed.username or "postgres", "POSTGRES_PASSWORD": parsed.password or "", "POSTGRES_DATABASE": parsed.path.lstrip("/"),
        "OPENAIR_ARTIFACT_STORAGE": "minio", "OPENAIR_MINIO_ENDPOINT": os.environ["IB01_TEST_MINIO_ENDPOINT"],
        "OPENAIR_MINIO_WORKER_ENDPOINT": os.environ["IB01_TEST_MINIO_ENDPOINT"],
        "OPENAIR_MINIO_ACCESS_KEY": os.environ["IB01_TEST_MINIO_ACCESS_KEY"],
        "OPENAIR_MINIO_SECRET_KEY": os.environ["IB01_TEST_MINIO_SECRET_KEY"], "OPENAIR_MINIO_BUCKET": os.environ["IB01_TEST_MINIO_BUCKET"],
        "OPENAIR_WORKER_AUTH_TOKEN": "ib01b-isolated-worker-token", "OPENAIR_DURABLE_TEST_FAKE_TRANSPORT": "1",
        "OPENAIR_DURABLE_TEST_FAKE_TRANSPORT_LOG": str(fake_log), "OPENAIR_DURABLE_TEST_FAKE_TRANSPORT_DELAY_SECONDS": str(fake_delay),
        "OPENAIR_DURABLE_RUN_LEASE_SECONDS": str(lease_seconds),
        "LLM_PROVIDER_MODEL": "ib01b-test-model",
        "LLM_PROVIDER_BASE_URL": "http://provider.invalid", "LLM_PROVIDER_API_KEY": "test-only-not-a-live-key",
    })
    return env


def _ib01b_start_api(env: dict[str, str]) -> tuple[subprocess.Popen, str]:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    api_dir = Path(__file__).resolve().parents[2]
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=api_dir, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    base_url = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        try:
            if urlopen(f"{base_url}/health", timeout=1).status == 200:  # nosec B310: isolated local API
                return process, base_url
        except OSError:
            time.sleep(0.1)
    subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    pytest.fail("IB-01B durable API did not start")


def _ib01b_post(base_url: str, path: str, body: dict) -> tuple[int, dict]:
    request = Request(
        f"{base_url}{path}", data=json.dumps(body).encode(), method="POST",
        headers={"Content-Type": "application/json", "X-OpenAIR-Worker-Token": "ib01b-isolated-worker-token"},
    )
    try:
        response = urlopen(request, timeout=20)  # nosec B310: isolated local API
        return response.status, json.loads(response.read().decode())
    except HTTPError as exc:
        return exc.code, json.loads(exc.read().decode())


def _ib01b_wait(predicate, *, timeout: float = 20) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.1)
    pytest.fail("IB-01B durable condition did not complete")


def _ib01b_worker_env(api_env: dict[str, str], base_url: str) -> dict[str, str]:
    env = api_env.copy()
    env.update({
        "OPENAIR_API_BASE_URL": base_url, "OPENAIR_DURABLE_WORKER_ID": "ib01b-http-worker",
        "OPENAIR_DURABLE_WORKER_POLL_SECONDS": "0.1", "OPENAIR_DURABLE_WORKER_EXIT_ON_HTTP_ERROR": "1",
        "OPENAIR_DURABLE_WORKER_RENEW_SECONDS": "0.5", "OPENAIR_DURABLE_ORCHESTRATE_TIMEOUT_SECONDS": "10",
        "NO_PROXY": "127.0.0.1,localhost",
    })
    return env


def _ib01b_start_worker(env: dict[str, str]) -> subprocess.Popen:
    api_dir = Path(__file__).resolve().parents[2]
    return subprocess.Popen(
        [sys.executable, "-m", "worker.entrypoint"], cwd=api_dir, env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def _ib01b_stop(process: subprocess.Popen | None) -> None:
    if process and process.poll() is None:
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def test_ib01b_worker_fails_closed_without_authorized_specialist(tmp_path: Path) -> None:
    _seed("task-ib01b-no-specialist")
    DurableExecutionRepository().start_initial(
        task_id="task-ib01b-no-specialist", owner_id=OWNER, workspace_id=WORKSPACE,
        idempotency_key="ib01b-no-specialist", source_mode="replay",
    )
    fake_log = tmp_path / "fake-calls.log"
    api_env = _ib01b_api_env(fake_log=fake_log)
    api, base_url = _ib01b_start_api(api_env)
    worker = None
    try:
        worker = _ib01b_start_worker(_ib01b_worker_env(api_env, base_url))
        _ib01b_wait(lambda: _query("SELECT state FROM openair_execution_runs")[0]["state"] == "failed")
        run = _query("SELECT state, external_action_started, failure_classification FROM openair_execution_runs")[0]
        assert run == {
            "state": "failed",
            "external_action_started": False,
            "failure_classification": "no_authorized_specialist",
        }
        assert not fake_log.exists()
        assert _query("SELECT count(*) AS count FROM openair_execution_idempotency")[0]["count"] == 1
        assert _query("SELECT count(*) AS count FROM openair_execution_capability_calls")[0]["count"] == 1
        assert _query("SELECT count(*) AS count FROM openair_artifacts")[0]["count"] == 0
        assert _query("SELECT count(*) AS count FROM openair_execution_run_events WHERE type = 'execution_started'")[0]["count"] == 0
        assert _query("SELECT count(*) AS count FROM openair_execution_run_events WHERE type = 'orchestration_plan_created'")[0]["count"] == 1
        assert _query("SELECT count(*) AS count FROM openair_execution_run_events WHERE type = 'abandoned'")[0]["count"] == 0
    finally:
        _ib01b_stop(worker)
        _ib01b_stop(api)


def test_ib01b_stop_before_orchestrate_calls_no_provider_and_creates_no_artifact(tmp_path: Path) -> None:
    _seed("task-ib01b-stop-before")
    run, claimed = _start_and_claim("task-ib01b-stop-before")
    fake_log = tmp_path / "fake-calls.log"
    api, base_url = _ib01b_start_api(_ib01b_api_env(fake_log=fake_log))
    try:
        DurableExecutionRepository().stop_current("task-ib01b-stop-before", OWNER, WORKSPACE)
        status, _ = _ib01b_post(base_url, f"/internal/durable-runs/{run['run_id']}/orchestrate", {"workerId": "W1", "leaseEpoch": claimed["lease_epoch"]})
        assert status == 409
        assert not fake_log.exists()
        assert _query("SELECT count(*) AS count FROM openair_artifacts")[0]["count"] == 0
        assert _query("SELECT count(*) AS count FROM openair_execution_run_events WHERE type = 'succeeded'")[0]["count"] == 0
    finally:
        _ib01b_stop(api)


def test_production_migration_clean_upgrade_supported_down_and_upgrade_again() -> None:
    admin_dsn = os.environ["IB01_TEST_DATABASE_URL"]
    migration_database = "openair_t00_migration_qa"
    parsed = urlparse(admin_dsn)
    assert parsed.path.lstrip("/") != migration_database
    migration_dsn = parsed._replace(path=f"/{migration_database}").geturl()
    migrations = Path(__file__).resolve().parents[2] / "migrations"
    down_version = "20260725_02_durable_research_start.sql"
    down_sql = (migrations / "20260725_02_durable_research_start_down.sql").read_text(encoding="utf-8")
    expected_versions = sorted(
        path.name for path in migrations.glob("*.sql") if not path.name.endswith("_down.sql")
    )
    postgres_settings = {
        "postgres_host": settings.postgres_host,
        "postgres_port": settings.postgres_port,
        "postgres_user": settings.postgres_user,
        "postgres_password": settings.postgres_password,
        "postgres_database": settings.postgres_database,
    }

    with psycopg.connect(admin_dsn, autocommit=True) as admin:
        assert admin.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", (migration_database,)
        ).fetchone() is None
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(migration_database)))

    try:
        settings.postgres_host = parsed.hostname or "127.0.0.1"
        settings.postgres_port = parsed.port or 5432
        settings.postgres_user = parsed.username or "postgres"
        settings.postgres_password = parsed.password or ""
        settings.postgres_database = migration_database

        TaskRepository().bootstrap()
        ArtifactRepository().bootstrap()
        DurableExecutionRepository().bootstrap()

        with psycopg.connect(migration_dsn, autocommit=True) as conn:
            applied = [
                row[0]
                for row in conn.execute(
                    "SELECT version FROM openair_schema_migrations ORDER BY version"
                ).fetchall()
            ]
            assert applied == expected_versions
            assert conn.execute("SELECT to_regclass('openair_execution_runs')").fetchone()[0] == "openair_execution_runs"
            assert conn.execute("SELECT to_regclass('openair_execution_work_units')").fetchone()[0] == "openair_execution_work_units"
            assert conn.execute("SELECT to_regclass('research_start_sessions')").fetchone()[0] == "research_start_sessions"
            assert conn.execute(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_name = 'openair_tasks' AND column_name = 'revision'"
            ).fetchone() == (1,)

            conn.execute(down_sql)
            assert conn.execute("SELECT to_regclass('research_start_sessions')").fetchone()[0] is None
            assert conn.execute("SELECT to_regclass('research_start_requests')").fetchone()[0] is None
            assert conn.execute("SELECT to_regclass('research_start_attempts')").fetchone()[0] is None
            assert conn.execute(
                "SELECT 1 FROM openair_schema_migrations WHERE version = %s", (down_version,)
            ).fetchone() is None

        DurableExecutionRepository().bootstrap()
        with psycopg.connect(migration_dsn, autocommit=True) as conn:
            restored = [
                row[0]
                for row in conn.execute(
                    "SELECT version FROM openair_schema_migrations ORDER BY version"
                ).fetchall()
            ]
            assert restored == expected_versions
            assert conn.execute("SELECT to_regclass('research_start_sessions')").fetchone()[0] == "research_start_sessions"
    finally:
        for field, value in postgres_settings.items():
            setattr(settings, field, value)
        with psycopg.connect(admin_dsn, autocommit=True) as admin:
            admin.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()",
                (migration_database,),
            )
            admin.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(migration_database)))
            assert admin.execute(
                "SELECT 1 FROM pg_database WHERE datname = %s", (migration_database,)
            ).fetchone() is None

    _record_scenario(
        "production_migration_upgrade_down_upgrade",
        task_id=None,
        run_id=None,
        call_id=None,
        artifact_id=None,
        null_reason="schema-only production migration lifecycle",
    )


@pytest.mark.parametrize(
    ("mode", "seam", "expected_error"),
    [
        ("replay", "", None),
        ("live", "controlled_result", None),
        ("live", "controlled_failure", "chat_runtime_failed"),
    ],
)
def test_explicit_collaboration_chat_uses_postgres_cas_and_never_dispatches(
    mode: str, seam: str, expected_error: str | None,
) -> None:
    """A real API process proves the preserved explicit chat path is not TaskStore-backed."""
    task_id = f"task-explicit-chat-{mode}"
    _seed(task_id)
    parsed = urlparse(os.environ["IB01_TEST_DATABASE_URL"])
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    env = os.environ.copy()
    env.update({
        "OPENAIR_ENV": "test", "OPENAIR_PERSISTENCE_BACKEND": "postgres",
        "POSTGRES_HOST": parsed.hostname or "127.0.0.1", "POSTGRES_PORT": str(parsed.port or 5432),
        "POSTGRES_USER": parsed.username or "postgres", "POSTGRES_PASSWORD": parsed.password or "",
        "POSTGRES_DATABASE": parsed.path.lstrip("/"), "OPENAIR_DEV_SEED_FIXTURES": "0",
        "SKILL_EXEC_MODE": mode, "OPENAIR_ARTIFACT_STORAGE": "minio",
        "OPENAIR_MINIO_ENDPOINT": os.environ["IB01_TEST_MINIO_ENDPOINT"],
        "OPENAIR_MINIO_ACCESS_KEY": os.environ["IB01_TEST_MINIO_ACCESS_KEY"],
        "OPENAIR_MINIO_SECRET_KEY": os.environ["IB01_TEST_MINIO_SECRET_KEY"],
        "OPENAIR_MINIO_BUCKET": os.environ["IB01_TEST_MINIO_BUCKET"],
    })
    if seam:
        env["OPENAIR_IB01_TEST_LIVE_CHAT_SEAM"] = seam
    api_dir = Path(__file__).resolve().parents[2]
    process = subprocess.Popen(
        [str(api_dir / ".venv" / "Scripts" / "uvicorn.exe"), "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=api_dir, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    base_url = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 20
        while True:
            try:
                with urlopen(f"{base_url}/health", timeout=1) as response:  # nosec B310: local isolated API
                    if response.status == 200:
                        break
            except OSError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.1)
        request = Request(
            f"{base_url}/api/tasks/{task_id}/agent/chat/stream",
            data=b'{"message":"Summarize this explicit collaboration request."}', method="POST",
            headers={"Content-Type": "application/json", "X-OpenAIR-Owner-Id": OWNER, "X-OpenAIR-Workspace-Id": WORKSPACE, "X-OpenAIR-Created-By": OWNER},
        )
        with urlopen(request, timeout=15) as response:  # nosec B310: local isolated API
            raw_stream = response.read().decode("utf-8")
            events = [json.loads(line) for line in raw_stream.splitlines() if line]
        done = events[-1]
        assert done["type"] == "done" and done["error"] == expected_error and done["capabilityCallId"]
        assert str(done["capabilityCallId"]).startswith("collab_")
        assert "controlled_live_runtime_failure" not in raw_stream
        refreshed_request = Request(f"{base_url}/api/tasks/{task_id}/agent/messages", headers={"X-OpenAIR-Owner-Id": OWNER, "X-OpenAIR-Workspace-Id": WORKSPACE})
        with urlopen(refreshed_request, timeout=5) as response:  # nosec B310
            refreshed = json.loads(response.read().decode("utf-8"))
        assert len(refreshed["messages"]) == 2
        assert refreshed["messages"][0]["role"] == "user"
        assert refreshed["messages"][1]["role"] == "assistant"
        if expected_error is None:
            assert refreshed["messages"][1]["text"]
            if mode == "live" and seam == "controlled_result":
                assert "[replay" not in done["reply"].lower()
        row = _query("SELECT revision, payload FROM openair_tasks WHERE task_id = %s", (task_id,))[0]
        assert row["revision"] == 2
        # Collaboration is messages-only; do not stuff chat turns into research capabilityCalls.
        assert row["payload"].get("capabilityCalls") == []
        assert row["payload"].get("lastCollaborationTurnId") == done["capabilityCallId"]
        assert _query("SELECT * FROM openair_execution_runs WHERE task_id = %s", (task_id,)) == []
        from app.modules.task_store import store

        assert task_id not in store._tasks
        _record_scenario(f"explicit_chat_postgres_{mode}_{seam or 'default'}", task_id=task_id, run_id=None, call_id=done["capabilityCallId"], artifact_id=None, null_reason="explicit chat intentionally creates no durable execution artifact")
    finally:
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()


@pytest.mark.parametrize(
    ("mode", "seam", "policy"),
    [("replay", "", "queue"), ("live", "controlled_failure", "cancel")],
)
def test_explicit_chat_active_gateway_is_pure_in_postgres(mode: str, seam: str, policy: str) -> None:
    """Active-call gateway policies must not touch TaskStore before PG CAS."""
    task_id = f"task-explicit-active-{mode}-{policy}"
    task = _task(task_id)
    task["stageDetails"] = [{"stageKey": "problem_modeling", "status": "running", "subStages": [{"subStage": "chat_substage", "subStageInstanceId": "sub_chat", "status": "running"}]}]
    task["capabilityCalls"] = [{"id": "call_active", "status": "running", "subStageInstanceId": "sub_chat", "stageKey": "problem_modeling", "name": "existing"}]
    TaskRepository().insert_task(task)
    parsed = urlparse(os.environ["IB01_TEST_DATABASE_URL"])
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    env = os.environ.copy()
    env.update({
        "OPENAIR_ENV": "test", "OPENAIR_PERSISTENCE_BACKEND": "postgres", "SKILL_EXEC_MODE": mode,
        "POSTGRES_HOST": parsed.hostname or "127.0.0.1", "POSTGRES_PORT": str(parsed.port or 5432),
        "POSTGRES_USER": parsed.username or "postgres", "POSTGRES_PASSWORD": parsed.password or "", "POSTGRES_DATABASE": parsed.path.lstrip("/"),
        "OPENAIR_DEV_SEED_FIXTURES": "0", "OPENAIR_ARTIFACT_STORAGE": "minio", "OPENAIR_MINIO_ENDPOINT": os.environ["IB01_TEST_MINIO_ENDPOINT"],
        "OPENAIR_MINIO_ACCESS_KEY": os.environ["IB01_TEST_MINIO_ACCESS_KEY"], "OPENAIR_MINIO_SECRET_KEY": os.environ["IB01_TEST_MINIO_SECRET_KEY"], "OPENAIR_MINIO_BUCKET": os.environ["IB01_TEST_MINIO_BUCKET"],
    })
    if seam:
        env["OPENAIR_IB01_TEST_LIVE_CHAT_SEAM"] = seam
    api_dir = Path(__file__).resolve().parents[2]
    process = subprocess.Popen([str(api_dir / ".venv" / "Scripts" / "uvicorn.exe"), "app.main:app", "--host", "127.0.0.1", "--port", str(port)], cwd=api_dir, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base_url = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 20
        while True:
            try:
                with urlopen(f"{base_url}/health", timeout=1) as response:  # nosec B310
                    if response.status == 200:
                        break
            except OSError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.1)
        request = Request(
            f"{base_url}/api/tasks/{task_id}/agent/chat/stream",
            data=json.dumps({"message": "replace or queue this explicit chat", "substageKey": "chat_substage", "inputPolicy": policy}).encode(), method="POST",
            headers={"Content-Type": "application/json", "X-OpenAIR-Owner-Id": OWNER, "X-OpenAIR-Workspace-Id": WORKSPACE},
        )
        with urlopen(request, timeout=15) as response:  # nosec B310
            events = [json.loads(line) for line in response.read().decode().splitlines() if line]
        done = events[-1]
        assert done["type"] == "done"
        assert str(done.get("capabilityCallId") or "").startswith("collab_")
        row = _query("SELECT payload FROM openair_tasks WHERE task_id = %s", (task_id,))[0]["payload"]
        calls = row["capabilityCalls"]
        # Gateway replies are messages-only; research capabilityCalls stay untouched.
        assert calls == [{"id": "call_active", "status": "running", "subStageInstanceId": "sub_chat", "stageKey": "problem_modeling", "name": "existing"}]
        assert row.get("lastCollaborationTurnId") == done["capabilityCallId"]
        assert _query("SELECT * FROM openair_execution_runs WHERE task_id = %s", (task_id,)) == []
        from app.modules.task_store import store

        assert task_id not in store._tasks
        _record_scenario(f"explicit_chat_active_{mode}_{policy}", task_id=task_id, run_id=None, call_id=None, artifact_id=None, null_reason="active gateway chat creates no durable execution artifact")
    finally:
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
