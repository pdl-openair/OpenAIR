"""Decisive PostgreSQL regressions for system-owned unknown recovery obligations."""

from __future__ import annotations

import pytest
from app.execution.research_dispatch_provider import DispatchSubmissionQueryResult
from app.modules.research_dispatch_service import ResearchDispatchService
from app.modules.research_external_recovery_service import ExternalRecoveryService
from app.modules.research_orchestration_repository import dispatch_capability_call_id
from tests.support.research_dispatch_transactions import (
    _authorize,
    _dispatch_row,
    _due_dispatch_ids,
    _integrity_blocker,
    _namespaced_dispatch_mutation_connection,
    _seed,
    _start,
    _task_row,
)
from tests.support.research_dispatch_transactions import clean_tables as clean_tables
from tests.support.research_dispatch_transactions import (
    postgres_environment as postgres_environment,
)
from tests.support.research_dispatch_transactions import repository as repository


def test_started_missing_work_unit_poison_settles_unknown_without_replay(
    repository, postgres_environment, monkeypatch: pytest.MonkeyPatch
) -> None:
    task, dispatch = _seed(
        repository, postgres_environment, "started_missing_work_unit_poison"
    )
    authorized = _authorize(repository, task.task_id)
    started = _start(repository, authorized)
    provider_submit_calls = 1
    with _namespaced_dispatch_mutation_connection(postgres_environment) as connection:
        connection.execute(
            """UPDATE openair_tasks AS task
                  SET payload = jsonb_set(
                      jsonb_set(
                          jsonb_set(
                              task.payload,
                              '{workUnits}',
                              COALESCE(
                                  (SELECT jsonb_agg(item)
                                     FROM jsonb_array_elements(task.payload->'workUnits') AS item
                                    WHERE item->>'workUnitId' <> %(work_unit_id)s),
                                  '[]'::jsonb
                              )
                          ),
                          '{capabilityCalls}',
                          COALESCE(
                              (SELECT jsonb_agg(item)
                                 FROM jsonb_array_elements(task.payload->'capabilityCalls') AS item
                                WHERE item->>'workUnitId' <> %(work_unit_id)s),
                              '[]'::jsonb
                          )
                      ),
                      '{substageRuntimeStates}',
                      COALESCE(
                          (SELECT jsonb_agg(
                                      jsonb_set(
                                          state,
                                          '{workUnitIds}',
                                          COALESCE(
                                              (SELECT jsonb_agg(to_jsonb(work_unit_id))
                                                 FROM jsonb_array_elements_text(
                                                     state->'workUnitIds'
                                                 ) AS work_unit_id
                                                WHERE work_unit_id <> %(work_unit_id)s),
                                              '[]'::jsonb
                                          )
                                      )
                                  )
                             FROM jsonb_array_elements(
                                 task.payload->'substageRuntimeStates'
                             ) AS state),
                          '[]'::jsonb
                      )
                  )
                WHERE task_id = %(task_id)s""",
            {"work_unit_id": started.work_unit_id, "task_id": task.task_id},
        )
        connection.execute(
            """UPDATE openair_dispatch
                  SET lease_expires_at = clock_timestamp() - interval '1 second'
                WHERE dispatch_id = %s""",
            (dispatch.dispatch_id,),
        )
        current_revision = int(
            connection.execute(
                "SELECT revision FROM openair_tasks WHERE task_id = %s",
                (task.task_id,),
            ).fetchone()["revision"]
        )

    assert repository.record_dispatch_resolution_failure(
        dispatch_id=started.dispatch_id,
        task_id=started.task_id,
        execution_decision_id=started.execution_decision_id,
        work_unit_id=started.work_unit_id,
        idempotency_key=started.idempotency_key,
        owner_id="owner",
        workspace_id="workspace",
        expected_task_revision=current_revision,
        error_code="dispatch_contract_mismatch",
        retryable=False,
        max_attempts=2,
        retry_delay_seconds=1,
    ) == "submission_unknown"

    row = _dispatch_row(postgres_environment, dispatch.dispatch_id)
    assert row["status"] == "submission_unknown"
    assert row["last_error_code"] == "dispatch_work_unit_missing_after_action_started"
    assert row["external_action_started_at"] is not None
    assert row["lease_owner"] is None
    assert row["lease_expires_at"] is None
    assert row["next_attempt_at"] is None
    blocker = _integrity_blocker(
        _task_row(postgres_environment, task.task_id),
        dispatch.dispatch_id,
    )
    assert blocker["reasonCode"] == "dispatch_work_unit_missing_after_action_started"
    assert blocker["status"] == "blocked_recoverable"
    assert blocker["allowedActions"] == []

    with postgres_environment() as connection:
        external_execution = connection.execute(
            """SELECT task_id, execution_decision_id, work_unit_id,
                      capability_call_id, provider_idempotency_key,
                      external_state, failure_code
                 FROM openair_external_execution WHERE dispatch_id = %s""",
            (dispatch.dispatch_id,),
        ).fetchone()
        evidence = connection.execute(
            """SELECT task_id, execution_decision_id, work_unit_id,
                      capability_call_id, normalized_finish_reason
                 FROM openair_external_evidence WHERE dispatch_id = %s""",
            (dispatch.dispatch_id,),
        ).fetchone()
        continuation = connection.execute(
            """SELECT status FROM openair_dispatch_continuation
                WHERE dispatch_id = %s""",
            (dispatch.dispatch_id,),
        ).fetchone()
        dispatch_count = connection.execute(
            "SELECT count(*) AS count FROM openair_dispatch WHERE task_id = %s",
            (task.task_id,),
        ).fetchone()["count"]
    assert external_execution == {
        "task_id": task.task_id,
        "execution_decision_id": started.execution_decision_id,
        "work_unit_id": started.work_unit_id,
        "capability_call_id": dispatch_capability_call_id(started),
        "provider_idempotency_key": started.idempotency_key,
        "external_state": "unknown",
        "failure_code": "dispatch_work_unit_missing_after_action_started",
    }
    assert evidence == {
        "task_id": task.task_id,
        "execution_decision_id": started.execution_decision_id,
        "work_unit_id": started.work_unit_id,
        "capability_call_id": dispatch_capability_call_id(started),
        "normalized_finish_reason": "dispatch_work_unit_missing_after_action_started",
    }
    assert continuation == {"status": "pending"}
    assert _task_row(postgres_environment, task.task_id)["status"] not in {
        "completed",
        "failed",
        "cancelled",
    }

    class PreparedQueryProvider:
        query_capable = True
        submit_calls = 0

        def __init__(self) -> None:
            self.query_calls: list[tuple[str, str | None]] = []

        def query_external_result(self, *, idempotency_key, remote_run_id):
            self.query_calls.append((idempotency_key, remote_run_id))
            return DispatchSubmissionQueryResult(
                status="running",
                remote_run_id="remote-original",
            )

    class QueryProvider:
        def __init__(self, prepared: PreparedQueryProvider) -> None:
            self.prepared = prepared

        def preflight_query(self):
            return self.prepared

    prepared = PreparedQueryProvider()
    recovery = ExternalRecoveryService(
        repository=repository,
        provider=QueryProvider(prepared),
    )
    service = ResearchDispatchService(repository, external_recovery_service=recovery)
    monkeypatch.setattr(
        service,
        "_release_ready_assignments_after_result",
        lambda fence, **_kwargs: service._no_continuation_proof(
            fence,
            "missing_work_unit_has_no_release_target",
        ),
    )
    assert service.process_next_dispatch_continuation(
        lease_owner="continuation-missing-work-unit",
        lease_seconds=30,
    ) is True
    assert prepared.query_calls == [(started.idempotency_key, None)]
    assert prepared.submit_calls == 0
    with postgres_environment() as connection:
        retained = connection.execute(
            """SELECT status FROM openair_dispatch_continuation
                WHERE dispatch_id = %s""",
            (dispatch.dispatch_id,),
        ).fetchone()
    assert retained == {"status": "pending"}

    assert dispatch.dispatch_id not in _due_dispatch_ids(postgres_environment)
    assert dispatch.dispatch_id not in _due_dispatch_ids(postgres_environment)
    assert repository.claim_authorized_recovery(
        lease_owner="worker-recovery-after-poison",
        lease_seconds=60,
        task_id=task.task_id,
        owner_id="owner",
        workspace_id="workspace",
    ) == []
    assert repository.record_dispatch_resolution_failure(
        dispatch_id=started.dispatch_id,
        task_id=started.task_id,
        execution_decision_id=started.execution_decision_id,
        work_unit_id=started.work_unit_id,
        idempotency_key=started.idempotency_key,
        owner_id="owner",
        workspace_id="workspace",
        expected_task_revision=int(_task_row(postgres_environment, task.task_id)["revision"]),
        error_code="dispatch_contract_mismatch",
        retryable=False,
        max_attempts=2,
        retry_delay_seconds=1,
    ) == "stale"
    assert dispatch_count == 1
    assert provider_submit_calls == 1


def test_started_invalid_task_payload_keeps_durable_unknown_recovery_owner(
    repository, postgres_environment
) -> None:
    task, dispatch = _seed(
        repository, postgres_environment, "started_invalid_task_payload"
    )
    started = _start(repository, _authorize(repository, task.task_id))
    provider_submit_calls = 1
    with _namespaced_dispatch_mutation_connection(postgres_environment) as connection:
        connection.execute(
            """UPDATE openair_tasks SET payload = payload - 'taskId'
                WHERE task_id = %s""",
            (task.task_id,),
        )
        connection.execute(
            """UPDATE openair_dispatch
                  SET lease_expires_at = clock_timestamp() - interval '1 second'
                WHERE dispatch_id = %s""",
            (dispatch.dispatch_id,),
        )
        current_revision = int(
            connection.execute(
                "SELECT revision FROM openair_tasks WHERE task_id = %s",
                (task.task_id,),
            ).fetchone()["revision"]
        )

    assert repository.record_dispatch_resolution_failure(
        dispatch_id=started.dispatch_id,
        task_id=started.task_id,
        execution_decision_id=started.execution_decision_id,
        work_unit_id=started.work_unit_id,
        idempotency_key=started.idempotency_key,
        owner_id="owner",
        workspace_id="workspace",
        expected_task_revision=current_revision,
        error_code="dispatch_contract_mismatch",
        retryable=False,
        max_attempts=2,
        retry_delay_seconds=1,
    ) == "submission_unknown"

    with postgres_environment() as connection:
        dispatch_row = connection.execute(
            """SELECT status, last_error_code, external_action_started_at,
                      lease_owner, lease_expires_at, next_attempt_at
                 FROM openair_dispatch WHERE dispatch_id = %s""",
            (dispatch.dispatch_id,),
        ).fetchone()
        external_execution = connection.execute(
            """SELECT task_id, execution_decision_id, work_unit_id,
                      capability_call_id, provider_idempotency_key,
                      external_state, failure_code
                 FROM openair_external_execution WHERE dispatch_id = %s""",
            (dispatch.dispatch_id,),
        ).fetchone()
        evidence = connection.execute(
            """SELECT task_id, execution_decision_id, work_unit_id,
                      capability_call_id, normalized_finish_reason
                 FROM openair_external_evidence WHERE dispatch_id = %s""",
            (dispatch.dispatch_id,),
        ).fetchone()
        continuation = connection.execute(
            """SELECT status FROM openair_dispatch_continuation
                WHERE dispatch_id = %s""",
            (dispatch.dispatch_id,),
        ).fetchone()
        task_status = connection.execute(
            "SELECT status FROM openair_tasks WHERE task_id = %s",
            (task.task_id,),
        ).fetchone()["status"]
        dispatch_count = connection.execute(
            "SELECT count(*) AS count FROM openair_dispatch WHERE task_id = %s",
            (task.task_id,),
        ).fetchone()["count"]

    assert dispatch_row["status"] == "submission_unknown"
    assert dispatch_row["last_error_code"] == "invalid_research_orchestration_payload"
    assert dispatch_row["external_action_started_at"] is not None
    assert dispatch_row["lease_owner"] is None
    assert dispatch_row["lease_expires_at"] is None
    assert dispatch_row["next_attempt_at"] is None
    assert external_execution == {
        "task_id": task.task_id,
        "execution_decision_id": started.execution_decision_id,
        "work_unit_id": started.work_unit_id,
        "capability_call_id": dispatch_capability_call_id(started),
        "provider_idempotency_key": started.idempotency_key,
        "external_state": "unknown",
        "failure_code": "invalid_research_orchestration_payload",
    }
    assert evidence == {
        "task_id": task.task_id,
        "execution_decision_id": started.execution_decision_id,
        "work_unit_id": started.work_unit_id,
        "capability_call_id": dispatch_capability_call_id(started),
        "normalized_finish_reason": "invalid_research_orchestration_payload",
    }
    assert continuation == {"status": "pending"}
    assert task_status not in {"completed", "failed", "cancelled"}
    assert dispatch_count == provider_submit_calls == 1

    class QueryUnsupportedRecovery:
        capability_checks = 0

        def capabilities(self):
            self.capability_checks += 1

            class Capabilities:
                query_supported = False

            return Capabilities()

        @staticmethod
        def reconcile(_command):
            raise AssertionError("query-unsupported recovery must not query or submit")

    recovery = QueryUnsupportedRecovery()
    assert ResearchDispatchService(
        repository,
        external_recovery_service=recovery,
    ).process_next_dispatch_continuation(
        lease_owner="continuation-invalid-task",
        lease_seconds=30,
    ) is True
    with postgres_environment() as connection:
        retained = connection.execute(
            """SELECT status, last_error_code
                 FROM openair_dispatch_continuation WHERE dispatch_id = %s""",
            (dispatch.dispatch_id,),
        ).fetchone()
    assert recovery.capability_checks == 1
    assert retained == {
        "status": "pending",
        "last_error_code": "external_recovery_task_payload_invalid",
    }
