"""Canonical controller-command PostgreSQL transaction gate.

Set OPENAIR_CONTROLLER_COMMAND_TEST_DATABASE_URL to an explicitly disposable
PostgreSQL test/qa database. No implicit developer database is used.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from copy import deepcopy
import os
from pathlib import Path
from threading import Barrier
from urllib.parse import urlparse

import pytest

psycopg = pytest.importorskip("psycopg")
from app.modules.controller_command_repository import ControllerCommandRepository

MIGRATIONS = Path(__file__).resolve().parents[4] / "migrations"
BASE_MIGRATION = MIGRATIONS / "20260724_01_controller_commands.sql"
UP_MIGRATION = MIGRATIONS / "20260806_02_controller_commands_canonical.sql"
RECOVERY_MIGRATION = MIGRATIONS / "20260807_02_controller_command_reconciliation.sql"
DOWN_MIGRATION = MIGRATIONS / "20260806_02_controller_commands_canonical_down.sql"
DSN_ENV = "OPENAIR_CONTROLLER_COMMAND_TEST_DATABASE_URL"


def _dsn() -> str:
    dsn = os.environ.get(DSN_ENV, "").strip()
    if not dsn:
        pytest.skip(f"{DSN_ENV} is not configured")
    database = urlparse(dsn).path.lstrip("/").lower()
    if "test" not in database and "qa" not in database:
        pytest.fail(f"{DSN_ENV} must identify an isolated test or qa database")
    return dsn


def _executor(executor_id: str) -> dict:
    return {"executorType": "agent", "executorId": executor_id, "definitionVersion": 1}


def _assignment(assignment_id: str, *, role: str = "primary", executor_id: str = "agent-primary") -> dict:
    return {
        "assignmentId": assignment_id,
        "role": role,
        "executorRef": _executor(executor_id),
    }


def _decision(decision_id: str, assignment: dict, *, sequence: int) -> dict:
    return {
        "executionDecisionId": decision_id,
        "decisionSequence": sequence,
        "taskId": "task-a",
        "status": "dispatched",
        "revisionReasonCode": None,
        "assignments": [assignment],
        "resultAssemblyPolicy": {
            "mode": "primary_output",
            "assemblerAssignmentId": None,
            "finalArtifactRequirements": [],
        },
    }


def _work_unit(
    work_unit_id: str,
    decision_id: str,
    assignment: dict,
    instruction: str,
    *,
    status: str = "queued",
    dispatched_at: str = "2026-08-06T00:01:00Z",
) -> dict:
    return {
        "workUnitId": work_unit_id,
        "taskId": "task-a",
        "executionDecisionId": decision_id,
        "assignmentId": assignment["assignmentId"],
        "executorRef": deepcopy(assignment["executorRef"]),
        "sourceMode": "live",
        "dispatchable": True,
        "status": status,
        "dispatchedAt": dispatched_at,
        "instruction": instruction,
    }


def _task_payload(task_id: str, *, status: str = "running") -> dict:
    existing_assignment = _assignment("assignment-existing")
    return {
        "taskId": task_id,
        "ownerId": "owner-a",
        "workspaceId": "workspace-a",
        "status": status,
        "revision": 5,
        "researchDesignVersions": [],
        "executionDecisions": [_decision("decision-old", existing_assignment, sequence=1)],
        "workUnits": [
            _work_unit(
                "wu-existing", "decision-old", existing_assignment, "旧输入",
                status="running", dispatched_at="2026-08-06T00:00:00Z",
            )
        ],
        "capabilityCalls": [],
        "agentMessages": [],
    }


def _append_unit(
    payload: dict,
    *,
    work_unit_id: str,
    decision_id: str,
    assignment_id: str,
    sequence: int,
    role: str = "primary",
    executor_id: str = "agent-primary",
    instruction: str = "形成下一项结果",
) -> None:
    assignment = _assignment(assignment_id, role=role, executor_id=executor_id)
    payload["executionDecisions"].append(_decision(decision_id, assignment, sequence=sequence))
    payload["workUnits"].append(
        _work_unit(work_unit_id, decision_id, assignment, instruction)
    )


@pytest.fixture()
def database():
    psycopg = pytest.importorskip("psycopg")
    from psycopg.rows import dict_row

    dsn = _dsn()
    with psycopg.connect(dsn, autocommit=True, row_factory=dict_row) as connection:
        connection.execute("DROP TABLE IF EXISTS openair_controller_commands CASCADE")
        connection.execute("DROP TABLE IF EXISTS openair_execution_work_units CASCADE")
        connection.execute("DROP TABLE IF EXISTS openair_execution_runs CASCADE")
        connection.execute("DROP TABLE IF EXISTS openair_tasks CASCADE")
        connection.execute(
            """CREATE TABLE openair_tasks (
               task_id TEXT PRIMARY KEY,
               owner_id TEXT NOT NULL,
               workspace_id TEXT NOT NULL,
               status TEXT NOT NULL,
               revision BIGINT NOT NULL,
               payload JSONB NOT NULL,
               updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW())"""
        )
        connection.execute(
            """CREATE TABLE openair_execution_runs (
               run_id TEXT PRIMARY KEY,
               task_id TEXT NOT NULL REFERENCES openair_tasks(task_id) ON DELETE CASCADE)"""
        )
        connection.execute(
            """CREATE TABLE openair_execution_work_units (
               work_unit_id TEXT PRIMARY KEY,
               run_id TEXT NOT NULL REFERENCES openair_execution_runs(run_id) ON DELETE CASCADE)"""
        )
        connection.execute(BASE_MIGRATION.read_text(encoding="utf-8"))
        connection.execute(UP_MIGRATION.read_text(encoding="utf-8"))
        connection.execute(RECOVERY_MIGRATION.read_text(encoding="utf-8"))
    yield dsn
    with psycopg.connect(dsn, autocommit=True) as connection:
        connection.execute(DOWN_MIGRATION.read_text(encoding="utf-8"))
        connection.execute("DROP TABLE IF EXISTS openair_controller_commands CASCADE")
        connection.execute("DROP TABLE IF EXISTS openair_execution_work_units CASCADE")
        connection.execute("DROP TABLE IF EXISTS openair_execution_runs CASCADE")
        connection.execute("DROP TABLE IF EXISTS openair_tasks CASCADE")


def _insert_task_and_command(dsn: str, *, command_id: str = "command-a") -> None:
    import psycopg

    payload = _task_payload("task-a")
    with psycopg.connect(dsn, autocommit=True) as connection:
        connection.execute(
            """INSERT INTO openair_tasks(task_id, owner_id, workspace_id, status, revision, payload)
               VALUES ('task-a','owner-a','workspace-a','running',5,%s::jsonb)""",
            (psycopg.types.json.Jsonb(payload),),
        )
        connection.execute(
            """INSERT INTO openair_controller_commands(
                   command_id, task_id, owner_id, workspace_id, kind, payload, state,
                   idempotency_key, request_fingerprint, user_message,
                   submitted_task_revision, lifecycle_kind, target_boundary)
               VALUES (%s,'task-a','owner-a','workspace-a','supplement_input',%s::jsonb,'received',
                   %s,'fingerprint','',4,'canonical_v1_2',%s::jsonb)""",
            (
                command_id,
                psycopg.types.json.Jsonb({"text": "只使用新增边界。"}),
                f"key-{command_id}",
                psycopg.types.json.Jsonb({
                    "knownWorkUnitIds": ["wu-existing"],
                    "submittedAfterExecutionDecisionId": "decision-old",
                }),
            ),
        )


def test_real_postgres_applies_once_and_persists_frozen_input_atomically(database) -> None:
    import psycopg
    from psycopg.rows import dict_row

    dsn = database
    _insert_task_and_command(dsn)
    with psycopg.connect(dsn, row_factory=dict_row) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT payload FROM openair_tasks WHERE task_id='task-a' FOR UPDATE")
            candidate = deepcopy(cursor.fetchone()["payload"])
            candidate["revision"] = 6
            _append_unit(
                candidate, work_unit_id="wu-next", decision_id="decision-next",
                assignment_id="assignment-next", sequence=2,
            )
            applied = ControllerCommandRepository.apply_canonical_at_materialization(
                cursor, task_payload=candidate, owner_id="owner-a", workspace_id="workspace-a"
            )
            cursor.execute(
                "UPDATE openair_tasks SET payload=%s::jsonb, revision=6 WHERE task_id='task-a'",
                (psycopg.types.json.Jsonb(applied),),
            )

    with psycopg.connect(dsn, row_factory=dict_row) as connection:
        command = connection.execute(
            "SELECT * FROM openair_controller_commands WHERE command_id='command-a'"
        ).fetchone()
        task = connection.execute("SELECT payload FROM openair_tasks WHERE task_id='task-a'").fetchone()
        target = next(row for row in task["payload"]["workUnits"] if row["workUnitId"] == "wu-next")
        assert command["state"] == "received"
        assert command["applied_execution_decision_id"] is None
        assert command["applied_canonical_work_unit_id"] is None
        assert command["target_boundary"]["canonicalExecutionDecisionId"] == "decision-next"
        assert command["target_boundary"]["canonicalWorkUnitId"] == "wu-next"
        assert command["applied_work_unit_id"] is None
        assert target["instruction"].count("只使用新增边界。") == 1
        assert "[controllerCommandId=command-a]" in target["instruction"]

    with psycopg.connect(dsn, row_factory=dict_row) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT payload FROM openair_tasks WHERE task_id='task-a' FOR UPDATE")
            current = cursor.fetchone()["payload"]
            repeated = ControllerCommandRepository.apply_canonical_at_materialization(
                cursor, task_payload=current, owner_id="owner-a", workspace_id="workspace-a"
            )
            cursor.execute(
                "UPDATE openair_tasks SET payload=%s::jsonb WHERE task_id='task-a'",
                (psycopg.types.json.Jsonb(repeated),),
            )
    with psycopg.connect(dsn, row_factory=dict_row) as connection:
        payload = connection.execute("SELECT payload FROM openair_tasks WHERE task_id='task-a'").fetchone()["payload"]
        target = next(row for row in payload["workUnits"] if row["workUnitId"] == "wu-next")
        assert target["instruction"].count("只使用新增边界。") == 1


def test_real_postgres_rollback_leaves_command_received_and_input_unmodified(database) -> None:
    import psycopg
    from psycopg.rows import dict_row

    dsn = database
    _insert_task_and_command(dsn, command_id="command-rollback")
    with pytest.raises(RuntimeError, match="rollback-injected"):
        with psycopg.connect(dsn, row_factory=dict_row) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT payload FROM openair_tasks WHERE task_id='task-a' FOR UPDATE")
                candidate = deepcopy(cursor.fetchone()["payload"])
                candidate["revision"] = 6
                _append_unit(
                    candidate, work_unit_id="wu-rollback", decision_id="decision-rollback",
                    assignment_id="assignment-rollback", sequence=2, instruction="回滚前输入",
                )
                ControllerCommandRepository.apply_canonical_at_materialization(
                    cursor, task_payload=candidate, owner_id="owner-a", workspace_id="workspace-a"
                )
                raise RuntimeError("rollback-injected")

    with psycopg.connect(dsn, row_factory=dict_row) as connection:
        command = connection.execute(
            "SELECT state, applied_canonical_work_unit_id FROM openair_controller_commands "
            "WHERE command_id='command-rollback'"
        ).fetchone()
        task = connection.execute("SELECT payload FROM openair_tasks WHERE task_id='task-a'").fetchone()
        assert command == {"state": "received", "applied_canonical_work_unit_id": None}
        assert all(row["workUnitId"] != "wu-rollback" for row in task["payload"]["workUnits"])


def test_real_postgres_reviewer_waits_then_primary_receives_same_command(database) -> None:
    import psycopg
    from psycopg.rows import dict_row

    dsn = database
    _insert_task_and_command(dsn, command_id="command-review-wait")
    with psycopg.connect(dsn, row_factory=dict_row) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT payload FROM openair_tasks WHERE task_id='task-a' FOR UPDATE")
            reviewer_only = deepcopy(cursor.fetchone()["payload"])
            reviewer_only["revision"] = 6
            _append_unit(
                reviewer_only, work_unit_id="wu-review", decision_id="decision-review",
                assignment_id="assignment-review", sequence=2, role="reviewer",
                executor_id="review-agent", instruction="独立复核",
            )
            waiting = ControllerCommandRepository.apply_canonical_at_materialization(
                cursor, task_payload=reviewer_only, owner_id="owner-a", workspace_id="workspace-a"
            )
            cursor.execute(
                "UPDATE openair_tasks SET payload=%s::jsonb, revision=6 WHERE task_id='task-a'",
                (psycopg.types.json.Jsonb(waiting),),
            )

    with psycopg.connect(dsn, row_factory=dict_row) as connection:
        command = connection.execute(
            "SELECT state FROM openair_controller_commands WHERE command_id='command-review-wait'"
        ).fetchone()
        assert command["state"] == "received"

    with psycopg.connect(dsn, row_factory=dict_row) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT payload FROM openair_tasks WHERE task_id='task-a' FOR UPDATE")
            candidate = deepcopy(cursor.fetchone()["payload"])
            candidate["revision"] = 7
            _append_unit(
                candidate, work_unit_id="wu-primary", decision_id="decision-primary",
                assignment_id="assignment-primary", sequence=3, executor_id="research-agent",
            )
            applied = ControllerCommandRepository.apply_canonical_at_materialization(
                cursor, task_payload=candidate, owner_id="owner-a", workspace_id="workspace-a"
            )
            cursor.execute(
                "UPDATE openair_tasks SET payload=%s::jsonb, revision=7 WHERE task_id='task-a'",
                (psycopg.types.json.Jsonb(applied),),
            )

    with psycopg.connect(dsn, row_factory=dict_row) as connection:
        command = connection.execute(
            "SELECT state, applied_canonical_work_unit_id FROM openair_controller_commands "
            "WHERE command_id='command-review-wait'"
        ).fetchone()
        payload = connection.execute(
            "SELECT payload FROM openair_tasks WHERE task_id='task-a'"
        ).fetchone()["payload"]
        reviewer = next(row for row in payload["workUnits"] if row["workUnitId"] == "wu-review")
        primary = next(row for row in payload["workUnits"] if row["workUnitId"] == "wu-primary")
        assert command == {"state": "received", "applied_canonical_work_unit_id": None}
        assert "只使用新增边界。" not in reviewer["instruction"]
        assert "只使用新增边界。" in primary["instruction"]


def test_real_postgres_command_isolation_binds_authorization_and_applied_authority(database) -> None:
    import psycopg
    from psycopg.rows import dict_row

    _insert_task_and_command(database, command_id="command-a")
    with psycopg.connect(database, row_factory=dict_row) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT payload FROM openair_tasks WHERE task_id='task-a' FOR UPDATE")
            candidate = deepcopy(cursor.fetchone()["payload"])
            candidate["revision"] = 6
            _append_unit(candidate, work_unit_id="wu-a", decision_id="decision-a", assignment_id="assignment-a", sequence=2)
            _append_unit(candidate, work_unit_id="wu-b", decision_id="decision-b", assignment_id="assignment-b", sequence=3)
            cursor.execute(
                """INSERT INTO openair_controller_commands(
                       command_id, task_id, owner_id, workspace_id, kind, payload, state,
                       idempotency_key, request_fingerprint, user_message,
                       submitted_task_revision, lifecycle_kind, target_boundary)
                   VALUES ('command-b','task-a','owner-a','workspace-a','supplement_input',%s::jsonb,'received',
                       'key-command-b','fingerprint','',4,'canonical_v1_2',%s::jsonb)""",
                (
                    psycopg.types.json.Jsonb({"text": "B only"}),
                    psycopg.types.json.Jsonb({"knownWorkUnitIds": ["wu-existing", "wu-a"], "submittedAfterExecutionDecisionId": "decision-old"}),
                ),
            )
            materialized = ControllerCommandRepository.apply_canonical_at_materialization(
                cursor, task_payload=candidate, owner_id="owner-a", workspace_id="workspace-a"
            )
            authorized_payload, command_ids = ControllerCommandRepository.prepare_canonical_at_authorization(
                cursor, task_payload=materialized, work_unit_id="wu-a", owner_id="owner-a", workspace_id="workspace-a"
            )
            assert command_ids == ["command-a"]
            assert "[controllerCommandId=command-a]" in authorized_payload["instruction"]
            assert "[controllerCommandId=command-b]" not in authorized_payload["instruction"]
            ControllerCommandRepository.mark_canonical_applied_at_action_start(
                cursor, task_id="task-a", owner_id="owner-a", workspace_id="workspace-a",
                execution_decision_id="decision-a", work_unit_id="wu-a", command_ids=command_ids,
            )

    with psycopg.connect(database, row_factory=dict_row) as connection:
        rows = connection.execute(
            "SELECT command_id, target_boundary, state, applied_canonical_work_unit_id "
            "FROM openair_controller_commands ORDER BY command_id"
        ).fetchall()
    assert rows == [
        {"command_id": "command-a", "target_boundary": {"knownWorkUnitIds": ["wu-existing"], "submittedAfterExecutionDecisionId": "decision-old", "canonicalWorkUnitId": "wu-a", "canonicalExecutionDecisionId": "decision-a"}, "state": "applied", "applied_canonical_work_unit_id": "wu-a"},
        {"command_id": "command-b", "target_boundary": {"knownWorkUnitIds": ["wu-existing", "wu-a"], "submittedAfterExecutionDecisionId": "decision-old", "canonicalWorkUnitId": "wu-b", "canonicalExecutionDecisionId": "decision-b"}, "state": "received", "applied_canonical_work_unit_id": None},
    ]


def test_real_postgres_command_scope_does_not_move_between_tasks(database) -> None:
    import psycopg
    from psycopg.rows import dict_row

    _insert_task_and_command(database, command_id="command-task-a")

    task_b = _task_payload("task-a")
    task_b["revision"] = 6
    _append_unit(
        task_b,
        work_unit_id="wu-task-b-next",
        decision_id="decision-task-b-next",
        assignment_id="assignment-task-b-next",
        sequence=2,
    )
    task_b["taskId"] = "task-b"
    for decision in task_b["executionDecisions"]:
        decision["taskId"] = "task-b"
    for work_unit in task_b["workUnits"]:
        work_unit["taskId"] = "task-b"

    with psycopg.connect(database, autocommit=True) as connection:
        connection.execute(
            """INSERT INTO openair_tasks(task_id, owner_id, workspace_id, status, revision, payload)
               VALUES ('task-b','owner-a','workspace-a','running',6,%s::jsonb)""",
            (psycopg.types.json.Jsonb(task_b),),
        )

    with psycopg.connect(database, row_factory=dict_row) as connection:
        with connection.cursor() as cursor:
            materialized_b = ControllerCommandRepository.apply_canonical_at_materialization(
                cursor, task_payload=task_b, owner_id="owner-a", workspace_id="workspace-a"
            )
            command_after_b = cursor.execute(
                "SELECT state, target_boundary FROM openair_controller_commands "
                "WHERE command_id='command-task-a'"
            ).fetchone()

            task_a = deepcopy(cursor.execute(
                "SELECT payload FROM openair_tasks WHERE task_id='task-a' FOR UPDATE"
            ).fetchone()["payload"])
            task_a["revision"] = 6
            _append_unit(
                task_a,
                work_unit_id="wu-task-a-next",
                decision_id="decision-task-a-next",
                assignment_id="assignment-task-a-next",
                sequence=2,
            )
            materialized_a = ControllerCommandRepository.apply_canonical_at_materialization(
                cursor, task_payload=task_a, owner_id="owner-a", workspace_id="workspace-a"
            )

    target_b = next(row for row in materialized_b["workUnits"] if row["workUnitId"] == "wu-task-b-next")
    target_a = next(row for row in materialized_a["workUnits"] if row["workUnitId"] == "wu-task-a-next")
    assert command_after_b["state"] == "received"
    assert "canonicalWorkUnitId" not in command_after_b["target_boundary"]
    assert "[controllerCommandId=command-task-a]" not in target_b["instruction"]
    assert "只使用新增边界。" not in target_b["instruction"]
    assert "[controllerCommandId=command-task-a]" in target_a["instruction"]
    assert "只使用新增边界。" in target_a["instruction"]

    with psycopg.connect(database, row_factory=dict_row) as connection:
        final_command = connection.execute(
            "SELECT state, target_boundary FROM openair_controller_commands "
            "WHERE command_id='command-task-a'"
        ).fetchone()
    assert final_command["state"] == "received"
    assert final_command["target_boundary"]["canonicalWorkUnitId"] == "wu-task-a-next"
    assert final_command["target_boundary"]["canonicalExecutionDecisionId"] == "decision-task-a-next"



def test_real_postgres_two_consumers_apply_exactly_once(database) -> None:
    import psycopg
    from psycopg.rows import dict_row

    dsn = database
    _insert_task_and_command(dsn, command_id="command-concurrent")
    barrier = Barrier(2)

    def consume() -> None:
        barrier.wait()
        with psycopg.connect(dsn, row_factory=dict_row) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT payload FROM openair_tasks WHERE task_id='task-a' FOR UPDATE")
                candidate = deepcopy(cursor.fetchone()["payload"])
                if not any(row["workUnitId"] == "wu-concurrent" for row in candidate["workUnits"]):
                    candidate["revision"] = 6
                    _append_unit(
                        candidate, work_unit_id="wu-concurrent", decision_id="decision-concurrent",
                        assignment_id="assignment-concurrent", sequence=2,
                    )
                applied = ControllerCommandRepository.apply_canonical_at_materialization(
                    cursor, task_payload=candidate, owner_id="owner-a", workspace_id="workspace-a"
                )
                cursor.execute(
                    "UPDATE openair_tasks SET payload=%s::jsonb WHERE task_id='task-a'",
                    (psycopg.types.json.Jsonb(applied),),
                )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(consume) for _ in range(2)]
        for future in futures:
            future.result(timeout=15)

    with psycopg.connect(dsn, row_factory=dict_row) as connection:
        command = connection.execute(
            "SELECT state, applied_canonical_work_unit_id FROM openair_controller_commands "
            "WHERE command_id='command-concurrent'"
        ).fetchone()
        payload = connection.execute(
            "SELECT payload FROM openair_tasks WHERE task_id='task-a'"
        ).fetchone()["payload"]
        target = next(row for row in payload["workUnits"] if row["workUnitId"] == "wu-concurrent")
        assert command == {"state": "received", "applied_canonical_work_unit_id": None}
        assert target["instruction"].count("只使用新增边界。") == 1
        assert target["instruction"].count("[controllerCommandId=command-concurrent]") == 1


def test_real_postgres_received_progression_claim_is_leased_once_and_recovers_after_expiry(database) -> None:
    import psycopg

    dsn = database
    _insert_task_and_command(dsn, command_id="command-lease-recovery")
    repository = ControllerCommandRepository()

    @contextmanager
    def connect():
        with psycopg.connect(dsn, row_factory=psycopg.rows.dict_row) as connection:
            yield connection

    repository._connect = connect  # type: ignore[method-assign]

    first = repository.claim_received_for_progression(
        task_id="task-a",
        command_id="command-lease-recovery",
        owner_id="owner-a",
        workspace_id="workspace-a",
        lease_owner="route-a",
        lease_seconds=60,
    )
    assert first is not None
    assert repository.claim_received_for_progression(
        task_id="task-a",
        command_id="command-lease-recovery",
        owner_id="owner-a",
        workspace_id="workspace-a",
        lease_owner="route-b",
        lease_seconds=60,
    ) is None

    with psycopg.connect(dsn, autocommit=True) as connection:
        connection.execute(
            "UPDATE openair_controller_commands "
            "SET progression_lease_expires_at = NOW() - INTERVAL '1 second' "
            "WHERE command_id = 'command-lease-recovery'"
        )

    recovered = repository.claim_received_for_progression(
        task_id="task-a",
        command_id="command-lease-recovery",
        owner_id="owner-a",
        workspace_id="workspace-a",
        lease_owner="route-b",
        lease_seconds=60,
    )
    assert recovered is not None

def test_real_postgres_payload_revision_fence_keeps_command_unbound(database) -> None:
    import psycopg
    from psycopg.rows import dict_row

    _insert_task_and_command(database, command_id="command-revision-fence")
    with psycopg.connect(database, row_factory=dict_row) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT payload FROM openair_tasks WHERE task_id='task-a' FOR UPDATE")
            candidate = deepcopy(cursor.fetchone()["payload"])
            candidate["revision"] = 4
            _append_unit(
                candidate, work_unit_id="wu-fenced", decision_id="decision-fenced",
                assignment_id="assignment-fenced", sequence=2,
            )
            unchanged = ControllerCommandRepository.apply_canonical_at_materialization(
                cursor, task_payload=candidate, owner_id="owner-a", workspace_id="workspace-a"
            )

    target = next(row for row in unchanged["workUnits"] if row["workUnitId"] == "wu-fenced")
    assert "只使用新增边界。" not in target["instruction"]
    with psycopg.connect(database, row_factory=dict_row) as connection:
        command = connection.execute(
            "SELECT state, target_boundary FROM openair_controller_commands "
            "WHERE command_id='command-revision-fence'"
        ).fetchone()
    assert command["state"] == "received"
    assert "canonicalWorkUnitId" not in command["target_boundary"]


def test_real_postgres_multiple_supplements_preserve_repository_order(database) -> None:
    import psycopg
    from psycopg.rows import dict_row

    _insert_task_and_command(database, command_id="command-a")
    boundary = {"knownWorkUnitIds": ["wu-existing"], "submittedAfterExecutionDecisionId": "decision-old"}
    with psycopg.connect(database, autocommit=True) as connection:
        connection.execute(
            "UPDATE openair_controller_commands SET created_at='2026-08-06T00:00:02Z' "
            "WHERE command_id='command-a'"
        )
        for command_id, text, revision, created_at in [
            ("command-b", "revision-three", 3, "2026-08-06T00:00:03Z"),
            ("command-c", "revision-four-earlier", 4, "2026-08-06T00:00:01Z"),
        ]:
            connection.execute(
                """INSERT INTO openair_controller_commands(
                       command_id, task_id, owner_id, workspace_id, kind, payload, state,
                       idempotency_key, request_fingerprint, user_message,
                       submitted_task_revision, lifecycle_kind, target_boundary, created_at)
                   VALUES (%s,'task-a','owner-a','workspace-a','supplement_input',%s::jsonb,'received',
                       %s,'fingerprint','',%s,'canonical_v1_2',%s::jsonb,%s)""",
                (
                    command_id,
                    psycopg.types.json.Jsonb({"text": text}),
                    f"key-{command_id}",
                    revision,
                    psycopg.types.json.Jsonb(boundary),
                    created_at,
                ),
            )
        connection.execute(
            "UPDATE openair_controller_commands SET payload=%s::jsonb WHERE command_id='command-a'",
            (psycopg.types.json.Jsonb({"text": "revision-four-later"}),),
        )

    with psycopg.connect(database, row_factory=dict_row) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT payload FROM openair_tasks WHERE task_id='task-a' FOR UPDATE")
            candidate = deepcopy(cursor.fetchone()["payload"])
            candidate["revision"] = 6
            _append_unit(
                candidate, work_unit_id="wu-ordered", decision_id="decision-ordered",
                assignment_id="assignment-ordered", sequence=2,
            )
            materialized = ControllerCommandRepository.apply_canonical_at_materialization(
                cursor, task_payload=candidate, owner_id="owner-a", workspace_id="workspace-a"
            )

    target = next(row for row in materialized["workUnits"] if row["workUnitId"] == "wu-ordered")
    instruction = target["instruction"]
    assert instruction.index("command-b") < instruction.index("command-c") < instruction.index("command-a")


def test_real_postgres_terminal_task_rejects_received_command(database) -> None:
    import psycopg
    from psycopg.rows import dict_row

    _insert_task_and_command(database, command_id="command-terminal")
    with psycopg.connect(database, row_factory=dict_row) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT payload FROM openair_tasks WHERE task_id='task-a' FOR UPDATE")
            candidate = deepcopy(cursor.fetchone()["payload"])
            candidate["status"] = "failed"
            ControllerCommandRepository.apply_canonical_at_materialization(
                cursor, task_payload=candidate, owner_id="owner-a", workspace_id="workspace-a"
            )

    with psycopg.connect(database, row_factory=dict_row) as connection:
        command = connection.execute(
            "SELECT state, terminal_reason_code FROM openair_controller_commands "
            "WHERE command_id='command-terminal'"
        ).fetchone()
    assert command == {
        "state": "rejected",
        "terminal_reason_code": "task_terminal_before_application",
    }
