"""T01 PostgreSQL CAS, outbox atomicity, migration, and performance evidence."""

from __future__ import annotations

import atexit
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from copy import deepcopy
import json
import math
import os
from pathlib import Path
from threading import Barrier
from time import perf_counter
from urllib.parse import urlparse, urlunparse

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
import pytest

from app.config import settings
from app.models.research_orchestration import DispatchRecord, ResearchTaskV12
from app.modules.research_orchestration_repository import (
    DispatchIdentityConflictError,
    ResearchOrchestrationRepository,
    ResearchTaskNotFoundError,
    TaskRevisionConflictError,
)


ADMIN_DSN_ENV = "OPENAIR_T01_POSTGRES_ADMIN_DSN"
TASK_DATABASE = "openair_t01_qa"
MIGRATION_DATABASE = "openair_t01_migration_qa"
DATABASE_MARKER = "OpenAIR T01 owned QA database (research-orchestration-root-cause/T01)"
MIGRATIONS = Path(__file__).resolve().parents[4] / "migrations"
UP_MIGRATION = MIGRATIONS / "20260728_01_research_orchestration_v1_2.sql"
DOWN_MIGRATION = MIGRATIONS / "20260728_01_research_orchestration_v1_2_down.sql"
SHARED_FIXTURE = (
    Path(__file__).resolve().parents[6]
    / "packages"
    / "shared"
    / "test-fixtures"
    / "research-orchestration-v1.2-valid.json"
)


def _database_dsn(admin_dsn: str, database: str) -> str:
    parsed = urlparse(admin_dsn)
    return urlunparse(parsed._replace(path=f"/{database}"))


def _validate_admin_dsn(dsn: str) -> None:
    parsed = urlparse(dsn)
    if parsed.scheme not in {"postgres", "postgresql"} or not parsed.hostname:
        pytest.fail(f"{ADMIN_DSN_ENV} must be an explicit PostgreSQL admin DSN")
    if parsed.path.lstrip("/").lower() == "openair_t00_qa":
        pytest.fail("T01 refuses to use openair_t00_qa as its admin database")


def _database_description(admin: psycopg.Connection, database: str) -> str | None:
    row = admin.execute(
        """SELECT pg_catalog.shobj_description(oid, 'pg_database') AS description
           FROM pg_catalog.pg_database WHERE datname = %s""",
        (database,),
    ).fetchone()
    return None if row is None else row["description"]


def _drop_owned_database(admin: psycopg.Connection, database: str) -> None:
    if database not in {TASK_DATABASE, MIGRATION_DATABASE}:
        raise AssertionError(f"refusing unexpected database target: {database}")
    description = _database_description(admin, database)
    if description is None:
        return
    if description != DATABASE_MARKER:
        pytest.fail(f"refusing to drop unowned database: {database}")
    admin.execute(
        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s AND pid <> pg_backend_pid()",
        (database,),
    )
    admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(database)))


def _create_owned_database(admin: psycopg.Connection, database: str) -> None:
    description = _database_description(admin, database)
    if description is not None:
        if description != DATABASE_MARKER:
            pytest.fail(f"database already exists without the T01 ownership marker: {database}")
        _drop_owned_database(admin, database)
    admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
    admin.execute(
        sql.SQL("COMMENT ON DATABASE {} IS {}").format(
            sql.Identifier(database),
            sql.Literal(DATABASE_MARKER),
        )
    )


def _bootstrap_production_schema(dsn: str) -> None:
    from tests.integration._production_postgres_bootstrap import bootstrap_production_postgres_schema

    with psycopg.connect(dsn, row_factory=dict_row) as connection:
        with connection.cursor() as cursor:
            bootstrap_production_postgres_schema(cursor)



def _bootstrap_pre_t01_schema(dsn: str) -> None:
    from app.modules.postgres_migrations import MIGRATIONS_DIR
    from tests.integration._production_postgres_bootstrap import bootstrap_production_migration_prerequisites

    with psycopg.connect(dsn, row_factory=dict_row) as connection:
        with connection.cursor() as cursor:
            bootstrap_production_migration_prerequisites(cursor)
            cursor.execute(
                """CREATE TABLE IF NOT EXISTS openair_schema_migrations (
                   version TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW())"""
            )
            for migration_path in sorted(
                path for path in MIGRATIONS_DIR.glob("*.sql") if not path.name.endswith("_down.sql")
            ):
                if migration_path.name == UP_MIGRATION.name:
                    break
                cursor.execute(migration_path.read_text(encoding="utf-8"))
                cursor.execute(
                    "INSERT INTO openair_schema_migrations(version) VALUES (%s)",
                    (migration_path.name,),
                )



def _bootstrap_t01_schema(dsn: str) -> None:
    _bootstrap_pre_t01_schema(dsn)
    with psycopg.connect(dsn, row_factory=dict_row) as connection:
        connection.execute(UP_MIGRATION.read_text(encoding="utf-8"))
        connection.execute(
            "INSERT INTO openair_schema_migrations(version) VALUES (%s)",
            (UP_MIGRATION.name,),
        )


def _persistent_table_catalog(connection: psycopg.Connection) -> set[tuple[str, str, str]]:
    return {
        (row["schema_name"], row["table_name"], row["relation_kind"])
        for row in connection.execute(
            """SELECT namespace.nspname AS schema_name,
                      relation.relname AS table_name,
                      relation.relkind::text AS relation_kind
                 FROM pg_catalog.pg_class AS relation
                 JOIN pg_catalog.pg_namespace AS namespace ON namespace.oid = relation.relnamespace
                WHERE namespace.nspname = 'public'
                  AND relation.relkind IN ('r', 'p')
                ORDER BY namespace.nspname, relation.relname"""
        ).fetchall()
    }


def _migration_versions(connection: psycopg.Connection) -> set[str]:
    return {
        row["version"]
        for row in connection.execute(
            "SELECT version FROM openair_schema_migrations ORDER BY version"
        ).fetchall()
    }


@pytest.fixture(scope="session")
def postgres_environment():
    admin_dsn = os.environ.get(ADMIN_DSN_ENV, "").strip()
    if not admin_dsn:
        pytest.fail(f"{ADMIN_DSN_ENV} is required; T01 refuses an implicit or shared database")
    _validate_admin_dsn(admin_dsn)
    task_dsn = _database_dsn(admin_dsn, TASK_DATABASE)

    def cleanup_at_process_exit() -> None:
        with psycopg.connect(admin_dsn, autocommit=True, row_factory=dict_row) as cleanup_admin:
            _drop_owned_database(cleanup_admin, TASK_DATABASE)

    atexit.register(cleanup_at_process_exit)
    with psycopg.connect(admin_dsn, autocommit=True, row_factory=dict_row) as admin:
        description = _database_description(admin, TASK_DATABASE)
        if description is None:
            _create_owned_database(admin, TASK_DATABASE)
        elif description != DATABASE_MARKER:
            pytest.fail(f"database already exists without the T01 ownership marker: {TASK_DATABASE}")
        _bootstrap_production_schema(task_dsn)

        def connect() -> psycopg.Connection:
            return psycopg.connect(task_dsn, row_factory=dict_row)

        yield admin_dsn, task_dsn, connect


@pytest.fixture(autouse=True)
def clean_t01_rows(postgres_environment):
    _admin_dsn, _task_dsn, connect = postgres_environment
    with connect() as connection:
        connection.execute("DELETE FROM openair_dispatch")
        connection.execute("DELETE FROM openair_work_unit_lineage")
        connection.execute("DELETE FROM openair_tasks")
    yield
    with connect() as connection:
        connection.execute("DELETE FROM openair_dispatch")
        connection.execute("DELETE FROM openair_work_unit_lineage")
        connection.execute("DELETE FROM openair_tasks")


@pytest.fixture()
def enabled_v1_repository(monkeypatch: pytest.MonkeyPatch, postgres_environment):
    _admin_dsn, _task_dsn, connect = postgres_environment
    monkeypatch.setattr(settings, "research_orchestration_v1", True)
    monkeypatch.setattr(settings, "persistence_backend", "postgres")
    yield ResearchOrchestrationRepository(
        connection_factory=connect,
        runtime_settings=settings,
    ), connect


def _replace_fixture_identifiers(value: object, suffix: str) -> object:
    identifiers = (
        "task_contract_full",
        "planning_contract_full",
        "request_contract_full",
        "provider_call_contract_full",
        "rd_contract_full",
        "sub_contract_full",
        "criterion_contract_full",
        "target_contract_full",
        "coverage_contract_full",
        "confirmation_design_contract_full",
        "selection_contract_full",
        "decision_contract_full",
        "candidate_contract_full",
        "assignment_contract_full",
        "wu_contract_full",
        "call_contract_full",
        "artifact_contract_full",
        "interaction_contract_full",
        "dispatch_contract_full",
    )
    if isinstance(value, dict):
        return {key: _replace_fixture_identifiers(item, suffix) for key, item in value.items()}
    if isinstance(value, list):
        return [_replace_fixture_identifiers(item, suffix) for item in value]
    if isinstance(value, str):
        transformed = value
        for identifier in identifiers:
            transformed = transformed.replace(identifier, f"{identifier}_{suffix}")
        return transformed
    return value


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
        {
            "status": "queued",
            "terminalReasonCode": None,
            "finishedAt": None,
        }
    )
    task_payload["workUnits"][0]["budgetSnapshot"]["consumedToolCalls"] = 0
    task_payload["workUnits"][0]["budgetSnapshot"]["consumedCostAmount"] = 0.0
    task_payload["capabilityCalls"] = []
    task_payload["artifacts"] = []
    task_payload["agentInteractionRecords"] = []
    candidate = ResearchTaskV12.model_validate(task_payload)

    baseline_payload = candidate.model_dump(mode="json", by_alias=True)
    baseline_payload["executionDecisions"] = []
    baseline_payload["workUnits"] = []
    baseline_payload["substageRuntimeStates"][0].update(
        {
            "status": "ready",
            "activeExecutionDecisionId": None,
            "workUnitIds": [],
        }
    )
    baseline = ResearchTaskV12.model_validate(baseline_payload)
    transformed["dispatchRecord"]["executionNamespace"] = "batch-g-test"
    dispatch = DispatchRecord.model_validate(transformed["dispatchRecord"])
    return baseline, candidate, dispatch


def _insert_task(connect, task: ResearchTaskV12, *, owner: str, workspace: str) -> None:
    payload = task.model_dump(mode="json", by_alias=True)
    with connect() as connection:
        connection.execute(
            """INSERT INTO openair_tasks (
                   task_id, owner_id, workspace_id, status, current_stage, title,
                   domain, created_at, updated_at, payload, revision
               ) VALUES (%s, %s, %s, %s, %s, %s, %s, NOW(), NOW(), %s::jsonb, %s)""",
            (
                task.task_id,
                owner,
                workspace,
                task.status.value,
                "solution_design",
                f"T01 {task.task_id}",
                "research_task",
                json.dumps(payload),
                task.revision,
            ),
        )


def _task_row(connect, task_id: str) -> dict:
    with connect() as connection:
        row = connection.execute(
            "SELECT payload, revision FROM openair_tasks WHERE task_id = %s",
            (task_id,),
        ).fetchone()
    assert row is not None
    return row


def _dispatch_constraint_catalog(connection: psycopg.Connection) -> dict[str, dict]:
    rows = connection.execute(
        """SELECT con.conname,
                  con.contype,
                  pg_get_constraintdef(con.oid, true) AS definition,
                  ARRAY(
                      SELECT att.attname
                        FROM unnest(con.conkey) WITH ORDINALITY AS key_column(attnum, ordinal)
                        JOIN pg_attribute AS att
                          ON att.attrelid = con.conrelid AND att.attnum = key_column.attnum
                       ORDER BY key_column.ordinal
                  ) AS local_columns,
                  con.confrelid::regclass::text AS referenced_table,
                  ARRAY(
                      SELECT att.attname
                        FROM unnest(con.confkey) WITH ORDINALITY AS key_column(attnum, ordinal)
                        JOIN pg_attribute AS att
                          ON att.attrelid = con.confrelid AND att.attnum = key_column.attnum
                       ORDER BY key_column.ordinal
                  ) AS referenced_columns,
                  con.confdeltype AS on_delete
             FROM pg_constraint AS con
            WHERE con.conrelid = 'openair_dispatch'::regclass
            ORDER BY con.conname"""
    ).fetchall()
    return {row["conname"]: row for row in rows}


def _dispatch_index_catalog(connection: psycopg.Connection) -> dict[str, dict]:
    rows = connection.execute(
        """SELECT index_class.relname AS name,
                  pg_get_indexdef(index_data.indexrelid, 0, true) AS definition,
                  COALESCE(pg_get_expr(index_data.indpred, index_data.indrelid, true), '') AS predicate,
                  ARRAY(
                      SELECT att.attname
                        FROM unnest(index_data.indkey) WITH ORDINALITY AS key_column(attnum, ordinal)
                        JOIN pg_attribute AS att
                          ON att.attrelid = index_data.indrelid AND att.attnum = key_column.attnum
                       WHERE key_column.attnum > 0
                       ORDER BY key_column.ordinal
                  ) AS columns
             FROM pg_index AS index_data
             JOIN pg_class AS index_class ON index_class.oid = index_data.indexrelid
            WHERE index_data.indrelid = 'openair_dispatch'::regclass
            ORDER BY index_class.relname"""
    ).fetchall()
    return {row["name"]: row for row in rows}


def test_domain_payload_round_trip_and_pending_outbox_share_one_transaction(
    enabled_v1_repository,
) -> None:
    repository, connect = enabled_v1_repository
    baseline, candidate, dispatch = _transaction_fixture("roundtrip")
    _insert_task(connect, baseline, owner="owner-roundtrip", workspace="workspace-roundtrip")

    saved = repository.save(
        candidate,
        owner_id="owner-roundtrip",
        workspace_id="workspace-roundtrip",
        expected_task_revision=0,
        pending_dispatches=[dispatch],
        automatic_dispatch=True,
    )
    loaded = repository.load(
        task_id=candidate.task_id,
        owner_id="owner-roundtrip",
        workspace_id="workspace-roundtrip",
    )

    assert saved == loaded
    assert saved.revision == 1
    row = _task_row(connect, candidate.task_id)
    assert row["revision"] == row["payload"]["revision"] == 1
    assert [unit.work_unit_id for unit in loaded.work_units] == [dispatch.work_unit_id]
    with connect() as connection:
        outbox = connection.execute(
            "SELECT dispatch_id, work_unit_id, idempotency_key, status FROM openair_dispatch"
        ).fetchall()
    assert outbox == [
        {
            "dispatch_id": dispatch.dispatch_id,
            "work_unit_id": dispatch.work_unit_id,
            "idempotency_key": dispatch.idempotency_key,
            "status": "pending",
        }
    ]
    with pytest.raises(ResearchTaskNotFoundError):
        repository.load(
            task_id=candidate.task_id,
            owner_id="another-owner",
            workspace_id="workspace-roundtrip",
        )


def test_two_concurrent_writers_with_one_expected_revision_have_one_winner(
    enabled_v1_repository,
) -> None:
    repository, connect = enabled_v1_repository
    baseline, candidate, dispatch = _transaction_fixture("concurrent")
    _insert_task(connect, baseline, owner="owner-concurrent", workspace="workspace-concurrent")
    barrier = Barrier(2)

    def write() -> ResearchTaskV12:
        barrier.wait()
        return repository.save(
            candidate,
            owner_id="owner-concurrent",
            workspace_id="workspace-concurrent",
            expected_task_revision=0,
            pending_dispatches=[dispatch],
            automatic_dispatch=True,
        )

    successes: list[ResearchTaskV12] = []
    conflicts: list[TaskRevisionConflictError] = []
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(write) for _ in range(2)]
        for future in futures:
            try:
                successes.append(future.result(timeout=10))
            except TaskRevisionConflictError as exc:
                conflicts.append(exc)

    assert len(successes) == len(conflicts) == 1
    loser_view = repository.load(
        task_id=candidate.task_id,
        owner_id="owner-concurrent",
        workspace_id="workspace-concurrent",
    )
    assert loser_view == successes[0]
    assert loser_view.revision == 1
    with connect() as connection:
        dispatch_count = connection.execute(
            "SELECT count(*) AS count FROM openair_dispatch"
        ).fetchone()["count"]
    print(
        "T01_CONCURRENCY_EVIDENCE "
        f"successes={len(successes)} conflicts={len(conflicts)} dispatches={dispatch_count}"
    )
    assert dispatch_count == 1


def test_stale_cas_conflict_leaves_no_work_unit_or_dispatch(enabled_v1_repository) -> None:
    repository, connect = enabled_v1_repository
    baseline, candidate, dispatch = _transaction_fixture("stale")
    _insert_task(connect, baseline, owner="owner-stale", workspace="workspace-stale")

    first_change_payload = baseline.model_dump(mode="json", by_alias=True)
    first_change_payload["executionPolicyVersion"] = "execution-policy.winner"
    first_change = ResearchTaskV12.model_validate(first_change_payload)
    repository.save(
        first_change,
        owner_id="owner-stale",
        workspace_id="workspace-stale",
        expected_task_revision=0,
    )

    with pytest.raises(TaskRevisionConflictError):
        repository.save(
            candidate,
            owner_id="owner-stale",
            workspace_id="workspace-stale",
            expected_task_revision=0,
            pending_dispatches=[dispatch],
            automatic_dispatch=True,
        )
    row = _task_row(connect, candidate.task_id)
    assert row["revision"] == row["payload"]["revision"] == 1
    assert row["payload"]["executionPolicyVersion"] == "execution-policy.winner"
    assert row["payload"]["workUnits"] == []
    with connect() as connection:
        assert connection.execute("SELECT count(*) AS count FROM openair_dispatch").fetchone()["count"] == 0


def test_pending_outbox_rejects_nonqueued_work_unit_before_task_cas(enabled_v1_repository) -> None:
    repository, connect = enabled_v1_repository
    baseline, candidate, dispatch = _transaction_fixture("nonqueued")
    _insert_task(connect, baseline, owner="owner-nonqueued", workspace="workspace-nonqueued")
    candidate_payload = candidate.model_dump(mode="json", by_alias=True)
    candidate_payload["workUnits"][0]["status"] = "reviewing"
    candidate_payload["substageRuntimeStates"][0]["status"] = "reviewing"
    candidate = ResearchTaskV12.model_validate(candidate_payload)

    with pytest.raises(ValueError, match="queued"):
        repository.save(
            candidate,
            owner_id="owner-nonqueued",
            workspace_id="workspace-nonqueued",
            expected_task_revision=0,
            pending_dispatches=[dispatch],
            automatic_dispatch=True,
        )
    row = _task_row(connect, candidate.task_id)
    assert row["revision"] == row["payload"]["revision"] == 0
    assert row["payload"]["workUnits"] == []
    with connect() as connection:
        assert connection.execute("SELECT count(*) AS count FROM openair_dispatch").fetchone()["count"] == 0


@pytest.mark.parametrize("conflict_field", ["work_unit_id", "idempotency_key"])
def test_dispatch_unique_conflict_rolls_back_task_payload_and_revision(
    enabled_v1_repository,
    conflict_field: str,
) -> None:
    repository, connect = enabled_v1_repository
    baseline_a, candidate_a, dispatch_a = _transaction_fixture(f"unique_a_{conflict_field}")
    baseline_b, candidate_b, dispatch_b = _transaction_fixture(f"unique_b_{conflict_field}")
    _insert_task(connect, baseline_a, owner="owner-unique", workspace="workspace-unique")
    _insert_task(connect, baseline_b, owner="owner-unique", workspace="workspace-unique")
    repository.save(
        candidate_a,
        owner_id="owner-unique",
        workspace_id="workspace-unique",
        expected_task_revision=0,
        pending_dispatches=[dispatch_a],
        automatic_dispatch=True,
    )

    candidate_b_payload = candidate_b.model_dump(mode="json", by_alias=True)
    dispatch_b_payload = dispatch_b.model_dump(mode="json", by_alias=True)
    if conflict_field == "work_unit_id":
        candidate_b_payload["workUnits"][0]["workUnitId"] = dispatch_a.work_unit_id
        candidate_b_payload["substageRuntimeStates"][0]["workUnitIds"] = [dispatch_a.work_unit_id]
        dispatch_b_payload["workUnitId"] = dispatch_a.work_unit_id
    else:
        candidate_b_payload["workUnits"][0]["idempotencyKey"] = dispatch_a.idempotency_key
        dispatch_b_payload["idempotencyKey"] = dispatch_a.idempotency_key
    candidate_b = ResearchTaskV12.model_validate(candidate_b_payload)
    dispatch_b = DispatchRecord.model_validate(dispatch_b_payload)

    with pytest.raises(DispatchIdentityConflictError):
        repository.save(
            candidate_b,
            owner_id="owner-unique",
            workspace_id="workspace-unique",
            expected_task_revision=0,
            pending_dispatches=[dispatch_b],
            automatic_dispatch=True,
        )
    row = _task_row(connect, candidate_b.task_id)
    assert row["revision"] == row["payload"]["revision"] == 0
    assert row["payload"]["workUnits"] == []
    with connect() as connection:
        assert connection.execute(
            "SELECT count(*) AS count FROM openair_dispatch WHERE task_id = %s",
            (candidate_b.task_id,),
        ).fetchone()["count"] == 0


def _superseded_transaction_latency_probe(enabled_v1_repository) -> None:
    repository, connect = enabled_v1_repository
    samples = [_transaction_fixture(f"perf_{index:02d}") for index in range(25)]
    for baseline, _candidate, _dispatch in samples:
        _insert_task(connect, baseline, owner="owner-perf", workspace="workspace-perf")

    durations: list[float] = []
    for _baseline, candidate, dispatch in samples:
        started = perf_counter()
        repository.save(
            candidate,
            owner_id="owner-perf",
            workspace_id="workspace-perf",
            expected_task_revision=0,
            pending_dispatches=[dispatch],
            automatic_dispatch=True,
        )
        durations.append(perf_counter() - started)

    p95 = sorted(durations)[math.ceil(len(durations) * 0.95) - 1]
    print(f"T01_CAS_OUTBOX_P95_MS={p95 * 1000:.2f} samples={len(durations)}")
    assert p95 <= 0.100, f"CAS + pending outbox p95 was {p95 * 1000:.2f}ms"


def test_dispatch_catalog_preserves_durability_constraints(postgres_environment) -> None:
    _admin_dsn, _task_dsn, connect = postgres_environment
    with connect() as connection:
        constraints = _dispatch_constraint_catalog(connection)
        indexes = _dispatch_index_catalog(connection)
        revision = connection.execute(
            """SELECT data_type, is_nullable, column_default FROM information_schema.columns
               WHERE table_name = 'openair_tasks' AND column_name = 'revision'"""
        ).fetchone()
        migration_versions = {
            row["version"]
            for row in connection.execute(
                """SELECT version FROM openair_schema_migrations
                   WHERE version IN (%s, %s)""",
                ("20260720_01_ib01_durable_execution.sql", UP_MIGRATION.name),
            ).fetchall()
        }

    assert constraints["openair_dispatch_task_id_fkey"] == {
        "conname": "openair_dispatch_task_id_fkey",
        "contype": "f",
        "definition": "FOREIGN KEY (task_id) REFERENCES openair_tasks(task_id) ON DELETE RESTRICT",
        "local_columns": ["task_id"],
        "referenced_table": "openair_tasks",
        "referenced_columns": ["task_id"],
        "on_delete": "r",
    }
    assert {
        name: constraint["definition"]
        for name, constraint in constraints.items()
        if constraint["contype"] == "c"
        and name not in {
            "openair_dispatch_action_start_requires_authorization_check",
            "openair_dispatch_execution_namespace_check",
            "openair_dispatch_result_blob_complete_check",
            "openair_dispatch_submission_mode_check",
        }
    } == {
        "openair_dispatch_attempt_count_check": "CHECK (attempt_count >= 0)",
        "openair_dispatch_authorization_task_revision_check": (
            "CHECK (authorization_task_revision IS NULL OR authorization_task_revision >= 0)"
        ),
        "openair_dispatch_check": "CHECK ((lease_owner IS NULL) = (lease_expires_at IS NULL))",
        "openair_dispatch_check1": "CHECK (updated_at >= created_at)",
        "openair_dispatch_dispatch_id_check": "CHECK (length(btrim(dispatch_id)) > 0)",
        "openair_dispatch_execution_decision_id_check": (
            "CHECK (length(btrim(execution_decision_id)) > 0)"
        ),
        "openair_dispatch_idempotency_key_check": "CHECK (length(btrim(idempotency_key)) > 0)",
        "openair_dispatch_last_error_code_check": (
            "CHECK (last_error_code IS NULL OR length(btrim(last_error_code)) > 0)"
        ),
        "openair_dispatch_lease_owner_check": (
            "CHECK (lease_owner IS NULL OR length(btrim(lease_owner)) > 0)"
        ),
        "openair_dispatch_remote_run_id_check": (
            "CHECK (remote_run_id IS NULL OR length(btrim(remote_run_id)) > 0)"
        ),
        "openair_dispatch_status_check": (
            "CHECK (status = ANY (ARRAY['pending'::text, 'claimed'::text, "
            "'submission_authorized'::text, 'submitted'::text, 'suppressed'::text, "
            "'submission_unknown'::text, 'failed'::text]))"
        ),
        "openair_dispatch_task_id_check": "CHECK (length(btrim(task_id)) > 0)",
        "openair_dispatch_work_unit_id_check": "CHECK (length(btrim(work_unit_id)) > 0)",
    }
    assert {
        name: {
            "definition": index["definition"],
            "predicate": index["predicate"],
            "columns": index["columns"],
        }
        for name, index in indexes.items()
        if name in {
            "idx_openair_dispatch_claim",
            "idx_openair_dispatch_lease_expiry",
            "idx_openair_dispatch_task",
        }
    } == {
        "idx_openair_dispatch_claim": {
            "definition": (
                "CREATE INDEX idx_openair_dispatch_claim ON openair_dispatch USING btree "
                "(status, next_attempt_at, created_at, dispatch_id) WHERE status = 'pending'::text"
            ),
            "predicate": "status = 'pending'::text",
            "columns": ["status", "next_attempt_at", "created_at", "dispatch_id"],
        },
        "idx_openair_dispatch_lease_expiry": {
            "definition": (
                "CREATE INDEX idx_openair_dispatch_lease_expiry ON openair_dispatch USING btree "
                "(lease_expires_at, dispatch_id) WHERE status = 'claimed'::text"
            ),
            "predicate": "status = 'claimed'::text",
            "columns": ["lease_expires_at", "dispatch_id"],
        },
        "idx_openair_dispatch_task": {
            "definition": (
                "CREATE INDEX idx_openair_dispatch_task ON openair_dispatch USING btree "
                "(task_id, status, created_at DESC, dispatch_id)"
            ),
            "predicate": "",
            "columns": ["task_id", "status", "created_at", "dispatch_id"],
        },
    }
    assert {
        name: constraint["local_columns"]
        for name, constraint in constraints.items()
        if constraint["contype"] in {"p", "u"}
    } == {
        "openair_dispatch_pkey": ["dispatch_id"],
        "openair_dispatch_work_unit_id_key": ["work_unit_id"],
        "openair_dispatch_idempotency_key_key": ["idempotency_key"],
    }
    assert revision == {"data_type": "bigint", "is_nullable": "NO", "column_default": "0"}
    assert migration_versions == {
        "20260720_01_ib01_durable_execution.sql",
        "20260728_01_research_orchestration_v1_2.sql",
    }


def test_down_migration_waits_for_exclusive_lock_before_nonempty_refusal(postgres_environment) -> None:
    admin_dsn, _task_dsn, _connect = postgres_environment
    migration_dsn = _database_dsn(admin_dsn, MIGRATION_DATABASE)
    with psycopg.connect(admin_dsn, autocommit=True, row_factory=dict_row) as admin:
        _create_owned_database(admin, MIGRATION_DATABASE)
        try:
            _bootstrap_t01_schema(migration_dsn)
            baseline, _candidate, dispatch = _transaction_fixture("down_lock")

            def migration_connect() -> psycopg.Connection:
                return psycopg.connect(migration_dsn, row_factory=dict_row)

            _insert_task(
                migration_connect,
                baseline,
                owner="owner-down-lock",
                workspace="workspace-down-lock",
            )
            with migration_connect() as connection:
                connection.execute(
                    """INSERT INTO openair_dispatch (
                           dispatch_id, task_id, execution_decision_id, work_unit_id,
                           idempotency_key, status
                       ) VALUES (%s, %s, %s, %s, %s, 'pending')""",
                    (
                        dispatch.dispatch_id,
                        baseline.task_id,
                        dispatch.execution_decision_id,
                        dispatch.work_unit_id,
                        dispatch.idempotency_key,
                    ),
                )

            with psycopg.connect(migration_dsn, autocommit=True, row_factory=dict_row) as holder:
                holder.execute("BEGIN")
                holder.execute("LOCK TABLE openair_dispatch IN ACCESS SHARE MODE")
                with psycopg.connect(
                    migration_dsn, autocommit=True, row_factory=dict_row
                ) as contender:
                    contender.execute("SET lock_timeout = '250ms'")
                    started = perf_counter()
                    with pytest.raises(psycopg.errors.LockNotAvailable, match="lock timeout"):
                        contender.execute(DOWN_MIGRATION.read_text(encoding="utf-8"))
                    elapsed = perf_counter() - started
                    contender.execute("ROLLBACK")
                    assert elapsed < 2.0, f"down migration lock timeout was not bounded: {elapsed:.2f}s"
                holder.execute("COMMIT")

            with psycopg.connect(migration_dsn, autocommit=True, row_factory=dict_row) as connection:
                with pytest.raises(psycopg.errors.RaiseException, match="non-empty"):
                    connection.execute(DOWN_MIGRATION.read_text(encoding="utf-8"))
                connection.execute("ROLLBACK")
                assert connection.execute(
                    "SELECT count(*) AS count FROM openair_dispatch"
                ).fetchone()["count"] == 1
                connection.execute("DELETE FROM openair_dispatch")
                connection.execute(DOWN_MIGRATION.read_text(encoding="utf-8"))
                assert connection.execute(
                    "SELECT to_regclass('openair_dispatch') AS name"
                ).fetchone()["name"] is None
                revision = connection.execute(
                    """SELECT data_type, is_nullable FROM information_schema.columns
                       WHERE table_name = 'openair_tasks' AND column_name = 'revision'"""
                ).fetchone()
                assert revision == {"data_type": "bigint", "is_nullable": "NO"}
                assert connection.execute(
                    "SELECT to_regclass('openair_execution_runs') AS name"
                ).fetchone()["name"] is not None
                remaining_migrations = {
                    row["version"]
                    for row in connection.execute(
                        """SELECT version FROM openair_schema_migrations
                           WHERE version IN (%s, %s)""",
                        ("20260720_01_ib01_durable_execution.sql", UP_MIGRATION.name),
                    ).fetchall()
                }
                assert remaining_migrations == {"20260720_01_ib01_durable_execution.sql"}
        finally:
            _drop_owned_database(admin, MIGRATION_DATABASE)


def test_safe_down_exactly_restores_pre_t01_table_catalog_and_migration_ledger(
    postgres_environment,
) -> None:
    admin_dsn, _task_dsn, _connect = postgres_environment
    migration_dsn = _database_dsn(admin_dsn, MIGRATION_DATABASE)
    with psycopg.connect(admin_dsn, autocommit=True, row_factory=dict_row) as admin:
        _create_owned_database(admin, MIGRATION_DATABASE)
        try:
            _bootstrap_pre_t01_schema(migration_dsn)
            with psycopg.connect(migration_dsn, autocommit=True, row_factory=dict_row) as connection:
                tables_before = _persistent_table_catalog(connection)
                ledger_before = _migration_versions(connection)

                connection.execute(UP_MIGRATION.read_text(encoding="utf-8"))
                connection.execute(
                    "INSERT INTO openair_schema_migrations(version) VALUES (%s)",
                    (UP_MIGRATION.name,),
                )
                assert _persistent_table_catalog(connection) == tables_before | {
                    ("public", "openair_dispatch", "r")
                }
                assert _migration_versions(connection) == ledger_before | {UP_MIGRATION.name}

                connection.execute(DOWN_MIGRATION.read_text(encoding="utf-8"))

                assert _persistent_table_catalog(connection) == tables_before
                assert _migration_versions(connection) == ledger_before
                revision = connection.execute(
                    """SELECT data_type, is_nullable FROM information_schema.columns
                       WHERE table_schema = 'public'
                         AND table_name = 'openair_tasks'
                         AND column_name = 'revision'"""
                ).fetchone()
                assert revision == {"data_type": "bigint", "is_nullable": "NO"}
        finally:
            _drop_owned_database(admin, MIGRATION_DATABASE)


def _superseded_repository_round_trip_across_historical_down_migration(postgres_environment) -> None:
    admin_dsn, _task_dsn, _connect = postgres_environment
    migration_dsn = _database_dsn(admin_dsn, MIGRATION_DATABASE)
    with psycopg.connect(admin_dsn, autocommit=True, row_factory=dict_row) as admin:
        _create_owned_database(admin, MIGRATION_DATABASE)
        try:
            _bootstrap_t01_schema(migration_dsn)
            baseline, candidate, dispatch = _transaction_fixture("migration")

            def migration_connect() -> psycopg.Connection:
                return psycopg.connect(migration_dsn, row_factory=dict_row)

            _insert_task(
                migration_connect,
                baseline,
                owner="owner-migration",
                workspace="workspace-migration",
            )
            repository = ResearchOrchestrationRepository(
                connection_factory=migration_connect,
                runtime_settings=type(
                    "MigrationSettings",
                    (),
                    {"research_orchestration_v1": True, "persistence_backend": "postgres"},
                )(),
            )
            repository.save(
                candidate,
                owner_id="owner-migration",
                workspace_id="workspace-migration",
                expected_task_revision=0,
                pending_dispatches=[dispatch],
                automatic_dispatch=True,
            )

            with psycopg.connect(migration_dsn, autocommit=True, row_factory=dict_row) as connection:
                with pytest.raises(psycopg.errors.RaiseException, match="non-empty"):
                    connection.execute(DOWN_MIGRATION.read_text(encoding="utf-8"))
                connection.rollback()
                assert connection.execute("SELECT count(*) AS count FROM openair_dispatch").fetchone()["count"] == 1
                connection.execute("DELETE FROM openair_dispatch")
                connection.execute("DELETE FROM openair_tasks")
                connection.execute(DOWN_MIGRATION.read_text(encoding="utf-8"))
                assert connection.execute("SELECT to_regclass('openair_dispatch') AS name").fetchone()["name"] is None
                assert connection.execute(
                    "SELECT count(*) AS count FROM information_schema.columns WHERE table_name = 'openair_tasks' AND column_name = 'revision'"
                ).fetchone()["count"] == 1
                assert connection.execute("SELECT to_regclass('openair_execution_runs') AS name").fetchone()["name"] is not None
                assert connection.execute(
                    "SELECT count(*) AS count FROM openair_schema_migrations WHERE version = %s",
                    (UP_MIGRATION.name,),
                ).fetchone()["count"] == 0

            from app.modules.postgres_migrations import apply_postgres_migrations

            with psycopg.connect(migration_dsn, row_factory=dict_row) as connection:
                with connection.cursor() as cursor:
                    apply_postgres_migrations(cursor)
                assert connection.execute("SELECT to_regclass('openair_dispatch') AS name").fetchone()["name"] == "openair_dispatch"
                assert connection.execute(
                    "SELECT count(*) AS count FROM openair_schema_migrations WHERE version = %s",
                    (UP_MIGRATION.name,),
                ).fetchone()["count"] == 1
        finally:
            _drop_owned_database(admin, MIGRATION_DATABASE)
