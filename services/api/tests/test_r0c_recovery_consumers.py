"""R0-C server recovery gates, with explicit transaction boundary doubles.

These tests execute the repository predicates and rejection paths. They do not
replace the PostgreSQL transaction tests or claim an external action occurred.
"""
from contextlib import contextmanager
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from app.modules import workbench_user_actions as actions
from app.modules.research_orchestration_repository import (
    ExternalRecoveryConflictError, ExternalRecoveryStateError,
    ResearchOrchestrationRepository, TaskRevisionConflictError,
)
from tests.support.r0c_interruption import r0c_canonical


def retry_context():
    command = SimpleNamespace(
        task_id="task_1", dispatch_id="dispatch_1", execution_decision_id="decision_1",
        work_unit_id="wu_1", prior_capability_call_id="call_1", prior_attempt_number=1,
        owner_id="owner_1", workspace_id="workspace_1", expected_task_revision=8,
        command_request_id="retry-request-1", canonical_payload_fingerprint="f" * 64,
        reason_code="user_confirmed_explicit_retry",
    )
    row = {
        "execution_decision_id": "decision_1", "work_unit_id": "wu_1",
        "capability_call_id": "call_1", "attempt_number": 1,
        "owner_id": "owner_1", "workspace_id": "workspace_1",
        "provider_idempotency_key": "provider-key-1", "idempotency_key": "provider-key-1",
        "status": "failed", "external_state": "never_submitted",
        "remote_run_id": None, "external_remote_run_id": None,
        "last_error_code": "provider_transport_not_submitted",
    }
    task = r0c_canonical(reason="provider_transport_not_submitted", work_status="blocked")
    return task, command, row


class RejectPathCursor:
    """Records SQL before rejection; it never commits or pretends to be a DB."""
    def __init__(self, row=None):
        self.row = row
        self.statements = []

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def execute(self, sql, parameters=None):
        self.statements.append((" ".join(sql.split()), parameters))

    def fetchone(self):
        if "openair_external_retry_attempt" in self.statements[-1][0]:
            return None
        return self.row


def rejecting_repository(task, raw, cursor):
    @contextmanager
    def transaction():
        yield SimpleNamespace(cursor=lambda: cursor)

    repository = object.__new__(ResearchOrchestrationRepository)
    repository._uow = SimpleNamespace(transaction=transaction)
    repository._require_write_enabled = lambda: None
    repository._require_postgres = lambda: None
    repository._load_locked_task = lambda _cursor, **_scope: (task, raw)
    return repository


class RetryConsumerContractTests(unittest.TestCase):
    def test_valid_current_proof_matches_snapshot_predicate(self):
        task, command, row = retry_context()
        reason = actions.explicit_retry_reason(
            canonical=task, work_unit_id=command.work_unit_id,
            execution_decision_id=command.execution_decision_id,
            prior_capability_call_id=command.prior_capability_call_id,
            prior_attempt_number=command.prior_attempt_number,
            dispatch_status=row["status"], external_state=row["external_state"],
            dispatch_remote_run_id=row["remote_run_id"],
            external_remote_run_id=row["external_remote_run_id"],
            last_error_code=row["last_error_code"],
        )
        self.assertEqual(reason, command.reason_code)
        self.assertIsNone(ResearchOrchestrationRepository._validate_retry_row(task, command, row))

    def test_forged_dispatch_call_or_owner_scope_is_rejected(self):
        task, command, row = retry_context()
        for field, value in (
            ("execution_decision_id", "other-decision"), ("work_unit_id", "other-work"),
            ("capability_call_id", "other-call"), ("attempt_number", 2),
            ("owner_id", "other-owner"), ("workspace_id", "other-workspace"),
            ("provider_idempotency_key", "other-provider-key"),
        ):
            with self.subTest(field=field):
                with self.assertRaisesRegex(ExternalRecoveryConflictError, "retry_lineage_mismatch"):
                    ResearchOrchestrationRepository._validate_retry_row(task, command, {**row, field: value})

    def test_stale_prior_attempt_cannot_become_another_current_attempt(self):
        task, command, row = retry_context()
        task.capability_calls.append(task.capability_calls[0].model_copy(update={
            "capability_call_id": "call_2", "attempt_number": 2,
        }))
        with self.assertRaisesRegex(ExternalRecoveryStateError, "retry_requires_proven_safe_outcome"):
            ResearchOrchestrationRepository._validate_retry_row(task, command, row)

    def test_unknown_or_a_remote_identity_cannot_be_blindly_retried(self):
        task, command, row = retry_context()
        for change in (
            {"external_state": "unknown"}, {"remote_run_id": "remote-1"},
            {"external_remote_run_id": "remote-1"},
        ):
            with self.subTest(change=change):
                with self.assertRaisesRegex(ExternalRecoveryStateError, "retry_requires_proven_safe_outcome"):
                    ResearchOrchestrationRepository._validate_retry_row(task, command, {**row, **change})

    def test_runtime_configuration_is_not_lost_behind_work_transport_reason(self):
        task, command, row = retry_context()
        task.substage_runtime_states[0].status_reason_code = "provider_configuration"
        with self.assertRaisesRegex(ExternalRecoveryStateError, "retry_requires_proven_safe_outcome"):
            ResearchOrchestrationRepository._validate_retry_row(task, command, row)

    def test_frozen_input_and_unknown_system_contracts_cannot_be_retried(self):
        for reason in ("skill_input_invalid", "future_system_failure"):
            with self.subTest(reason=reason):
                _task, command, row = retry_context()
                task = r0c_canonical(reason=reason, work_status="blocked")
                with self.assertRaisesRegex(ExternalRecoveryStateError, "retry_requires_proven_safe_outcome"):
                    ResearchOrchestrationRepository._validate_retry_row(task, command, row)

    def test_retry_replay_requires_the_exact_payload_and_owner(self):
        _task, command, _row = retry_context()
        replay = {"canonical_payload_fingerprint": command.canonical_payload_fingerprint,
                  "owner_id": command.owner_id, "workspace_id": command.workspace_id}
        self.assertIsNone(ResearchOrchestrationRepository._validate_retry_replay_scope(command, replay))
        for field in replay:
            with self.subTest(field=field):
                with self.assertRaisesRegex(ExternalRecoveryConflictError, "retry_command_payload_conflict"):
                    ResearchOrchestrationRepository._validate_retry_replay_scope(command, {**replay, field: "changed"})

    def test_both_retry_entry_points_reject_stale_revision_before_mutation(self):
        for method in ("prepare_retry", "create_retry_attempt"):
            with self.subTest(method=method):
                task, command, row = retry_context()
                command.expected_task_revision = task.revision - 1
                cursor = RejectPathCursor(row)
                repository = rejecting_repository(task, {"revision": task.revision}, cursor)
                with self.assertRaisesRegex(TaskRevisionConflictError, "task_revision_conflict"):
                    getattr(repository, method)(command)
                self.assertTrue(all(sql.startswith("SELECT ") for sql, _ in cursor.statements))

    def test_both_retry_entry_points_recheck_current_configuration_before_mutation(self):
        for method in ("prepare_retry", "create_retry_attempt"):
            with self.subTest(method=method):
                task, command, row = retry_context()
                task.substage_runtime_states[0].status_reason_code = "provider_configuration"
                cursor = RejectPathCursor(row)
                repository = rejecting_repository(task, {"revision": task.revision}, cursor)
                with self.assertRaisesRegex(ExternalRecoveryStateError, "retry_requires_proven_safe_outcome"):
                    getattr(repository, method)(command)
                self.assertTrue(all(sql.startswith("SELECT ") for sql, _ in cursor.statements))


class ResumeConsumerContractTests(unittest.TestCase):
    def test_failure_then_stop_is_rejected_before_dispatch_or_task_mutation(self):
        task = r0c_canonical(task_status="cancelled", runtime_status="cancelled")
        raw = {"taskId": task.task_id, "revision": task.revision, "status": "cancelled", "stopReceipt": {
            "taskId": task.task_id, "status": "stopped", "active": True, "controlAction": "stop",
        }}
        cursor = RejectPathCursor()
        repository = rejecting_repository(task, raw, cursor)
        with patch.object(actions, "_canonical_task", return_value=task) as canonical_boundary:
            with self.assertRaisesRegex(TaskRevisionConflictError, "resume_research_not_allowed"):
                repository.resume_suppressed_dispatches_with_receipt(
                    task_id=task.task_id, owner_id="owner_1", workspace_id="workspace_1",
                    expected_task_revision=task.revision,
                )
        canonical_boundary.assert_called_once()
        self.assertEqual(cursor.statements, [])


if __name__ == "__main__":
    unittest.main()
