"""PostgreSQL integration coverage for the public ResearchDesign lifecycle."""

from __future__ import annotations

import json
import os
from copy import deepcopy
from pathlib import Path
from urllib.parse import urlparse

import psycopg
from psycopg.rows import dict_row
import pytest

from app.config import settings
from app.models.research_orchestration import ResearchTaskV12
from app.modules.research_design_service import (
    ConfirmationIdentityConflictError,
    PlannerProviderResult,
    PlannerTransportUnknown,
    PlanningAttemptReplayError,
    ResearchDesignService,
    ResolvedDefinition,
    new_research_task,
)
from app.modules.research_orchestration_repository import (
    ResearchOrchestrationRepository,
    ResearchTaskNotFoundError,
    TaskRevisionConflictError,
)


CASES = json.loads(
    (Path(__file__).parents[4] / "tests" / "fixtures" / "research_design_v1_cases.json").read_text(encoding="utf-8")
)
SNAPSHOT = {"schemaVersion": 1, "contentHash": "sha256:" + "d" * 64}








def _bootstrap(dsn: str) -> None:
    from tests.integration._production_postgres_bootstrap import bootstrap_production_postgres_schema

    with psycopg.connect(dsn, row_factory=dict_row) as connection:
        with connection.cursor() as cursor:
            bootstrap_production_postgres_schema(cursor)




@pytest.fixture(scope="session")
def postgres_environment():
    dsn = os.environ.get("OPENAIR_TEST_POSTGRES_DSN", "").strip()
    if not dsn:
        pytest.skip("OPENAIR_TEST_POSTGRES_DSN is required for the PostgreSQL lifecycle integration test")

    parsed = urlparse(dsn)
    database = parsed.path.lstrip("/")
    if parsed.scheme not in {"postgres", "postgresql"} or not parsed.hostname or not database:
        pytest.fail("OPENAIR_TEST_POSTGRES_DSN must be a complete PostgreSQL DSN")

    database_lower = database.lower()
    forbidden_names = {"openair", "postgres", "template0", "template1"}
    forbidden_fragments = ("prod", "production", "staging", "develop", "development")
    if database_lower in forbidden_names or any(part in database_lower for part in forbidden_fragments):
        pytest.fail("refusing ResearchDesign integration test against a non-test PostgreSQL database")
    if "test" not in database_lower and "qa" not in database_lower:
        pytest.fail("ResearchDesign integration database name must contain 'test' or 'qa'")

    _bootstrap(dsn)

    def connect():
        return psycopg.connect(dsn, row_factory=dict_row)

    yield dsn, connect



@pytest.fixture(autouse=True)
def clean_rows(postgres_environment):
    _dsn, connect = postgres_environment
    with connect() as connection:
        connection.execute("DELETE FROM openair_artifacts")
        connection.execute("DELETE FROM openair_dispatch")
        connection.execute("DELETE FROM openair_tasks")
    yield
    with connect() as connection:
        connection.execute("DELETE FROM openair_artifacts")
        connection.execute("DELETE FROM openair_dispatch")
        connection.execute("DELETE FROM openair_tasks")


class FixedDefinitionSource:
    def snapshot_ref(self):
        return deepcopy(SNAPSHOT)

    def resolve(self, definition_id: str):
        definitions = {
            "sdr:evidence_synthesis": (1, "solution_design"),
            "sdr:source_collection": (3, "problem_modeling"),
            "sdr:perf_node": (7, "execution_validation"),
        }
        value = definitions.get(definition_id)
        if value is None:
            return None
        return ResolvedDefinition(
            definition_id=definition_id,
            definition_version=value[0],
            stage_key=value[1],
        )

    def definition_is_eligible_for_workflow_admission(self, definition_id, workflow_admission):
        return definition_id in {
            "sdr:evidence_synthesis",
            "sdr:source_collection",
            "sdr:perf_node",
        }


@pytest.fixture()
def repository(monkeypatch: pytest.MonkeyPatch, postgres_environment):
    _dsn, connect = postgres_environment
    monkeypatch.setattr(settings, "research_orchestration_v1", True)
    monkeypatch.setattr(settings, "persistence_backend", "postgres")
    return ResearchOrchestrationRepository(
        connection_factory=connect,
        runtime_settings=settings,
    ), connect


def _insert_task(connect, task: ResearchTaskV12, *, owner: str = "owner", workspace: str = "workspace") -> None:
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
                "problem_modeling",
                f"T02 {task.task_id}",
                "research_task",
                json.dumps(payload),
                task.revision,
            ),
        )


def _service(repository) -> ResearchDesignService:
    repository.load_registry_snapshot = lambda _content_hash: {}
    return ResearchDesignService(
        repository=repository,
        definition_source=FixedDefinitionSource(),
    )


def _assert_zero_execution_facts(task: ResearchTaskV12, connect) -> None:
    assert task.execution_decisions == []
    assert task.work_units == []
    assert task.capability_calls == []
    assert task.artifacts == []
    assert task.agent_interaction_records == []
    with connect() as connection:
        assert connection.execute("SELECT count(*) AS count FROM openair_dispatch").fetchone()["count"] == 0
        assert connection.execute("SELECT count(*) AS count FROM openair_artifacts").fetchone()["count"] == 0


def test_planning_attempt_is_committed_before_provider_and_unknown_is_not_replayed(repository) -> None:
    repo, connect = repository
    task = new_research_task(task_id="task_started_commit", registry_snapshot_ref=SNAPSHOT)
    _insert_task(connect, task)
    service = _service(repo)
    observed: list[tuple[str, int, str]] = []

    def success_provider(key: str) -> PlannerProviderResult:
        with connect() as second_connection:
            row = second_connection.execute(
                "SELECT payload, revision FROM openair_tasks WHERE task_id = %s",
                (task.task_id,),
            ).fetchone()
        attempt = row["payload"]["planningAttempts"][-1]
        observed.append((attempt["status"], row["revision"], key))
        return PlannerProviderResult(
            proposal=deepcopy(CASES["simple"]),
            provider_call_ref="provider:success",
        )

    completed = service.plan(
        task_id=task.task_id,
        owner_id="owner",
        workspace_id="workspace",
        request_id="request_success",
        expected_task_revision=0,
        provider=success_provider,
    )
    assert completed.scientific_states == []
    assert observed == [("started", 1, "planner:request_success")]
    assert completed.planning_attempts[-1].status.value == "completed"
    assert completed.planning_attempts[-1].planner_provider_call_ref == "provider:success"
    _assert_zero_execution_facts(completed, connect)

    unknown_task = new_research_task(task_id="task_unknown_commit", registry_snapshot_ref=SNAPSHOT)
    _insert_task(connect, unknown_task)
    unknown_service = _service(repo)
    unknown_calls: list[str] = []

    def unknown_provider(key: str):
        with connect() as second_connection:
            payload = second_connection.execute(
                "SELECT payload FROM openair_tasks WHERE task_id = %s",
                (unknown_task.task_id,),
            ).fetchone()["payload"]
        assert payload["planningAttempts"][-1]["status"] == "started"
        unknown_calls.append(key)
        raise PlannerTransportUnknown(
            "transport unknown",
            provider_call_ref="provider:unknown",
        )

    with pytest.raises(PlannerTransportUnknown):
        unknown_service.plan(
            task_id=unknown_task.task_id,
            owner_id="owner",
            workspace_id="workspace",
            request_id="request_unknown",
            expected_task_revision=0,
            provider=unknown_provider,
        )
    failed = repo.load(task_id=unknown_task.task_id, owner_id="owner", workspace_id="workspace")
    assert failed.scientific_states == []
    assert failed.planning_attempts[-1].status.value == "failed"
    assert failed.planning_attempts[-1].planner_provider_call_ref == "provider:unknown"
    with pytest.raises(PlanningAttemptReplayError):
        unknown_service.plan(
            task_id=unknown_task.task_id,
            owner_id="owner",
            workspace_id="workspace",
            request_id="request_unknown",
            expected_task_revision=failed.revision,
            provider=unknown_provider,
        )
    assert unknown_calls == ["planner:request_unknown"]
    _assert_zero_execution_facts(failed, connect)


def test_confirmation_cas_idempotency_scope_and_supersede_are_atomic(repository) -> None:
    repo, connect = repository
    task = new_research_task(task_id="task_confirm_atomic", registry_snapshot_ref=SNAPSHOT)
    _insert_task(connect, task)
    service = _service(repo)

    def provider(proposal: dict, ref: str):
        return lambda _key: PlannerProviderResult(proposal=deepcopy(proposal), provider_call_ref=ref)

    first = service.plan(
        task_id=task.task_id, owner_id="owner", workspace_id="workspace",
        request_id="request_one", expected_task_revision=0,
        provider=provider(CASES["simple"], "provider:first"),
    )
    design_one = first.research_design_versions[-1]
    confirmed_one = service.confirm(
        task_id=task.task_id, owner_id="owner", workspace_id="workspace", actor_id="actor",
        design_id=design_one.research_design_id, version=1, confirmation_id="confirmation_one",
        decision="approved", expected_task_revision=first.revision,
    )
    assert len(confirmed_one.scientific_states) == 1
    initial_scientific_state = confirmed_one.scientific_states[-1]
    assert initial_scientific_state.scientific_state_version == 1
    assert initial_scientific_state.research_design_version == design_one.version
    assert initial_scientific_state.scientific_facts == {}
    assert initial_scientific_state.execution_state == {}
    assert initial_scientific_state.source_refs == []
    assert initial_scientific_state.created_at == confirmed_one.research_design_versions[-1].confirmed_at
    replay = service.confirm(
        task_id=task.task_id, owner_id="owner", workspace_id="workspace", actor_id="actor",
        design_id=design_one.research_design_id, version=1, confirmation_id="confirmation_one",
        decision="approved", expected_task_revision=first.revision,
    )
    assert replay == confirmed_one
    assert len(replay.scientific_states) == 1
    with pytest.raises(ConfirmationIdentityConflictError):
        service.confirm(
            task_id=task.task_id, owner_id="owner", workspace_id="workspace", actor_id="other",
            design_id=design_one.research_design_id, version=1, confirmation_id="confirmation_one",
            decision="approved", expected_task_revision=confirmed_one.revision,
        )
    with pytest.raises(ResearchTaskNotFoundError):
        repo.load(task_id=task.task_id, owner_id="other", workspace_id="workspace")

    second = service.plan(
        task_id=task.task_id, owner_id="owner", workspace_id="workspace",
        request_id="request_two", expected_task_revision=confirmed_one.revision,
        provider=provider(CASES["sdr"], "provider:second"),
    )
    design_two = second.research_design_versions[-1]
    before_stale = repo.load(task_id=task.task_id, owner_id="owner", workspace_id="workspace")
    with pytest.raises(TaskRevisionConflictError):
        service.confirm(
            task_id=task.task_id, owner_id="owner", workspace_id="workspace", actor_id="actor",
            design_id=design_two.research_design_id, version=2, confirmation_id="confirmation_stale",
            decision="approved", expected_task_revision=confirmed_one.revision,
        )
    assert repo.load(task_id=task.task_id, owner_id="owner", workspace_id="workspace") == before_stale

    confirmed_two = service.confirm(
        task_id=task.task_id, owner_id="owner", workspace_id="workspace", actor_id="actor",
        design_id=design_two.research_design_id, version=2, confirmation_id="confirmation_two",
        decision="approved", expected_task_revision=second.revision,
    )
    assert len(confirmed_two.scientific_states) == 2
    current_scientific_state = confirmed_two.scientific_states[-1]
    assert current_scientific_state.scientific_state_version == 2
    assert current_scientific_state.research_design_version == design_two.version
    assert current_scientific_state.scientific_facts == initial_scientific_state.scientific_facts
    assert current_scientific_state.source_refs == initial_scientific_state.source_refs
    assert current_scientific_state.execution_state == {}
    old = confirmed_two.research_design_versions[0]
    assert old.project_completion_requirements == confirmed_one.research_design_versions[0].project_completion_requirements
    assert old.status.value == "superseded"
    for field in (
        "goal", "understanding", "constraints", "scope_boundary", "budget_boundary",
        "external_action_boundary", "risk_boundary", "instances", "coverage_report",
        "created_at", "confirmed_at", "confirmation_ref",
    ):
        assert getattr(old, field) == getattr(confirmed_one.research_design_versions[0], field)
    assert confirmed_two.active_research_design_ref.version == 2
    _assert_zero_execution_facts(confirmed_two, connect)


def test_request_revision_is_durable_but_does_not_activate_or_dispatch(repository) -> None:
    repo, connect = repository
    task = new_research_task(task_id="task_revision_requested", registry_snapshot_ref=SNAPSHOT)
    _insert_task(connect, task)
    service = _service(repo)
    planned = service.plan(
        task_id=task.task_id,
        owner_id="owner",
        workspace_id="workspace",
        request_id="request_revision_requested",
        expected_task_revision=0,
        provider=lambda _key: PlannerProviderResult(
            proposal=deepcopy(CASES["simple"]),
            provider_call_ref="provider:revision-requested",
        ),
    )
    design = planned.research_design_versions[-1]
    result = service.confirm(
        task_id=task.task_id,
        owner_id="owner",
        workspace_id="workspace",
        actor_id="actor",
        design_id=design.research_design_id,
        version=design.version,
        confirmation_id="confirmation_revision_requested",
        decision="request_revision",
        expected_task_revision=planned.revision,
    )
    assert result.scientific_states == []
    assert result.research_design_versions[-1].status.value == "draft"
    assert result.active_research_design_ref is None
    assert result.confirmations[-1].decision == "request_revision"
    _assert_zero_execution_facts(result, connect)


def _fifty_instance_proposal() -> dict:
    proposal = deepcopy(CASES["simple"])
    proposal["scopeBoundary"]["maxSubstageInstances"] = 50
    proposal["instances"] = []
    for index in range(50):
        instance = deepcopy(CASES["simple"]["instances"][0])
        instance["instanceKey"] = f"node_{index:02d}"
        instance["definitionId"] = "sdr:perf_node"
        instance["objective"] = f"Produce deterministic node output {index}."
        instance["expectedArtifactTypes"] = [
            "evidence_summary" if index == 0 else f"intermediate_{index:02d}"
        ]
        instance["acceptanceCriteria"][0]["criterionKey"] = "output"
        if index == 0:
            instance["acceptanceCriteria"][0]["requiredArtifactType"] = "evidence_summary"
        else:
            instance["acceptanceCriteria"][0].update(
                {
                    "criterionKind": "artifact_quality",
                    "coverageSourcePaths": [],
                    "requiredArtifactType": f"intermediate_{index:02d}",
                }
            )
        instance["sequenceHint"] = index
        proposal["instances"].append(instance)
    proposal["coverageClaimCandidates"][0].update(
        {
            "instanceKeys": ["node_00"],
            "acceptanceCriterionKeys": ["node_00:output"],
        }
    )
    return proposal


def test_fifty_instance_design_contract_and_payload_budget(repository) -> None:
    repo, connect = repository
    task = new_research_task(
        task_id="task_large_design_contract",
        registry_snapshot_ref=SNAPSHOT,
    )
    _insert_task(connect, task)
    service = _service(repo)
    provider_calls = 0

    def provider(_key: str) -> PlannerProviderResult:
        nonlocal provider_calls
        provider_calls += 1
        return PlannerProviderResult(
            proposal=deepcopy(_fifty_instance_proposal()),
            provider_call_ref="provider:large-design-contract",
        )

    planned = service.plan(
        task_id=task.task_id,
        owner_id="owner",
        workspace_id="workspace",
        request_id="request_large_design_contract",
        expected_task_revision=0,
        provider=provider,
    )
    assert planned.scientific_states == []
    design = planned.research_design_versions[-1]
    payload_size = len(
        json.dumps(
            design.model_dump(mode="json", by_alias=True)
        ).encode("utf-8")
    )

    assert provider_calls == 1
    assert len(design.instances) == 50
    assert design.coverage_report is not None
    assert design.coverage_report.status.value == "passed_structural"
    assert design.project_completion_requirements
    assert payload_size <= 1024 * 1024
    _assert_zero_execution_facts(planned, connect)


