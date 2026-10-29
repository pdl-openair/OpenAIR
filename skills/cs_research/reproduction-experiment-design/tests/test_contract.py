"""B03 contract tests for source-grounded reproduction experiment design."""

from __future__ import annotations

import json
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[4]
DEFINITION_ID = "cs_reproduction:reproduction_experiment_design"
METHOD_ID = "cs_reproduction_experiment_design_grounding"


def _load_json(relative_path: str) -> dict:
    return json.loads((ROOT / relative_path).read_text(encoding="utf-8"))


def _load_yaml(relative_path: str) -> object:
    return yaml.safe_load((ROOT / relative_path).read_text(encoding="utf-8"))


def _method_contract() -> dict:
    text = (
        ROOT
        / "schemas/cs_reproduction/specs/02-solution/cs-reproduction-experiment-design.md"
    ).read_text(encoding="utf-8")
    payload = text.split("## 执行方法\n\n```yaml\n", 1)[1].split("\n```", 1)[0]
    return yaml.safe_load(payload)


def test_experiment_design_method_has_source_facts_and_fail_closed_closure() -> None:
    method = _method_contract()
    assert method["methodId"] == METHOD_ID
    assert [role["roleId"] for role in method["roles"]] == [
        "execution_source_interpretation",
        "execution_fact_assembly",
        "experiment_design_closure",
    ]
    assert method["completionRoleIds"] == ["experiment_design_closure"]
    assert [role["failurePolicy"] for role in method["roles"]] == [
        "fail_closed",
        "fail_closed",
        "fail_closed",
    ]
    assert method["roles"][-1]["failurePolicy"] == "fail_closed"
    assert set(method["roles"][-1]["qualificationContract"]["requiredInputRefs"]) == {
        "reproduction_baseline_and_criteria",
        "execution_plan_facts",
    }


def test_baseline_source_chain_does_not_skip_missing_required_material() -> None:
    text = (
        ROOT
        / "schemas/cs_reproduction/specs/01-problem/cs-reproduction-baseline-criteria.md"
    ).read_text(encoding="utf-8")
    payload = text.split("## 执行方法\n\n```yaml\n", 1)[1].split("\n```", 1)[0]
    method = yaml.safe_load(payload)
    policies = {
        role["roleId"]: role["failurePolicy"] for role in method["roles"]
    }
    assert policies["code_identity_acquisition"] == "fail_closed"
    assert policies["official_source_acquisition"] == "fail_closed"
    assert policies["reference_interpretation"] == "fail_closed"
    assert policies["reference_fact_assembly"] == "fail_closed"


def test_registered_roles_reuse_existing_execution_fact_chain() -> None:
    registry = _load_yaml("skills/registry.yaml")
    skills = {row["skill_id"]: row for row in registry["skills"]}
    expected_roles = {
        "execution_plan_interpretation": "execution_source_interpretation",
        "execution_plan_fact_assembly": "execution_fact_assembly",
        "reproduction_experiment_design": "experiment_design_closure",
    }
    for skill_id, role_id in expected_roles.items():
        skill = skills[skill_id]
        assert f"{METHOD_ID}:{role_id}" in skill["execution_method_roles"]
        assert "solution_design" in skill["stages"]
    assert skills["execution_plan_interpretation"]["output_file"] == "result.json"


def test_bindings_preserve_official_source_and_reference_lineage() -> None:
    document = _load_yaml("skills/skill-bindings.yaml")
    bindings = {
        row["skillId"]: row
        for row in document["bindings"]
        if row["definitionId"] == DEFINITION_ID
    }
    assert set(bindings) == {
        "execution_plan_interpretation",
        "execution_plan_fact_assembly",
        "reproduction_experiment_design",
    }

    interpretation_fields = {
        row["targetField"]: row
        for row in bindings["execution_plan_interpretation"]["inputSources"]["fields"]
    }
    assert interpretation_fields["source_package"]["source"]["bindingId"] == (
        "bind:cs_reproduction:reproduction_baseline_criteria:skill:"
        "official_reference_source_acquisition"
    )
    assert interpretation_fields["reproduction_baseline_and_criteria"]["source"] == {
        "kind": "artifact",
        "bindingId": (
            "bind:cs_reproduction:reproduction_baseline_criteria:skill:"
            "reproduction_baseline_and_criteria"
        ),
        "bindingVersion": 2,
        "field": "$",
    }
    assert interpretation_fields["reference_metrics"]["source"] == {
        "kind": "artifact",
        "bindingId": (
            "bind:cs_reproduction:reproduction_baseline_criteria:skill:"
            "reference_metric_fact_assembly"
        ),
        "bindingVersion": 2,
        "field": "reference_metric_records",
    }

    fact_fields = {
        row["targetField"]: row
        for row in bindings["execution_plan_fact_assembly"]["inputSources"]["fields"]
    }
    assert fact_fields["interpretation"]["source"]["bindingId"] == (
        "bind:cs_reproduction:reproduction_experiment_design:skill:"
        "execution_plan_interpretation"
    )

    closure = bindings["reproduction_experiment_design"]
    closure_fields = {
        row["targetField"]: row for row in closure["inputSources"]["fields"]
    }
    assert closure_fields["execution_plan_facts"]["sourcePresence"] == "optional"
    assert closure_fields["execution_plan_facts"]["source"]["bindingId"] == (
        "bind:cs_reproduction:reproduction_experiment_design:skill:"
        "execution_plan_fact_assembly"
    )
    runtime_fields = closure["runtimeInputs"]["fields"]
    assert runtime_fields == [
        {
            "targetField": "research_goal",
            "source": {
                "kind": "invocationContext",
                "field": "canonical_user_query",
            },
        }
    ]


def test_design_schema_separates_authorities_and_never_accepts_actual_results() -> None:
    input_schema = _load_json(
        "skills/cs_research/reproduction-experiment-design/input.schema.json"
    )
    assert set(input_schema["required"]) == {
        "research_goal",
        "reproduction_baseline_and_criteria",
    }
    assert input_schema["properties"]["execution_plan_facts"]["properties"][
        "schema_version"
    ]["const"] == "openair-execution-plan/v1"

    output_schema = _load_json(
        "skills/cs_research/reproduction-experiment-design/output.schema.json"
    )
    assert {
        "source_required_facts",
        "system_selected_details",
        "project_constraints",
        "intentional_deviations",
        "unknowns",
    } <= set(output_schema["required"])
    assert not {
        "actual_metrics",
        "actual_metric_records",
        "reproduction_execution_record",
    } & set(output_schema["properties"])


def test_interpretation_input_accepts_the_matching_versioned_contract() -> None:
    input_schema = _load_json(
        "skills/cs_research/execution-plan-interpretation/input.schema.json"
    )
    assert {"research_goal", "reproduction_baseline_and_criteria"} <= set(
        input_schema["properties"]
    )
    alternatives = [set(row["required"]) for row in input_schema["anyOf"]]
    assert {"research_goal"} in alternatives
    assert {"reproduction_baseline_and_criteria"} in alternatives
