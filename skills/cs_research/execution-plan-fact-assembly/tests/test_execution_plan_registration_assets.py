from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import yaml
from jsonschema import Draft7Validator

ROOT = Path(__file__).resolve().parents[4]


def _yaml(path: str):
    return yaml.safe_load((ROOT / path).read_text(encoding="utf-8"))


def test_w3_workflow_and_pack_metadata_form_one_required_next_node() -> None:
    workflow = _yaml("schemas/workflows/cs_paper_reproduction_workflow.yaml")
    assert [row["nodeKey"] for row in workflow["nodes"]][-2:] == [
        "reference_metric_acquisition",
        "execution_plan_acquisition",
    ]
    assert workflow["dependencies"][-1] == {
        "upstreamNodeKey": "reference_metric_acquisition",
        "downstreamNodeKey": "execution_plan_acquisition",
        "dependencyType": "scientific_dependency",
    }
    overrides = _yaml("schemas/cs_reproduction/research_type_overrides.yaml")
    assert overrides["C"]["required_substages"][-1] == "execution_plan_acquisition"
    composition = _yaml("schemas/cs_reproduction/substage-skill-map.yaml")
    assert [row["skill_id"] for row in composition["execution_plan_acquisition"]] == [
        "execution_plan_interpretation",
        "execution_plan_fact_assembly",
    ]


def test_w3_registry_and_bindings_reuse_upstream_source_instead_of_copying_authority() -> None:
    registry = _yaml("skills/registry.yaml")["skills"]
    by_id = {row["skill_id"]: row for row in registry}
    assert "execution_plan_acquisition" not in by_id
    assert by_id["execution_plan_interpretation"]["runtime"] == "llm_structured"
    assert by_id["execution_plan_fact_assembly"]["runtime"] == "python_adapter"

    bindings = _yaml("skills/skill-bindings.yaml")["bindings"]
    rows = [row for row in bindings if row.get("definitionId") == "cs_reproduction:execution_plan_acquisition"]
    assert {row["skillId"] for row in rows} == {
        "execution_plan_interpretation",
        "execution_plan_fact_assembly",
    }
    serialized = json.dumps(rows, ensure_ascii=False)
    assert "bind:cs_reproduction:reference_metric_acquisition:skill:official_reference_source_acquisition" in serialized
    assert "reference_metric_records" in serialized
    assert "resource_identity_acquisition" not in serialized
    assert "actual_metrics" not in serialized


def test_w3_new_json_schemas_are_draft7_valid() -> None:
    for rel in [
        "skills/cs_research/execution-plan-interpretation/input.schema.json",
        "skills/cs_research/execution-plan-interpretation/output.schema.json",
        "skills/cs_research/execution-plan-fact-assembly/input.schema.json",
        "skills/cs_research/execution-plan-fact-assembly/output.schema.json",
    ]:
        Draft7Validator.check_schema(json.loads((ROOT / rel).read_text(encoding="utf-8")))


def test_w3_python_adapter_registration_is_canonical() -> None:
    module_path = ROOT / "services/api/app/execution/python_adapter_skills.py"
    spec = importlib.util.spec_from_file_location("python_adapter_skills", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    assert module.resolve_python_adapter_skill("execution_plan_fact_assembly") == (
        "execution_plan_fact_assembly",
        "skills/cs_research/execution-plan-fact-assembly",
    )
    assert module.resolve_python_adapter_entrypoint("python_adapter:execution_plan_fact_assembly") == (
        "adapter.execution_plan_fact_assembly_adapter",
        "run_execution_plan_fact_assembly",
        "payload_v1",
    )


def test_w3_has_no_case_specific_branching() -> None:
    paths = [
        ROOT / "skills/cs_research/execution-plan-interpretation/SKILL.md",
        ROOT / "skills/cs_research/execution-plan-fact-assembly/adapter/execution_plan_fact_assembly_adapter.py",
        ROOT / "skills/cs_reproduction/execution-plan-acquisition/SKILL.md",
    ]
    text = "\n".join(path.read_text(encoding="utf-8") for path in paths).lower()
    assert "simcse" not in text
    assert "senteval" not in text
