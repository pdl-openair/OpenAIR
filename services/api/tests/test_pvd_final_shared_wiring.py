from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import jsonschema
import yaml

ROOT = Path(__file__).resolve().parents[3]
ACTIVE = {"research_goal_equipment_constraints","research_retrieval_experiment_design","literature_experiment_record_verification","evidence_applicability_comparability_analysis","experimental_feedback_round_analysis","process_factor_candidate_experiment_analysis","parameter_experiment_plan_feasibility_validation","round_experiment_plan_parameter_recommendation"}
RETIRED = {"process_result_comparability_analysis","process_factor_candidate_direction_analysis","parameter_feasibility_experiment_plan_validation","experimental_feedback_round_comparison","four_parameter_recommendation"}
def _yaml(path: str): return yaml.safe_load((ROOT / path).read_text(encoding="utf-8"))
def _bindings(): return {row["bindingId"]: row for row in _yaml("skills/skill-bindings.yaml")["bindings"]}
def test_current_v2_definition_enablement_is_explicit_and_retired_definitions_stay_off():
    rows={row["key"]:row for stage in _yaml("schemas/pvd/stage-substage-map.yaml").values() for row in stage.get("substages",[])}
    assert all(rows[key]["enabled"] is True for key in ACTIVE)
    assert all(rows[key]["enabled"] is False for key in RETIRED)
def test_cap2_shortage_reentry_is_direct_from_cap3_artifact_and_schema_identical():
    b = _bindings()["bind:pvd:research_retrieval_experiment_design:skill:pvd_evidence_retrieval_design"]
    fields = {row["targetField"]: row for row in b["inputSources"]["fields"]}
    assert "defaultValue" not in fields["prior_retrieval_design"]
    assert fields["prior_retrieval_design"]["source"] == {
        "kind": "artifact",
        "bindingId": "bind:pvd:literature_experiment_record_verification:skill:pvd_literature_evidence_acquisition_closure",
        "bindingVersion": 1,
        "field": "prior_retrieval_design",
    }
    assert fields["evidence_shortage_facts"]["source"] == {
        "kind": "artifact",
        "bindingId": "bind:pvd:literature_experiment_record_verification:skill:pvd_literature_evidence_acquisition_closure",
        "bindingVersion": 1,
        "field": "evidence_shortage_facts",
    }
    assert fields["evidence_shortage_facts"]["defaultValue"] == []
    cap2 = json.loads((ROOT / "skills/pvd/pvd-evidence-retrieval-design/input.schema.json").read_text(encoding="utf-8"))
    cap3 = json.loads((ROOT / "skills/pvd/pvd-literature-evidence-acquisition-closure/output.schema.json").read_text(encoding="utf-8"))
    cap3_input = json.loads((ROOT / "skills/pvd/pvd-literature-evidence-acquisition-closure/input.schema.json").read_text(encoding="utf-8"))
    assert cap3["$defs"]["evidenceShortageFact"] == cap2["$defs"]["shortageFact"]
    assert cap3["properties"]["prior_retrieval_design"]["type"] == cap3_input["properties"]["retrieval_design"]["type"]
    assert cap3["properties"]["prior_retrieval_design"]["properties"] == cap3_input["properties"]["retrieval_design"]["properties"]


def test_cap2_first_round_and_shortage_reentry_pass_the_formal_runtime_schema():
    schema = json.loads(
        (
            ROOT
            / "skills/pvd/pvd-evidence-retrieval-design/input.schema.json"
        ).read_text(encoding="utf-8")
    )
    first_round = {
        "structured_context": {"research_goal": "Optimize PVD parameters."},
        "recommendation_parameter_set": {
            "parameters": [{"parameter_key": "pulse_power"}]
        },
        "parameter_admission_judgments": [],
        "evidence_shortage_facts": [],
    }
    jsonschema.validate(first_round, schema)
    assert "prior_retrieval_design" not in first_round

    historical = {
        **first_round,
        "prior_retrieval_design": {
            "contract": "pvd-evidence-retrieval-design/v1",
            "mode": "core",
            "query_variants": [
                {
                    "query_id": "query_001",
                    "search_intent": "constraint_focused",
                    "query": "AlScN sputtering pulse power",
                    "covered_parameter_keys": ["pulse_power"],
                    "rationale": "Cover the confirmed parameter.",
                    "priority": 1,
                }
            ],
            "design_rationale": "Initial retrieval design.",
        },
        "evidence_shortage_facts": [
            {
                "shortage_id": "coverage_gap:power",
                "source_artifact_type": "coverage_report",
                "source_artifact_ref": "artifact://coverage/power",
                "reason_code": "coverage_gap",
                "description": "Comparable power evidence remains insufficient.",
                "affected_parameter_keys": ["pulse_power"],
                "affected_target_ids": ["target_power"],
            }
        ],
    }
    jsonschema.validate(historical, schema)
def test_cap3_shortage_projection_is_deterministic_and_empty_when_sufficient():
    p=ROOT/"skills/pvd/pvd-literature-evidence-acquisition-closure/adapter/literature_evidence_closure.py"
    spec=importlib.util.spec_from_file_location("pvd_shortage_projection_test",p); assert spec and spec.loader
    m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    result={"task_id":"task-1","coverage":{"decision_coverage_sufficient":False,"missing_required_target_ids":["t1"],"missing_required_parameter_keys":["power"]},"unresolved_gaps":[{"target_id":"t1","covered_parameter_keys":["power"],"description":"Need traceable power evidence."}]}
    facts=m._build_evidence_shortage_facts(result)
    assert facts==[{"shortage_id":"coverage_gap:t1","source_artifact_type":"coverage_report","source_artifact_ref":"pvd_literature_evidence_acquisition_closure:task-1:coverage","reason_code":"coverage_gap","description":"Need traceable power evidence.","affected_parameter_keys":["power"],"affected_target_ids":["t1"]}]
    assert m._build_evidence_shortage_facts({"coverage":{"decision_coverage_sufficient":True}})==[]
def test_cap5_to_6_to_7_uses_direction_analysis_without_mapper():
    bs=_bindings(); c6=bs["bind:pvd:parameter_experiment_plan_feasibility_validation:skill:pvd_parameter_experiment_plan_feasibility_validation"]; c7=bs["bind:pvd:round_experiment_plan_parameter_recommendation:skill:pvd_round_experiment_plan_parameter_recommendation"]
    f6={r["targetField"]:r["source"] for r in c6["inputSources"]["fields"]}; f7={r["targetField"]:r["source"] for r in c7["inputSources"]["fields"]}
    assert "candidate_analysis" not in f6 and "candidate_analysis" not in f7
    assert f6["direction_analysis"]["field"]=="$" and f7["direction_analysis"]["field"]=="$" and f7["feasibility_validation"]["field"]=="$"
def test_cap8_receives_explicit_experiment_and_original_artifacts():
    b=_bindings()["bind:pvd:experimental_feedback_round_analysis:skill:pvd_experimental_feedback_round_analysis"]
    inputs={r["targetField"]:r["source"] for r in b["inputSources"]["fields"]}; runtime={r["targetField"]:r["source"] for r in b["runtimeInputs"]["fields"]}
    assert runtime["experiment_ref"]=={"kind":"invocationContext","field":"experiment_ref"}
    assert inputs["scientific_state_facts"]=={"kind":"scientificState","field":"$"}
    assert all(inputs[k]["field"]=="$" for k in ["evidence_comparability","factor_analysis","recommendation"])
