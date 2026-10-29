"""PostgreSQL proof that an immutable Research Package can be a traced Planner source.

This is R3 engineering evidence, not product-value validation of the fixture's
scientific content. The Package remains prior-research planning input only;
ResearchDesign, Evidence/ScientificState, execution facts, and user confirmation
keep their existing authorities.
"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from urllib.parse import unquote, urlparse
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row
import pytest
import yaml

from app.config import settings
from app.models.research_orchestration import ResearchTaskV12
from app.modules.research_design_service import (
    PlannerProviderResult,
    ResearchDesignService,
    ResolvedDefinition,
    new_research_task,
)
from app.modules.research_orchestration_repository import ResearchOrchestrationRepository
from app.modules.research_package_asset import ResearchPackageAssetService
from app.modules.research_planning_sources import (
    PlanningSourceUseDecision,
    materialize_planning_source_usages,
    resolve_research_package_planning_sources,
)


PACKAGE_FIXTURE = (
    Path(__file__).parents[1]
    / "fixtures"
    / "research_packages"
    / "ag4ge3s5_engineering_v02.yaml"
)
DESIGN_CASES = json.loads(
    (Path(__file__).parents[1] / "fixtures" / "research_design_v1_cases.json").read_text(
        encoding="utf-8"
    )
)
SNAPSHOT = {"schemaVersion": 1, "contentHash": "sha256:" + "d" * 64}


def _test_dsn() -> str:
    dsn = os.environ.get("OPENAIR_TEST_POSTGRES_DSN", "").strip()
    if not dsn:
        pytest.skip("OPENAIR_TEST_POSTGRES_DSN is required for the R3 PostgreSQL test")

    parsed = urlparse(dsn)
    database = parsed.path.lstrip("/")
    if parsed.scheme not in {"postgres", "postgresql"} or not parsed.hostname or not database:
        pytest.fail("OPENAIR_TEST_POSTGRES_DSN must be a complete PostgreSQL DSN")

    lowered = database.lower()
    forbidden_names = {"openair", "postgres", "template0", "template1"}
    forbidden_fragments = ("prod", "production", "staging", "develop", "development")
    if lowered in forbidden_names or any(part in lowered for part in forbidden_fragments):
        pytest.fail("refusing R3 integration test against a non-test PostgreSQL database")
    if "test" not in lowered and "qa" not in lowered:
        pytest.fail("R3 integration database name must contain 'test' or 'qa'")
    return dsn


def _configure_postgres(monkeypatch: pytest.MonkeyPatch, dsn: str, tmp_path: Path) -> None:
    parsed = urlparse(dsn)
    monkeypatch.setattr(settings, "postgres_host", parsed.hostname or "localhost")
    monkeypatch.setattr(settings, "postgres_port", parsed.port or 5432)
    monkeypatch.setattr(settings, "postgres_user", unquote(parsed.username or "postgres"))
    monkeypatch.setattr(settings, "postgres_password", unquote(parsed.password or ""))
    monkeypatch.setattr(settings, "postgres_database", parsed.path.lstrip("/"))
    monkeypatch.setattr(settings, "persistence_backend", "postgres")
    monkeypatch.setattr(settings, "research_orchestration_v1", True)
    monkeypatch.setattr(settings, "artifact_storage", "local")
    monkeypatch.setattr(settings, "workspace_root", tmp_path)


def _bootstrap(dsn: str) -> None:
    from tests.integration._production_postgres_bootstrap import bootstrap_production_postgres_schema

    with psycopg.connect(dsn, row_factory=dict_row) as connection:
        with connection.cursor() as cursor:
            bootstrap_production_postgres_schema(cursor)


@pytest.fixture
def r3_postgres(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    dsn = _test_dsn()
    _configure_postgres(monkeypatch, dsn, tmp_path)
    _bootstrap(dsn)

    owner = f"rp-r3-{uuid4().hex}"
    workspace = f"workspace-{uuid4().hex}"

    def connect():
        return psycopg.connect(dsn, row_factory=dict_row)

    try:
        yield owner, workspace, connect
    finally:
        with connect() as connection:
            connection.execute(
                """DELETE FROM openair_dispatch
                   WHERE task_id IN (SELECT task_id FROM openair_tasks WHERE owner_id = %s)""",
                (owner,),
            )
            connection.execute("DELETE FROM openair_tasks WHERE owner_id = %s", (owner,))
            connection.execute("DELETE FROM openair_artifacts WHERE owner_id = %s", (owner,))


class FixedDefinitionSource:
    def snapshot_ref(self):
        return copy.deepcopy(SNAPSHOT)

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
                "problem_modeling",
                f"R3 {task.task_id}",
                "research_task",
                json.dumps(payload),
                task.revision,
            ),
        )


def _package() -> dict:
    return yaml.safe_load(PACKAGE_FIXTURE.read_text(encoding="utf-8"))


def _assert_no_execution_facts(task: ResearchTaskV12, connect, *, owner: str) -> None:
    assert task.execution_decisions == []
    assert task.work_units == []
    assert task.capability_calls == []
    assert task.artifacts == []
    assert task.agent_interaction_records == []
    with connect() as connection:
        dispatch_count = connection.execute(
            """SELECT count(*) AS count
               FROM openair_dispatch AS d
               JOIN openair_tasks AS t ON t.task_id = d.task_id
              WHERE t.owner_id = %s""",
            (owner,),
        ).fetchone()["count"]
    assert dispatch_count == 0


def test_exact_package_source_survives_plan_confirm_and_new_package_version(r3_postgres) -> None:
    owner, workspace, connect = r3_postgres
    package = _package()
    package_service = ResearchPackageAssetService()
    original_asset = package_service.save(
        package,
        owner_id=owner,
        workspace_id=workspace,
        created_by="r3-postgres-test",
    )

    planning_sources = resolve_research_package_planning_sources(
        [
            {
                "kind": "research_package",
                "id": package["id"],
                "version": package["version"],
            }
        ],
        owner_id=owner,
        workspace_id=workspace,
        asset_service=package_service,
    )
    assert len(planning_sources) == 1
    projected = planning_sources[0]
    unit_numbers = [row["unitNumber"] for row in projected["units"]]
    assert len(unit_numbers) >= 3

    decision = PlanningSourceUseDecision(
        sourceChoice=projected["sourceChoice"],
        adoptedUnitNumbers=[unit_numbers[0]],
        revalidationUnitNumbers=[unit_numbers[1]],
        notAdoptedUnitNumbers=unit_numbers[2:],
    )
    usages = materialize_planning_source_usages([decision], planning_sources)
    assert len(usages) == 1

    task = new_research_task(
        task_id=f"task_r3_{uuid4().hex}",
        registry_snapshot_ref=SNAPSHOT,
    )
    _insert_task(connect, task, owner=owner, workspace=workspace)
    repository = ResearchOrchestrationRepository(
        connection_factory=connect,
        runtime_settings=settings,
    )
    service = ResearchDesignService(
        repository=repository,
        definition_source=FixedDefinitionSource(),
    )

    planned = service.plan(
        task_id=task.task_id,
        owner_id=owner,
        workspace_id=workspace,
        request_id="r3-package-plan",
        expected_task_revision=0,
        provider=lambda _key: PlannerProviderResult(
            proposal=copy.deepcopy(DESIGN_CASES["simple"]),
            provider_call_ref="provider:r3-package-plan",
            planning_source_usages=usages,
        ),
    )

    assert planned.scientific_states == []
    _assert_no_execution_facts(planned, connect, owner=owner)
    attempt = planned.planning_attempts[-1]
    assert attempt.status.value == "completed"
    assert len(attempt.planning_source_usages) == 1
    persisted_usage = attempt.planning_source_usages[0]
    assert persisted_usage.source_kind == "research_package"
    assert persisted_usage.source_id == package["id"]
    assert persisted_usage.source_version == package["version"]
    assert persisted_usage.source_artifact_ref == original_asset.artifact_id
    assert persisted_usage.source_content_hash == f"sha256:{original_asset.package.content_sha256}"
    assert persisted_usage.adopted_source_paths == [projected["units"][0]["sourcePath"]]
    assert persisted_usage.revalidation_source_paths == [projected["units"][1]["sourcePath"]]
    assert persisted_usage.not_adopted_source_paths == [
        row["sourcePath"] for row in projected["units"][2:]
    ]

    design = planned.research_design_versions[-1]
    assert design.coverage_report.planning_attempt_id == attempt.planning_attempt_id
    design_payload = design.model_dump(mode="json", by_alias=True)
    assert "planningSourceUsages" not in design_payload
    assert "researchPackage" not in json.dumps(design_payload, ensure_ascii=False)

    confirmed = service.confirm(
        task_id=task.task_id,
        owner_id=owner,
        workspace_id=workspace,
        actor_id="r3-user",
        design_id=design.research_design_id,
        version=design.version,
        confirmation_id="r3-confirmation",
        decision="approved",
        expected_task_revision=planned.revision,
    )
    assert confirmed.active_research_design_ref is not None
    assert confirmed.active_research_design_ref.version == design.version
    assert len(confirmed.scientific_states) == 1
    state = confirmed.scientific_states[-1]
    assert state.scientific_facts == {}
    assert state.source_refs == []
    assert state.execution_state == {}
    _assert_no_execution_facts(confirmed, connect, owner=owner)

    confirmed_design_before = confirmed.research_design_versions[-1].model_dump(
        mode="json", by_alias=True
    )
    confirmed_usage_before = confirmed.planning_attempts[-1].planning_source_usages[0].model_dump(
        mode="json", by_alias=True
    )

    next_package = copy.deepcopy(package)
    next_package["version"] = "0.2.1"
    next_package["summary"] = "new package version published after ResearchDesign confirmation"
    next_asset = package_service.save(
        next_package,
        owner_id=owner,
        workspace_id=workspace,
        created_by="r3-postgres-test",
    )
    assert next_asset.artifact_id != original_asset.artifact_id

    reloaded = repository.load(task_id=task.task_id, owner_id=owner, workspace_id=workspace)
    assert reloaded.research_design_versions[-1].model_dump(
        mode="json", by_alias=True
    ) == confirmed_design_before
    assert reloaded.planning_attempts[-1].planning_source_usages[0].model_dump(
        mode="json", by_alias=True
    ) == confirmed_usage_before
    assert reloaded.planning_attempts[-1].planning_source_usages[0].source_version == "0.2.0"
    assert reloaded.planning_attempts[-1].planning_source_usages[0].source_artifact_ref == original_asset.artifact_id
    assert reloaded.scientific_states[-1].scientific_facts == {}
    assert reloaded.scientific_states[-1].source_refs == []
    _assert_no_execution_facts(reloaded, connect, owner=owner)
