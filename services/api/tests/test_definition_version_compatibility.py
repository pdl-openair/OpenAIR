from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from app.modules.production_definition_source import ProductionDefinitionSource
from app.modules.production_workflow_source import ProductionWorkflowSource
from app.modules.registry_snapshot_repository import InMemoryRegistrySnapshotRepository
from app.modules.research_orchestration_resolver import (
    RegistryAuthorityError,
    build_execution_registry_snapshot,
    build_planning_registry_snapshot,
    build_registry_snapshot,
)


BASELINE_ID = "cs_reproduction:reproduction_baseline_criteria"
CONCLUSION_ID = "cs_reproduction:reproduction_conclusion_recommendation"
AFFECTED_IDS = {
    BASELINE_ID,
    "cs_reproduction:reproduction_experiment_design",
    "cs_reproduction:execution_readiness_validation",
    "cs_reproduction:real_execution_result_acquisition",
    "cs_reproduction:result_comparability_judgment",
    "cs_reproduction:reproduction_conclusion_recommendation",
    "cs_reproduction:deviation_diagnosis_validation",
}
WORKFLOW_DIR = Path(__file__).resolve().parents[3] / "schemas" / "workflows"


def _old_planning_envelope() -> dict:
    envelope = deepcopy(build_planning_registry_snapshot().envelope)
    envelope["definitions"]["substages"] = [
        row
        for row in envelope["definitions"]["substages"]
        if row["definitionId"] not in AFFECTED_IDS or row["definitionVersion"] == 1
    ]
    return envelope


def test_registry_preserves_old_definition_and_publishes_new_version() -> None:
    envelope = build_registry_snapshot().envelope
    rows = {
        row["definitionVersion"]: row
        for row in envelope["definitions"]["substages"]
        if row["definitionId"] == BASELINE_ID
    }

    assert set(rows) == {1, 2, 3}
    assert "executionMethod" not in rows[1]
    assert rows[2]["executionMethod"]["methodId"] == "cs_reproduction_baseline_grounding"
    assert rows[3]["executionMethod"]["methodId"] == "cs_reproduction_baseline_grounding"
    assert rows[1]["specRef"] == rows[2]["specRef"]
    assert rows[1]["specSource"]["repoPath"] == rows[2]["specSource"]["repoPath"]
    assert rows[1]["specSource"]["content"] != rows[2]["specSource"]["content"]
    assert rows[3]["specRef"].endswith("-v3.md")


def test_old_planning_snapshot_builds_execution_snapshot_with_closed_bindings() -> None:
    execution = build_execution_registry_snapshot(
        _old_planning_envelope(),
        required_definition_refs=((BASELINE_ID, 1),),
    ).envelope
    baseline_bindings = [
        row
        for row in execution["bindings"]["skills"]
        if row["definitionId"] == BASELINE_ID and row["definitionVersion"] == 1
    ]
    retained = {
        (row["bindingId"], row["bindingVersion"])
        for row in execution["bindings"]["skills"]
    }

    assert baseline_bindings
    assert all(row["bindingVersion"] == 1 for row in baseline_bindings)
    assert all(
        (source["bindingId"], source["bindingVersion"]) in retained
        for row in baseline_bindings
        for field in (row.get("inputSources") or {}).get("fields", [])
        for source in [field.get("source") or {}]
        if source.get("kind") == "artifact"
    )


def test_versioning_does_not_relax_frozen_contract_rejection() -> None:
    planning = _old_planning_envelope()
    row = next(
        row
        for row in planning["definitions"]["substages"]
        if row["definitionId"] == BASELINE_ID
    )
    row["label"] = "mutated frozen contract"

    with pytest.raises(
        RegistryAuthorityError,
        match="execution_definition_contract_changed_since_planning",
    ):
        build_execution_registry_snapshot(
            planning,
            required_definition_refs=((BASELINE_ID, 1),),
        )


def test_sources_resolve_exact_definition_and_workflow_versions() -> None:
    definitions = ProductionDefinitionSource(
        archive=InMemoryRegistrySnapshotRepository()
    )
    workflows = ProductionWorkflowSource(
        WORKFLOW_DIR,
        definition_authority=definitions,
    )

    assert definitions.resolve(BASELINE_ID).definition_version == 3
    assert definitions.resolve_version(BASELINE_ID, 1).definition_version == 1
    assert definitions.resolve_version(BASELINE_ID, 2).definition_version == 2
    assert definitions.resolve(CONCLUSION_ID).definition_version == 4
    assert definitions.resolve_version(CONCLUSION_ID, 2).definition_version == 2
    assert definitions.resolve_version(CONCLUSION_ID, 3).definition_version == 3
    assert definitions.resolve_version(CONCLUSION_ID, 4).definition_version == 4
    assert all(
        node.definition_version == 1
        for node in workflows.require("cs_paper_reproduction_workflow", 2).nodes
    )
    assert all(
        node.definition_version == 2
        for node in workflows.require("cs_paper_reproduction_workflow", 3).nodes
    )
    assert [
        node.definition_version
        for node in workflows.require("cs_paper_reproduction_workflow", 4).nodes
    ] == [2, 2, 2, 2, 2, 3, 2]
    assert [
        node.definition_version
        for node in workflows.require("cs_paper_reproduction_workflow", 6).nodes
    ] == [3, 3, 3, 3, 3, 4, 4]


def test_real_execution_binding_versions_keep_historical_and_current_semantics() -> None:
    definition_id = "cs_reproduction:real_execution_result_acquisition"
    closure_skill = "reproduction_execution_input_closure"
    closure_binding = (
        "bind:cs_reproduction:real_execution_result_acquisition:skill:"
        "reproduction_execution_input_closure"
    )
    design_binding = (
        "bind:cs_reproduction:reproduction_experiment_design:skill:"
        "reproduction_experiment_design"
    )

    old_execution = build_execution_registry_snapshot(
        _old_planning_envelope(),
        required_definition_refs=((definition_id, 1),),
    ).envelope
    current_execution = build_execution_registry_snapshot(
        build_planning_registry_snapshot().envelope,
        required_definition_refs=((definition_id, 2),),
    ).envelope

    def version_bindings(envelope: dict, version: int) -> dict[str, dict]:
        return {
            row["skillId"]: row
            for row in envelope["bindings"]["skills"]
            if row["definitionId"] == definition_id
            and row["definitionVersion"] == version
        }

    old = version_bindings(old_execution, 1)
    current = version_bindings(current_execution, 2)

    assert closure_skill not in old
    old_run_sources = old["exploratory_run"]["inputSources"]["fields"]
    assert {row["source"]["bindingId"] for row in old_run_sources} == {
        design_binding
    }
    for skill_id in (
        "reproduction_execution_record_closure",
        "actual_metric_records_closure",
    ):
        assert "frozen_execution_input" not in {
            row["targetField"] for row in old[skill_id]["inputSources"]["fields"]
        }

    assert current[closure_skill]["bindingVersion"] == 2
    current_run_sources = current["exploratory_run"]["inputSources"]["fields"]
    assert {row["source"]["bindingId"] for row in current_run_sources} == {
        closure_binding
    }
    for skill_id in (
        "reproduction_execution_record_closure",
        "actual_metric_records_closure",
    ):
        fields = {
            row["targetField"]: row
            for row in current[skill_id]["inputSources"]["fields"]
        }
        assert fields["frozen_execution_input"]["source"]["bindingId"] == (
            closure_binding
        )
        assert fields["frozen_execution_input"]["source"]["bindingVersion"] == 2

