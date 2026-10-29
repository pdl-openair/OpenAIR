from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
import yaml

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "services" / "api"))
from app.modules.research_orchestration_resolver import build_registry_snapshot


ROOT = Path(__file__).resolve().parents[4]
WORKFLOW = ROOT / "schemas/workflows/materials_candidate_discovery_workflow.yaml"
SKILL_MAP = ROOT / "schemas/materials/candidate-discovery-workflow-skill-map.yaml"
REGISTRY = ROOT / "skills/registry.yaml"
BINDINGS = ROOT / "skills/skill-bindings.yaml"
WORKFLOW_ID = "materials_candidate_discovery_workflow"
REQUIRED_NODES = {
    "database_search": ("materials:database_search", "materials_mp_constraint_search"),
    "candidate_generation": (
        "materials:candidate_generation",
        "crystal_design_structure_crossover_py",
    ),
}


def _load(path: Path) -> dict:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def _one(rows: list[dict]) -> dict:
    assert len(rows) == 1
    return rows[0]


def _assert_admission(
    workflow: dict, mapping: dict, registry: dict, bindings: dict, snapshot: dict
) -> None:
    assert workflow["workflowId"] == WORKFLOW_ID
    assert workflow["skillPack"] == "materials"

    nodes = {row["nodeKey"]: row for row in workflow["nodes"]}
    assert set(nodes) == set(REQUIRED_NODES) == set(mapping)
    for node_key, (definition_id, skill_id) in REQUIRED_NODES.items():
        node = nodes[node_key]
        assert {
            key: node[key] for key in ("definitionId", "definitionVersion", "requirement")
        } == {
            "definitionId": definition_id,
            "definitionVersion": 1,
            "requirement": "required",
        }
        assert mapping[node_key] == [skill_id]

        skill = _one([row for row in registry["skills"] if row["skill_id"] == skill_id])
        assert skill.get("enabled", True) is True
        assert skill["status"] == "ready"
        assert skill["skill_type"] == "atomic"

        binding = _one([
            row for row in bindings["bindings"]
            if (row["definitionId"], row["definitionVersion"], row["skillId"])
            == (definition_id, 1, skill_id)
        ])
        definition = _one([
            row for row in snapshot["definitions"]["substages"]
            if (row["definitionId"], row["definitionVersion"]) == (definition_id, 1)
        ])
        assert definition["enabled"] is True
        assert definition["executionComposition"] == {
            "workflowId": WORKFLOW_ID,
            "skillPack": "materials",
            "compositionVersion": 1,
            "semantics": "ordered_required",
            "sourceAuthority": "schemas/materials/candidate-discovery-workflow-skill-map.yaml",
            "requiredSkillRefs": [{"skillId": skill_id, "definitionVersion": 1}],
        }
        snapshot_binding = _one([
            row for row in snapshot["bindings"]["skills"]
            if (row["definitionId"], row["definitionVersion"], row["skillId"])
            == (definition_id, 1, skill_id)
        ])
        assert snapshot_binding["bindingId"] == binding["bindingId"]
        assert snapshot_binding["enabled"] is True


@pytest.fixture(scope="module")
def snapshot() -> dict:
    return build_registry_snapshot().envelope


def test_materials_candidate_discovery_workflow_admission(snapshot: dict) -> None:
    _assert_admission(
        _load(WORKFLOW), _load(SKILL_MAP), _load(REGISTRY), _load(BINDINGS), snapshot
    )


@pytest.mark.parametrize(
    "drift",
    (
        "identity",
        "definition",
        "definition_disabled",
        "mapping",
        "registry",
        "binding",
    ),
)
def test_materials_admission_rejects_required_configuration_drift(
    drift: str, snapshot: dict
) -> None:
    workflow = deepcopy(_load(WORKFLOW))
    mapping = deepcopy(_load(SKILL_MAP))
    registry = deepcopy(_load(REGISTRY))
    bindings = deepcopy(_load(BINDINGS))
    drift_snapshot = deepcopy(snapshot)

    if drift == "identity":
        workflow["workflowId"] = "other_workflow"
    elif drift == "definition":
        workflow["nodes"][0]["definitionId"] = "materials:missing_definition"
    elif drift == "definition_disabled":
        for row in drift_snapshot["definitions"]["substages"]:
            if row["definitionId"] == "materials:database_search":
                row["enabled"] = False
    elif drift == "mapping":
        mapping["database_search"] = ["other_skill"]
    elif drift == "registry":
        _one([
            row for row in registry["skills"]
            if row["skill_id"] == "materials_mp_constraint_search"
        ])["enabled"] = False
    else:
        bindings["bindings"] = [
            row for row in bindings["bindings"]
            if row["bindingId"] != "bind:materials:database_search:skill:materials_mp_constraint_search"
        ]

    with pytest.raises(AssertionError):
        _assert_admission(workflow, mapping, registry, bindings, drift_snapshot)
