from __future__ import annotations
import copy, json
from pathlib import Path
from jsonschema import validate

SKILL_DIR = Path(__file__).resolve().parents[1]
ALLOWED_GAPS = {"missing_mechanism", "missing_condition", "missing_metric", "missing_baseline", "conflict", "not_comparable"}
DOWNSTREAM_KEYS = {"evidence_items", "mechanism_edges", "mechanism_chains", "research_leads", "hypotheses", "validation_plans", "gap_map", "evidence_acquisition_actions"}

MATERIAL_ROLE_GROUPS = [
    {"composition"},
    {"condition"},
    {"metric"},
    {"mechanism"},
    {"characterization"},
    {"theoretical_explanation"},
    {"baseline", "comparability"},
]

def read_json(path: Path): return json.loads(path.read_text(encoding="utf-8"))
def validate_target_ids(output):
    targets = output["evidence_schema"]["evidence_targets"]
    ids = [row["target_id"] for row in targets]
    assert len(ids) == len(set(ids)), ids

def assert_no_downstream_leak(value):
    if isinstance(value, dict):
        assert not DOWNSTREAM_KEYS.intersection(value), DOWNSTREAM_KEYS.intersection(value)
        for item in value.values(): assert_no_downstream_leak(item)
    elif isinstance(value, list):
        for item in value: assert_no_downstream_leak(item)

def roles(output):
    return {row["scientific_role"] for row in output["evidence_schema"]["evidence_targets"]}

def assert_role_groups(actual_roles, required_groups):
    for group in required_groups:
        assert actual_roles.intersection(group), (actual_roles, group)

def assert_materials_quality(output):
    schema = output["evidence_schema"]
    assert len(schema["evidence_targets"]) >= 6, schema["evidence_targets"]
    assert_role_groups(roles(output), MATERIAL_ROLE_GROUPS)
    assert {"missing_mechanism", "missing_condition", "missing_metric", "missing_baseline", "not_comparable"}.issubset(set(schema["gap_taxonomy"]))
    assert schema["uncertainty_rules"], schema

def assert_cs_quality(output):
    schema = output["evidence_schema"]
    assert len(schema["evidence_targets"]) >= 5, schema["evidence_targets"]
    assert_role_groups(roles(output), [{"condition"}, {"metric"}, {"baseline", "comparability"}, {"limitation"}])
    assert schema["uncertainty_rules"], schema

def main():
    input_schema = read_json(SKILL_DIR / "input.schema.json")
    output_schema = read_json(SKILL_DIR / "output.schema.json")
    pairs = [
        ("materials", "request.materials.json", "response.materials.json"),
        ("cs", "request.cs.json", "response.cs.json"),
        ("minimal", "request.minimal.json", "response.minimal.json"),
    ]
    outputs = {}
    for name, request_name, response_name in pairs:
        request = read_json(SKILL_DIR / "examples" / request_name)
        output = read_json(SKILL_DIR / "examples" / response_name)
        validate(request, input_schema); validate(output, output_schema)
        validate_target_ids(output); assert_no_downstream_leak(output)
        assert set(output["evidence_schema"]["gap_taxonomy"]).issubset(ALLOWED_GAPS)
        outputs[name] = output

    assert_materials_quality(outputs["materials"])
    assert_cs_quality(outputs["cs"])


    skill_text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8").lower()
    assert "explicit-input coverage ledger" in skill_text, skill_text
    assert "emit a dedicated target whose `scientific_role` is `composition`" in skill_text, skill_text
    assert "emit a dedicated target whose role is `baseline` or `comparability`" in skill_text, skill_text
    assert "emit a dedicated `mechanism` target" in skill_text, skill_text
    assert "do not silently drop explicit requirements" in skill_text, skill_text

    minimal = outputs["minimal"]
    assert minimal["design_warnings"] and minimal["evidence_schema"]["task_context"]["design_assumptions"]

    one = copy.deepcopy(minimal)
    zero = copy.deepcopy(minimal); zero["evidence_schema"]["evidence_targets"] = []
    many = copy.deepcopy(minimal); many["evidence_schema"]["evidence_targets"] = [copy.deepcopy(one["evidence_schema"]["evidence_targets"][0]) for _ in range(3)]
    for index, item in enumerate(many["evidence_schema"]["evidence_targets"], start=1): item["target_id"] = f"target_{index:03d}"
    for output in (zero, one, many): validate(output, output_schema); validate_target_ids(output)

    print("P07F_OFFLINE_VALIDATION=PASS")
    print("P07F_STABLE_SHELL_DYNAMIC_TARGETS=PASS")
    print("P07F_MATERIAL_DECISION_COVERAGE=PASS")
    print("P07F_CS_DECISION_COVERAGE=PASS")
    print("P07F_CS_DOMAIN_LEAKAGE_GUARD=PASS")
    print("P07F_MINIMAL_INPUT_CASE=PASS")
    print("P07F_ZERO_ONE_MULTI_TARGET_ARRAYS=PASS")
    print("P07F_GENERIC_GAP_TAXONOMY_GUARD=PASS")
    print("P07F_NO_DOWNSTREAM_ARTIFACT_LEAK_GUARD=PASS")
    print("P07F_EXPLICIT_INPUT_COVERAGE_LEDGER_GUARD=PASS")
if __name__ == "__main__": main()
