from __future__ import annotations

import ast
import inspect
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest


REPOSITORY_PATH = Path(__file__).parents[1] / "app" / "modules" / "research_start_repository.py"
ATTEMPT_LEASE_PATH = (
    Path(__file__).parents[1] / "app" / "modules" / "research_start_attempt_lease.py"
)
PROJECTION_PATH = Path(__file__).parents[1] / "app" / "modules" / "research_start_projection.py"
FINALIZATION_PATH = Path(__file__).parents[1] / "app" / "modules" / "research_start_finalization.py"
RECOVERY_OPERATIONS_PATH = Path(__file__).parents[1] / "app" / "modules" / "research_start_recovery.py"
ARTIFACT_REPOSITORY_PATH = Path(__file__).parents[1] / "app" / "modules" / "artifact_repository.py"
RECOVERY_REPOSITORY_PATH = Path(__file__).parents[1] / "app" / "modules" / "recovery_repository.py"
TASK_REPOSITORY_PATH = Path(__file__).parents[1] / "app" / "modules" / "task_repository.py"
MIGRATION_PATH = Path(__file__).parents[1] / "migrations" / "20260725_02_durable_research_start.sql"
DOWN_MIGRATION_PATH = Path(__file__).parents[1] / "migrations" / "20260725_02_durable_research_start_down.sql"


def _intent(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "input": "durable research",
        "sourcePage": "home",
        "selectedHint": None,
        "presetQuery": None,
        "sessionId": None,
        "createClarificationDraft": False,
    }
    value.update(overrides)
    return value


def test_early_planning_task_terminal_projection_advances_same_revision() -> None:
    from app.modules.research_start_repository import ResearchStartRepository

    class Cursor:
        rowcount = 0

        def __init__(self) -> None:
            self.current_sql = ""
            self.calls: list[tuple[str, object]] = []

        def execute(self, sql: str, params: object = None) -> None:
            self.current_sql = sql
            self.calls.append((sql, params))
            self.rowcount = 1 if "UPDATE openair_tasks" in sql else 0

        def fetchone(self):
            if "SELECT payload, revision FROM openair_tasks" not in self.current_sql:
                return None
            return {
                "revision": 4,
                "payload": {
                    "taskId": "task_reserved",
                    "revision": 4,
                    "status": "draft",
                    "progressProjection": {
                        "revision": 4,
                        "taskProgress": {"status": "draft"},
                    },
                },
            }

    cursor = Cursor()
    ResearchStartRepository._mark_reserved_planning_task(
        cursor,
        {
            "reserved_task_id": "task_reserved",
            "owner_id": "owner",
            "workspace_id": "workspace",
        },
        task_status="failed",
        projection_status="failed",
    )

    update = next(params for sql, params in cursor.calls if "UPDATE openair_tasks" in sql)
    assert isinstance(update, tuple)
    assert update[0:2] == ("failed", 5)
    payload = json.loads(update[2])
    assert payload["revision"] == 5
    assert payload["status"] == "failed"
    assert payload["progressProjection"]["revision"] == 5
    assert payload["progressProjection"]["taskProgress"]["status"] == "failed"


def test_research_start_delegates_task_state_writes_to_lifecycle_boundary() -> None:
    source = REPOSITORY_PATH.read_text(encoding="utf-8")

    assert "UPDATE openair_tasks" not in source
    assert "apply_research_start_lifecycle_fact_locked" in source


def test_recovery_public_operations_are_extracted_behind_compatibility_facades() -> None:
    repository_source = REPOSITORY_PATH.read_text(encoding="utf-8")
    recovery_source = RECOVERY_OPERATIONS_PATH.read_text(encoding="utf-8")
    repository_tree = ast.parse(repository_source)
    recovery_tree = ast.parse(recovery_source)
    operation_names = {
        "resolve_known_planner_http_failure",
        "retry_external_result_unknown",
        "mark_external_result_unknown",
        "reconcile_expired_leases",
    }

    repository_class = next(
        node
        for node in repository_tree.body
        if isinstance(node, ast.ClassDef) and node.name == "ResearchStartRepository"
    )
    facades = {
        node.name: node
        for node in repository_class.body
        if isinstance(node, ast.FunctionDef) and node.name in operation_names
    }
    recovery_class = next(
        node
        for node in recovery_tree.body
        if isinstance(node, ast.ClassDef) and node.name == "ResearchStartRecoveryOperations"
    )
    extracted = {
        node.name
        for node in recovery_class.body
        if isinstance(node, ast.FunctionDef) and node.name in operation_names
    }

    assert set(facades) == operation_names
    assert extracted == operation_names
    for name, facade in facades.items():
        statements = [
            statement
            for statement in facade.body
            if not (
                isinstance(statement, ast.Expr)
                and isinstance(statement.value, ast.Constant)
                and isinstance(statement.value.value, str)
            )
        ]
        assert len(statements) == 1
        statement = statements[0]
        assert isinstance(statement, ast.Return)
        assert isinstance(statement.value, ast.Call)
        assert isinstance(statement.value.func, ast.Attribute)
        assert statement.value.func.attr == name
        assert isinstance(statement.value.func.value, ast.Call)
        assert isinstance(statement.value.func.value.func, ast.Attribute)
        assert statement.value.func.value.func.attr == "_recovery_operations"


def test_artifact_repository_delegates_task_state_writes_to_lifecycle_boundary() -> None:
    source = ARTIFACT_REPOSITORY_PATH.read_text(encoding="utf-8")

    assert "UPDATE openair_tasks" not in source
    assert "commit_task_lifecycle_mutation_locked" in source


def test_recovery_repository_delegates_task_state_writes_to_lifecycle_boundary() -> None:
    source = RECOVERY_REPOSITORY_PATH.read_text(encoding="utf-8")

    assert "UPDATE openair_tasks" not in source
    assert "RecoveryLifecycleFact.SUCCESSOR_EXECUTION_CONFIRMED" in source
    assert "commit_task_lifecycle_mutation_locked" in source


def test_task_repository_lifecycle_mutations_delegate_task_status_to_the_boundary() -> None:
    source = TASK_REPOSITORY_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    methods = {
        node.name: ast.get_source_segment(source, node) or ""
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name in {"confirm_research_start_plan", "_commit_user_task_mutation"}
    }

    assert set(methods) == {"confirm_research_start_plan", "_commit_user_task_mutation"}
    assert all("UPDATE openair_tasks" not in method for method in methods.values())
    assert all("commit_task_lifecycle_mutation_locked" in method for method in methods.values())


class _FencedCursor:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []
        self.current_sql = ""

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql: str, params: object = None) -> None:
        self.current_sql = sql
        self.calls.append((sql, params))

    def fetchone(self):
        if "SELECT request_id, session_id, owner_id, workspace_id\n                 FROM research_start_requests" in self.current_sql:
            return {"request_id": "rsr_test", "session_id": "intent_123456789abc", "owner_id": "owner", "workspace_id": "workspace"}
        if "FROM research_start_sessions" in self.current_sql:
            return {
                "session_id": "intent_123456789abc", "owner_id": "owner", "workspace_id": "workspace", "state": "running",
                "reserved_task_id": "task_reserved", "reserved_artifact_id": "artifact_reserved",
            }
        if "FROM research_start_requests" in self.current_sql or "UPDATE research_start_requests" in self.current_sql:
            return {
                "request_id": "rsr_test", "session_id": "intent_123456789abc", "owner_id": "owner", "workspace_id": "workspace",
                "client_request_id": "client", "fingerprint": "f" * 64, "state": "leased", "lease_owner": "worker",
                "lease_epoch": 1, "lease_expires_at": "later", "lease_is_live": True,
                "request_payload": {**_intent(), "_createdBy": "actor-user"}, "result_payload": None,
            }
        return None

    def fetchall(self):
        return []


class _Connection:
    def __init__(self, cursor: _FencedCursor) -> None:
        self._cursor = cursor

    def cursor(self):
        return self._cursor

    def commit(self): pass
    def rollback(self): pass
    def close(self): pass


def test_attempt_lease_lock_path_preserves_session_then_request_lock_order() -> None:
    from app.modules.research_start_repository import ResearchStartRepository

    class Cursor:
        def __init__(self) -> None:
            self.calls: list[tuple[str, object]] = []
            self.current_sql = ""

        def execute(self, sql: str, params: object = None) -> None:
            self.current_sql = sql
            self.calls.append((sql, params))

        def fetchone(self):
            if "FROM research_start_sessions" in self.current_sql:
                return {
                    "session_id": "intent_123456789abc",
                    "owner_id": "owner",
                    "workspace_id": "workspace",
                    "state": "running",
                }
            if "lease_is_live" in self.current_sql:
                return {
                    "request_id": "rsr_test",
                    "session_id": "intent_123456789abc",
                    "owner_id": "owner",
                    "workspace_id": "workspace",
                    "state": "leased",
                    "lease_owner": "worker",
                    "lease_epoch": 1,
                    "lease_is_live": True,
                }
            return {
                "request_id": "rsr_test",
                "session_id": "intent_123456789abc",
                "owner_id": "owner",
                "workspace_id": "workspace",
            }

    cursor = Cursor()

    ResearchStartRepository()._lock_path(cursor, "rsr_test")

    assert [params for _sql, params in cursor.calls] == [
        ("rsr_test",),
        ("intent_123456789abc", "owner", "workspace"),
        ("rsr_test", "intent_123456789abc", "owner", "workspace"),
    ]
    assert "FROM research_start_requests" in cursor.calls[0][0]
    assert "FOR UPDATE" not in cursor.calls[0][0]
    assert "FROM research_start_sessions" in cursor.calls[1][0]
    assert "FOR UPDATE" in cursor.calls[1][0]
    assert "FROM research_start_requests" in cursor.calls[2][0]
    assert "FOR UPDATE" in cursor.calls[2][0]


@pytest.mark.parametrize(
    "request_update",
    [
        {"lease_owner": "another-worker"},
        {"lease_epoch": 2},
        {"lease_is_live": False},
    ],
)
def test_attempt_lease_rejects_each_lost_lease_signal(request_update: dict[str, object]) -> None:
    from app.modules.research_start_repository import LeaseLostError, ResearchStartRepository

    session = {"state": "running"}
    request = {
        "state": "leased",
        "lease_owner": "worker",
        "lease_epoch": 1,
        "lease_is_live": True,
    }
    request.update(request_update)

    with pytest.raises(LeaseLostError):
        ResearchStartRepository._require_live_lease(session, request, "worker", 1)


class _SessionProjectionCursor:
    def __init__(self, *, session_state: str = "running", attempt_phase: str | None = None) -> None:
        self.calls: list[tuple[str, object]] = []
        self.current_sql = ""
        self.session_state = session_state
        self.attempt_phase = attempt_phase

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql: str, params: object = None) -> None:
        self.current_sql = sql
        self.calls.append((sql, params))

    def fetchone(self):
        if "FROM research_start_sessions" in self.current_sql:
            return {
                "session_id": "intent_123456789abc",
                "owner_id": "owner",
                "workspace_id": "workspace",
                "state": self.session_state,
                "reserved_task_id": "task_reserved",
                "reserved_artifact_id": "artifact_reserved",
                "result_payload": None,
                "user_message": None,
                "failure_code": None,
                "created_at": datetime(2026, 8, 5, 8, 0, tzinfo=timezone.utc),
                "updated_at": datetime(2026, 8, 5, 8, 0, 12, tzinfo=timezone.utc),
                "request_state": "leased" if self.attempt_phase else "queued",
                "request_updated_at": datetime(2026, 8, 5, 8, 0, 4, tzinfo=timezone.utc),
                "attempt_phase": self.attempt_phase,
                "attempt_ordinal": 1 if self.attempt_phase else None,
                "attempt_state": "started" if self.attempt_phase else None,
                "attempt_result_payload": None,
                "attempt_started_at": datetime(2026, 8, 5, 8, 0, 5, tzinfo=timezone.utc) if self.attempt_phase else None,
                "attempt_finished_at": None,
                "elapsed_seconds": 12,
            }
        return None


def test_canonical_fingerprint_is_versioned_strict_and_default_expanding() -> None:
    from app.modules.research_start_repository import InvalidIntentError, canonical_fingerprint

    compact = canonical_fingerprint({"input": "durable research"})
    assert compact == canonical_fingerprint(_intent())
    assert compact == canonical_fingerprint(dict(reversed(list(_intent().items()))))
    assert canonical_fingerprint({"input": "caf\u00e9"}) == canonical_fingerprint({"input": "cafe\u0301"})
    assert compact != canonical_fingerprint({"input": "durable research", "createClarificationDraft": True})

    invalid_values = (
        {},
        {"input": "x", "extra": "not contracted"},
        {"input": 1},
        {"input": float("nan")},
        {"input": "x", "sourcePage": float("nan")},
    )
    for value in invalid_values:
        with pytest.raises(InvalidIntentError) as raised:
            canonical_fingerprint(value)
        assert raised.value.code == "invalid_intent"


def test_error_codes_and_two_phase_repository_api_are_stable() -> None:
    from app.modules.research_start_repository import (
        AttemptAlreadyStartedError,
        LeaseLostError,
        RequestConflictError,
        ResearchStartRepository,
        TaskFinalizeRequiredError,
        SessionBusyError,
        SessionNotFoundError,
        TerminalStateError,
    )

    assert RequestConflictError.code == "fingerprint_conflict"
    assert SessionNotFoundError.code == "session_not_found"
    assert SessionBusyError.code == "session_busy"
    assert LeaseLostError.code == "lease_lost"
    assert AttemptAlreadyStartedError.code == "attempt_already_started"
    assert TerminalStateError.code == "terminal_state"
    assert callable(ResearchStartRepository.finish_attempt)
    assert callable(ResearchStartRepository.complete_request)
    assert callable(ResearchStartRepository.finalize_from_authoritative_receipt)
    assert callable(ResearchStartRepository.finalize_task_request)
    assert callable(ResearchStartRepository.fail_request)
    assert TaskFinalizeRequiredError.code == "task_finalize_required"
    assert not hasattr(ResearchStartRepository, "finish")


def test_repository_has_no_route_worker_or_external_runtime_imports() -> None:
    tree = ast.parse(REPOSITORY_PATH.read_text(encoding="utf-8"))
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.append(node.module)

    forbidden = ("provider", "worker", "route", "runtime", "docker", "httpx")
    assert not [module for module in imports if any(word in module.lower() for word in forbidden)]


def test_repository_contract_uses_db_clock_explicit_lock_order_and_minimal_dtos() -> None:
    source = REPOSITORY_PATH.read_text(encoding="utf-8")
    attempt_lease_source = ATTEMPT_LEASE_PATH.read_text(encoding="utf-8")
    projection_source = PROJECTION_PATH.read_text(encoding="utf-8")
    finalization_source = FINALIZATION_PATH.read_text(encoding="utf-8")

    assert "clock_timestamp()" in source
    assert "datetime.now" not in source
    assert "FOR UPDATE OF" not in source
    assert "_lock_session" in source and "_lock_request" in source and "_lock_attempt" in source
    assert "lease_expires_at > clock_timestamp()" in source
    assert "sessionId" in attempt_lease_source
    assert "reservedTaskId" in attempt_lease_source
    assert "reservedArtifactId" in attempt_lease_source
    assert "result" in attempt_lease_source and "userMessage" in projection_source
    assert "SELECT *" not in source
    assert "finalize_task_request" in source
    assert "TaskRepository().insert_task_with_cursor" in finalization_source
    assert "ArtifactRepository().insert_artifact_with_cursor" in finalization_source


def test_renew_uses_parameterized_transaction_local_statement_timeout() -> None:
    from app.modules.research_start_repository import ResearchStartRepository

    cursor = _FencedCursor()
    repository = ResearchStartRepository(connection_factory=lambda: _Connection(cursor))

    repository.renew("rsr_test", "worker", 1, query_timeout_ms=2000)

    sql, params = cursor.calls[0]
    assert "SELECT set_config('statement_timeout', %s, true)" in sql
    assert params == ("2000ms",)
    update = next(call for call in cursor.calls if "UPDATE research_start_requests" in call[0])
    assert update[1] == (60, "rsr_test", "intent_123456789abc", "owner", "workspace", "worker", 1)


def test_begin_attempt_keeps_the_fenced_insert_parameters(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.modules.research_start_repository import ResearchStartRepository

    class Cursor(_FencedCursor):
        def fetchone(self):
            if "INSERT INTO research_start_attempts" in self.current_sql:
                return {
                    "attempt_id": "rsa_fixed",
                    "request_id": "rsr_test",
                    "phase": "research_planner",
                    "ordinal": 1,
                    "state": "started",
                    "fence_epoch": 1,
                    "result_payload": None,
                    "started_at": None,
                    "finished_at": None,
                }
            return super().fetchone()

    monkeypatch.setattr("app.modules.research_start_repository._new_id", lambda _prefix: "rsa_fixed")
    cursor = Cursor()
    repository = ResearchStartRepository(connection_factory=lambda: _Connection(cursor))

    attempt = repository.begin_attempt("rsr_test", "worker", 1, "research_planner", 1)

    assert attempt["attemptId"] == "rsa_fixed"
    insert = next(call for call in cursor.calls if "INSERT INTO research_start_attempts" in call[0])
    assert insert[1] == (
        "rsa_fixed",
        "rsr_test",
        "research_planner",
        1,
        1,
        "rsr_test",
        "intent_123456789abc",
        "owner",
        "workspace",
        "worker",
        1,
    )


def test_claim_accepts_operator_cutoff_and_applies_it_to_both_queue_queries() -> None:
    from app.config import settings
    from app.modules.research_start_repository import ResearchStartRepository

    signature = inspect.signature(ResearchStartRepository.claim)
    source = ATTEMPT_LEASE_PATH.read_text(encoding="utf-8")

    assert "not_before" in signature.parameters
    assert source.count("r.created_at >= %s") == 1
    assert source.count("created_at >= %s") >= 2

    cutoff = datetime(2026, 7, 26, 8, 55, tzinfo=timezone.utc)
    cursor = _FencedCursor()
    repository = ResearchStartRepository(connection_factory=lambda: _Connection(cursor))

    claim = repository.claim("worker", not_before=cutoff)

    assert claim is not None
    session_query = next(call for call in cursor.calls if "EXISTS (" in call[0])
    request_query = next(call for call in cursor.calls if "SELECT request_id FROM" in call[0])
    assert session_query[1] == (settings.runtime_source_version, cutoff)
    assert request_query[1] == (
        "intent_123456789abc",
        "owner",
        "workspace",
        settings.runtime_source_version,
        cutoff,
    )


def test_execution_context_separates_persisted_actor_from_provider_intent() -> None:
    from app.modules.research_start_repository import ResearchStartRepository

    cursor = _FencedCursor()
    repository = ResearchStartRepository(connection_factory=lambda: _Connection(cursor))

    context = repository.load_execution_context("rsr_test", "worker", 1)

    assert context["ownerId"] == "owner"
    assert context["createdBy"] == "actor-user"
    assert "_createdBy" not in context["intent"]




def test_catalog_execution_context_carries_authoritative_resource_pin() -> None:
    from app.modules.research_start_repository import ResearchStartRepository

    class CatalogCursor(_FencedCursor):
        def fetchone(self):
            row = super().fetchone()
            if isinstance(row, dict) and "FROM research_start_sessions" in self.current_sql:
                return {
                    **row,
                    "catalog_intent_id": "intent_aaaaaaaaaaaa",
                    "selected_resource_id": "skill:catalog_skill",
                    "selected_resource_version": "1.2.3",
                    "selected_capability_id": "catalog_skill",
                }
            return row

    cursor = CatalogCursor()
    repository = ResearchStartRepository(connection_factory=lambda: _Connection(cursor))

    context = repository.load_execution_context("rsr_test", "worker", 1)

    assert context["catalogBinding"] == {
        "catalogIntentId": "intent_aaaaaaaaaaaa",
        "resourceId": "skill:catalog_skill",
        "resourceVersion": "1.2.3",
        "capabilityId": "catalog_skill",
        "capabilityKind": "skill",
    }

def test_reserve_persists_actor_and_includes_it_in_fingerprint() -> None:
    from app.modules.research_start_repository import ResearchStartRepository

    signature = inspect.signature(ResearchStartRepository.reserve)
    source = inspect.getsource(ResearchStartRepository.reserve)

    assert signature.parameters["created_by"].default is None
    assert '"_createdBy"' in source
    assert "_durable_fingerprint(normalized, actor_id)" in source


def test_research_start_execution_mode_is_canonical_and_fingerprinted() -> None:
    from app.modules.research_start_repository import (
        InvalidIntentError,
        _canonical_intent,
        canonical_fingerprint,
    )

    default_intent = _canonical_intent(_intent())
    automatic_intent = _canonical_intent(_intent(executionMode="automatic_research"))

    assert default_intent["executionMode"] == "stage_confirmation"
    assert automatic_intent["executionMode"] == "automatic_research"
    assert canonical_fingerprint(default_intent) != canonical_fingerprint(automatic_intent)
    with pytest.raises(InvalidIntentError):
        _canonical_intent(_intent(executionMode="unsupported"))


def test_fingerprint_upgrade_replays_only_exact_legacy_owner_rows() -> None:
    from app.modules.research_start_repository import (
        _canonical_intent,
        _durable_fingerprint,
        _fingerprint_normalized,
        _request_fingerprint_matches,
    )

    intent = _canonical_intent(_intent())
    pre_execution_mode_intent = dict(intent)
    pre_execution_mode_intent.pop("executionMode")
    legacy_row = {
        "owner_id": "owner",
        "fingerprint": _fingerprint_normalized(pre_execution_mode_intent),
        "request_payload": dict(pre_execution_mode_intent),
    }
    assert _request_fingerprint_matches(legacy_row, intent, "owner") is True
    assert _request_fingerprint_matches(legacy_row, _canonical_intent(_intent(input="different")), "owner") is False
    assert _request_fingerprint_matches(legacy_row, intent, "different-actor") is False

    new_payload = {**pre_execution_mode_intent, "_createdBy": "actor-user"}
    new_row = {
        "owner_id": "owner",
        "fingerprint": _durable_fingerprint(pre_execution_mode_intent, "actor-user"),
        "request_payload": new_payload,
    }
    assert _request_fingerprint_matches(new_row, intent, "actor-user") is True
    assert _request_fingerprint_matches(new_row, intent, "different-actor") is False


def test_public_session_projection_never_exposes_worker_attempt_decision() -> None:
    from app.modules.research_start_repository import _session_public

    public = _session_public({
        "session_id": "intent_123456789abc",
        "state": "completed",
        "reserved_task_id": "task_reserved",
        "result_payload": {"outcome": "research_task", "taskId": "task_reserved"},
        "user_message": "draft ready",
        "attempt_result_payload": {"reason": "INTERNAL_START_DECISION"},
    })

    assert public == {
        "sessionId": "intent_123456789abc",
        "status": "completed",
        "currentPhase": "planning",
        "reservedTaskId": "task_reserved",
        "planningProgress": {
            "code": "design_ready",
            "message": "研究规划已通过校验，可以查看并确认。",
            "elapsedSeconds": 0,
            "startedAt": None,
            "updatedAt": None,
            "attemptOrdinal": None,
            "repairRound": 0,
            "keyResults": None,
        },
        "result": {"outcome": "research_task", "taskId": "task_reserved"},
        "userMessage": "draft ready",
    }
    assert "INTERNAL_START_DECISION" not in repr(public)


def test_public_session_projection_recovers_completed_non_task_result_without_session_id() -> None:
    from app.modules.research_start_repository import _session_public
    from app.routes.research_start import ResearchStartSessionStatusOut

    persisted_result = {
        "outcome": "lightweight_assist",
        "decision": {"userOutcome": "lightweight_assist", "userFacingSummary": "answer"},
    }

    public = _session_public({
        "session_id": "intent_123456789abc",
        "state": "completed",
        "result_payload": persisted_result,
        "user_message": "answer",
    })

    assert public["result"] == {**persisted_result, "sessionId": "intent_123456789abc"}
    assert ResearchStartSessionStatusOut.model_validate(public).result.sessionId == "intent_123456789abc"


@pytest.mark.parametrize(
    ("state", "persisted_result"),
    (
        ("running", {"outcome": "lightweight_assist"}),
        ("completed", {"outcome": "unexpected_outcome"}),
        ("completed", {"outcome": "research_taks", "taskId": "task_corrupt"}),
        ("completed", {"outcome": "research_task", "taskId": "task_corrupt"}),
        ("completed", {"outcome": "lightweight_assist", "taskId": "task_corrupt"}),
    ),
)
def test_public_session_projection_does_not_repair_invalid_or_non_completed_result(
    state: str,
    persisted_result: dict[str, str],
) -> None:
    from pydantic import ValidationError

    from app.modules.research_start_repository import _session_public
    from app.routes.research_start import ResearchStartSessionStatusOut

    public = _session_public({
        "session_id": "intent_123456789abc",
        "state": state,
        "result_payload": persisted_result,
        "user_message": None,
    })

    assert public["result"] is persisted_result
    with pytest.raises(ValidationError, match="result.sessionId"):
        ResearchStartSessionStatusOut.model_validate(public)


def test_planning_progress_exposes_only_safe_durable_summary() -> None:
    from app.modules.research_start_repository import _planning_progress

    progress = _planning_progress({
        "state": "running",
        "request_state": "leased",
        "attempt_phase": "research_planner",
        "attempt_ordinal": 2,
        "attempt_state": "succeeded",
        "attempt_result_payload": {
            "providerCallRef": "provider-secret-ref",
            "proposal": {
                "planningMode": "partial",
                "understanding": {
                    "primaryResearchQuestions": ["Which claims are supported by the available evidence?"],
                    "targetOutputs": ["evidence_map"],
                    "planningRationale": "Use one evidence map so claims and unresolved gaps share a consistent scope.",
                },
                "instances": [{
                    "instanceKey": "internal-instance",
                    "definitionId": "internal-definition",
                    "objective": "Map the available evidence and unresolved gaps.",
                    "expectedArtifactTypes": ["evidence_map", "gap_list"],
                }],
                "rawPrompt": "must never be exposed",
            },
        },
        "elapsed_seconds": 38,
    })

    assert progress["code"] == "compiling_design"
    assert progress["attemptOrdinal"] == 2
    assert progress["repairRound"] == 1
    assert progress["keyResults"] == {
        "planningMode": "partial",
        "substageCount": 1,
        "researchQuestions": ["Which claims are supported by the available evidence?"],
        "objectives": ["Map the available evidence and unresolved gaps."],
        "targetOutputs": ["evidence_map"],
        "expectedArtifactTypes": ["evidence_map", "gap_list"],
        "planningRationale": "Use one evidence map so claims and unresolved gaps share a consistent scope.",
    }
    assert "provider-secret-ref" not in repr(progress)
    assert "internal-instance" not in repr(progress)
    assert "internal-definition" not in repr(progress)
    assert "must never be exposed" not in repr(progress)


def test_scoped_session_projects_router_attempt_as_durable_organizing_phase() -> None:
    from app.modules.research_start_repository import ResearchStartRepository

    cursor = _SessionProjectionCursor(attempt_phase="research_planner")
    repository = ResearchStartRepository(connection_factory=lambda: _Connection(cursor))

    public = repository.get_scoped_session("owner", "workspace", "intent_123456789abc")

    assert public["currentPhase"] == "organizing"
    assert len(cursor.calls) == 1
    scoped_sql, scoped_params = cursor.calls[0]
    normalized_scoped_sql = " ".join(scoped_sql.split())
    assert "FROM research_start_sessions s" in scoped_sql
    assert scoped_sql.count("LEFT JOIN LATERAL") == 2
    assert "FROM research_start_requests" in scoped_sql
    assert "FROM research_start_attempts" in scoped_sql
    assert "session_id = s.session_id AND owner_id = s.owner_id" in normalized_scoped_sql
    assert "workspace_id = s.workspace_id" in scoped_sql
    assert "AS attempt_phase" in scoped_sql
    assert "AS elapsed_seconds" in scoped_sql
    assert "FOR UPDATE" not in scoped_sql
    assert (
        "ORDER BY CASE phase WHEN 'research_planner' THEN 2 WHEN 'research_planner' THEN 1 ELSE 0 END DESC, "
        "ordinal DESC, started_at DESC, attempt_id DESC LIMIT 1"
    ) in normalized_scoped_sql
    assert scoped_params == ("intent_123456789abc", "owner", "workspace")


@pytest.mark.parametrize(
    ("session_state", "attempt_phase", "expected_phase"),
    (
        ("running", None, "understanding"),
        ("running", "research_planner", "organizing"),
        ("running", "research_planner", "planning"),
        ("completed", None, "planning"),
    ),
)
def test_scoped_session_waiting_phase_is_monotonic_durable_projection(
    session_state: str,
    attempt_phase: str | None,
    expected_phase: str,
) -> None:
    from app.modules.research_start_repository import ResearchStartRepository

    cursor = _SessionProjectionCursor(session_state=session_state, attempt_phase=attempt_phase)
    repository = ResearchStartRepository(connection_factory=lambda: _Connection(cursor))

    public = repository.get_scoped_session("owner", "workspace", "intent_123456789abc")

    assert public["currentPhase"] == expected_phase
    assert public["reservedTaskId"] == "task_reserved"
    assert public["planningProgress"]["elapsedSeconds"] == 12
    assert set(public) == {
        "sessionId", "status", "currentPhase", "reservedTaskId",
        "planningProgress", "result", "userMessage",
    }
    assert len(cursor.calls) == 1
    assert all("FOR UPDATE" not in sql for sql, _params in cursor.calls)


def test_up_migration_enforces_scope_ids_attempt_phases_and_terminal_payloads() -> None:
    up = MIGRATION_PATH.read_text(encoding="utf-8")

    assert "UNIQUE(session_id, owner_id, workspace_id)" in up
    assert "FOREIGN KEY (session_id, owner_id, workspace_id)" in up
    assert "REFERENCES research_start_sessions(session_id, owner_id, workspace_id)" in up
    assert "session_id ~ '^intent_[0-9a-f]{12}$'" in up
    assert "request_id ~ '^rsr_[0-9a-f]{32}$'" in up
    assert "attempt_id ~ '^rsa_[0-9a-f]{32}$'" in up
    assert "WHERE state = 'started'" in up
    assert "WHERE state = 'succeeded'" in up
    assert "result_payload JSONB" in up and "user_message TEXT" in up and "failure_code TEXT" in up
    assert "state = 'completed' AND result_payload IS NOT NULL" in up
    assert "state NOT IN ('failed', 'external_result_unknown') OR result_payload IS NULL" in up


def test_down_migration_locks_checks_drops_and_rewinds_ledger_atomically() -> None:
    down = DOWN_MIGRATION_PATH.read_text(encoding="utf-8")
    lock_sessions = down.index("LOCK TABLE research_start_sessions IN ACCESS EXCLUSIVE MODE")
    lock_requests = down.index("LOCK TABLE research_start_requests IN ACCESS EXCLUSIVE MODE")
    lock_attempts = down.index("LOCK TABLE research_start_attempts IN ACCESS EXCLUSIVE MODE")
    drop_attempts = down.index("DROP TABLE research_start_attempts")
    drop_requests = down.index("DROP TABLE research_start_requests")
    drop_sessions = down.index("DROP TABLE research_start_sessions")

    assert down.lstrip().startswith("BEGIN;") and down.rstrip().endswith("COMMIT;")
    assert lock_sessions < lock_requests < lock_attempts
    assert lock_attempts < drop_attempts < drop_requests < drop_sessions
    assert "refusing to drop non-empty durable research-start tables" in down
    assert "DELETE FROM openair_schema_migrations" in down
    assert "20260725_02_durable_research_start.sql" in down


def test_migration_runner_will_discover_the_up_migration() -> None:
    from app.modules.postgres_migrations import MIGRATIONS_DIR

    assert MIGRATIONS_DIR / MIGRATION_PATH.name in set(MIGRATIONS_DIR.glob("*.sql"))


def test_durable_admission_rejects_before_creating_any_reservation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.modules import research_start_repository as repository_module
    from app.modules.research_start_repository import (
        ResearchStartAdmissionFullError,
        ResearchStartRepository,
    )

    class Cursor:
        def __init__(self) -> None:
            self.calls: list[tuple[str, object]] = []
            self.current_sql = ""

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, sql: str, params: object = None) -> None:
            self.current_sql = sql
            self.calls.append((sql, params))

        def fetchone(self):
            if "WHERE owner_id = %s AND workspace_id = %s AND client_request_id = %s" in self.current_sql:
                return None
            if "COUNT(*) FILTER" in self.current_sql:
                return {"global_backlog": 8, "scoped_backlog": 2}
            return None

    cursor = Cursor()
    repository = ResearchStartRepository(connection_factory=lambda: _Connection(cursor))
    monkeypatch.setattr(repository_module.settings, "heavy_task_queue_enabled", True)
    monkeypatch.setattr(repository_module.settings, "heavy_task_global_limit", 8)
    monkeypatch.setattr(repository_module.settings, "heavy_task_per_user_limit", 2)

    with pytest.raises(ResearchStartAdmissionFullError) as raised:
        repository.reserve("owner", "workspace", "client-request", _intent())

    assert raised.value.scope == "global"
    assert raised.value.position == 9
    sql = "\n".join(statement for statement, _params in cursor.calls)
    assert "research-start-durable-admission-v1" in repr(cursor.calls)
    assert "INSERT INTO research_start_sessions" not in sql
    assert "INSERT INTO research_start_requests" not in sql


@pytest.mark.parametrize(
    ("age_seconds", "has_live_lease", "expected_code", "expected_retry"),
    (
        (12, False, "still_queued", 3),
        (75, True, "queued_stale", 5),
        (75, False, "worker_unavailable", 10),
    ),
)
def test_queued_recovery_projection_is_durable_and_actionable(
    age_seconds: int,
    has_live_lease: bool,
    expected_code: str,
    expected_retry: int,
) -> None:
    from app.modules.research_start_repository import _session_public

    public = _session_public(
        {
            "session_id": "intent_123456789abc",
            "state": "running",
            "reserved_task_id": "task_reserved",
            "result_payload": None,
            "user_message": None,
            "request_state": "queued",
            "request_age_seconds": age_seconds,
            "has_live_research_start_lease": has_live_lease,
            "elapsed_seconds": age_seconds,
        }
    )

    assert public["planningProgress"]["code"] == expected_code
    assert public["recoveryReason"] == expected_code
    assert public["retryAfter"] == expected_retry
    assert public["automaticRetryAllowed"] is True
    assert public["reservedTaskId"] == "task_reserved"


def test_external_unknown_projection_is_explicit_and_never_claims_running() -> None:
    from app.modules.research_start_repository import _session_public

    public = _session_public(
        {
            "session_id": "intent_123456789abc",
            "state": "external_result_unknown",
            "reserved_task_id": "task_reserved",
            "result_payload": None,
            "user_message": "Result cannot be verified.",
            "request_state": "unknown",
            "attempt_state": "unknown",
            "attempt_phase": "research_planner",
            "attempt_ordinal": 1,
            "elapsed_seconds": 90,
        },
        attempt_phase="research_planner",
    )

    assert public["status"] == "external_result_unknown"
    assert public["planningProgress"]["code"] == "external_result_unknown"
    assert public["recoveryReason"] == "external_result_unknown"
    assert public["automaticRetryAllowed"] is False
    assert "retryAfter" not in public


def test_external_unknown_has_no_automatic_replay_entrypoint() -> None:
    import inspect

    from app.modules.research_start_repository import ResearchStartRepository

    signature = inspect.signature(ResearchStartRepository.retry_external_result_unknown)
    assert "automatic" not in signature.parameters


def test_committed_design_receipt_requires_scoped_task_and_exact_request_lineage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import types

    from app.models.research_orchestration import (
        CoverageStatus,
        PlanningAttemptStatus,
        ResearchDesignStatus,
        ResearchTaskV12,
    )
    from app.modules import research_orchestration_repository as orchestration_repository_module
    from app.modules.research_start_repository import ResearchStartRepository

    task = types.SimpleNamespace(
        task_id="task_reserved",
        planning_attempts=[
            types.SimpleNamespace(
                request_id="rsr_test",
                status=PlanningAttemptStatus.COMPLETED,
                planner_provider_call_ref="provider-call-1",
                planning_attempt_id="planning-1",
            )
        ],
        research_design_versions=[
            types.SimpleNamespace(
                version=2,
                research_design_id="design-2",
                status=ResearchDesignStatus.DRAFT,
                coverage_report=types.SimpleNamespace(
                    planning_attempt_id="planning-1",
                    status=CoverageStatus.PASSED_SEMANTIC,
                ),
            )
        ],
    )
    monkeypatch.setattr(
        ResearchTaskV12,
        "model_validate",
        classmethod(lambda _cls, _payload: task),
    )
    monkeypatch.setattr(orchestration_repository_module, "_extract_contract_payload", lambda payload: payload)

    class Cursor:
        def __init__(self) -> None:
            self.params: tuple[str, str, str] | None = None

        def execute(self, _sql: str, params: tuple[str, str, str]) -> None:
            self.params = params

        def fetchone(self):
            if self.params == ("task_reserved", "owner", "workspace"):
                return {"payload": {"taskId": "task_reserved"}}
            return None

    session = {
        "session_id": "intent_123456789abc",
        "reserved_task_id": "task_reserved",
        "owner_id": "owner",
        "workspace_id": "workspace",
    }
    request = {"request_id": "rsr_test"}
    attempt = {"phase": "research_planner", "ordinal": 1, "state": "started"}
    cursor = Cursor()

    receipt = ResearchStartRepository._committed_design_receipt(cursor, session, request, attempt)

    assert receipt == {
        "providerCallRef": "provider-call-1",
        "taskId": "task_reserved",
        "researchDesignRef": {"researchDesignId": "design-2", "version": 2},
        "researchDesignStatus": "draft",
        "reconciledFromCommittedDesign": True,
    }
    assert cursor.params == ("task_reserved", "owner", "workspace")

    stale_scope = {**session, "owner_id": "other-owner"}
    assert ResearchStartRepository._committed_design_receipt(Cursor(), stale_scope, request, attempt) is None
    wrong_request = {"request_id": "rsr_other"}
    assert ResearchStartRepository._committed_design_receipt(Cursor(), session, wrong_request, attempt) is None


@pytest.mark.parametrize("terminal_status", ("cancelled", "superseded"))
def test_committed_design_receipt_rejects_latest_terminal_design_without_falling_back(
    monkeypatch: pytest.MonkeyPatch,
    terminal_status: str,
) -> None:
    import types

    from app.models.research_orchestration import (
        CoverageStatus,
        PlanningAttemptStatus,
        ResearchDesignStatus,
        ResearchTaskV12,
    )
    from app.modules import research_orchestration_repository as orchestration_repository_module
    from app.modules.research_start_repository import ResearchStartRepository

    task = types.SimpleNamespace(
        task_id="task_reserved",
        planning_attempts=[
            types.SimpleNamespace(
                request_id="rsr_test",
                status=PlanningAttemptStatus.COMPLETED,
                planner_provider_call_ref="provider-call-1",
                planning_attempt_id="planning-1",
            )
        ],
        research_design_versions=[
            types.SimpleNamespace(
                version=1,
                research_design_id="design-1",
                status=ResearchDesignStatus.DRAFT,
                coverage_report=types.SimpleNamespace(
                    planning_attempt_id="planning-1",
                    status=CoverageStatus.PASSED_SEMANTIC,
                ),
            ),
            types.SimpleNamespace(
                version=2,
                research_design_id="design-2",
                status=terminal_status,
                coverage_report=types.SimpleNamespace(
                    planning_attempt_id="planning-1",
                    status=CoverageStatus.PASSED_SEMANTIC,
                ),
            ),
        ],
    )
    monkeypatch.setattr(ResearchTaskV12, "model_validate", classmethod(lambda _cls, _payload: task))
    monkeypatch.setattr(orchestration_repository_module, "_extract_contract_payload", lambda payload: payload)

    class Cursor:
        def execute(self, _sql: str, _params: object) -> None:
            pass

        def fetchone(self):
            return {"payload": {"taskId": "task_reserved"}}

    receipt = ResearchStartRepository._committed_design_receipt(
        Cursor(),
        {
            "session_id": "intent_123456789abc",
            "reserved_task_id": "task_reserved",
            "owner_id": "owner",
            "workspace_id": "workspace",
        },
        {"request_id": "rsr_test"},
        {"phase": "research_planner", "ordinal": 1, "state": "started"},
    )

    assert receipt is None


def test_committed_design_receipt_accepts_confirmed_design_with_explicit_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import types

    from app.models.research_orchestration import (
        CoverageStatus,
        PlanningAttemptStatus,
        ResearchDesignStatus,
        ResearchTaskV12,
    )
    from app.modules import research_orchestration_repository as orchestration_repository_module
    from app.modules.research_start_repository import ResearchStartRepository

    task = types.SimpleNamespace(
        task_id="task_reserved",
        planning_attempts=[
            types.SimpleNamespace(
                request_id="rsr_test",
                status=PlanningAttemptStatus.COMPLETED,
                planner_provider_call_ref="provider-call-1",
                planning_attempt_id="planning-1",
            )
        ],
        research_design_versions=[
            types.SimpleNamespace(
                version=3,
                research_design_id="design-3",
                status=ResearchDesignStatus.CONFIRMED,
                coverage_report=types.SimpleNamespace(
                    planning_attempt_id="planning-1",
                    status=CoverageStatus.PASSED_SEMANTIC,
                ),
            )
        ],
    )
    monkeypatch.setattr(ResearchTaskV12, "model_validate", classmethod(lambda _cls, _payload: task))
    monkeypatch.setattr(orchestration_repository_module, "_extract_contract_payload", lambda payload: payload)

    class Cursor:
        def execute(self, _sql: str, _params: object) -> None:
            pass

        def fetchone(self):
            return {"payload": {"taskId": "task_reserved"}}

    receipt = ResearchStartRepository._committed_design_receipt(
        Cursor(),
        {
            "session_id": "intent_123456789abc",
            "reserved_task_id": "task_reserved",
            "owner_id": "owner",
            "workspace_id": "workspace",
        },
        {"request_id": "rsr_test"},
        {"phase": "research_planner", "ordinal": 1, "state": "started"},
    )

    assert receipt is not None
    assert receipt["researchDesignStatus"] == "confirmed"
    assert receipt["researchDesignRef"] == {"researchDesignId": "design-3", "version": 3}


def test_expired_succeeded_attempt_without_receipt_closes_unknown_without_rewriting_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.modules.research_start_repository import ResearchStartRepository

    session = {
        "session_id": "intent_123456789abc",
        "owner_id": "owner",
        "workspace_id": "workspace",
        "state": "running",
        "reserved_task_id": "task_reserved",
    }
    request = {
        "request_id": "rsr_test",
        "session_id": session["session_id"],
        "owner_id": "owner",
        "workspace_id": "workspace",
        "state": "leased",
    }
    attempt = {
        "attempt_id": "rsa_test",
        "request_id": "rsr_test",
        "phase": "research_planner",
        "ordinal": 1,
        "state": "succeeded",
        "fence_epoch": 1,
    }

    class Cursor:
        def __init__(self) -> None:
            self.current_sql = ""
            self.rowcount = 0
            self.session_reads = 0
            self.attempt_updates = 0

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, sql: str, _params: object = None) -> None:
            self.current_sql = " ".join(sql.split())
            if "UPDATE research_start_attempts" in self.current_sql:
                self.attempt_updates += 1
            self.rowcount = 1

        def fetchone(self):
            if "FROM research_start_sessions s" in self.current_sql:
                self.session_reads += 1
                return session if self.session_reads == 1 else None
            if "SELECT request_id FROM research_start_requests" in self.current_sql:
                return {"request_id": "rsr_test"}
            if "FROM research_start_attempts" in self.current_sql:
                return attempt
            return None

    cursor = Cursor()
    repository = ResearchStartRepository(connection_factory=lambda: _Connection(cursor))
    monkeypatch.setattr(repository, "_lock_request", lambda *_args, **_kwargs: request)
    monkeypatch.setattr(repository, "_committed_design_receipt", lambda *_args, **_kwargs: None)

    result = repository.reconcile_expired_leases()

    assert result == {"reclaimed": 0, "unknown": 1, "reconciled": 0}
    assert cursor.attempt_updates == 0


def test_committed_design_lineage_raises_to_rollback_on_partial_close() -> None:
    from app.modules.research_start_repository import LeaseLostError, ResearchStartRepository

    class Cursor:
        def __init__(self) -> None:
            self.rowcount = 0
            self.calls: list[str] = []

        def execute(self, sql: str, _params: object = None) -> None:
            self.calls.append(sql)
            if "UPDATE research_start_attempts" in sql:
                self.rowcount = 1
            elif "UPDATE research_start_requests" in sql:
                self.rowcount = 0
            else:
                self.rowcount = 1

        def fetchone(self):
            return None

    cursor = Cursor()
    with pytest.raises(LeaseLostError):
        ResearchStartRepository._complete_committed_design_lineage(
            cursor,
            {
                "session_id": "intent_123456789abc",
                "owner_id": "owner",
                "workspace_id": "workspace",
                "reserved_task_id": "task_reserved",
            },
            {
                "request_id": "rsr_test",
                "session_id": "intent_123456789abc",
                "owner_id": "owner",
                "workspace_id": "workspace",
            },
            {
                "attempt_id": "rsa_test",
                "state": "started",
                "fence_epoch": 1,
            },
            {"providerCallRef": "provider-call-1"},
            worker_id="worker",
            lease_epoch=1,
            require_expired_lease=False,
            user_message="Research design is ready.",
        )

    assert any("UPDATE research_start_attempts" in sql for sql in cursor.calls)
    assert any("UPDATE research_start_requests" in sql for sql in cursor.calls)
    assert not any("UPDATE research_start_sessions" in sql for sql in cursor.calls)


class _AtomicFinalizeCursor:
    def __init__(self, attempt: dict[str, object]) -> None:
        self.attempt = attempt
        self.current_sql = ""
        self.calls: list[tuple[str, object]] = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql: str, params: object = None) -> None:
        self.current_sql = " ".join(sql.split())
        self.calls.append((self.current_sql, params))

    def fetchone(self):
        if "FROM research_start_attempts" in self.current_sql:
            return self.attempt
        return None


class _AtomicFinalizeConnection:
    def __init__(self, cursor: _AtomicFinalizeCursor) -> None:
        self._cursor = cursor
        self.commits = 0
        self.rollbacks = 0
        self.closed = 0

    def cursor(self):
        return self._cursor

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed += 1


def test_authoritative_finalize_validates_and_completes_on_one_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.modules.research_start_repository import ResearchStartRepository

    attempt = {
        "attempt_id": "rsa_test",
        "request_id": "rsr_test",
        "phase": "research_planner",
        "ordinal": 1,
        "state": "succeeded",
        "fence_epoch": 7,
        "result_payload": {"providerCallRef": "provider:shared"},
    }
    cursor = _AtomicFinalizeCursor(attempt)
    connection = _AtomicFinalizeConnection(cursor)
    repository = ResearchStartRepository(connection_factory=lambda: connection)
    events: list[tuple[str, object]] = []
    session = {
        "session_id": "intent_123456789abc",
        "owner_id": "owner",
        "workspace_id": "workspace",
        "state": "running",
        "reserved_task_id": "task_reserved",
    }
    request = {
        "request_id": "rsr_test",
        "session_id": session["session_id"],
        "owner_id": "owner",
        "workspace_id": "workspace",
        "state": "leased",
        "lease_owner": "worker",
        "lease_epoch": 7,
        "lease_is_live": True,
    }
    receipt = {
        "providerCallRef": "provider:shared",
        "taskId": "task_reserved",
        "researchDesignRef": {"researchDesignId": "design-1", "version": 1},
        "researchDesignStatus": "draft",
        "reconciledFromCommittedDesign": True,
    }

    def lock_path(cur, request_id):
        events.append(("lock_path", cur))
        assert request_id == "rsr_test"
        return session, request

    def require_live(locked_session, locked_request, worker_id, lease_epoch):
        events.append(("live_lease", (locked_session, locked_request)))
        assert worker_id == "worker" and lease_epoch == 7

    def committed(cur, locked_session, locked_request, locked_attempt):
        events.append(("receipt", cur))
        assert locked_attempt == attempt
        return receipt

    def complete(cur, locked_session, locked_request, locked_attempt, locked_receipt, **kwargs):
        events.append(("complete", cur))
        assert locked_receipt is receipt
        assert kwargs["require_expired_lease"] is False
        return {"status": "completed", "reservedTaskId": "task_reserved"}

    monkeypatch.setattr(repository, "_lock_path", lock_path)
    monkeypatch.setattr(repository, "_require_live_lease", require_live)
    monkeypatch.setattr(repository, "_committed_design_receipt", committed)
    monkeypatch.setattr(repository, "_complete_committed_design_lineage", complete)

    result = repository.finalize_from_authoritative_receipt(
        "rsr_test",
        "worker",
        7,
        user_message="ready",
    )

    assert result["finalized"] is True
    assert result["receipt"] == receipt
    assert [name for name, _ in events] == ["lock_path", "live_lease", "receipt", "complete"]
    assert events[0][1] is events[2][1] is events[3][1] is cursor
    assert connection.commits == 1 and connection.rollbacks == 0 and connection.closed == 1


def test_authoritative_finalize_provider_receipt_mismatch_never_completes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.modules.research_start_repository import ResearchStartRepository

    attempt = {
        "attempt_id": "rsa_test",
        "request_id": "rsr_test",
        "phase": "research_planner",
        "ordinal": 1,
        "state": "succeeded",
        "fence_epoch": 1,
        "result_payload": {"providerCallRef": "provider:A"},
    }
    cursor = _AtomicFinalizeCursor(attempt)
    connection = _AtomicFinalizeConnection(cursor)
    repository = ResearchStartRepository(connection_factory=lambda: connection)
    session = {
        "session_id": "intent_123456789abc",
        "owner_id": "owner",
        "workspace_id": "workspace",
        "state": "running",
        "reserved_task_id": "task_reserved",
    }
    request = {
        "request_id": "rsr_test",
        "session_id": session["session_id"],
        "owner_id": "owner",
        "workspace_id": "workspace",
        "state": "leased",
        "lease_owner": "worker",
        "lease_epoch": 1,
        "lease_is_live": True,
    }

    monkeypatch.setattr(repository, "_lock_path", lambda *_args: (session, request))
    monkeypatch.setattr(repository, "_require_live_lease", lambda *_args: None)
    # The real receipt validator returns None when the succeeded attempt says
    # provider:A but the Task PlanningAttempt says provider:B.
    monkeypatch.setattr(repository, "_committed_design_receipt", lambda *_args: None)
    monkeypatch.setattr(
        repository,
        "_complete_committed_design_lineage",
        lambda *_args, **_kwargs: pytest.fail("mismatched provider receipt must not complete"),
    )
    closed: list[str] = []

    def close_failure(*_args, **kwargs):
        closed.append(kwargs["reason"])
        return {"status": "failed", "failureCode": "provider_contract_invalid"}

    monkeypatch.setattr(repository, "_close_authoritative_finalize_failure", close_failure)

    result = repository.finalize_from_authoritative_receipt(
        "rsr_test",
        "worker",
        1,
        user_message="ready",
    )

    assert result == {
        "finalized": False,
        "closed": True,
        "reason": "authoritative_receipt_unavailable",
        "session": {"status": "failed", "failureCode": "provider_contract_invalid"},
    }
    assert closed == ["authoritative_receipt_unavailable"]
    assert attempt["result_payload"] == {"providerCallRef": "provider:A"}
    assert connection.commits == 1 and connection.rollbacks == 0


def test_stale_failure_delegates_to_canonical_design_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A late legacy clarification has no authority over a ready Design."""
    from app.modules.research_start_repository import ResearchStartRepository

    attempt = {
        "attempt_id": "rsa_test",
        "request_id": "rsr_test",
        "phase": "research_planner",
        "ordinal": 1,
        "state": "succeeded",
        "fence_epoch": 7,
        "result_payload": {"providerCallRef": "provider:shared"},
    }
    cursor = _AtomicFinalizeCursor(attempt)
    connection = _AtomicFinalizeConnection(cursor)
    repository = ResearchStartRepository(connection_factory=lambda: connection)
    session = {
        "session_id": "intent_123456789abc",
        "owner_id": "owner",
        "workspace_id": "workspace",
        "state": "running",
        "reserved_task_id": "task_reserved",
    }
    request = {
        "request_id": "rsr_test",
        "session_id": session["session_id"],
        "owner_id": "owner",
        "workspace_id": "workspace",
        "state": "leased",
        "lease_owner": "worker",
        "lease_epoch": 7,
        "lease_is_live": True,
    }
    receipt = {
        "providerCallRef": "provider:shared",
        "taskId": "task_reserved",
        "researchDesignRef": {"researchDesignId": "design-1", "version": 1},
        "researchDesignStatus": "draft",
        "reconciledFromCommittedDesign": True,
    }
    completed: list[dict[str, object]] = []

    monkeypatch.setattr(repository, "_lock_path", lambda *_args: (session, request))
    monkeypatch.setattr(repository, "_require_live_lease", lambda *_args: None)
    monkeypatch.setattr(repository, "_committed_design_receipt", lambda *_args: receipt)
    monkeypatch.setattr(
        repository,
        "_complete_committed_design_lineage",
        lambda *_args, **kwargs: completed.append(kwargs) or {"status": "completed", "reservedTaskId": "task_reserved"},
    )
    monkeypatch.setattr(repository, "_assert_no_started_attempt", lambda *_args: pytest.fail("must not downgrade ready Design"))
    monkeypatch.setattr(repository, "_mark_reserved_planning_task", lambda *_args, **_kwargs: pytest.fail("must not fail ready Design"))

    result = repository.fail_request(
        "rsr_test", "worker", 7,
        failure_code="needs_clarification",
        user_message="stale clarification",
    )

    assert result == {"status": "completed", "reservedTaskId": "task_reserved"}
    assert len(completed) == 1
    assert completed[0]["require_expired_lease"] is False
    assert not any("UPDATE research_start_sessions" in sql for sql, _ in cursor.calls)


def test_worker_has_no_direct_task_only_research_design_success_exit() -> None:
    from app.execution.research_start_worker_runtime import _run_research_design_planner
    from app.modules.research_start_repository import ResearchStartRepository

    worker_source = inspect.getsource(_run_research_design_planner)
    compatibility_source = inspect.getsource(ResearchStartRepository.complete_existing_task_request)

    assert "complete_existing_task_request(" not in worker_source
    assert "finalize_from_authoritative_receipt" in worker_source
    assert "_finalization_operations" in compatibility_source


def test_planning_sources_are_exact_versioned_intent_and_empty_list_is_backward_compatible() -> None:
    from app.modules.research_start_repository import InvalidIntentError, canonical_fingerprint

    base = _intent()
    without_sources = canonical_fingerprint(base)
    assert without_sources == canonical_fingerprint({**base, "planningSources": []})

    source_v1 = {
        **base,
        "planningSources": [
            {"kind": "research_package", "id": "rp-method-alpha", "version": "1.0"}
        ],
    }
    source_v2 = {
        **base,
        "planningSources": [
            {"kind": "research_package", "id": "rp-method-alpha", "version": "2.0"}
        ],
    }
    assert canonical_fingerprint(source_v1) != without_sources
    assert canonical_fingerprint(source_v1) != canonical_fingerprint(source_v2)

    with pytest.raises(InvalidIntentError):
        canonical_fingerprint(
            {
                **base,
                "planningSources": [
                    {"kind": "research_package", "id": "rp-method-alpha", "version": "2.0"},
                    {"kind": "research_package", "id": "rp-method-alpha", "version": "2.0"},
                ],
            }
        )
