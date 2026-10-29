from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[4]
WORKFLOW_PATH = ROOT / "schemas" / "workflows" / "opentrajectory_research_workflow_v3.yaml"
BINDINGS_PATH = ROOT / "skills" / "skill-bindings.yaml"
RESEARCH_RECORD_DEFINITION_ID = "opentrajectory:research_record_establishment"
PROBLEM_FRAMING_BINDING_ID = (
    "bind:opentrajectory:research_record_establishment:skill:problem_framing_generation"
)
RETRIEVAL_MATRIX_BINDING_ID = (
    "bind:opentrajectory:research_record_establishment:skill:retrieval_matrix_generation"
)


def _binding(binding_id: str) -> dict:
    bindings = yaml.safe_load(BINDINGS_PATH.read_text(encoding="utf-8"))["bindings"]
    rows = [row for row in bindings if row["bindingId"] == binding_id]
    assert len(rows) == 1
    return rows[0]


def _source_map(binding: dict) -> dict[str, dict]:
    return {
        entry["targetField"]: entry
        for entry in binding.get("inputSources", {}).get("fields", [])
    }


def _runtime_map(binding: dict) -> dict[str, dict]:
    return {
        entry["targetField"]: entry
        for entry in binding.get("runtimeInputs", {}).get("fields", [])
    }


def test_v3_workflow_declares_research_record_establishment() -> None:
    workflow = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))

    assert workflow["schemaVersion"] == "workflow-definition.v2"
    assert workflow["workflowId"] == "opentrajectory_research_workflow"
    assert workflow["workflowVersion"] == 3
    assert [
        node
        for node in workflow["nodes"]
        if node["nodeKey"] == "research_record_establishment"
    ] == [{
        "nodeKey": "research_record_establishment",
        "definitionId": RESEARCH_RECORD_DEFINITION_ID,
        "definitionVersion": 1,
        "requirement": "optional",
        "displayOrder": 10,
    }]


def test_problem_framing_binding_uses_only_canonical_user_query() -> None:
    binding = _binding(PROBLEM_FRAMING_BINDING_ID)

    assert {
        key: binding[key]
        for key in (
            "bindingId", "bindingVersion", "definitionId", "definitionVersion",
            "skillId", "skillDefinitionVersion",
        )
    } == {
        "bindingId": PROBLEM_FRAMING_BINDING_ID,
        "bindingVersion": 1,
        "definitionId": RESEARCH_RECORD_DEFINITION_ID,
        "definitionVersion": 1,
        "skillId": "problem_framing_generation",
        "skillDefinitionVersion": 1,
    }
    assert "inputSources" not in binding
    assert _runtime_map(binding) == {
        "user_query": {
            "targetField": "user_query",
            "source": {
                "kind": "invocationContext",
                "field": "canonical_user_query",
            },
        },
    }


def test_retrieval_matrix_binding_consumes_problem_framing_outputs() -> None:
    binding = _binding(RETRIEVAL_MATRIX_BINDING_ID)

    assert {
        key: binding[key]
        for key in (
            "bindingId", "bindingVersion", "definitionId", "definitionVersion",
            "skillId", "skillDefinitionVersion",
        )
    } == {
        "bindingId": RETRIEVAL_MATRIX_BINDING_ID,
        "bindingVersion": 1,
        "definitionId": RESEARCH_RECORD_DEFINITION_ID,
        "definitionVersion": 1,
        "skillId": "retrieval_matrix_generation",
        "skillDefinitionVersion": 1,
    }
    sources = _source_map(binding)
    assert set(sources) == {"intent_understanding", "constraints_criteria"}
    for field in ("intent_understanding", "constraints_criteria"):
        assert sources[field] == {
            "targetField": field,
            "sourcePresence": "required",
            "source": {
                "kind": "artifact",
                "bindingId": PROBLEM_FRAMING_BINDING_ID,
                "bindingVersion": 1,
                "field": field,
            },
        }
