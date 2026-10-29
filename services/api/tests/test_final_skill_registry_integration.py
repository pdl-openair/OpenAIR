from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
import yaml
from jsonschema import ValidationError
from jsonschema.validators import validator_for

from app.execution.python_adapter_skills import (
    resolve_python_adapter_entrypoint,
    resolve_python_adapter_skill,
)
from app.modules.capability_applicability_producer import (
    EXECUTION_INSTANCE_FACT_REF,
    RESEARCH_TASK_FACT_REF,
    SCIENTIFIC_STATE_FACT_REF,
    produce_capability_applicability_results,
)
from app.modules.conditional_capability_binding import CapabilityApplicabilityStatus
from app.modules.research_orchestration_resolver import build_registry_snapshot
from referencing import Registry, Resource

ROOT = Path(__file__).parents[3]
REGISTRY_PATH = ROOT / "skills" / "registry.yaml"
OT = ROOT / "skills" / "opentrajectory"
PVD = ROOT / "skills" / "pvd"

PYTHON_FINAL = {
    "academic_source_retrieval_skill": "academic-source-retrieval-skill",
    "candidate_dedup_and_provenance": "candidate-dedup-and-provenance",
    "source_access_readiness_resolver": "source-access-readiness-resolver-skill",
    "deepxiv_progressive_reading": "deepxiv-progressive-reading-skill",
    "pvd_bayesian_optimization_candidate_generation": "pvd-bayesian-optimization-candidate-generation",
}
FINAL_SYNTHESIS_ID = "research_lead_hypothesis_generation"
READY_DURABLE_SKILLS = ("evidence_node_extraction", "mechanism_chain_synthesis")
PVD_DEFINITION_IDENTITY = ("pvd:process_factor_candidate_direction_analysis", 1)
BO_CAPABILITY_REF = "skill:pvd_bayesian_optimization_candidate_generation:1"


def _load_yaml(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _validate(instance: Any, schema: dict[str, Any]) -> None:
    cls = validator_for(schema)
    cls.check_schema(schema)

    repo_root = Path(__file__).resolve().parents[3]
    registry = Registry()
    seen_ids: dict[str, Path] = {}

    for root in (repo_root / "schemas", repo_root / "skills"):
        if not root.exists():
            continue
        for schema_path in root.rglob("*.schema.json"):
            candidate = _load_json(schema_path)
            schema_id = candidate.get("$id")
            if not isinstance(schema_id, str) or not schema_id:
                continue
            previous = seen_ids.get(schema_id)
            if previous is not None and previous != schema_path:
                raise AssertionError(
                    f"duplicate schema $id: {schema_id}: {previous} vs {schema_path}"
                )
            seen_ids[schema_id] = schema_path
            registry = registry.with_resource(
                schema_id,
                Resource.from_contents(candidate),
            )

    cls(schema, registry=registry).validate(instance)

def _registry_rows() -> list[dict[str, Any]]:
    value = _load_yaml(REGISTRY_PATH)
    assert isinstance(value, dict) and isinstance(value.get("skills"), list)
    return value["skills"]


def _row(skill_id: str) -> dict[str, Any]:
    rows = [row for row in _registry_rows() if row.get("skill_id") == skill_id]
    assert len(rows) == 1
    return rows[0]


def _skill_dir(skill_id: str, directory: str) -> Path:
    if skill_id == "pvd_bayesian_optimization_candidate_generation":
        return PVD / directory
    return OT / directory


def _snapshot_skill_state(snapshot: dict[str, Any], skill_id: str) -> tuple[str, list[bool]]:
    definitions = {
        row["skillId"]: row for row in snapshot["definitions"]["skills"]
    }
    bindings = [
        bool(row.get("enabled"))
        for row in snapshot["bindings"]["skills"]
        if row.get("skillId") == skill_id
    ]
    return definitions[skill_id]["status"], bindings


def test_single_final_synthesis_is_active_in_registry_and_ot_composition() -> None:
    composition = _load_yaml(ROOT / "schemas" / "opentrajectory" / "substage-skill-map.yaml")
    assert [row["skill_id"] for row in composition["findings_synthesis"]] == [
        FINAL_SYNTHESIS_ID
    ]

    workflow = _load_yaml(ROOT / "schemas" / "workflows" / "opentrajectory_research_workflow.yaml")
    node_keys = [row["nodeKey"] for row in workflow["nodes"]]
    assert "findings_synthesis" in node_keys
    assert "research_lead_generation" not in node_keys
    assert "hypothesis_update" not in node_keys
    assert any(
        row.get("upstreamNodeKey") == "evidence_extraction"
        and row.get("downstreamNodeKey") == "findings_synthesis"
        for row in workflow["dependencies"]
    )

    row = _row(FINAL_SYNTHESIS_ID)
    assert row["status"] == "ready"
    assert row.get("enabled") is True
    assert row["runtime"] == "llm_structured"
    assert row["runtime_contract"] == {
        "kind": "llm_reasoning",
        "requires_external_action": False,
        "adapter_id": "huiyan_chat_completions",
        "evidence_contract_version": "none",
    }
    status, binding_enabled = _snapshot_skill_state(
        build_registry_snapshot().envelope,
        FINAL_SYNTHESIS_ID,
    )
    assert status == "ready"
    assert binding_enabled == [True]


def test_split_findings_skill_is_preserved_but_disabled_and_unbound() -> None:
    skill_id = "ot_evidence_supported_findings_synthesis"
    row = _row(skill_id)
    assert row["status"] == "ready"
    assert row.get("enabled") is False
    status, binding_enabled = _snapshot_skill_state(build_registry_snapshot().envelope, skill_id)
    assert status == "ready"
    assert binding_enabled == []

    composition = _load_yaml(ROOT / "schemas" / "opentrajectory" / "substage-skill-map.yaml")
    assert all(
        item.get("skill_id") != skill_id
        for rows in composition.values()
        for item in rows
    )


def test_split_research_lead_skill_is_preserved_but_disabled_and_unbound() -> None:
    skill_id = "ot_research_lead_generation"
    row = _row(skill_id)
    assert row["status"] == "ready"
    assert row.get("enabled") is False
    status, binding_enabled = _snapshot_skill_state(build_registry_snapshot().envelope, skill_id)
    assert status == "ready"
    assert binding_enabled == []

    composition = _load_yaml(ROOT / "schemas" / "opentrajectory" / "substage-skill-map.yaml")
    assert all(
        item.get("skill_id") != skill_id
        for rows in composition.values()
        for item in rows
    )


def test_split_hypothesis_skill_is_preserved_but_disabled_and_unbound() -> None:
    skill_id = "ot_falsifiable_hypothesis_generation"
    row = _row(skill_id)
    assert row["status"] == "ready"
    assert row.get("enabled") is False
    status, binding_enabled = _snapshot_skill_state(build_registry_snapshot().envelope, skill_id)
    assert status == "ready"
    assert binding_enabled == []

    composition = _load_yaml(ROOT / "schemas" / "opentrajectory" / "substage-skill-map.yaml")
    assert all(
        item.get("skill_id") != skill_id
        for rows in composition.values()
        for item in rows
    )


def test_ready_durable_skills_keep_registry_status() -> None:
    snapshot = build_registry_snapshot().envelope
    for skill_id in READY_DURABLE_SKILLS:
        row = _row(skill_id)
        assert row["status"] == "ready"
        status, _ = _snapshot_skill_state(snapshot, skill_id)
        assert status == "ready"



def test_python_adapter_final_skills_have_one_canonical_identity_and_registered_callable() -> None:
    for skill_id, directory in PYTHON_FINAL.items():
        expected_path = (
            f"skills/pvd/{directory}"
            if skill_id == "pvd_bayesian_optimization_candidate_generation"
            else f"skills/opentrajectory/{directory}"
        )
        assert resolve_python_adapter_skill(skill_id) == (skill_id, expected_path)
        entrypoint = resolve_python_adapter_entrypoint(
            _row(skill_id)["runtime_contract"]["adapter_id"]
        )
        assert entrypoint is not None
        module_name, callable_name, invocation = entrypoint
        assert module_name.startswith("adapter.")
        assert callable_name
        assert invocation in {"payload_v1", "academic_retrieval_v1"}


def test_ot_source_acquisition_uses_consolidated_v2_binding_without_legacy_top_level_resolver() -> None:
    binding_source = _load_yaml(ROOT / "skills" / "skill-bindings.yaml")
    source_rows = [
        row
        for row in binding_source["bindings"]
        if row.get("definitionId") == "opentrajectory:source_acquisition"
    ]
    rows = [
        row
        for row in source_rows
        if row.get("skillId") == "opentrajectory_source_acquisition"
    ]
    assert len(rows) == 1
    row = rows[0]
    assert row["inputSources"] == {
        "contractVersion": 2,
        "fields": [
            {
                "targetField": "search_matrix",
                "sourcePresence": "required",
                "source": {
                    "kind": "artifact",
                    "bindingId": "bind:opentrajectory:retrieval_strategy_design:skill:retrieval_matrix_generation",
                    "bindingVersion": 1,
                    "field": "search_matrix",
                },
            },
            {
                "targetField": "expanded_query_variants",
                "sourcePresence": "required",
                "source": {
                    "kind": "artifact",
                    "bindingId": "bind:opentrajectory:retrieval_strategy_design:skill:retrieval_matrix_generation",
                    "bindingVersion": 1,
                    "field": "expanded_query_variants",
                },
            },
        ],
    }
    assert row["runtimeInputs"] == {
        "contractVersion": 2,
        "fields": [
            {
                "targetField": "user_query",
                "source": {
                    "kind": "invocationContext",
                    "field": "canonical_user_query",
                },
            },
            {
                "targetField": "task_id",
                "source": {
                    "kind": "executionIdentity",
                    "field": "task_id",
                },
            },
        ],
    }
    assert not any(
        candidate.get("skillId") == "source_access_readiness_resolver"
        for candidate in source_rows
    )

    source_acquisition = _row("opentrajectory_source_acquisition")
    assert source_acquisition["status"] == "ready"
    assert source_acquisition.get("enabled") is True
    assert source_acquisition["runtime_contract"] == {
        "kind": "external_adapter",
        "requires_external_action": True,
        "adapter_id": "python_adapter:opentrajectory_source_acquisition",
        "evidence_contract_version": "external-action-evidence/v1",
    }


    serialized = json.dumps(row, sort_keys=True)
    for forbidden in ("input_handoff", "normalizers", "mapper", "shim", "fallback"):
        assert forbidden not in serialized


def test_bo_public_input_schema_allows_only_scientific_state_facts() -> None:
    schema = _load_json(PVD / "pvd-bayesian-optimization-candidate-generation" / "input.schema.json")
    assert set(schema["properties"]) == {"scientific_state_facts"}
    assert schema["required"] == ["scientific_state_facts"]
    assert "project_observations" not in schema["properties"]

    request = _load_json(
        PVD / "pvd-bayesian-optimization-candidate-generation" / "examples/current_system_request.json"
    )
    _validate(request, schema)

    invalid_request = deepcopy(request)
    invalid_request["project_observations"] = []
    with pytest.raises(ValidationError):
        _validate(invalid_request, schema)




def test_bo_registry_declares_conditional_binding_policy() -> None:
    row = _row("pvd_bayesian_optimization_candidate_generation")
    assert row["packs"] == ["pvd"]
    assert row.get("enabled") is True
    assert row["conditional_binding"] == {
        "policy_version": "conditional_capability_binding_v1",
        "evaluator_ref": "python:pvd_bo.applicability:evaluate_capability_applicability",
        "required_runtime_fact_refs": [
            "runtime:research-task:v1",
            "runtime:execution-instance:v1",
            "runtime:scientific-state:v1",
        ],
    }
