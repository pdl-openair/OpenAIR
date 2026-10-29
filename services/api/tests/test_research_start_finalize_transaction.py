from __future__ import annotations

import pytest
import json
import re


def test_atomic_research_start_finalize_repository_contract_is_available() -> None:
    from app.modules.artifact_repository import ArtifactRepository
    from app.modules.research_start_repository import ResearchStartRepository
    from app.modules.task_repository import TaskRepository

    assert callable(ResearchStartRepository.finalize_task_request)
    assert callable(TaskRepository.insert_task_with_cursor)
    assert callable(ArtifactRepository.insert_artifact_with_cursor)


def test_pure_builder_replaces_every_temporary_identity_with_reserved_ids() -> None:
    from app.modules.research_start_planner import _deterministic_research_start_plan
    from app.modules.research_start_task import build_research_start_draft_task_payload

    payload = build_research_start_draft_task_payload(
        task_id="task_reserved",
        artifact_id="artifact_reserved",
        owner_id="owner_1",
        workspace_id="workspace_1",
        created_by="owner_1",
        goal_summary="Compare two methods",
        planner_plan_override=_deterministic_research_start_plan(raw_input="Compare two methods"),
    )
    assert payload["task"]["taskId"] == "task_reserved"
    assert payload["artifact"]["id"] == "artifact_reserved"
    assert payload["artifact"]["sourceTaskId"] == "task_reserved"
    assert payload["task"]["artifacts"] == [payload["artifact"]]
    assert payload["task"]["stages"] == [
        "problem_modeling", "solution_design", "execution_validation", "insight_generation"
    ]
    assert [row["stageKey"] for row in payload["task"]["stageDetails"]] == payload["task"]["stages"]

    def assert_no_temporary_reserved_identity(value: object) -> None:
        if isinstance(value, dict):
            for child in value.values():
                assert_no_temporary_reserved_identity(child)
        elif isinstance(value, list):
            for child in value:
                assert_no_temporary_reserved_identity(child)
        elif isinstance(value, str):
            assert re.fullmatch(r"(?:task|artifact)_[0-9a-f]{12}", value) is None

    assert_no_temporary_reserved_identity(payload)
    from app.modules.artifact_repository import _row_to_dict

    restored = _row_to_dict(_artifact_row(payload["artifact"]))
    mismatches = {
        key: (value, restored.get(key))
        for key, value in payload["artifact"].items()
        if restored.get(key) != value
    }
    assert mismatches == {}


def test_artifact_cursor_mapping_preserves_research_start_metadata() -> None:
    from app.modules.artifact_repository import ArtifactRepository, _row_to_dict

    artifact = _reserved_payload()[1]
    artifact.update({
        "structuredResearchStart": {"research_goal": "goal"},
        "researchStartConfirmation": {"status": "unconfirmed"},
        "missingInputs": ["materials"],
        "nonCitableBoundaries": ["no sources"],
        "downstreamInput": {"status": "blocked_by_confirmation"},
    })
    metadata = json.loads(ArtifactRepository._artifact_insert_params("task_reserved", artifact)["metadata"])
    for key in ("structuredResearchStart", "researchStartConfirmation", "missingInputs", "nonCitableBoundaries", "downstreamInput"):
        assert metadata[key] == artifact[key]
    params = ArtifactRepository._artifact_insert_params("task_reserved", artifact)
    row = {key: value for key, value in params.items() if key != "metadata"}
    row["metadata"] = params["metadata"]
    restored = _row_to_dict(row)
    for key in ("structuredResearchStart", "researchStartConfirmation", "missingInputs", "nonCitableBoundaries", "downstreamInput"):
        assert restored[key] == artifact[key]


class _Cursor:
    rowcount = 1

    def __init__(self, attempt_rows: list[dict[str, object]] | None = None) -> None:
        self.attempt_rows = attempt_rows or []

    def __enter__(self):
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def execute(self, *_args: object) -> None:
        return None

    def fetchall(self) -> list[dict[str, object]]:
        return self.attempt_rows

    def fetchone(self) -> dict[str, object]:
        return {
            "session_id": "intent_reserved",
            "state": "completed",
            "result_payload": {"outcome": "message"},
            "user_message": "disguised",
        }


class _Connection:
    def __init__(self, cursor: _Cursor | None = None) -> None:
        self.cursor_instance = cursor or _Cursor()
        self.commits = 0
        self.rollbacks = 0
        self.closes = 0

    def cursor(self) -> _Cursor:
        return self.cursor_instance

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1

    def close(self) -> None:
        self.closes += 1


class _SequenceCursor(_Cursor):
    def __init__(self, rows: list[dict[str, object] | None]) -> None:
        super().__init__()
        self.rows = list(rows)

    def fetchone(self) -> dict[str, object] | None:
        return self.rows.pop(0) if self.rows else None


def _reserved_payload() -> tuple[dict[str, object], dict[str, object], dict[str, str]]:
    artifact = {
        "id": "artifact_reserved",
        "taskId": "task_reserved",
        "sourceTaskId": "task_reserved",
        "ownerId": "owner_1",
        "workspaceId": "workspace_1",
        "createdBy": "owner_1",
        "kind": "research_start_draft",
        "origin": "generated",
        "storageType": "inline",
        "inlineContent": "draft",
        "sourceCapabilityCallId": None,
        "artifactState": "needs_review",
        "createdAt": "2026-07-25T08:30:00Z",
        "structuredResearchStart": {"research_goal": "goal"},
        "researchStartConfirmation": {"status": "unconfirmed"},
        "missingInputs": [],
        "nonCitableBoundaries": ["no sources"],
        "downstreamInput": {"status": "blocked_by_confirmation"},
    }
    task = {
        "taskId": "task_reserved",
        "ownerId": "owner_1",
        "workspaceId": "workspace_1",
        "createdBy": "owner_1",
        "domain": "research_task",
        "status": "draft",
        "creationStatus": "awaiting_confirmation",
        "resultSource": "live",
        "currentStage": "problem_modeling",
        "title": "Research",
        "createdAt": "2026-07-25T08:30:00Z",
        "stages": ["problem_modeling", "solution_design", "execution_validation", "insight_generation"],
        "stageDetails": [
            {"stageKey": "problem_modeling", "artifactRefs": ["artifact_reserved"]},
            {"stageKey": "solution_design", "artifactRefs": []},
            {"stageKey": "execution_validation", "artifactRefs": []},
            {"stageKey": "insight_generation", "artifactRefs": []},
        ],
        "artifacts": [artifact],
    }
    return task, artifact, {"outcome": "research_task", "sessionId": "intent_reserved", "taskId": "task_reserved"}


def test_complete_request_uses_persisted_planner_authority_even_when_result_is_disguised(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.modules.research_start_repository import ResearchStartRepository, TaskFinalizeRequiredError

    cursor = _Cursor([{"phase": "research_planner", "ordinal": 1, "state": "succeeded", "result_payload": {"plan": "known"}}])
    repository = ResearchStartRepository(connection_factory=lambda: _Connection(cursor))
    session = {"session_id": "intent_reserved", "owner_id": "owner_1", "workspace_id": "workspace_1", "state": "running"}
    request = {"session_id": "intent_reserved", "owner_id": "owner_1", "workspace_id": "workspace_1", "state": "leased", "lease_owner": "worker_1", "lease_epoch": 1, "lease_is_live": True}
    monkeypatch.setattr(repository, "_lock_path", lambda _cur, _request_id: (session, request))
    monkeypatch.setattr(repository, "_assert_no_started_attempt", lambda *_args: None)
    with pytest.raises(TaskFinalizeRequiredError):
        repository.complete_request(
            "request_1", "worker_1", 1, result={"outcome": "message"}, user_message="disguised"
        )


@pytest.mark.parametrize("planner_result", [
    {"userOutcome": "research_task"},
    {"outcome": "research_task"},
    {"shouldCreateTask": True},
])
def test_complete_request_uses_persisted_planner_task_authority(planner_result: dict[str, object], monkeypatch: pytest.MonkeyPatch) -> None:
    from app.modules.research_start_repository import ResearchStartRepository, TaskFinalizeRequiredError

    cursor = _Cursor([{"phase": "research_planner", "ordinal": 2, "state": "succeeded", "result_payload": planner_result}])
    repository = ResearchStartRepository(connection_factory=lambda: _Connection(cursor))
    session = {"session_id": "intent_reserved", "owner_id": "owner_1", "workspace_id": "workspace_1", "state": "running"}
    request = {"session_id": "intent_reserved", "owner_id": "owner_1", "workspace_id": "workspace_1", "state": "leased", "lease_owner": "worker_1", "lease_epoch": 1, "lease_is_live": True}
    monkeypatch.setattr(repository, "_lock_path", lambda _cur, _request_id: (session, request))
    monkeypatch.setattr(repository, "_assert_no_started_attempt", lambda *_args: None)
    with pytest.raises(TaskFinalizeRequiredError):
        repository.complete_request("request_1", "worker_1", 1, result={"outcome": "message"}, user_message="disguised")


def test_finalize_attempt_validation_allows_failed_history_and_any_succeeded_ordinal() -> None:
    from app.modules.research_start_repository import ResearchStartRepository

    cursor = _Cursor([
        {"phase": "research_planner", "ordinal": 1, "state": "succeeded"},
        {"phase": "research_planner", "ordinal": 1, "state": "failed"},
        {"phase": "research_planner", "ordinal": 2, "state": "succeeded"},
    ])
    ResearchStartRepository._assert_exact_succeeded_attempts(cursor, "request_1")


@pytest.mark.parametrize("bad_state", ["started", "unknown"])
def test_finalize_attempt_validation_rejects_nonterminal_or_unknown_attempts(bad_state: str) -> None:
    from app.modules.research_start_repository import FinalizeInvariantBrokenError, ResearchStartRepository

    cursor = _Cursor([
        {"phase": "research_planner", "ordinal": 1, "state": "succeeded"},
        {"phase": "research_planner", "ordinal": 2, "state": "succeeded"},
        {"phase": "research_planner", "ordinal": 3, "state": bad_state},
    ])
    with pytest.raises(FinalizeInvariantBrokenError):
        ResearchStartRepository._assert_exact_succeeded_attempts(cursor, "request_1")


def test_finalize_rejects_a_task_without_the_exact_four_stage_projection() -> None:
    from app.modules.research_start_repository import FinalizeInvariantBrokenError, ResearchStartRepository

    task, artifact, result = _reserved_payload()
    task["stages"] = ["problem_modeling"]
    task["stageDetails"] = [{"stageKey": "problem_modeling", "artifactRefs": ["artifact_reserved"]}]
    session = {
        "session_id": "intent_reserved", "owner_id": "owner_1", "workspace_id": "workspace_1",
        "reserved_task_id": "task_reserved", "reserved_artifact_id": "artifact_reserved",
    }
    with pytest.raises(FinalizeInvariantBrokenError):
        ResearchStartRepository._assert_finalize_payload(session, task, artifact, result)


def test_fresh_finalize_rejects_missing_created_at_on_task_and_artifact() -> None:
    from app.modules.research_start_repository import FinalizeInvariantBrokenError, ResearchStartRepository

    task, artifact, result = _reserved_payload()
    task.pop("createdAt", None)
    artifact.pop("createdAt", None)
    session = {
        "session_id": "intent_reserved", "owner_id": "owner_1", "workspace_id": "workspace_1",
        "reserved_task_id": "task_reserved", "reserved_artifact_id": "artifact_reserved",
    }
    with pytest.raises(FinalizeInvariantBrokenError):
        ResearchStartRepository._assert_finalize_payload(session, task, artifact, result)


def test_fresh_finalize_rejects_invalid_artifact_created_at() -> None:
    from app.modules.research_start_repository import FinalizeInvariantBrokenError, ResearchStartRepository

    task, artifact, result = _reserved_payload()
    artifact["createdAt"] = "not-an-instant"
    task["artifacts"] = [artifact]
    session = {
        "session_id": "intent_reserved", "owner_id": "owner_1", "workspace_id": "workspace_1",
        "reserved_task_id": "task_reserved", "reserved_artifact_id": "artifact_reserved",
    }
    with pytest.raises(FinalizeInvariantBrokenError):
        ResearchStartRepository._assert_finalize_payload(session, task, artifact, result)


def _terminal_authority() -> tuple[dict[str, object], dict[str, object], dict[str, str]]:
    result = {"outcome": "research_task", "sessionId": "intent_reserved", "taskId": "task_reserved"}
    session = {
        "session_id": "intent_reserved", "owner_id": "owner_1", "workspace_id": "workspace_1",
        "reserved_task_id": "task_reserved", "reserved_artifact_id": "artifact_reserved",
        "state": "completed", "result_payload": result,
    }
    request = {"state": "succeeded", "result_payload": result}
    return session, request, result


def _artifact_row(artifact: dict[str, object]) -> dict[str, object]:
    from app.modules.artifact_repository import ArtifactRepository

    params = ArtifactRepository._artifact_insert_params("task_reserved", artifact, "Research")
    return {key: value for key, value in params.items() if key != "metadata"} | {"metadata": json.loads(params["metadata"])}


def test_replay_uses_terminal_database_authority_and_ignores_new_call_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.modules.research_start_repository import ResearchStartRepository

    task, artifact, _caller_result = _reserved_payload()
    session, request, expected = _terminal_authority()
    cursor = _SequenceCursor([{"payload": task}, {"artifact_row": _artifact_row(artifact)}])
    repository = ResearchStartRepository(connection_factory=lambda: _Connection(cursor))
    monkeypatch.setattr(repository, "_lock_path", lambda _cur, _request_id: (session, request))
    assert repository.finalize_task_request(
        "request_1", "old-worker", 99, task={}, artifact={}, result={}, user_message="ignored"
    ) == expected


def test_replay_accepts_equivalent_utc_created_at_encodings(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.modules.research_start_repository import ResearchStartRepository

    task, artifact, _caller_result = _reserved_payload()
    artifact["createdAt"] = "2026-07-25T08:30:00Z"
    task["artifacts"] = [artifact]
    artifact_row = _artifact_row(artifact)
    artifact_row["created_at"] = "2026-07-25T08:30:00+00:00"
    session, request, expected = _terminal_authority()
    repository = ResearchStartRepository(connection_factory=lambda: _Connection(_SequenceCursor([
        {"payload": task}, {"artifact_row": artifact_row}
    ])))
    monkeypatch.setattr(repository, "_lock_path", lambda _cur, _request_id: (session, request))
    assert repository.finalize_task_request(
        "request_1", "old-worker", 99, task={}, artifact={}, result={}, user_message="ignored"
    ) == expected


def test_replay_rejects_invalid_created_at_even_when_both_rows_match(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.modules.research_start_repository import FinalizeInvariantBrokenError, ResearchStartRepository

    task, artifact, _caller_result = _reserved_payload()
    artifact["createdAt"] = "not-an-instant"
    task["artifacts"] = [artifact]
    artifact_row = _artifact_row(artifact)
    session, request, _expected = _terminal_authority()
    repository = ResearchStartRepository(connection_factory=lambda: _Connection(_SequenceCursor([
        {"payload": task}, {"artifact_row": artifact_row}
    ])))
    monkeypatch.setattr(repository, "_lock_path", lambda _cur, _request_id: (session, request))
    with pytest.raises(FinalizeInvariantBrokenError):
        repository.finalize_task_request(
            "request_1", "old-worker", 99, task={}, artifact={}, result={}, user_message="ignored"
        )


@pytest.mark.parametrize("request_state,session_state", [("succeeded", "running"), ("leased", "completed")])
def test_replay_rejects_partial_terminal_authority(
    request_state: str, session_state: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.modules.research_start_repository import FinalizeInvariantBrokenError, ResearchStartRepository

    task, artifact, result = _reserved_payload()
    session, request, _expected = _terminal_authority()
    session["state"] = session_state
    request["state"] = request_state
    repository = ResearchStartRepository(connection_factory=lambda: _Connection(_SequenceCursor([])))
    monkeypatch.setattr(repository, "_lock_path", lambda _cur, _request_id: (session, request))
    with pytest.raises(FinalizeInvariantBrokenError):
        repository.finalize_task_request(
            "request_1", "worker", 1, task=task, artifact=artifact, result=result, user_message="ready"
        )


@pytest.mark.parametrize("rows", [
    [],
    [{"payload": _reserved_payload()[0]}],
])
def test_replay_rejects_missing_persistent_task_or_artifact_rows(
    rows: list[dict[str, object]], monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.modules.research_start_repository import FinalizeInvariantBrokenError, ResearchStartRepository

    session, request, _expected = _terminal_authority()
    repository = ResearchStartRepository(connection_factory=lambda: _Connection(_SequenceCursor(rows)))
    monkeypatch.setattr(repository, "_lock_path", lambda _cur, _request_id: (session, request))
    with pytest.raises(FinalizeInvariantBrokenError):
        repository.finalize_task_request("request_1", "worker", 1, task={}, artifact={}, result={}, user_message="ready")


def test_replay_rejects_tampered_persistent_artifact(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.modules.research_start_repository import FinalizeInvariantBrokenError, ResearchStartRepository

    task, artifact, _result = _reserved_payload()
    tampered = _artifact_row(artifact)
    tampered["storage_ref"] = "tampered"
    session, request, _expected = _terminal_authority()
    repository = ResearchStartRepository(connection_factory=lambda: _Connection(_SequenceCursor([
        {"payload": task}, {"artifact_row": tampered}
    ])))
    monkeypatch.setattr(repository, "_lock_path", lambda _cur, _request_id: (session, request))
    with pytest.raises(FinalizeInvariantBrokenError):
        repository.finalize_task_request("request_1", "worker", 1, task={}, artifact={}, result={}, user_message="ready")


def test_finalize_rolls_back_the_single_connection_when_artifact_cursor_insert_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.modules.artifact_repository import ArtifactRepository
    from app.modules.research_start_repository import ResearchStartRepository
    from app.modules.task_repository import TaskRepository

    connection = _Connection()
    repository = ResearchStartRepository(connection_factory=lambda: connection)
    session = {
        "session_id": "intent_reserved", "owner_id": "owner_1", "workspace_id": "workspace_1",
        "reserved_task_id": "task_reserved", "reserved_artifact_id": "artifact_reserved", "state": "running",
    }
    request = {"state": "leased", "lease_owner": "worker_1", "lease_epoch": 1, "lease_is_live": True}
    monkeypatch.setattr(repository, "_lock_path", lambda _cur, _request_id: (session, request))
    monkeypatch.setattr(repository, "_assert_no_started_attempt", lambda *_args: None)
    monkeypatch.setattr(repository, "_assert_exact_succeeded_attempts", lambda *_args: None)
    seen: list[tuple[str, object]] = []

    def stage_task(_repository: object, cur: object, task: object) -> None:
        seen.append(("task", cur))
        assert isinstance(task, dict) and task["taskId"] == "task_reserved"

    monkeypatch.setattr(TaskRepository, "insert_task_with_cursor", stage_task)

    def fail_artifact_insert(_repository: object, cur: object, *_args: object) -> None:
        seen.append(("artifact", cur))
        raise RuntimeError("artifact cursor failure")

    monkeypatch.setattr(ArtifactRepository, "insert_artifact_with_cursor", fail_artifact_insert)
    task, artifact, result = _reserved_payload()
    with pytest.raises(RuntimeError, match="artifact cursor failure"):
        repository.finalize_task_request(
            "request_1", "worker_1", 1, task=task, artifact=artifact, result=result, user_message="ready"
        )
    assert connection.commits == 0
    assert connection.rollbacks == 1
    assert connection.closes == 1
    assert seen == [("task", connection.cursor_instance), ("artifact", connection.cursor_instance)]
