"""Real PostgreSQL transaction tests for the durable research-start store.

Set OPENAIR_TEST_POSTGRES_DSN to an explicit disposable test/qa database.  One
isolated schema is created per test session; no application database settings
are read and a database without a visible test/qa name is rejected.
"""

from __future__ import annotations

import os
import re
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from datetime import datetime, timezone
import json
from pathlib import Path
from threading import Event
from uuid import uuid4

import pytest


DSN_ENV = "OPENAIR_TEST_POSTGRES_DSN"
MIGRATION_PATH = Path(__file__).parents[2] / "migrations" / "20260725_02_durable_research_start.sql"
CATALOG_PROMOTE_MIGRATION_PATH = Path(__file__).parents[2] / "migrations" / "20260805_01_research_start_catalog_promote.sql"
RUNTIME_SOURCE_FENCE_MIGRATION_PATH = Path(__file__).parents[2] / "migrations" / "20260830_02_research_start_runtime_source_fence.sql"
PROVIDER_ATTEMPTS_MIGRATION_PATH = Path(__file__).parents[2] / "migrations" / "20260802_04_c1_provider_http_attempts.sql"
PROVIDER_ATTEMPTS_MULTI_MIGRATION_PATH = Path(__file__).parents[2] / "migrations" / "20260805_03_provider_http_attempts_multi.sql"
RESPONSE_EVIDENCE_MIGRATION_PATH = Path(__file__).parents[2] / "migrations" / "20260920_90_research_start_attempt_response_evidence.sql"
PHYSICAL_EXECUTION_TRUTH_MIGRATION_PATH = Path(__file__).parents[2] / "migrations" / "20260928_01_physical_execution_truth.sql"
RESPONSE_BODY_MIGRATION_PATH = Path(__file__).parents[2] / "migrations" / "20261008_02_research_start_response_body.sql"
DOWN_MIGRATION_PATH = Path(__file__).parents[2] / "migrations" / "20260725_02_durable_research_start_down.sql"

RESEARCH_DESIGN_CASES_PATH = Path(__file__).parents[1] / "fixtures" / "research_design_v1_cases.json"
RESEARCH_DESIGN_SNAPSHOT = {"schemaVersion": 1, "contentHash": "sha256:" + "b" * 64}


class _ResearchDesignDefinitionSource:
    definitions = {
        "sdr:evidence_synthesis": (1, "solution_design"),
        "sdr:source_collection": (3, "problem_modeling"),
    }

    def snapshot_ref(self):
        return RESEARCH_DESIGN_SNAPSHOT

    def resolve(self, definition_id: str):
        from app.modules import research_design_service as design_service

        if definition_id not in self.definitions:
            return None
        version, stage = self.definitions[definition_id]
        return design_service.ResolvedDefinition(
            definition_id=definition_id,
            definition_version=version,
            stage_key=stage,
        )

    def definition_is_eligible_for_workflow_admission(self, definition_id, workflow_admission):
        return definition_id in self.definitions


class _MemoryResearchTaskRepository:
    def __init__(self, task_id: str) -> None:
        from app.modules import research_design_service as design_service

        self.task = design_service.new_research_task(
            task_id=task_id,
            registry_snapshot_ref=RESEARCH_DESIGN_SNAPSHOT,
        )

    def load(self, **_kwargs):
        return self.task.model_copy(deep=True)

    def load_planning_workflow_admission(
        self, *, task_id: str, owner_id: str, workspace_id: str
    ):
        assert task_id == self.task.task_id
        return None

    def save(self, task, *, expected_task_revision: int, **_kwargs):
        from app.models.research_orchestration import ResearchTaskV12

        assert self.task.revision == expected_task_revision
        payload = task.model_dump(mode="json", by_alias=True)
        payload["revision"] = expected_task_revision + 1
        self.task = ResearchTaskV12.model_validate(payload)
        return self.task.model_copy(deep=True)


def _build_planned_task(
    *,
    task_id: str,
    request_id: str,
    provider_call_ref: str,
    later_request_id: str | None = None,
    cancel_latest: bool = False,
):
    from app.modules import research_design_service as design_service

    cases = json.loads(RESEARCH_DESIGN_CASES_PATH.read_text(encoding="utf-8"))
    repository = _MemoryResearchTaskRepository(task_id)
    service = design_service.ResearchDesignService(
        repository=repository,
        definition_source=_ResearchDesignDefinitionSource(),
        clock=lambda: datetime(2026, 8, 7, tzinfo=timezone.utc),
    )

    def plan(value: str, call_ref: str) -> None:
        service.plan(
            task_id=task_id,
            owner_id="owner",
            workspace_id="workspace",
            request_id=value,
            expected_task_revision=repository.task.revision,
            provider=lambda _key: design_service.PlannerProviderResult(
                proposal=deepcopy(cases["simple"]),
                provider_call_ref=call_ref,
            ),
        )

    plan(request_id, provider_call_ref)
    if later_request_id is not None:
        plan(later_request_id, f"provider:{later_request_id}")
    if cancel_latest:
        latest = max(repository.task.research_design_versions, key=lambda row: row.version)
        service.confirm(
            task_id=task_id,
            owner_id="owner",
            workspace_id="workspace",
            actor_id="owner",
            design_id=latest.research_design_id,
            version=latest.version,
            confirmation_id=f"reject-{latest.version}",
            decision="rejected",
            expected_task_revision=repository.task.revision,
        )
    return repository.task


def _persist_research_task(connect, task, *, owner_id: str, workspace_id: str) -> None:
    payload = task.model_dump(mode="json", by_alias=True)
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO openair_tasks(
                       task_id, owner_id, workspace_id, status, current_stage, title, payload
                   ) VALUES (%s, %s, %s, 'draft', 'problem_modeling', 'Research start test', %s::jsonb)
                   ON CONFLICT (task_id) DO UPDATE
                      SET status = EXCLUDED.status, current_stage = EXCLUDED.current_stage,
                          title = EXCLUDED.title, payload = EXCLUDED.payload""",
                (task.task_id, owner_id, workspace_id, json.dumps(payload)),
            )
        conn.commit()
    finally:
        conn.close()


def _replace_research_task_payload(cur, task) -> None:
    cur.execute(
        "UPDATE openair_tasks SET payload = %s::jsonb WHERE task_id = %s",
        (json.dumps(task.model_dump(mode="json", by_alias=True)), task.task_id),
    )


def _read_task_snapshot(connect, task_id: str) -> dict[str, object]:
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT status, revision, payload FROM openair_tasks WHERE task_id = %s",
                (task_id,),
            )
            row = cur.fetchone()
        assert row is not None
        payload = row["payload"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        assert isinstance(payload, dict)
        return {**row, "payload": payload}
    finally:
        conn.close()


def _intent(text: str, *, session_id: str | None = None) -> dict[str, object]:
    return {
        "input": text,
        "sourcePage": "home",
        "selectedHint": None,
        "presetQuery": None,
        "sessionId": session_id,
        "createClarificationDraft": False,
    }


@pytest.fixture(scope="session")
def postgres_test_environment():
    psycopg = pytest.importorskip("psycopg")
    from psycopg import sql
    from psycopg.rows import dict_row

    dsn = os.environ.get(DSN_ENV)
    if not dsn:
        pytest.skip(f"{DSN_ENV} is required; refusing to connect to an unspecified database")
    admin = psycopg.connect(dsn, row_factory=dict_row)
    schema = f"w4_research_start_{uuid4().hex}"
    try:
        with admin.cursor() as cur:
            cur.execute("SELECT current_database() AS database_name")
            database_name = cur.fetchone()["database_name"]
            if not re.search(r"(?:^|[_-])(test|qa)(?:[_-]|$)", database_name, re.IGNORECASE):
                pytest.fail(f"refusing durable-store integration test against non-test database: {database_name}")
            cur.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
            cur.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
            cur.execute(MIGRATION_PATH.read_text(encoding="utf-8"))
            cur.execute(CATALOG_PROMOTE_MIGRATION_PATH.read_text(encoding="utf-8"))
            cur.execute(RUNTIME_SOURCE_FENCE_MIGRATION_PATH.read_text(encoding="utf-8"))
            cur.execute(PROVIDER_ATTEMPTS_MIGRATION_PATH.read_text(encoding="utf-8"))
            cur.execute(PROVIDER_ATTEMPTS_MULTI_MIGRATION_PATH.read_text(encoding="utf-8"))
            cur.execute(RESPONSE_EVIDENCE_MIGRATION_PATH.read_text(encoding="utf-8"))
            cur.execute(PHYSICAL_EXECUTION_TRUTH_MIGRATION_PATH.read_text(encoding="utf-8"))
            cur.execute(RESPONSE_BODY_MIGRATION_PATH.read_text(encoding="utf-8"))
            from app.modules.artifact_repository import ARTIFACTS_TABLE_SQL
            from app.modules.task_repository import TASKS_TABLE_SQL

            cur.execute(TASKS_TABLE_SQL)
            # TASKS_TABLE_SQL is the pre-migration startup table. Production adds
            # task revision in 20260720_01_ib01_durable_execution.sql; this isolated
            # ResearchStart fixture needs that same formal column without running
            # the unrelated full migration chain (some later migrations are public-only).
            cur.execute(
                "ALTER TABLE openair_tasks "
                "ADD COLUMN IF NOT EXISTS revision BIGINT NOT NULL DEFAULT 0"
            )
            cur.execute(ARTIFACTS_TABLE_SQL)
        admin.commit()

        def connect():
            conn = psycopg.connect(dsn, row_factory=dict_row)
            with conn.cursor() as cur:
                cur.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
            conn.commit()
            return conn

        yield psycopg, sql, dsn, schema, connect
    finally:
        admin.rollback()
        with admin.cursor() as cur:
            cur.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))
        admin.commit()
        admin.close()


@pytest.fixture()
def pg_repository(postgres_test_environment):
    _psycopg, _sql, _dsn, _schema, connect = postgres_test_environment
    from app.modules.research_start_repository import ResearchStartRepository

    repository = ResearchStartRepository(connection_factory=connect)
    owner_id = f"durable-test-owner-{uuid4().hex}"
    try:
        yield repository, connect, owner_id
    finally:
        cleanup = connect()
        try:
            with cleanup.cursor() as cur:
                cur.execute(
                    "DELETE FROM research_start_attempts WHERE request_id IN "
                    "(SELECT request_id FROM research_start_requests WHERE owner_id = %s)",
                    (owner_id,),
                )
                cur.execute("DELETE FROM research_start_requests WHERE owner_id = %s", (owner_id,))
                cur.execute("DELETE FROM research_start_sessions WHERE owner_id = %s", (owner_id,))
                cur.execute("DELETE FROM openair_artifacts WHERE owner_id = %s", (owner_id,))
                cur.execute("DELETE FROM openair_tasks WHERE owner_id = %s", (owner_id,))
            cleanup.commit()
        finally:
            cleanup.close()


def test_same_scoped_key_has_exactly_one_stable_reservation_across_connections(pg_repository) -> None:
    repository, connect, owner_id = pg_repository
    first = type(repository)(connection_factory=connect)
    second = type(repository)(connection_factory=connect)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(repo.reserve, owner_id, "scope-a", "request-a", _intent("one")) for repo in (first, second)]
    results = [future.result() for future in futures]

    assert sorted(row["outcome"] for row in results) == ["replay", "reserved"]
    assert len({row["sessionId"] for row in results}) == 1
    assert len({row["requestId"] for row in results}) == 1
    assert len({row["reservedTaskId"] for row in results}) == 1
    assert len({row["reservedArtifactId"] for row in results}) == 1
    assert re.fullmatch(r"intent_[0-9a-f]{12}", results[0]["sessionId"])
    assert set(results[0]) == {
        "outcome", "sessionId", "requestId", "status", "reservedTaskId", "reservedArtifactId"
    }


def test_catalog_promote_is_scoped_idempotent_and_pins_snapshot_without_work(pg_repository) -> None:
    repository, connect, owner_id = pg_repository
    catalog_intent_id = "intent_0123456789ab"
    args = (owner_id, "scope-a", catalog_intent_id, "skill:example", "1.0.0", "example_capability")

    first = repository.reserve_catalog_session(*args)
    replay = repository.reserve_catalog_session(*args)
    assert first == replay
    assert first["catalogIntentId"] == catalog_intent_id
    assert first["selectedResourceVersion"] == "1.0.0"

    with ThreadPoolExecutor(max_workers=2) as pool:
        concurrent = [
            pool.submit(
                type(repository)(connection_factory=connect).reserve_catalog_session,
                owner_id,
                "scope-a",
                "intent_abcdef012345",
                "skill:concurrent",
                "2.0.0",
                "concurrent_capability",
            )
            for _ in range(2)
        ]
    concurrent_results = [future.result() for future in concurrent]
    assert len({row["sessionId"] for row in concurrent_results}) == 1

    other_workspace = repository.reserve_catalog_session(
        owner_id, "scope-b", catalog_intent_id, "skill:example", "1.0.0", "example_capability"
    )
    assert other_workspace["sessionId"] != first["sessionId"]

    from app.modules.research_start_repository import CatalogSessionConflictError

    with pytest.raises(CatalogSessionConflictError):
        repository.reserve_catalog_session(*args[:-2], "2.0.0", "example_capability")

    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT selected_resource_id, selected_resource_version, selected_capability_id "
                "FROM research_start_sessions WHERE session_id = %s",
                (first["sessionId"],),
            )
            assert cur.fetchone() == {
                "selected_resource_id": "skill:example",
                "selected_resource_version": "1.0.0",
                "selected_capability_id": "example_capability",
            }
            cur.execute("SELECT count(*) AS count FROM research_start_requests WHERE owner_id = %s", (owner_id,))
            assert cur.fetchone()["count"] == 0
            cur.execute("SELECT count(*) AS count FROM research_start_attempts")
            assert cur.fetchone()["count"] == 0
            cur.execute("SELECT count(*) AS count FROM openair_tasks WHERE owner_id = %s", (owner_id,))
            assert cur.fetchone()["count"] == 0
            cur.execute("SELECT count(*) AS count FROM openair_artifacts WHERE owner_id = %s", (owner_id,))
            assert cur.fetchone()["count"] == 0
    finally:
        conn.close()


@pytest.mark.parametrize("resource_type_value", ["skill", "agent"])
def test_real_catalog_submit_pins_worker_and_execution_candidate(
    pg_repository,
    resource_type_value: str,
) -> None:
    from types import SimpleNamespace

    from app.models.resource_catalog import ResourceAvailability, ResourceType
    from app.modules.catalog_execution_binding import binding_from_public, bound_definition_refs, candidate_identity
    from app.modules.intent_session_store import intent_session_store
    from app.modules.resource_catalog import list_resources
    from app.modules.research_execution_decision_service import constrain_resolution_facts_to_catalog_binding
    from app.modules.research_orchestration_resolver import ResolutionFacts, build_registry_snapshot

    repository, _connect, owner_id = pg_repository
    resource_type = ResourceType(resource_type_value)
    resources = list_resources(resource_type=resource_type, availability=ResourceAvailability.TRYABLE)
    if not resources and resource_type == ResourceType.AGENT:
        pytest.skip("current CatalogRelease has no real tryable Agent")
    assert resources, f"no real tryable {resource_type_value} in CatalogRelease"
    resource = resources[0]
    capability_id = str(resource["capabilityId"])
    resource_version = str(resource["releaseVersion"])

    catalog_intent = intent_session_store.create_catalog_resource_intent(
        owner_id=owner_id,
        workspace_id="scope-a",
        created_by=owner_id,
        resource_id=str(resource["id"]),
        resource_version=resource_version,
        capability_id=capability_id,
    )
    promoted = repository.reserve_catalog_session(
        owner_id,
        "scope-a",
        catalog_intent["sessionId"],
        str(resource["id"]),
        resource_version,
        capability_id,
    )
    intent = _intent("catalog bound request", session_id=promoted["sessionId"])
    intent["selectedHint"] = "tampered_other_capability"
    reserved = repository.reserve(
        owner_id,
        "scope-a",
        f"catalog-bound-submit-{resource_type_value}",
        intent,
        session_id=promoted["sessionId"],
    )
    claim = repository.claim(f"catalog-worker-{resource_type_value}")
    assert claim["requestId"] == reserved["requestId"]
    context = repository.load_execution_context(
        reserved["requestId"],
        f"catalog-worker-{resource_type_value}",
        claim["leaseEpoch"],
    )

    assert context["intent"]["selectedHint"] == capability_id
    assert context["catalogBinding"] == {
        "catalogIntentId": catalog_intent["sessionId"],
        "resourceId": resource["id"],
        "resourceVersion": resource_version,
        "capabilityId": capability_id,
        "capabilityKind": resource_type_value,
    }

    snapshot = build_registry_snapshot().envelope
    binding = binding_from_public(context["catalogBinding"])
    assert binding is not None
    runtime_candidate = candidate_identity(snapshot, binding)
    definition_refs = bound_definition_refs(snapshot, binding)
    assert runtime_candidate is not None
    assert definition_refs
    definition_id, definition_version = sorted(definition_refs)[0]
    facts = ResolutionFacts(
        runtimeReadiness={runtime_candidate: True},
        providerReadiness={},
        authorizedCandidateRefs={runtime_candidate},
        availableInputRefs=set(),
        applicableRuleRefs=set(),
        satisfiedHumanGateRefs=set(),
        riskAllowed=True,
        budgetAvailable=True,
        policyVersion=str(snapshot["staticPolicies"]["policyVersion"]),
        activeWorkUnitCounts={},
        lastAssignedAt={},
    )
    constrained = constrain_resolution_facts_to_catalog_binding(
        facts,
        snapshot=snapshot,
        instance=SimpleNamespace(
            substage_definition_id=definition_id,
            substage_definition_version=definition_version,
        ),
        binding_payload=context["catalogBinding"],
    )
    assert constrained.authorized_candidate_refs == {runtime_candidate}

    replay_intent = _intent("catalog bound request", session_id=promoted["sessionId"])
    replay_intent["selectedHint"] = "another_tamper"
    replay = repository.reserve(
        owner_id,
        "scope-a",
        f"catalog-bound-submit-{resource_type_value}",
        replay_intent,
        session_id=promoted["sessionId"],
    )
    assert replay["outcome"] == "replay"
    assert replay["requestId"] == reserved["requestId"]


def test_catalog_promote_schema_constraints_allow_ordinary_sessions(pg_repository, postgres_test_environment) -> None:
    _repository, connect, _owner_id = pg_repository
    psycopg, _sql, _dsn, _schema, _fixture_connect = postgres_test_environment

    def insert_session(session_id: str, *, catalog_intent_id: str | None = None, resource_id: str | None = None,
                       version: str | None = None, capability_id: str | None = None) -> None:
        conn = connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO research_start_sessions(
                           session_id, owner_id, workspace_id, reserved_task_id, reserved_artifact_id,
                           catalog_intent_id, selected_resource_id, selected_resource_version, selected_capability_id
                       ) VALUES (%s, 'constraint-owner', 'constraint-workspace', %s, %s, %s, %s, %s, %s)""",
                    (session_id, f"task_{uuid4().hex}", f"artifact_{uuid4().hex}", catalog_intent_id, resource_id, version, capability_id),
                )
            conn.commit()
        finally:
            conn.close()

    with pytest.raises(psycopg.errors.CheckViolation):
        insert_session(f"intent_{uuid4().hex[:12]}", catalog_intent_id="intent_0123456789ab")
    with pytest.raises(psycopg.errors.CheckViolation):
        insert_session(
            f"intent_{uuid4().hex[:12]}",
            catalog_intent_id="invalid",
            resource_id="skill:example",
            version="1.0.0",
            capability_id="example_capability",
        )

    insert_session(
        "intent_aaaaaaaaaaaa",
        catalog_intent_id="intent_0123456789ab",
        resource_id="skill:example",
        version="1.0.0",
        capability_id="example_capability",
    )
    with pytest.raises(psycopg.errors.UniqueViolation):
        insert_session(
            "intent_bbbbbbbbbbbb",
            catalog_intent_id="intent_0123456789ab",
            resource_id="skill:example",
            version="1.0.0",
            capability_id="example_capability",
        )
    insert_session(f"intent_{uuid4().hex[:12]}")
    insert_session(f"intent_{uuid4().hex[:12]}")


def test_scope_conflict_and_not_found_are_non_enumerating(pg_repository) -> None:
    repository, _connect, owner_id = pg_repository
    reserved = repository.reserve(owner_id, "scope-a", "same", _intent("one"))
    from app.modules.research_start_repository import RequestConflictError, SessionNotFoundError

    with pytest.raises(RequestConflictError) as conflict:
        repository.reserve(owner_id, "scope-a", "same", _intent("different"))
    assert conflict.value.code == "fingerprint_conflict"
    repository.reserve(owner_id, "scope-b", "same", _intent("different"))

    messages = []
    for session_id in (reserved["sessionId"], "intent_000000000000"):
        with pytest.raises(SessionNotFoundError) as missing:
            repository.get_scoped_session(owner_id, "scope-b", session_id)
        messages.append(str(missing.value))
    assert len(set(messages)) == 1



def test_runtime_source_fence_rejects_prefence_worker_and_allows_matching_worker(
    pg_repository,
    postgres_test_environment,
) -> None:
    repository, connect, owner_id = pg_repository
    psycopg, _sql, _dsn, _schema, _fixture_connect = postgres_test_environment
    reserved = repository.reserve(
        owner_id,
        "scope-runtime-fence",
        "runtime-fence",
        _intent("runtime source fence"),
    )

    # Simulate a pre-fence worker: it still knows the old queued -> leased SQL,
    # but it does not set openair.runtime_source_version in the transaction.
    old_worker = connect()
    try:
        with pytest.raises(psycopg.errors.RaiseException, match="research_start_runtime_source_mismatch"):
            with old_worker.cursor() as cur:
                cur.execute(
                    """UPDATE research_start_requests
                          SET state = 'leased',
                              lease_owner = 'pre-fence-worker',
                              lease_epoch = lease_epoch + 1,
                              lease_expires_at = clock_timestamp() + INTERVAL '60 seconds',
                              updated_at = clock_timestamp()
                        WHERE request_id = %s AND state = 'queued'
                    RETURNING request_id""",
                    (reserved["requestId"],),
                )
        old_worker.rollback()
    finally:
        old_worker.close()

    # The rejected transition must be atomic: the request is still queued.
    check = connect()
    try:
        with check.cursor() as cur:
            cur.execute(
                "SELECT state, lease_owner, runtime_source_version "
                "FROM research_start_requests WHERE request_id = %s",
                (reserved["requestId"],),
            )
            row = cur.fetchone()
            assert row["state"] == "queued"
            assert row["lease_owner"] is None
            assert re.fullmatch(r"[0-9a-f]{64}", row["runtime_source_version"])
    finally:
        check.close()

    # Current worker sets its transaction-local identity; the DB admits the
    # exact same request and exposes the matched source version for tracing.
    claim = repository.claim("matching-worker")
    assert claim is not None
    assert claim["requestId"] == reserved["requestId"]
    assert claim["leaseOwner"] == "matching-worker"
    assert claim["runtimeSourceVersion"] == row["runtime_source_version"]



def test_runtime_source_fence_quarantines_unversioned_legacy_queue_rows(
    pg_repository,
    postgres_test_environment,
) -> None:
    repository, connect, owner_id = pg_repository
    psycopg, _sql, _dsn, _schema, _fixture_connect = postgres_test_environment
    reserved = repository.reserve(
        owner_id,
        "scope-runtime-fence-legacy",
        "runtime-fence-legacy",
        _intent("legacy runtime source fence"),
    )

    # Model one queued request that existed before the source-version column
    # was populated. It remains visible, but it is not claimable by any worker.
    mutate = connect()
    try:
        with mutate.cursor() as cur:
            cur.execute(
                "UPDATE research_start_requests SET runtime_source_version = NULL "
                "WHERE request_id = %s AND state = 'queued'",
                (reserved["requestId"],),
            )
        mutate.commit()
    finally:
        mutate.close()

    assert repository.claim("matching-worker") is None

    old_worker = connect()
    try:
        with pytest.raises(psycopg.errors.RaiseException, match="research_start_runtime_source_mismatch"):
            with old_worker.cursor() as cur:
                cur.execute(
                    """UPDATE research_start_requests
                          SET state = 'leased',
                              lease_owner = 'pre-fence-worker',
                              lease_epoch = lease_epoch + 1,
                              lease_expires_at = clock_timestamp() + INTERVAL '60 seconds',
                              updated_at = clock_timestamp()
                        WHERE request_id = %s AND state = 'queued'
                    RETURNING request_id""",
                    (reserved["requestId"],),
                )
        old_worker.rollback()
    finally:
        old_worker.close()


def test_router_then_planner_finish_and_explicit_complete(pg_repository) -> None:
    repository, connect, owner_id = pg_repository
    reserved = repository.reserve(owner_id, "scope-a", "two-phase", _intent("two phase"))
    claim = repository.claim("worker-a")
    route = repository.begin_attempt(claim["requestId"], "worker-a", claim["leaseEpoch"], "research_planner", 1)

    from app.modules.research_start_repository import AttemptAlreadyStartedError

    with pytest.raises(AttemptAlreadyStartedError):
        repository.begin_attempt(claim["requestId"], "worker-a", claim["leaseEpoch"], "research_planner", 1)
    repository.finish_attempt(
        claim["requestId"], "worker-a", claim["leaseEpoch"], route["attemptId"], succeeded=True, result={"route": "research"}
    )
    planner = repository.begin_attempt(
        claim["requestId"], "worker-a", claim["leaseEpoch"], "research_planner", 1
    )
    repository.finish_attempt(
        claim["requestId"], "worker-a", claim["leaseEpoch"], planner["attemptId"], succeeded=True, result={"plan": "ready"}
    )
    from app.modules.research_start_repository import TaskFinalizeRequiredError

    with pytest.raises(TaskFinalizeRequiredError):
        repository.complete_request(
            claim["requestId"], "worker-a", claim["leaseEpoch"], result={"plan": "ready"}, user_message="Research start ready."
        )
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) AS count FROM research_start_attempts WHERE request_id = %s AND state = 'started'", (claim["requestId"],))
            assert cur.fetchone()["count"] == 0
    finally:
        conn.close()


def test_planner_response_body_is_stored_before_attempt_finishes(pg_repository) -> None:
    repository, connect, owner_id = pg_repository
    repository.reserve(owner_id, "scope-response", "response", _intent("capture response"))
    claim = repository.claim("worker-response")
    planner = repository.begin_attempt(
        claim["requestId"], "worker-response", claim["leaseEpoch"], "research_planner", 1
    )
    raw = b'{"id":"provider-call","choices":[]}'

    repository.record_attempt_response(
        claim["requestId"],
        "worker-response",
        claim["leaseEpoch"],
        planner["attemptId"],
        raw=raw,
        metadata={"phase": "research_planner", "ordinal": 1, "provider": "test", "model": "test"},
    )

    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT state, result_payload, response_body FROM research_start_attempts WHERE attempt_id = %s",
                (planner["attemptId"],),
            )
            stored = cur.fetchone()
        assert stored["state"] == "started"
        assert bytes(stored["response_body"]) == raw
        assert stored["result_payload"]["responseEvidence"]["metadata"]["responseBytes"] == len(raw)
    finally:
        conn.close()


def test_scoped_session_projects_durable_planning_progress_and_safe_key_results(pg_repository) -> None:
    repository, _connect, owner_id = pg_repository
    reserved = repository.reserve(owner_id, "scope-progress", "progress", _intent("plan progress"))

    queued = repository.get_scoped_session(owner_id, "scope-progress", reserved["sessionId"])
    assert queued["reservedTaskId"] == reserved["reservedTaskId"]
    assert queued["planningProgress"]["code"] == "still_queued"
    assert queued["planningProgress"]["elapsedSeconds"] >= 0

    claim = repository.claim("worker-progress")
    assert claim is not None
    claimed = repository.get_scoped_session(owner_id, "scope-progress", reserved["sessionId"])
    assert claimed["planningProgress"]["code"] == "worker_assigned"

    first = repository.begin_attempt(
        claim["requestId"], "worker-progress", claim["leaseEpoch"], "research_planner", 1
    )
    planning = repository.get_scoped_session(owner_id, "scope-progress", reserved["sessionId"])
    assert planning["currentPhase"] == "planning"
    assert planning["planningProgress"]["code"] == "planning_design"
    assert planning["planningProgress"]["attemptOrdinal"] == 1

    repository.finish_attempt(
        claim["requestId"],
        "worker-progress",
        claim["leaseEpoch"],
        first["attemptId"],
        succeeded=False,
        result={"failureCode": "provider_contract_invalid", "rawPrompt": "hidden"},
    )
    repairing = repository.get_scoped_session(owner_id, "scope-progress", reserved["sessionId"])
    assert repairing["planningProgress"]["code"] == "repairing_design"
    assert "hidden" not in repr(repairing)

    second = repository.begin_attempt(
        claim["requestId"], "worker-progress", claim["leaseEpoch"], "research_planner", 2
    )
    repository.finish_attempt(
        claim["requestId"],
        "worker-progress",
        claim["leaseEpoch"],
        second["attemptId"],
        succeeded=True,
        result={
            "providerCallRef": "provider-secret",
            "proposal": {
                "planningMode": "partial",
                "understanding": {
                    "primaryResearchQuestions": ["Which claims remain supported?"],
                    "targetOutputs": ["evidence_map"],
                    "planningRationale": "Keep claims and unresolved gaps in one comparable evidence scope.",
                },
                "instances": [{
                    "instanceKey": "internal-key",
                    "definitionId": "internal-definition",
                    "objective": "Map the evidence and unresolved gaps.",
                    "expectedArtifactTypes": ["evidence_map", "gap_list"],
                }],
            },
        },
    )
    validated = repository.get_scoped_session(owner_id, "scope-progress", reserved["sessionId"])
    assert validated["planningProgress"]["code"] == "compiling_design"
    assert validated["planningProgress"]["repairRound"] == 1
    assert validated["planningProgress"]["keyResults"] == {
        "planningMode": "partial",
        "substageCount": 1,
        "researchQuestions": ["Which claims remain supported?"],
        "objectives": ["Map the evidence and unresolved gaps."],
        "targetOutputs": ["evidence_map"],
        "expectedArtifactTypes": ["evidence_map", "gap_list"],
        "planningRationale": "Keep claims and unresolved gaps in one comparable evidence scope.",
    }
    assert "provider-secret" not in repr(validated)
    assert "internal-key" not in repr(validated)
    assert "internal-definition" not in repr(validated)


def _finish_finalize_attempts(repository, claim: dict[str, object]) -> None:
    worker = "worker-finalize"
    failed = repository.begin_attempt(claim["requestId"], worker, claim["leaseEpoch"], "research_planner", 1)
    repository.finish_attempt(claim["requestId"], worker, claim["leaseEpoch"], failed["attemptId"], succeeded=False)
    for phase, ordinal in (("research_planner", 2), ("research_planner", 1)):
        attempt = repository.begin_attempt(claim["requestId"], worker, claim["leaseEpoch"], phase, ordinal)
        repository.finish_attempt(
            claim["requestId"], worker, claim["leaseEpoch"], attempt["attemptId"], succeeded=True, result={"known": phase}
        )


def _finalize_payload(reserved: dict[str, object], owner_id: str, workspace_id: str):
    artifact = {
        "id": reserved["reservedArtifactId"], "taskId": reserved["reservedTaskId"], "sourceTaskId": reserved["reservedTaskId"],
        "ownerId": owner_id, "workspaceId": workspace_id, "createdBy": owner_id, "kind": "research_start_draft",
        "origin": "generated", "title": "Draft", "storageType": "inline", "inlineContent": "draft",
        "createdAt": "2026-07-25T08:30:00Z",
        "sourceCapabilityCallId": None, "artifactState": "needs_review",
        "structuredResearchStart": {"research_goal": "finalize"},
        "researchStartConfirmation": {"status": "unconfirmed"},
        "missingInputs": [], "nonCitableBoundaries": ["no sources"],
        "downstreamInput": {"status": "blocked_by_confirmation"},
    }
    task = {
        "taskId": reserved["reservedTaskId"], "ownerId": owner_id, "workspaceId": workspace_id, "createdBy": owner_id,
        "title": "Draft", "domain": "research_task", "status": "draft", "creationStatus": "awaiting_confirmation",
        "createdAt": "2026-07-25T08:30:00Z",
        "resultSource": "live", "currentStage": "problem_modeling",
        "stages": ["problem_modeling", "solution_design", "execution_validation", "insight_generation"],
        "stageDetails": [
            {"stageKey": "problem_modeling", "artifactRefs": [reserved["reservedArtifactId"]]},
            {"stageKey": "solution_design", "artifactRefs": []},
            {"stageKey": "execution_validation", "artifactRefs": []},
            {"stageKey": "insight_generation", "artifactRefs": []},
        ], "artifacts": [artifact],
    }
    result = {"outcome": "research_task", "sessionId": reserved["sessionId"], "taskId": reserved["reservedTaskId"]}
    return task, artifact, result


def test_finalize_writes_reserved_task_artifact_once_and_replays_after_lost_response(pg_repository) -> None:
    repository, connect, owner_id = pg_repository
    reserved = repository.reserve(owner_id, "scope-finalize", "finalize", _intent("finalize"))
    claim = repository.claim("worker-finalize")
    _finish_finalize_attempts(repository, claim)
    task, artifact, result = _finalize_payload(reserved, owner_id, "scope-finalize")
    first = repository.finalize_task_request(
        claim["requestId"], "worker-finalize", claim["leaseEpoch"], task=task, artifact=artifact, result=result, user_message="ready"
    )
    replay = repository.finalize_task_request(
        claim["requestId"], "worker-finalize", claim["leaseEpoch"], task=task, artifact=artifact, result=result, user_message="ready"
    )
    assert first == replay == result
    verify = connect()
    try:
        with verify.cursor() as cur:
            cur.execute("SELECT count(*) AS count FROM openair_tasks WHERE task_id = %s", (reserved["reservedTaskId"],))
            assert cur.fetchone()["count"] == 1
            cur.execute("SELECT count(*) AS count FROM openair_artifacts WHERE artifact_id = %s", (reserved["reservedArtifactId"],))
            assert cur.fetchone()["count"] == 1
    finally:
        verify.close()


def test_finalize_rolls_back_staged_task_when_artifact_cursor_helper_fails(pg_repository, monkeypatch) -> None:
    repository, connect, owner_id = pg_repository
    from app.modules.artifact_repository import ArtifactRepository

    reserved = repository.reserve(owner_id, "scope-fault", "fault", _intent("fault"))
    claim = repository.claim("worker-finalize")
    _finish_finalize_attempts(repository, claim)
    task, artifact, result = _finalize_payload(reserved, owner_id, "scope-fault")

    def fail_after_task_insert(_self, _cur, *_args, **_kwargs):
        raise RuntimeError("artifact cursor fault")

    monkeypatch.setattr(ArtifactRepository, "insert_artifact_with_cursor", fail_after_task_insert)
    with pytest.raises(RuntimeError, match="artifact cursor fault"):
        repository.finalize_task_request(
            claim["requestId"], "worker-finalize", claim["leaseEpoch"],
            task=task, artifact=artifact, result=result, user_message="ready",
        )
    verify = connect()
    try:
        with verify.cursor() as cur:
            cur.execute("SELECT count(*) AS count FROM openair_tasks WHERE task_id = %s", (reserved["reservedTaskId"],))
            assert cur.fetchone()["count"] == 0
            cur.execute("SELECT count(*) AS count FROM openair_artifacts WHERE artifact_id = %s", (reserved["reservedArtifactId"],))
            assert cur.fetchone()["count"] == 0
            cur.execute("SELECT state FROM research_start_requests WHERE request_id = %s", (claim["requestId"],))
            assert cur.fetchone()["state"] == "leased"
            cur.execute("SELECT state FROM research_start_sessions WHERE session_id = %s", (reserved["sessionId"],))
            assert cur.fetchone()["state"] == "running"
    finally:
        verify.close()


def test_finalize_identity_conflict_rolls_back_staged_task_and_keeps_authority_live(pg_repository) -> None:
    repository, connect, owner_id = pg_repository
    from app.modules.artifact_repository import ArtifactRepository
    from app.modules.research_start_repository import FinalizeIdentityConflictError

    reserved = repository.reserve(owner_id, "scope-conflict", "conflict", _intent("conflict"))
    claim = repository.claim("worker-finalize")
    _finish_finalize_attempts(repository, claim)
    task, artifact, result = _finalize_payload(reserved, owner_id, "scope-conflict")
    setup = connect()
    try:
        with setup.cursor() as cur:
            ArtifactRepository().insert_artifact_with_cursor(cur, reserved["reservedTaskId"], artifact, "occupied")
        setup.commit()
    finally:
        setup.close()
    with pytest.raises(FinalizeIdentityConflictError):
        repository.finalize_task_request(
            claim["requestId"], "worker-finalize", claim["leaseEpoch"],
            task=task, artifact=artifact, result=result, user_message="ready",
        )
    verify = connect()
    try:
        with verify.cursor() as cur:
            cur.execute("SELECT count(*) AS count FROM openair_tasks WHERE task_id = %s", (reserved["reservedTaskId"],))
            assert cur.fetchone()["count"] == 0
            cur.execute("SELECT count(*) AS count FROM openair_artifacts WHERE artifact_id = %s", (reserved["reservedArtifactId"],))
            assert cur.fetchone()["count"] == 1
            cur.execute("SELECT state FROM research_start_requests WHERE request_id = %s", (claim["requestId"],))
            assert cur.fetchone()["state"] == "leased"
            cur.execute("SELECT state FROM research_start_sessions WHERE session_id = %s", (reserved["sessionId"],))
            assert cur.fetchone()["state"] == "running"
    finally:
        verify.close()


def test_known_failed_attempt_allows_new_ordinal_but_request_failure_is_explicit(pg_repository) -> None:
    repository, _connect, owner_id = pg_repository
    repository.reserve(owner_id, "scope-a", "retry-known", _intent("retry"))
    claim = repository.claim("worker-a")
    first = repository.begin_attempt(claim["requestId"], "worker-a", claim["leaseEpoch"], "research_planner", 1)
    repository.finish_attempt(claim["requestId"], "worker-a", claim["leaseEpoch"], first["attemptId"], succeeded=False)
    second = repository.begin_attempt(claim["requestId"], "worker-a", claim["leaseEpoch"], "research_planner", 2)
    assert second["externalCallPermitted"] is True
    repository.finish_attempt(claim["requestId"], "worker-a", claim["leaseEpoch"], second["attemptId"], succeeded=False)
    failed = repository.fail_request(
        claim["requestId"], "worker-a", claim["leaseEpoch"], failure_code="planner_unavailable", user_message="Research planning is unavailable."
    )
    assert failed["status"] == "failed" and failed["result"] is None


def test_db_clock_expiry_reclaims_before_begin_and_before_physical_submit(pg_repository) -> None:
    repository, connect, owner_id = pg_repository
    repository.reserve(owner_id, "scope-a", "before", _intent("before"))
    first = repository.claim("worker-a")
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE research_start_requests SET lease_expires_at = clock_timestamp() - INTERVAL '1 second' WHERE request_id = %s", (first["requestId"],))
        conn.commit()
    finally:
        conn.close()
    assert repository.reconcile_expired_leases()["reclaimed"] == 1
    repository.claim("worker-b")

    second_reserved = repository.reserve(owner_id, "scope-b", "after", _intent("after"))
    second = repository.claim("worker-c")
    attempt = repository.begin_attempt(second["requestId"], "worker-c", second["leaseEpoch"], "research_planner", 1)
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE research_start_requests SET lease_expires_at = clock_timestamp() - INTERVAL '1 second' WHERE request_id = %s", (second["requestId"],))
        conn.commit()
    finally:
        conn.close()
    outcome = repository.reconcile_expired_leases()
    assert outcome["reclaimed"] == 1
    assert outcome["unknown"] == 0
    session = repository.get_scoped_session(owner_id, "scope-b", second_reserved["sessionId"])
    assert session["status"] == "running" and session["result"] is None
    recovered = repository.claim("worker-d")
    assert recovered is not None and recovered["requestId"] != second["requestId"]


def _insert_provider_evidence(
    connect,
    *,
    request_id: str,
    phase: str,
    execution_state: str,
    transport_outcome: str,
    io_started: bool,
    submission_proven: bool | None,
) -> str:
    attempt_id = f"pha_{uuid4().hex}"
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO openair_provider_http_attempts(
                       attempt_id, capability_call_id, attempt_number, request_hash,
                       provider_endpoint_identity, transport_outcome, evidence_kind,
                       parent_kind, parent_id, operation_identity,
                       logical_attempt_ordinal, execution_state,
                       originating_authority, io_started, submission_proven
                   ) VALUES (
                       %s, NULL, 1, %s, %s, %s, 'provider_submission',
                       'research_start', %s, %s, 1, %s, %s, %s, %s
                   )""",
                (
                    attempt_id,
                    "a" * 64,
                    "test:sha256:" + "b" * 64,
                    transport_outcome,
                    request_id,
                    f"research_start:{phase}",
                    execution_state,
                    f"research_start:{phase}",
                    io_started,
                    submission_proven,
                ),
            )
        conn.commit()
    finally:
        conn.close()
    return attempt_id


@pytest.mark.parametrize(
    ("physical", "expected"),
    [
        (None, "not_submitted"),
        (("completed", "response_received", True), "response_observed"),
        (("unknown", "transport_uncertain", None), "external_result_unknown"),
    ],
)
def test_provider_phase_failure_settlement_uses_physical_truth_under_fence(
    pg_repository,
    physical,
    expected: str,
) -> None:
    repository, connect, owner_id = pg_repository
    reserved = repository.reserve(owner_id, "scope-settle", f"settle-{expected}", _intent("settle"))
    claim = repository.claim("worker-settle")
    assert claim is not None
    attempt = repository.begin_attempt(
        claim["requestId"], "worker-settle", claim["leaseEpoch"], "research_planner", 1
    )
    if physical is not None:
        state, outcome, submission = physical
        _insert_provider_evidence(
            connect,
            request_id=str(claim["requestId"]),
            phase="research_planner",
            execution_state=state,
            transport_outcome=outcome,
            io_started=True,
            submission_proven=submission,
        )

    disposition = repository.settle_provider_phase_failure(
        claim["requestId"],
        "worker-settle",
        claim["leaseEpoch"],
        attempt["attemptId"],
        failure_result={"failureCode": "provider_phase_processing_failed"},
    )
    assert disposition == expected

    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT state, result_payload FROM research_start_attempts WHERE attempt_id = %s",
                (attempt["attemptId"],),
            )
            persisted = cur.fetchone()
    finally:
        conn.close()
    assert persisted["state"] == ("unknown" if expected == "external_result_unknown" else "failed")
    if physical is not None:
        assert persisted["result_payload"]["providerPhysicalEvidence"]["submissionProven"] is physical[2]
    public = repository.get_scoped_session(owner_id, "scope-settle", reserved["sessionId"])
    if expected == "external_result_unknown":
        assert public["status"] == "external_result_unknown"
    else:
        assert public["status"] == "running"


@pytest.mark.parametrize(
    ("execution_state", "transport_outcome", "submission_proven"),
    [
        ("unknown", "transport_uncertain", None),
        ("completed", "response_received", True),
    ],
)
def test_expired_started_attempt_with_physical_submission_never_replays_provider(
    pg_repository,
    execution_state: str,
    transport_outcome: str,
    submission_proven: bool | None,
) -> None:
    repository, connect, owner_id = pg_repository
    reserved = repository.reserve(owner_id, "scope-physical", f"physical-{execution_state}", _intent("physical"))
    claim = repository.claim("worker-physical")
    assert claim is not None
    repository.begin_attempt(
        claim["requestId"], "worker-physical", claim["leaseEpoch"], "research_planner", 1
    )
    _insert_provider_evidence(
        connect,
        request_id=str(claim["requestId"]),
        phase="research_planner",
        execution_state=execution_state,
        transport_outcome=transport_outcome,
        io_started=True,
        submission_proven=submission_proven,
    )
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE research_start_requests SET lease_expires_at = clock_timestamp() - INTERVAL '1 second' WHERE request_id = %s",
                (claim["requestId"],),
            )
        conn.commit()
    finally:
        conn.close()

    result = repository.reconcile_expired_leases()
    assert result["unknown"] == 1
    assert result["reclaimed"] == 0
    public = repository.get_scoped_session(owner_id, "scope-physical", reserved["sessionId"])
    assert public["status"] == "external_result_unknown"
    assert repository.claim("worker-replay") is None


def test_unknown_physical_result_rejects_request_replay_and_keeps_single_submission(
    pg_repository,
) -> None:
    from app.modules.research_start_repository import TerminalStateError

    repository, connect, owner_id = pg_repository
    reserved = repository.reserve(owner_id, "scope-no-replay", "unknown-no-replay", _intent("unknown"))
    claim = repository.claim("worker-unknown")
    assert claim is not None
    attempt = repository.begin_attempt(
        claim["requestId"], "worker-unknown", claim["leaseEpoch"], "research_planner", 1
    )
    _insert_provider_evidence(
        connect,
        request_id=str(claim["requestId"]),
        phase="research_planner",
        execution_state="unknown",
        transport_outcome="transport_uncertain",
        io_started=True,
        submission_proven=None,
    )
    repository.mark_external_result_unknown(
        claim["requestId"],
        "worker-unknown",
        claim["leaseEpoch"],
        attempt["attemptId"],
        {"failureCode": "external_result_unknown"},
    )

    with pytest.raises(TerminalStateError, match="cannot be replayed"):
        repository.retry_external_result_unknown(
            owner_id,
            "scope-no-replay",
            reserved["sessionId"],
        )

    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) AS count FROM openair_provider_http_attempts WHERE parent_kind = 'research_start' AND parent_id = %s",
                (claim["requestId"],),
            )
            assert cur.fetchone()["count"] == 1
            cur.execute(
                "SELECT count(*) AS count FROM research_start_requests WHERE session_id = %s",
                (reserved["sessionId"],),
            )
            assert cur.fetchone()["count"] == 1
    finally:
        conn.close()


def test_formal_recovery_request_wins_over_old_unknown_projection(pg_repository) -> None:
    from app.modules.research_design_service import new_research_task

    repository, connect, owner_id = pg_repository
    reserved = repository.reserve(owner_id, "scope-recovery", "formal-recovery", _intent("recover"))
    _persist_research_task(
        connect,
        new_research_task(
            task_id=reserved["reservedTaskId"],
            registry_snapshot_ref=RESEARCH_DESIGN_SNAPSHOT,
        ),
        owner_id=owner_id,
        workspace_id="scope-recovery",
    )
    claim = repository.claim("worker-recovery")
    assert claim is not None
    attempt = repository.begin_attempt(
        claim["requestId"], "worker-recovery", claim["leaseEpoch"], "research_planner", 1
    )
    _insert_provider_evidence(
        connect,
        request_id=str(claim["requestId"]),
        phase="research_planner",
        execution_state="failed",
        transport_outcome="transport_not_submitted",
        io_started=True,
        submission_proven=False,
    )
    repository.mark_external_result_unknown(
        claim["requestId"],
        "worker-recovery",
        claim["leaseEpoch"],
        attempt["attemptId"],
        {"failureCode": "external_result_unknown"},
    )

    unknown_task = _read_task_snapshot(connect, reserved["reservedTaskId"])
    assert unknown_task["status"] == "awaiting_confirmation"
    assert unknown_task["revision"] == 1
    assert unknown_task["payload"]["status"] == "awaiting_confirmation"
    assert unknown_task["payload"]["progressProjection"]["revision"] == 1
    assert (
        unknown_task["payload"]["progressProjection"]["taskProgress"]["status"]
        == "external_result_unknown"
    )

    recovery = repository.retry_external_result_unknown(
        owner_id,
        "scope-recovery",
        reserved["sessionId"],
    )
    assert recovery["status"] == "queued"
    public = repository.get_scoped_session(owner_id, "scope-recovery", reserved["sessionId"])
    assert public["status"] == "running"
    assert public["planningProgress"]["code"] == "still_queued"
    assert repository.claim("worker-recovery-next")["requestId"] == recovery["requestId"]

    restored_task = _read_task_snapshot(connect, reserved["reservedTaskId"])
    assert restored_task["status"] == "draft"
    assert restored_task["revision"] == 2
    assert restored_task["payload"]["status"] == "draft"
    assert restored_task["payload"]["progressProjection"]["revision"] == 2
    assert restored_task["payload"]["progressProjection"]["taskProgress"]["status"] == "draft"


def test_postgres_public_projection_rejects_orphaned_running_session(pg_repository) -> None:
    repository, connect, owner_id = pg_repository
    reserved = repository.reserve(owner_id, "scope-orphan", "orphaned", _intent("orphan"))
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE research_start_requests
                      SET state = 'unknown', lease_owner = NULL, lease_expires_at = NULL
                    WHERE request_id = %s""",
                (reserved["requestId"],),
            )
        conn.commit()
    finally:
        conn.close()

    public = repository.get_scoped_session(owner_id, "scope-orphan", reserved["sessionId"])
    assert public["status"] == "failed"
    assert public["recoveryReason"] == "responsibility_gap"
    assert public["automaticRetryAllowed"] is False



def test_expired_succeeded_planner_without_committed_receipt_closes_unknown_and_sweep_continues(
    pg_repository,
) -> None:
    repository, connect, owner_id = pg_repository
    first_reserved = repository.reserve(owner_id, "scope-a", "missing-receipt", _intent("missing receipt"))
    first_claim = repository.claim("worker-a")
    assert first_claim is not None
    attempt = repository.begin_attempt(
        first_claim["requestId"],
        "worker-a",
        first_claim["leaseEpoch"],
        "research_planner",
        1,
    )
    repository.finish_attempt(
        first_claim["requestId"],
        "worker-a",
        first_claim["leaseEpoch"],
        attempt["attemptId"],
        succeeded=True,
        result={"providerCallRef": "provider-call-without-design"},
    )
    second_reserved = repository.reserve(owner_id, "scope-b", "next-request", _intent("next request"))

    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE research_start_requests SET lease_expires_at = clock_timestamp() - INTERVAL '1 second' "
                "WHERE request_id = %s",
                (first_claim["requestId"],),
            )
        conn.commit()
    finally:
        conn.close()

    assert repository.reconcile_expired_leases() == {
        "reclaimed": 0,
        "unknown": 1,
        "reconciled": 0,
    }

    verify = connect()
    try:
        with verify.cursor() as cur:
            cur.execute(
                "SELECT state FROM research_start_attempts WHERE attempt_id = %s",
                (attempt["attemptId"],),
            )
            assert cur.fetchone()["state"] == "succeeded"
            cur.execute(
                "SELECT state FROM research_start_requests WHERE request_id = %s",
                (first_claim["requestId"],),
            )
            assert cur.fetchone()["state"] == "unknown"
            cur.execute(
                "SELECT state, failure_code FROM research_start_sessions WHERE session_id = %s",
                (first_reserved["sessionId"],),
            )
            assert cur.fetchone() == {
                "state": "external_result_unknown",
                "failure_code": "external_result_unknown",
            }
    finally:
        verify.close()

    next_claim = repository.claim("worker-next")
    assert next_claim is not None
    assert next_claim["requestId"] == second_reserved["requestId"]


def test_claim_and_reconcile_interleaving_has_no_deadlock(pg_repository) -> None:
    repository, connect, owner_id = pg_repository
    for index in range(4):
        repository.reserve(owner_id, f"scope-{index}", f"request-{index}", _intent(str(index)))
    first = repository.claim("worker-expiring")
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE research_start_requests SET lease_expires_at = clock_timestamp() - INTERVAL '1 second' WHERE request_id = %s", (first["requestId"],))
        conn.commit()
    finally:
        conn.close()
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(repository.claim, "worker-claim"), pool.submit(repository.reconcile_expired_leases)]
        results = [future.result(timeout=5) for future in futures]
    assert results[0] is not None and isinstance(results[1], dict)


def test_down_blocks_concurrent_insert_and_runner_rebuilds(postgres_test_environment, monkeypatch) -> None:
    psycopg, sql, dsn, _shared_schema, _connect = postgres_test_environment
    schema = f"w4_down_{uuid4().hex}"

    def connect(*, autocommit: bool = False):
        conn = psycopg.connect(dsn, autocommit=autocommit)
        with conn.cursor() as cur:
            cur.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
        return conn

    admin = psycopg.connect(dsn, autocommit=True)
    try:
        with admin.cursor() as cur:
            cur.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        lifecycle = connect()
        from app.modules import postgres_migrations

        class OnlyThisMigration:
            def glob(self, _pattern: str):
                return [MIGRATION_PATH]

        monkeypatch.setattr(postgres_migrations, "MIGRATIONS_DIR", OnlyThisMigration())
        with lifecycle.cursor() as cur:
            postgres_migrations.apply_postgres_migrations(cur)
        lifecycle.commit()

        lock_conn = connect()
        insert_conn = connect()
        with lock_conn.cursor() as cur:
            cur.execute("LOCK TABLE research_start_sessions IN ACCESS EXCLUSIVE MODE")
        with ThreadPoolExecutor(max_workers=1) as pool:
            blocked = pool.submit(
                lambda: insert_conn.execute(
                    "INSERT INTO research_start_sessions(session_id, owner_id, workspace_id, reserved_task_id, reserved_artifact_id) "
                    "VALUES ('intent_000000000001', 'o', 'w', 'task_00000000000000000000000000000001', "
                    "'artifact_00000000000000000000000000000001')"
                )
            )
            with pytest.raises(TimeoutError):
                blocked.result(timeout=0.2)
            lock_conn.rollback()
            blocked.result(timeout=5)
        insert_conn.rollback()
        lock_conn.close()
        insert_conn.close()

        lifecycle.close()
        down_conn = connect(autocommit=True)
        down_conn.execute(DOWN_MIGRATION_PATH.read_text(encoding="utf-8"))
        down_conn.close()
        rebuilt = connect()
        with rebuilt.cursor() as cur:
            postgres_migrations.apply_postgres_migrations(cur)
            cur.execute("SELECT to_regclass('research_start_sessions')")
            assert cur.fetchone()[0] == "research_start_sessions"
        rebuilt.rollback()
        rebuilt.close()
    finally:
        with admin.cursor() as cur:
            cur.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))
        admin.close()


def _prepare_authoritative_design_attempt(
    repository,
    connect,
    owner_id: str,
    *,
    workspace_id: str,
    client_request_id: str,
    provider_call_ref: str,
    finish: bool,
):
    reserved = repository.reserve(
        owner_id,
        workspace_id,
        client_request_id,
        _intent(client_request_id),
    )
    worker_id = f"worker-{client_request_id}"
    claim = repository.claim(worker_id)
    assert claim is not None and claim["requestId"] == reserved["requestId"]
    attempt = repository.begin_attempt(
        claim["requestId"],
        worker_id,
        claim["leaseEpoch"],
        "research_planner",
        1,
    )
    task = _build_planned_task(
        task_id=reserved["reservedTaskId"],
        request_id=claim["requestId"],
        provider_call_ref=provider_call_ref,
    )
    _persist_research_task(
        connect,
        task,
        owner_id=owner_id,
        workspace_id=workspace_id,
    )
    if finish:
        repository.finish_attempt(
            claim["requestId"],
            worker_id,
            claim["leaseEpoch"],
            attempt["attemptId"],
            succeeded=True,
            result={"providerCallRef": provider_call_ref},
        )
    return reserved, claim, attempt, task, worker_id


def test_postgres_design_commit_before_finish_reconciles_without_planner_replay(
    pg_repository,
) -> None:
    repository, connect, owner_id = pg_repository
    reserved, claim, attempt, _task, worker_id = _prepare_authoritative_design_attempt(
        repository,
        connect,
        owner_id,
        workspace_id="scope-design-before-finish",
        client_request_id="design-before-finish",
        provider_call_ref="provider:before-finish",
        finish=False,
    )

    completed = repository.reconcile_committed_design_request(
        claim["requestId"],
        worker_id,
        claim["leaseEpoch"],
        user_message="ready",
    )

    assert completed is not None and completed["status"] == "completed"
    assert completed["reservedTaskId"] == reserved["reservedTaskId"]
    verify = connect()
    try:
        with verify.cursor() as cur:
            cur.execute(
                "SELECT state, result_payload FROM research_start_attempts WHERE attempt_id = %s",
                (attempt["attemptId"],),
            )
            row = cur.fetchone()
            assert row["state"] == "succeeded"
            assert row["result_payload"]["providerCallRef"] == "provider:before-finish"
    finally:
        verify.close()


def test_postgres_finish_before_finalize_reloads_and_persists_authoritative_receipt(
    pg_repository,
) -> None:
    repository, connect, owner_id = pg_repository
    reserved, claim, _attempt, _task, worker_id = _prepare_authoritative_design_attempt(
        repository,
        connect,
        owner_id,
        workspace_id="scope-finish-before-finalize",
        client_request_id="finish-before-finalize",
        provider_call_ref="provider:finish-before-finalize",
        finish=True,
    )

    finalized = repository.finalize_from_authoritative_receipt(
        claim["requestId"],
        worker_id,
        claim["leaseEpoch"],
        user_message="ready",
    )

    assert finalized["finalized"] is True
    assert finalized["receipt"]["taskId"] == reserved["reservedTaskId"]
    verify = connect()
    try:
        with verify.cursor() as cur:
            cur.execute(
                "SELECT state, result_payload FROM research_start_requests WHERE request_id = %s",
                (claim["requestId"],),
            )
            request = cur.fetchone()
            assert request["state"] == "succeeded"
            assert request["result_payload"]["authoritativeReceipt"]["providerCallRef"] == (
                "provider:finish-before-finalize"
            )
            cur.execute(
                "SELECT state, result_payload FROM research_start_sessions WHERE session_id = %s",
                (reserved["sessionId"],),
            )
            session = cur.fetchone()
            assert session["state"] == "completed"
            assert session["result_payload"]["taskId"] == reserved["reservedTaskId"]
    finally:
        verify.close()


@pytest.mark.parametrize("terminal_status", ("cancelled", "superseded"))
def test_postgres_finalize_rejects_terminal_latest_design_after_finish(
    pg_repository,
    terminal_status: str,
) -> None:
    repository, connect, owner_id = pg_repository
    reserved, claim, _attempt, _task, worker_id = _prepare_authoritative_design_attempt(
        repository,
        connect,
        owner_id,
        workspace_id=f"scope-terminal-{terminal_status}",
        client_request_id=f"terminal-{terminal_status}",
        provider_call_ref=f"provider:{terminal_status}",
        finish=True,
    )
    terminal_task = _build_planned_task(
        task_id=reserved["reservedTaskId"],
        request_id=claim["requestId"],
        provider_call_ref=f"provider:{terminal_status}",
        cancel_latest=True,
    )
    if terminal_status == "superseded":
        payload = terminal_task.model_dump(mode="json", by_alias=True)
        payload["researchDesignVersions"][-1]["status"] = "superseded"
        from app.models.research_orchestration import ResearchTaskV12

        terminal_task = ResearchTaskV12.model_validate(payload)
    update = connect()
    try:
        with update.cursor() as cur:
            _replace_research_task_payload(cur, terminal_task)
        update.commit()
    finally:
        update.close()

    finalized = repository.finalize_from_authoritative_receipt(
        claim["requestId"],
        worker_id,
        claim["leaseEpoch"],
        user_message="ready",
    )

    assert finalized["finalized"] is False
    assert finalized["closed"] is True
    assert finalized["reason"] == "authoritative_receipt_unavailable"
    verify = connect()
    try:
        with verify.cursor() as cur:
            cur.execute(
                "SELECT state FROM research_start_requests WHERE request_id = %s",
                (claim["requestId"],),
            )
            assert cur.fetchone()["state"] == "failed"
            cur.execute(
                "SELECT state, failure_code FROM research_start_sessions WHERE session_id = %s",
                (reserved["sessionId"],),
            )
            assert cur.fetchone() == {
                "state": "failed",
                "failure_code": "provider_contract_invalid",
            }
    finally:
        verify.close()


def test_postgres_finalize_rejects_later_canonical_revision_from_other_lineage(
    pg_repository,
) -> None:
    repository, connect, owner_id = pg_repository
    reserved, claim, _attempt, _task, worker_id = _prepare_authoritative_design_attempt(
        repository,
        connect,
        owner_id,
        workspace_id="scope-later-revision",
        client_request_id="later-revision-original",
        provider_call_ref="provider:original",
        finish=True,
    )
    revised_task = _build_planned_task(
        task_id=reserved["reservedTaskId"],
        request_id=claim["requestId"],
        provider_call_ref="provider:original",
        later_request_id="later-planning-request",
    )
    update = connect()
    try:
        with update.cursor() as cur:
            _replace_research_task_payload(cur, revised_task)
        update.commit()
    finally:
        update.close()

    finalized = repository.finalize_from_authoritative_receipt(
        claim["requestId"],
        worker_id,
        claim["leaseEpoch"],
        user_message="ready",
    )

    assert finalized["finalized"] is False
    assert finalized["closed"] is True
    verify = connect()
    try:
        with verify.cursor() as cur:
            cur.execute(
                "SELECT state FROM research_start_requests WHERE request_id = %s",
                (claim["requestId"],),
            )
            assert cur.fetchone()["state"] == "failed"
            cur.execute(
                "SELECT state, failure_code FROM research_start_sessions WHERE session_id = %s",
                (reserved["sessionId"],),
            )
            assert cur.fetchone() == {
                "state": "failed",
                "failure_code": "provider_contract_invalid",
            }
    finally:
        verify.close()


def test_postgres_finalize_rejects_polluted_provider_receipt_without_rewriting_attempt(
    pg_repository,
) -> None:
    repository, connect, owner_id = pg_repository
    reserved, claim, attempt, _task, worker_id = _prepare_authoritative_design_attempt(
        repository,
        connect,
        owner_id,
        workspace_id="scope-provider-mismatch",
        client_request_id="provider-mismatch",
        provider_call_ref="provider:task",
        finish=False,
    )
    repository.finish_attempt(
        claim["requestId"],
        worker_id,
        claim["leaseEpoch"],
        attempt["attemptId"],
        succeeded=True,
        result={"providerCallRef": "provider:attempt"},
    )

    finalized = repository.finalize_from_authoritative_receipt(
        claim["requestId"],
        worker_id,
        claim["leaseEpoch"],
        user_message="ready",
    )

    assert finalized["finalized"] is False
    verify = connect()
    try:
        with verify.cursor() as cur:
            cur.execute(
                "SELECT state, result_payload FROM research_start_attempts WHERE attempt_id = %s",
                (attempt["attemptId"],),
            )
            row = cur.fetchone()
            assert row == {
                "state": "succeeded",
                "result_payload": {"providerCallRef": "provider:attempt"},
            }
            cur.execute(
                "SELECT state FROM research_start_requests WHERE request_id = %s",
                (claim["requestId"],),
            )
            assert cur.fetchone()["state"] == "failed"
            cur.execute(
                "SELECT state, failure_code FROM research_start_sessions WHERE session_id = %s",
                (reserved["sessionId"],),
            )
            assert cur.fetchone() == {
                "state": "failed",
                "failure_code": "provider_contract_invalid",
            }
    finally:
        verify.close()


def test_postgres_stale_epoch_cannot_finalize_authoritative_receipt(pg_repository) -> None:
    repository, connect, owner_id = pg_repository
    _reserved, claim, _attempt, _task, worker_id = _prepare_authoritative_design_attempt(
        repository,
        connect,
        owner_id,
        workspace_id="scope-stale-finalize",
        client_request_id="stale-finalize",
        provider_call_ref="provider:stale",
        finish=True,
    )
    mutate = connect()
    try:
        with mutate.cursor() as cur:
            cur.execute(
                """UPDATE research_start_requests
                      SET lease_owner = 'new-worker', lease_epoch = lease_epoch + 1,
                          lease_expires_at = clock_timestamp() + INTERVAL '30 seconds'
                    WHERE request_id = %s""",
                (claim["requestId"],),
            )
        mutate.commit()
    finally:
        mutate.close()

    from app.modules.research_start_repository import LeaseLostError

    with pytest.raises(LeaseLostError):
        repository.finalize_from_authoritative_receipt(
            claim["requestId"],
            worker_id,
            claim["leaseEpoch"],
            user_message="ready",
        )


def test_postgres_two_concurrent_finalizers_only_one_completes(pg_repository) -> None:
    repository, connect, owner_id = pg_repository
    _reserved, claim, _attempt, _task, worker_id = _prepare_authoritative_design_attempt(
        repository,
        connect,
        owner_id,
        workspace_id="scope-double-finalize",
        client_request_id="double-finalize",
        provider_call_ref="provider:double",
        finish=True,
    )

    def finalize_once():
        candidate = type(repository)(connection_factory=connect)
        try:
            result = candidate.finalize_from_authoritative_receipt(
                claim["requestId"],
                worker_id,
                claim["leaseEpoch"],
                user_message="ready",
            )
            return "completed" if result["finalized"] else "not_finalized"
        except Exception as exc:  # the losing finalizer must observe terminal/fence authority
            return type(exc).__name__

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = [future.result(timeout=5) for future in [pool.submit(finalize_once), pool.submit(finalize_once)]]

    assert outcomes.count("completed") == 1
    assert len(outcomes) == 2
    assert outcomes[0] != outcomes[1]


def test_postgres_authoritative_finalize_rolls_back_partial_success_update(
    pg_repository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, connect, owner_id = pg_repository
    reserved, claim, _attempt, _task, worker_id = _prepare_authoritative_design_attempt(
        repository,
        connect,
        owner_id,
        workspace_id="scope-finalize-rollback",
        client_request_id="finalize-rollback",
        provider_call_ref="provider:rollback",
        finish=True,
    )

    def fail_after_request_update(cur, _session, request, _attempt, _receipt, **_kwargs):
        cur.execute(
            "UPDATE research_start_requests SET state = 'succeeded' WHERE request_id = %s",
            (request["request_id"],),
        )
        raise RuntimeError("injected finalize failure")

    monkeypatch.setattr(repository, "_complete_committed_design_lineage", fail_after_request_update)
    with pytest.raises(RuntimeError, match="injected finalize failure"):
        repository.finalize_from_authoritative_receipt(
            claim["requestId"],
            worker_id,
            claim["leaseEpoch"],
            user_message="ready",
        )

    verify = connect()
    try:
        with verify.cursor() as cur:
            cur.execute(
                "SELECT state FROM research_start_requests WHERE request_id = %s",
                (claim["requestId"],),
            )
            assert cur.fetchone()["state"] == "leased"
            cur.execute(
                "SELECT state FROM research_start_sessions WHERE session_id = %s",
                (reserved["sessionId"],),
            )
            assert cur.fetchone()["state"] == "running"
    finally:
        verify.close()


@pytest.mark.parametrize("competing_change", ("cancel", "later_revision"))
def test_postgres_authoritative_finalize_holds_task_lock_through_completion(
    pg_repository,
    monkeypatch: pytest.MonkeyPatch,
    competing_change: str,
) -> None:
    repository, connect, owner_id = pg_repository
    reserved, claim, _attempt, _task, worker_id = _prepare_authoritative_design_attempt(
        repository,
        connect,
        owner_id,
        workspace_id=f"scope-lock-{competing_change}",
        client_request_id=f"lock-{competing_change}",
        provider_call_ref=f"provider:{competing_change}",
        finish=True,
    )
    task_locked = Event()
    release_finalize = Event()
    original_complete = repository._complete_committed_design_lineage

    def pause_after_receipt(*args, **kwargs):
        task_locked.set()
        assert release_finalize.wait(timeout=5)
        return original_complete(*args, **kwargs)

    monkeypatch.setattr(repository, "_complete_committed_design_lineage", pause_after_receipt)

    def finalize():
        return repository.finalize_from_authoritative_receipt(
            claim["requestId"],
            worker_id,
            claim["leaseEpoch"],
            user_message="ready",
        )

    def competing_task_write():
        conn = connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT payload FROM openair_tasks WHERE task_id = %s FOR UPDATE",
                    (reserved["reservedTaskId"],),
                )
                cur.fetchone()
            conn.rollback()
            return "lock_acquired_after_finalize"
        finally:
            conn.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        finalize_future = pool.submit(finalize)
        assert task_locked.wait(timeout=5)
        competing_future = pool.submit(competing_task_write)
        with pytest.raises(TimeoutError):
            competing_future.result(timeout=0.2)
        release_finalize.set()
        finalized = finalize_future.result(timeout=5)
        competing = competing_future.result(timeout=5)

    assert finalized["finalized"] is True
    assert competing == "lock_acquired_after_finalize"

def test_known_planner_504_queues_one_durable_retry_and_preserves_known_failure(pg_repository) -> None:
    repository, connect, owner_id = pg_repository
    reserved = repository.reserve(owner_id, "scope-recoverable-504", "recoverable-504", _intent("recoverable 504"))
    first_claim = repository.claim("worker-a")
    assert first_claim is not None

    planner = repository.begin_attempt(
        first_claim["requestId"], "worker-a", first_claim["leaseEpoch"], "research_planner", 1
    )
    first_failure = {
        "failureCode": "provider_http_rejected",
        "providerDiagnostic": {
            "httpStatus": 504,
            "providerRequestId": "req-first-504",
            "elapsedSeconds": 61.4,
        },
    }
    retry = repository.resolve_known_planner_http_failure(
        first_claim["requestId"], "worker-a", first_claim["leaseEpoch"], planner["attemptId"],
        failure_result=first_failure, automatic_recovery_limit=1, terminal_user_message="planner failed",
    )
    assert retry == "recovery_queued"

    session = repository.get_scoped_session(owner_id, "scope-recoverable-504", reserved["sessionId"])
    assert session["status"] == "running"
    assert session["result"] is None

    second_claim = repository.claim("worker-b")
    assert second_claim is not None
    assert second_claim["requestId"] != first_claim["requestId"]
    second_context = repository.load_execution_context(
        second_claim["requestId"], "worker-b", second_claim["leaseEpoch"]
    )
    assert [
        (row["phase"], row["ordinal"], row["status"])
        for row in second_context["attempts"]
    ] == [("research_planner", 1, "succeeded")]

    second_planner = repository.begin_attempt(
        second_claim["requestId"], "worker-b", second_claim["leaseEpoch"], "research_planner", 1
    )
    second_failure = {
        "failureCode": "provider_http_rejected",
        "providerDiagnostic": {
            "httpStatus": 504,
            "providerRequestId": "req-second-504",
            "elapsedSeconds": 62.1,
        },
    }
    assert repository.resolve_known_planner_http_failure(
        second_claim["requestId"], "worker-b", second_claim["leaseEpoch"], second_planner["attemptId"],
        failure_result=second_failure, automatic_recovery_limit=1, terminal_user_message="planner failed",
    ) == "terminal"

    terminal_session = repository.get_scoped_session(
        owner_id, "scope-recoverable-504", reserved["sessionId"]
    )
    assert terminal_session["status"] == "failed"

    verify = connect()
    try:
        with verify.cursor() as cur:
            cur.execute(
                "SELECT state, result_payload FROM research_start_attempts WHERE attempt_id = %s",
                (planner["attemptId"],),
            )
            row = cur.fetchone()
            assert row["state"] == "failed"
            payload = row["result_payload"]
            if isinstance(payload, str):
                payload = json.loads(payload)
            assert payload["failureCode"] == "provider_http_rejected"
            assert payload["providerDiagnostic"]["httpStatus"] == 504
            assert payload["recoveryAction"] == "durable_retry_queued"
            cur.execute(
                "SELECT result_payload FROM research_start_attempts WHERE attempt_id = %s",
                (second_planner["attemptId"],),
            )
            second_payload = cur.fetchone()["result_payload"]
            if isinstance(second_payload, str):
                second_payload = json.loads(second_payload)
            assert second_payload["failureCode"] == "provider_http_rejected"
            assert second_payload["providerDiagnostic"]["httpStatus"] == 504
            assert second_payload["recoveryAction"] == "durable_retry_budget_exhausted"
            cur.execute(
                "SELECT count(*) AS count FROM research_start_requests WHERE session_id = %s",
                (reserved["sessionId"],),
            )
            assert cur.fetchone()["count"] == 2
    finally:
        verify.close()
