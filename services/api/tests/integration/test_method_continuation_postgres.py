"""Run the production continuation SELECT against PostgreSQL temporary tables.

Set OPENAIR_METHOD_CONTINUATION_TEST_DSN to an isolated PostgreSQL DSN. The
connection creates only TEMP tables, addresses every fixture write through
pg_temp, and rolls back on exit. No application schema or Provider is used.
"""
from contextlib import contextmanager
from copy import deepcopy
from datetime import timedelta
import os
from types import SimpleNamespace
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
import pytest

from app.modules.research_orchestration_repository import ResearchOrchestrationRepository


@pytest.fixture
def temp_continuation_database():
    dsn = os.environ.get("OPENAIR_METHOD_CONTINUATION_TEST_DSN")
    if not dsn:
        pytest.skip("OPENAIR_METHOD_CONTINUATION_TEST_DSN is required for the PostgreSQL check")
    with psycopg.connect(dsn, row_factory=dict_row, autocommit=False) as connection:
        try:
            # Unqualified names in the production SELECT can resolve only to our
            # temp tables or pg_catalog; an absent temp table cannot expose public.
            connection.execute("SET LOCAL search_path = pg_temp, pg_catalog")
            connection.execute("SET LOCAL statement_timeout = '5s'")
            connection.execute("""CREATE TEMP TABLE openair_tasks (
                task_id text PRIMARY KEY,
                owner_id text NOT NULL,
                workspace_id text NOT NULL,
                revision bigint NOT NULL,
                status text NOT NULL,
                payload jsonb NOT NULL,
                updated_at timestamptz NOT NULL
            ) ON COMMIT DROP""")
            connection.execute("""CREATE TEMP TABLE openair_dispatch (
                task_id text NOT NULL,
                execution_decision_id text NOT NULL,
                execution_namespace text NOT NULL,
                status text NOT NULL
            ) ON COMMIT DROP""")
            actual_tables = connection.execute("""
                SELECT relname, relpersistence
                FROM pg_catalog.pg_class
                WHERE relnamespace = pg_my_temp_schema()
                  AND relname IN ('openair_tasks', 'openair_dispatch')
                ORDER BY relname
            """).fetchall()
            assert actual_tables == [
                {"relname": "openair_dispatch", "relpersistence": "t"},
                {"relname": "openair_tasks", "relpersistence": "t"},
            ]
            yield connection
        finally:
            connection.rollback()


class SameConnectionUnitOfWork:
    def __init__(self, connection):
        self.connection = connection
        self.calls = 0

    @contextmanager
    def transaction(self):
        self.calls += 1
        # Do not enter/exit the psycopg connection context here: that would
        # commit setup and drop the ON COMMIT DROP tables between SELECT calls.
        yield self.connection


def test_postgres_continuation_query_preserves_recovery_and_excludes_unsafe_candidates(
    temp_continuation_database,
):
    connection = temp_continuation_database
    namespace = "method-continuation-sql-" + uuid4().hex
    now = connection.execute("SELECT clock_timestamp() AS now").fetchone()["now"]
    old = now - timedelta(minutes=10)
    inserted = []

    def add_case(
        name, *, revision, work_status="failed", dispatch_status="failed",
        task_status="running", decision_status="dispatched", strategy="execution_method",
        active_version=1, decision_version=1, own_namespace=True, stopped=False,
        has_history=True, preflight=False, young=False,
    ):
        task_id = "sql_" + name
        decision_id = "decision_" + name
        decision = {
            "executionDecisionId": decision_id,
            "strategy": strategy,
            "status": decision_status,
            "researchDesignVersion": decision_version,
            "assignmentPreflightOutcomes": [{"failureKind": "material_unavailable"}] if preflight else [],
        }
        payload = {
            "activeResearchDesignRef": {"researchDesignId": "fixture-design", "version": active_version},
            "executionDecisions": [decision],
            "workUnits": [
                {"executionDecisionId": decision_id, "status": "completed"},
                {"executionDecisionId": decision_id, "status": work_status},
            ] if has_history else [],
        }
        if stopped:
            payload["stopReceipt"] = {"status": "stopped", "active": True}
        stamp = now if young else old + timedelta(seconds=len(inserted))
        connection.execute(
            """INSERT INTO pg_temp.openair_tasks
               (task_id, owner_id, workspace_id, revision, status, payload, updated_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (task_id, "owner_" + name, "workspace_" + name, revision, task_status, Jsonb(payload), stamp),
        )
        # The first completed role has already been submitted. The latest
        # dispatch state is varied independently from the durable WorkUnit.
        dispatch_namespace = namespace if own_namespace else namespace + "-foreign"
        for status in ("submitted", dispatch_status):
            connection.execute(
                """INSERT INTO pg_temp.openair_dispatch
                   (task_id, execution_decision_id, execution_namespace, status)
                   VALUES (%s, %s, %s, %s)""",
                (task_id, decision_id, dispatch_namespace, status),
            )
        result = {
            "task_id": task_id, "owner_id": "owner_" + name, "workspace_id": "workspace_" + name,
            "revision": revision, "execution_decision_id": decision_id,
        }
        inserted.append(result)
        return result

    known_failed = add_case("known_failed", revision=81)
    completed = add_case("completed_callback_interrupted", revision=82, work_status="completed", dispatch_status="submitted")
    blocked = add_case("blocked_with_preflight", revision=83, work_status="blocked", preflight=True)
    young_failed = add_case("young_known_failed", revision=84, young=True)

    for status in ("pending", "claimed", "submission_authorized", "submission_unknown"):
        add_case("dispatch_" + status, revision=100, dispatch_status=status)
    for status in ("queued", "running", "reviewing", "external_result_unknown", "cancelled"):
        add_case("work_" + status, revision=101, work_status=status, dispatch_status="submitted")
    for status in ("cancelled", "closed", "superseded"):
        add_case("decision_" + status, revision=102, decision_status=status)
    add_case("task_cancelled", revision=103, task_status="cancelled")
    add_case("task_awaiting_confirmation", revision=104, task_status="awaiting_confirmation")
    add_case("stop_receipt_active", revision=105, stopped=True)
    add_case("foreign_namespace", revision=106, own_namespace=False)
    add_case("old_design", revision=107, active_version=2)
    add_case("direct_skill", revision=108, strategy="direct_skill")
    add_case("no_terminal_history", revision=109, has_history=False)

    def forbidden_new_connection():
        pytest.fail("the repository must use the existing temporary-table connection")

    repository = ResearchOrchestrationRepository(
        connection_factory=forbidden_new_connection,
        runtime_settings=SimpleNamespace(persistence_backend="postgres", execution_namespace=namespace),
    )
    unit_of_work = SameConnectionUnitOfWork(connection)
    repository._uow = unit_of_work
    before = deepcopy(inserted)

    worker_candidates = repository.list_pending_method_continuations(min_pending_age_seconds=0)
    assert worker_candidates == [known_failed, completed, blocked, young_failed]
    fallback_candidates = repository.list_pending_method_continuations(min_pending_age_seconds=120)
    assert fallback_candidates == [known_failed, completed, blocked]
    assert unit_of_work.calls == 2
    # The observed SQL revision is returned unchanged for the later CAS. A
    # continuation poll does not mutate Task state or reclaim failed dispatches.
    remaining = connection.execute("""
        SELECT task_id, owner_id, workspace_id, revision,
               payload->'executionDecisions'->0->>'executionDecisionId' AS execution_decision_id
        FROM pg_temp.openair_tasks ORDER BY updated_at, task_id
    """).fetchall()
    assert {row["task_id"]: row for row in remaining} == {row["task_id"]: row for row in before}
    assert connection.execute("SELECT count(*) AS n FROM pg_temp.openair_dispatch").fetchone()["n"] == 2 * len(before)
