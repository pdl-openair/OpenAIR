from __future__ import annotations

from types import SimpleNamespace

import pytest


def test_planning_snapshot_excludes_execution_capabilities_and_catalog_pin_does_not_prune() -> None:
    from app.modules.production_definition_source import ProductionDefinitionSource
    from app.modules.registry_snapshot_repository import InMemoryRegistrySnapshotRepository

    archive = InMemoryRegistrySnapshotRepository()
    source = ProductionDefinitionSource(archive=archive, planning_only=True)
    ref = source.snapshot_ref()
    envelope = archive.get(ref["contentHash"]).envelope

    assert envelope["scope"] == {"kind": "research_planning"}
    assert envelope["definitions"]["skills"] == []
    assert envelope["definitions"]["agents"] == []
    assert envelope["bindings"] == {"skills": [], "agents": [], "agentSkills": []}
    assert envelope["recipes"] == []

    baseline = source.planner_catalog_for_research_design(None)
    pinned = source.planner_catalog_for_catalog_binding(object())  # type: ignore[arg-type]
    assert baseline
    assert pinned == baseline


def test_execution_snapshot_is_child_of_frozen_planning_contract() -> None:
    from app.modules.registry_snapshot_repository import (
        execution_snapshot_matches_planning,
        planning_envelope_from_execution,
        registry_envelope_ref,
    )
    from app.modules.research_orchestration_resolver import (
        build_execution_registry_snapshot,
        build_planning_registry_snapshot,
    )

    planning = build_planning_registry_snapshot().envelope
    definition = next(
        row
        for row in planning["definitions"]["substages"]
        if row["definitionId"] == "sdr:question_refinement"
    )
    required = ((definition["definitionId"], definition["definitionVersion"]),)
    execution = build_execution_registry_snapshot(
        planning,
        required_definition_refs=required,
    ).envelope

    planning_ref = registry_envelope_ref(planning)
    execution_ref = registry_envelope_ref(execution)
    assert execution["scope"] == {
        "kind": "execution",
        "planningSnapshotRef": planning_ref,
    }
    assert execution_snapshot_matches_planning(planning_ref, execution_ref, execution)
    assert planning_envelope_from_execution(execution) == planning


def test_catalog_pinned_research_start_does_not_read_execution_availability_before_planning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.execution.research_start_worker_runtime as runtime
    from app.modules import catalog_execution_binding as binding_module
    from app.modules import production_definition_source as source_module
    from app.modules import resource_catalog as resource_catalog_module

    planning_only_values: list[bool] = []

    class StopAfterPlanningSource(RuntimeError):
        pass

    class Source:
        def __init__(self, *, planning_only: bool = False, **_kwargs) -> None:
            planning_only_values.append(planning_only)
            raise StopAfterPlanningSource("stop-after-planning-source")

    binding = SimpleNamespace(
        resource_id="resource-1",
        resource_version=1,
        capability_id="capability-1",
    )
    monkeypatch.setattr(binding_module, "binding_from_public", lambda _value: binding)
    monkeypatch.setattr(
        resource_catalog_module,
        "get_resource_for_research_start",
        lambda *_args, **_kwargs: pytest.fail(
            "Planner must not inspect current execution availability before ResearchDesign confirmation"
        ),
    )
    monkeypatch.setattr(source_module, "ProductionDefinitionSource", Source)
    monkeypatch.setattr(runtime.settings, "persistence_backend", "postgres")

    context = {
        "requestId": "rsr_t0a_catalog",
        "sessionId": "intent_t0a_catalog",
        "ownerId": "owner",
        "workspaceId": "workspace",
        "intent": {"input": "research", "sourcePage": "home"},
        "reservedTaskId": "task",
        "reservedArtifactId": "artifact",
        "createdBy": "owner",
        "workerId": "worker",
        "leaseEpoch": 1,
        "attempts": [],
        "catalogBinding": {"present": True},
    }

    with pytest.raises(StopAfterPlanningSource, match="stop-after-planning-source"):
        runtime._run_research_design_planner(
            object(),
            object(),
            context,
            object(),
            SimpleNamespace(userOutcome="research_task", userFacingSummary="ready"),
        )

    assert planning_only_values == [True]
