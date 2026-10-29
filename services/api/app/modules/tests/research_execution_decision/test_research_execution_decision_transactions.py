"""T05 PostgreSQL selection, fairness, CAS, and outbox transactions."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from hashlib import sha256
import json
import os
from pathlib import Path
from threading import Barrier
from time import perf_counter

import psycopg
from psycopg.rows import dict_row
import pytest

from app.config import settings
from app.models.research_orchestration import DispatchRecord, ResearchTaskV12
from app.modules.research_execution_decision_service import (
    ExecutionMaterialization,
    FairnessDescriptor,
    PreparedSelection,
)
from app.modules.research_orchestration_repository import (
    DispatchIdentityConflictError,
    ResearchTaskNotFoundError,
    ResearchOrchestrationRepository,
    SelectionRequestPayloadConflictError,
    TaskRevisionConflictError,
)
from app.modules.research_execution_start_status import (
    ResearchExecutionStartStatusReader,
    ResearchExecutionStartTaskNotFound,
)
from app.modules.research_orchestration_resolver import ResolutionResult


DSN_ENV = "OPENAIR_T05_POSTGRES_DSN"
SHARED_FIXTURE = (
    Path(__file__).resolve().parents[6]
    / "packages"
    / "shared"
    / "test-fixtures"
    / "research-orchestration-v1.2-valid.json"
)


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
def clean_tables(monkeypatch: pytest.MonkeyPatch, postgres_environment):
    monkeypatch.setattr(settings, "research_orchestration_v1", True)
    monkeypatch.setattr(settings, "persistence_backend", "postgres")
    connect = postgres_environment
    with connect() as connection:
        connection.execute("DELETE FROM openair_execution_selection_request")
        connection.execute("DELETE FROM openair_equivalent_candidate_fairness")
        connection.execute("DELETE FROM openair_dispatch")
        connection.execute("DELETE FROM openair_work_unit_lineage")
        connection.execute("DELETE FROM openair_tasks")
    yield
    with connect() as connection:
        connection.execute("DELETE FROM openair_execution_selection_request")
        connection.execute("DELETE FROM openair_equivalent_candidate_fairness")
        connection.execute("DELETE FROM openair_dispatch")
        connection.execute("DELETE FROM openair_work_unit_lineage")
        connection.execute("DELETE FROM openair_tasks")


@pytest.fixture()
def repository(monkeypatch: pytest.MonkeyPatch, postgres_environment):
    monkeypatch.setattr(settings, "research_orchestration_v1", True)
    monkeypatch.setattr(settings, "persistence_backend", "postgres")
    return ResearchOrchestrationRepository(
        connection_factory=postgres_environment,
        runtime_settings=settings,
    )


def _replace_identifiers(value: object, suffix: str) -> object:
    identities = (
        "task_contract_full", "planning_contract_full", "request_contract_full",
        "provider_call_contract_full", "rd_contract_full", "sub_contract_full",
        "criterion_contract_full", "target_contract_full", "coverage_contract_full",
        "confirmation_design_contract_full", "selection_contract_full",
        "decision_contract_full", "candidate_contract_full", "assignment_contract_full",
        "wu_contract_full", "call_contract_full", "artifact_contract_full",
        "interaction_contract_full", "dispatch_contract_full",
    )
    if isinstance(value, dict):
        return {key: _replace_identifiers(item, suffix) for key, item in value.items()}
    if isinstance(value, list):
        return [_replace_identifiers(item, suffix) for item in value]
    if isinstance(value, str):
        for identity in identities:
            value = value.replace(identity, f"{identity}_{suffix}")
    return value


def _selection_fixture(suffix: str, request_id: str):
    fixture = json.loads(SHARED_FIXTURE.read_text(encoding="utf-8"))
    transformed = _replace_identifiers(fixture, suffix)
    payload = transformed["researchTask"]
    payload["revision"] = 0
    payload["executionDecisions"][0]["selectionRequestId"] = request_id
    payload["substageRuntimeStates"][0].update(
        {
            "status": "queued",
            "activeExecutionDecisionId": payload["executionDecisions"][0]["executionDecisionId"],
            "workUnitIds": [payload["workUnits"][0]["workUnitId"]],
            "artifactRefs": [],
        }
    )
    payload["workUnits"][0].update(
        {"status": "queued", "terminalReasonCode": None, "finishedAt": None}
    )
    payload["capabilityCalls"] = []
    payload["artifacts"] = []
    payload["agentInteractionRecords"] = []
    candidate = ResearchTaskV12.model_validate(payload)
    baseline_payload = candidate.model_dump(mode="json", by_alias=True)
    baseline_payload["executionDecisions"] = []
    baseline_payload["workUnits"] = []
    baseline_payload["substageRuntimeStates"][0].update(
        {"status": "ready", "activeExecutionDecisionId": None, "workUnitIds": []}
    )
    baseline = ResearchTaskV12.model_validate(baseline_payload)
    dispatch = DispatchRecord.model_validate(transformed["dispatchRecord"])
    dispatch_payload = dispatch.model_dump(mode="json", by_alias=True)
    # Pending inserts require a namespaced pristine DispatchRecord; shared
    # fixtures predate the fence and omit executionNamespace.
    if not dispatch_payload.get("executionNamespace"):
        dispatch_payload["executionNamespace"] = (
            getattr(settings, "execution_namespace", None) or "local-development"
        )
    dispatch_payload.update(
        {
            "taskId": candidate.task_id,
            "executionDecisionId": candidate.execution_decisions[0].execution_decision_id,
            "workUnitId": candidate.work_units[0].work_unit_id,
            "idempotencyKey": candidate.work_units[0].idempotency_key,
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
    return baseline, candidate, DispatchRecord.model_validate(dispatch_payload)


def _insert_task(connect, task: ResearchTaskV12) -> None:
    payload = task.model_dump(mode="json", by_alias=True)
    with connect() as connection:
        connection.execute(
            """INSERT INTO openair_tasks (
                   task_id, owner_id, workspace_id, status, current_stage, title,
                   domain, created_at, updated_at, payload, revision
               ) VALUES (%s, 'owner', 'workspace', %s, 'solution_design', %s,
                         'research_task', NOW(), NOW(), %s::jsonb, %s)""",
            (task.task_id, task.status.value, task.task_id, json.dumps(payload), task.revision),
        )


def _prepared(baseline, candidate, dispatch, request_id: str) -> PreparedSelection:
    decision = candidate.execution_decisions[0]
    instance = candidate.research_design_versions[0].instances[0]
    resolution = ResolutionResult(
        designRef=candidate.active_research_design_ref,
        instanceRef={
            "substageInstanceId": instance.substage_instance_id,
            "instanceRevision": instance.instance_revision,
        },
        registrySnapshotRef=candidate.registry_snapshot_ref,
        candidateEvaluations=decision.candidate_evaluations,
        preferredCandidateEvaluationRefs=[],
    )
    equivalence_hash = "sha256:" + sha256(b"candidate-a,candidate-b").hexdigest()
    fairness = FairnessDescriptor(
        policy_version="equivalent_candidate_fairness.v1",
        owner_id="owner",
        workspace_id="workspace",
        registry_snapshot_content_hash=candidate.registry_snapshot_ref.content_hash,
        substage_definition_ref=(
            f"{instance.substage_definition_id}:{instance.substage_definition_version}"
        ),
        equivalence_set_hash=equivalence_hash,
        candidate_identities=("candidate:ring-a", "candidate:ring-b"),
    )
    prepared = PreparedSelection(
        task=baseline,
        instance=instance,
        resolution=resolution,
        snapshot_envelope={},
        runtime_readiness={},
        selection_request_id=request_id,
        selection_command_hash="sha256:" + sha256(
            f"command:{request_id}".encode()
        ).hexdigest(),
        selection_payload_hash="sha256:" + sha256(request_id.encode()).hexdigest(),
        expected_task_revision=0,
        owner_id="owner",
        workspace_id="workspace",
        final_tie_candidate_ids=fairness.candidate_identities,
        fairness=fairness,
        decided_at=decision.decided_at,
    )

    def materialize(_task, winner: str) -> ExecutionMaterialization:
        payload = candidate.model_dump(mode="json", by_alias=True)
        payload["executionDecisions"][0]["rationale"] = f"winner={winner}"
        selected_task = ResearchTaskV12.model_validate(payload)
        selected_decision = selected_task.execution_decisions[0]
        return ExecutionMaterialization(
            task=selected_task,
            decision=selected_decision,
            work_units=tuple(selected_task.work_units),
            dispatches=(dispatch,),
            selected_candidate_identity=winner,
        )

    return prepared, materialize


def _cursor_slot(connect) -> int | None:
    with connect() as connection:
        row = connection.execute(
            "SELECT next_slot FROM openair_equivalent_candidate_fairness"
        ).fetchone()
    return None if row is None else int(row["next_slot"])


def test_same_request_replays_without_cursor_advance_and_different_payload_conflicts(
    repository, postgres_environment
) -> None:
    baseline, candidate, dispatch = _selection_fixture("replay", "selection-replay")
    _insert_task(postgres_environment, baseline)
    prepared, materialize = _prepared(baseline, candidate, dispatch, "selection-replay")

    first = repository.commit_prepared_selection(prepared, materialize)
    replay = repository.commit_prepared_selection(prepared, materialize)

    assert first.replayed is False
    assert replay.replayed is True
    assert replay.decision.execution_decision_id == first.decision.execution_decision_id
    assert _cursor_slot(postgres_environment) == 1
    conflict = replace(
        prepared,
        selection_command_hash="sha256:" + sha256(b"different-command").hexdigest(),
    )
    with pytest.raises(SelectionRequestPayloadConflictError):
        repository.commit_prepared_selection(conflict, materialize)
    assert _cursor_slot(postgres_environment) == 1


def test_early_task_first_replay_after_revision_advance_is_read_only(
    repository, postgres_environment
) -> None:
    baseline, candidate, dispatch = _selection_fixture("early-replay", "selection-early-replay")
    _insert_task(postgres_environment, baseline)
    prepared, materialize = _prepared(
        baseline, candidate, dispatch, "selection-early-replay"
    )

    first = repository.commit_prepared_selection(prepared, materialize)
    with postgres_environment() as connection:
        before = {
            "revision": connection.execute(
                "SELECT revision FROM openair_tasks WHERE task_id = %s", (baseline.task_id,)
            ).fetchone()["revision"],
            "dispatches": connection.execute(
                "SELECT count(*) AS count FROM openair_dispatch"
            ).fetchone()["count"],
            "requests": connection.execute(
                "SELECT count(*) AS count FROM openair_execution_selection_request"
            ).fetchone()["count"],
            "cursor": _cursor_slot(postgres_environment),
        }

    replay = repository.lookup_selection_replay(
        task_id=baseline.task_id,
        owner_id="owner",
        workspace_id="workspace",
        selection_request_id=prepared.selection_request_id,
        selection_command_hash=prepared.selection_command_hash,
    )
    assert replay is not None
    assert replay.replayed is True
    assert replay.decision.execution_decision_id == first.decision.execution_decision_id
    with pytest.raises(SelectionRequestPayloadConflictError, match="selection_request_command_conflict"):
        repository.lookup_selection_replay(
            task_id=baseline.task_id,
            owner_id="owner",
            workspace_id="workspace",
            selection_request_id=prepared.selection_request_id,
            selection_command_hash="sha256:" + sha256(b"changed-instance").hexdigest(),
        )
    with pytest.raises(ResearchTaskNotFoundError, match="research_task_not_found"):
        repository.lookup_selection_replay(
            task_id=baseline.task_id,
            owner_id="other-owner",
            workspace_id="workspace",
            selection_request_id=prepared.selection_request_id,
            selection_command_hash=prepared.selection_command_hash,
        )
    with postgres_environment() as connection:
        after = {
            "revision": connection.execute(
                "SELECT revision FROM openair_tasks WHERE task_id = %s", (baseline.task_id,)
            ).fetchone()["revision"],
            "dispatches": connection.execute(
                "SELECT count(*) AS count FROM openair_dispatch"
            ).fetchone()["count"],
            "requests": connection.execute(
                "SELECT count(*) AS count FROM openair_execution_selection_request"
            ).fetchone()["count"],
            "cursor": _cursor_slot(postgres_environment),
        }
    assert before == after


def test_selection_migration_command_hash_constraint_survives_up_down(
    postgres_environment,
) -> None:
    migrations = Path(__file__).resolve().parents[4] / "migrations"
    up_sql = (migrations / "20260730_03_research_execution_selection.sql").read_text(
        encoding="utf-8"
    )
    down_sql = (migrations / "20260730_03_research_execution_selection_down.sql").read_text(
        encoding="utf-8"
    )
    with postgres_environment() as connection:
        constraints = connection.execute(
            """SELECT pg_get_constraintdef(oid) AS definition
               FROM pg_constraint
               WHERE conrelid = 'openair_execution_selection_request'::regclass"""
        ).fetchall()
        definitions = "\n".join(row["definition"] for row in constraints)
        assert "command_hash" in definitions
        assert "sha256:[0-9a-f]{64}" in definitions
        connection.execute(down_sql)
        assert connection.execute(
            "SELECT to_regclass('openair_execution_selection_request') AS table_name"
        ).fetchone()["table_name"] is None
        connection.execute(up_sql)
        restored = connection.execute(
            """SELECT pg_get_constraintdef(oid) AS definition
               FROM pg_constraint
               WHERE conrelid = 'openair_execution_selection_request'::regclass"""
        ).fetchall()
        assert "command_hash" in "\n".join(row["definition"] for row in restored)


def test_cas_loser_does_not_create_or_advance_cursor(repository, postgres_environment) -> None:
    baseline, candidate, dispatch = _selection_fixture("cas", "selection-cas")
    _insert_task(postgres_environment, baseline)
    with postgres_environment() as connection:
        payload = baseline.model_dump(mode="json", by_alias=True)
        payload["revision"] = 1
        connection.execute(
            "UPDATE openair_tasks SET revision = 1, payload = %s::jsonb WHERE task_id = %s",
            (json.dumps(payload), baseline.task_id),
        )
    prepared, materialize = _prepared(baseline, candidate, dispatch, "selection-cas")

    with pytest.raises(TaskRevisionConflictError):
        repository.commit_prepared_selection(prepared, materialize)

    assert _cursor_slot(postgres_environment) is None


def test_outbox_unique_failure_rolls_back_task_request_and_cursor(
    repository, postgres_environment
) -> None:
    baseline, candidate, dispatch = _selection_fixture("rollback", "selection-rollback")
    _insert_task(postgres_environment, baseline)
    with postgres_environment() as connection:
        connection.execute(
            """INSERT INTO openair_dispatch (
                   dispatch_id, task_id, execution_decision_id, work_unit_id,
                   idempotency_key, status, attempt_count, created_at, updated_at
               ) VALUES (%s, %s, 'preexisting-decision', 'preexisting-workunit',
                         'preexisting-key', 'failed', 0, NOW(), NOW())""",
            (dispatch.dispatch_id, baseline.task_id),
        )
    prepared, materialize = _prepared(baseline, candidate, dispatch, "selection-rollback")

    with pytest.raises(DispatchIdentityConflictError):
        repository.commit_prepared_selection(prepared, materialize)

    with postgres_environment() as connection:
        task_row = connection.execute(
            "SELECT revision FROM openair_tasks WHERE task_id = %s", (baseline.task_id,)
        ).fetchone()
        requests = connection.execute(
            "SELECT count(*) AS count FROM openair_execution_selection_request"
        ).fetchone()["count"]
    assert task_row["revision"] == 0
    assert requests == 0
    assert _cursor_slot(postgres_environment) is None


def test_two_tasks_share_round_robin_cursor_and_new_repository_continues_after_restart(
    repository, postgres_environment
) -> None:
    rows = []
    for suffix in ("fair-a", "fair-b", "fair-c"):
        baseline, candidate, dispatch = _selection_fixture(suffix, f"selection-{suffix}")
        _insert_task(postgres_environment, baseline)
        prepared, materialize = _prepared(
            baseline, candidate, dispatch, f"selection-{suffix}"
        )
        rows.append((prepared, materialize))
    barrier = Barrier(2)

    def commit(row):
        barrier.wait()
        return repository.commit_prepared_selection(*row)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first_two = list(pool.map(commit, rows[:2]))
    winners = {result.decision.rationale for result in first_two}

    assert winners == {"winner=candidate:ring-a", "winner=candidate:ring-b"}
    assert _cursor_slot(postgres_environment) == 2
    restarted = ResearchOrchestrationRepository(
        connection_factory=postgres_environment,
        runtime_settings=settings,
    )
    third = restarted.commit_prepared_selection(*rows[2])
    assert third.decision.rationale == "winner=candidate:ring-a"
    assert _cursor_slot(postgres_environment) == 3


def _superseded_transaction_latency_probe(
    repository, postgres_environment
) -> None:
    rows = []
    for index in range(25):
        suffix = f"perf-{index:02d}"
        request_id = f"selection-{suffix}"
        baseline, candidate, dispatch = _selection_fixture(suffix, request_id)
        _insert_task(postgres_environment, baseline)
        rows.append(_prepared(baseline, candidate, dispatch, request_id))

    samples_ms: list[float] = []
    for prepared, materialize in rows:
        started = perf_counter()
        repository.commit_prepared_selection(prepared, materialize)
        samples_ms.append((perf_counter() - started) * 1000)
    ordered = sorted(samples_ms)
    nearest_rank_p95 = ordered[23]

    print(
        "T05_TRANSACTION_SAMPLES_MS="
        + ",".join(f"{sample:.3f}" for sample in samples_ms)
    )
    print(f"T05_TRANSACTION_NEAREST_RANK_P95_MS={nearest_rank_p95:.3f}")
    assert nearest_rank_p95 < 100.0


def test_start_status_reader_separates_current_authority_and_request_receipt_without_writes(
    repository, postgres_environment
) -> None:
    request_id = "selection-start-status"
    baseline, candidate, dispatch = _selection_fixture("start-status", request_id)
    _insert_task(postgres_environment, baseline)
    prepared, materialize = _prepared(baseline, candidate, dispatch, request_id)
    committed = repository.commit_prepared_selection(prepared, materialize)
    reader = ResearchExecutionStartStatusReader(
        connection_factory=postgres_environment,
        runtime_settings=settings,
    )

    with postgres_environment() as connection:
        before = connection.execute(
            """SELECT revision, payload,
                      (SELECT count(*) FROM openair_execution_selection_request) AS ledger_count,
                      (SELECT count(*) FROM openair_dispatch) AS dispatch_count
               FROM openair_tasks WHERE task_id = %s""",
            (baseline.task_id,),
        ).fetchone()

    current = reader.read_current(
        task_id=baseline.task_id,
        owner_id="owner",
        workspace_id="workspace",
    )
    receipt = reader.read(
        task_id=baseline.task_id,
        owner_id="owner",
        workspace_id="workspace",
        selection_request_id=request_id,
    )
    missing = reader.read(
        task_id=baseline.task_id,
        owner_id="owner",
        workspace_id="workspace",
        selection_request_id="selection-not-submitted",
    )

    with postgres_environment() as connection:
        after = connection.execute(
            """SELECT revision, payload,
                      (SELECT count(*) FROM openair_execution_selection_request) AS ledger_count,
                      (SELECT count(*) FROM openair_dispatch) AS dispatch_count
               FROM openair_tasks WHERE task_id = %s""",
            (baseline.task_id,),
        ).fetchone()

    assert current.state == "committed"
    assert current.executionDecisionId == committed.decision.execution_decision_id
    assert current.taskRevision == committed.task.revision == 1
    assert receipt.state == "committed"
    assert receipt.executionDecisionId == committed.decision.execution_decision_id
    assert receipt.taskRevision is None
    assert missing.state == "not_committed"
    assert missing.taskRevision is None
    assert after == before


def test_start_status_reader_keeps_ledger_receipt_separate_from_current_authority(
    postgres_environment,
) -> None:
    baseline, candidate, _dispatch = _selection_fixture(
        "start-status-inconsistent",
        "selection-start-status-inconsistent",
    )
    _insert_task(postgres_environment, baseline)
    decision = candidate.execution_decisions[0]
    with postgres_environment() as connection:
        connection.execute(
            """INSERT INTO openair_execution_selection_request (
                   task_id, owner_id, workspace_id, selection_request_id,
                   command_hash, payload_hash, execution_decision_id
               ) VALUES (%s, 'owner', 'workspace', %s, %s, %s, %s)""",
            (
                baseline.task_id,
                decision.selection_request_id,
                "sha256:" + sha256(b"inconsistent-command").hexdigest(),
                "sha256:" + sha256(b"inconsistent-payload").hexdigest(),
                decision.execution_decision_id,
            ),
        )

    reader = ResearchExecutionStartStatusReader(
        connection_factory=postgres_environment,
        runtime_settings=settings,
    )
    receipt = reader.read(
        task_id=baseline.task_id,
        owner_id="owner",
        workspace_id="workspace",
        selection_request_id=decision.selection_request_id,
    )
    current = reader.read_current(
        task_id=baseline.task_id,
        owner_id="owner",
        workspace_id="workspace",
    )

    assert receipt.state == "committed"
    assert receipt.executionDecisionId == decision.execution_decision_id
    assert receipt.taskRevision is None
    assert current.state == "unknown"
    assert current.executionDecisionId is None
    assert current.taskRevision == baseline.revision == 0


def test_start_status_reader_keeps_owner_workspace_and_task_scope_invisible(
    postgres_environment,
) -> None:
    baseline, _candidate, _dispatch = _selection_fixture(
        "start-status-scope",
        "selection-start-status-scope",
    )
    _insert_task(postgres_environment, baseline)
    reader = ResearchExecutionStartStatusReader(
        connection_factory=postgres_environment,
        runtime_settings=settings,
    )

    for task_id, owner_id, workspace_id in (
        (baseline.task_id, "other-owner", "workspace"),
        (baseline.task_id, "owner", "other-workspace"),
        ("other-task", "owner", "workspace"),
    ):
        with pytest.raises(ResearchExecutionStartTaskNotFound):
            reader.read(
                task_id=task_id,
                owner_id=owner_id,
                workspace_id=workspace_id,
                selection_request_id="selection-start-status-scope",
            )
