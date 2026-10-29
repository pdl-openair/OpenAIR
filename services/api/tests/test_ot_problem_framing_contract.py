from __future__ import annotations

import json
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[3]
OT = ROOT / "skills" / "opentrajectory"
WORKFLOWS = ROOT / "schemas" / "workflows"


def _yaml(path: Path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _schema_body(value: dict) -> dict:
    value = dict(value)
    value.pop("$schema", None)
    value.pop("title", None)
    return value


def _declared_property_names(value: object) -> set[str]:
    if isinstance(value, dict):
        names = set(value.get("properties", {}))
        for child in value.values():
            names.update(_declared_property_names(child))
        return names
    if isinstance(value, list):
        names: set[str] = set()
        for child in value:
            names.update(_declared_property_names(child))
        return names
    return set()


def test_ot_workflow_v2_formally_replaces_two_problem_nodes_with_one_problem_framing_node() -> None:
    v1 = _yaml(WORKFLOWS / "opentrajectory_research_workflow.yaml")
    v2 = _yaml(WORKFLOWS / "opentrajectory_research_workflow_v2.yaml")

    assert v1["workflowVersion"] == 1
    assert v2["workflowVersion"] == 2

    keys = [row["nodeKey"] for row in v2["nodes"]]
    assert keys[:2] == ["problem_framing", "retrieval_strategy_design"]
    assert "intent_understanding" not in keys
    assert "constraints_criteria" not in keys
    assert any(
        row["upstreamNodeKey"] == "problem_framing"
        and row["downstreamNodeKey"] == "retrieval_strategy_design"
        for row in v2["dependencies"]
    )


def test_problem_framing_output_exposes_only_professional_semantics() -> None:
    merged = _json(OT / "problem-framing-generation" / "output.schema.json")

    assert merged["required"] == ["intent_understanding", "constraints_criteria"]
    assert merged["additionalProperties"] is False
    intent = merged["properties"]["intent_understanding"]
    constraints = merged["properties"]["constraints_criteria"]
    assert "requirement_confidence" not in intent["properties"]
    assert constraints["required"] == [
        "interpreted_constraints",
        "missing_information",
        "research_risks",
    ]
    assert "quality_report" not in constraints["properties"]
    constraint = constraints["properties"]["interpreted_constraints"]["items"]
    assert constraint["properties"]["semantic_state"]["enum"] == [
        "confirmed",
        "unknown",
        "unrestricted",
        "ambiguous",
        "conflicting",
    ]
    assert "confidence" not in constraint["properties"]
    assert "needs_user_confirmation" not in constraint["properties"]
    assert not {
        "confidence",
        "coverage_level",
        "formal_gate",
        "meta",
        "needs_manual_review",
        "needs_user_confirmation",
        "quality_report",
        "ready",
        "requirement_confidence",
        "status",
    } & _declared_property_names(merged)


def test_problem_framing_professional_fields_have_current_fresh_consumers() -> None:
    retrieval = _json(OT / "retrieval-matrix-generation" / "input.schema.json")
    evidence = _json(OT / "evidence-schema-design-skill" / "input.schema.json")

    assert retrieval["properties"]["constraints_criteria"]["required"] == [
        "interpreted_constraints",
        "missing_information",
        "research_risks",
    ]
    evidence_constraints = evidence["definitions"]["constraints_criteria_artifact"]
    assert evidence_constraints["required"] == [
        "interpreted_constraints",
        "missing_information",
        "research_risks",
    ]
    assert "quality_report" not in evidence_constraints["properties"]

    skill = _yaml(OT / "problem-framing-generation" / "skill.yaml")
    assert skill["capability_contract"]["judgment_support"] == {
        "deterministic": False,
        "domain_professional": True,
    }


def test_only_problem_framing_is_enabled_as_current_problem_modeling_authority() -> None:
    registry = _yaml(ROOT / "skills" / "registry.yaml")["skills"]
    rows = {row["skill_id"]: row for row in registry}

    assert rows["opentrajectory_problem_framing"]["enabled"] is True
    assert rows["opentrajectory_intent_understanding"]["enabled"] is False
    assert rows["opentrajectory_constraints_criteria"]["enabled"] is False
    assert rows["opentrajectory_retrieval_strategy_design"]["input_skills"] == [
        "opentrajectory_problem_framing"
    ]

    composition = _yaml(ROOT / "schemas" / "opentrajectory" / "substage-skill-map.yaml")
    assert [row["skill_id"] for row in composition["problem_framing"]] == [
        "problem_framing_generation"
    ]
    # Historical v1 compositions remain readable, but their L2 definitions are disabled.
    stage_map = _yaml(ROOT / "schemas" / "opentrajectory" / "stage-substage-map.yaml")
    problem_rows = {row["key"]: row for row in stage_map["problem_modeling"]["substages"]}
    assert problem_rows["intent_understanding"]["enabled"] is False
    assert problem_rows["constraints_criteria"]["enabled"] is False
    assert problem_rows["problem_framing"].get("enabled", True) is True


def test_retrieval_skill_accepts_formal_problem_framing_and_keeps_legacy_direct_call_compatibility() -> None:
    schema = _json(OT / "retrieval-matrix-generation" / "input.schema.json")
    assert {tuple(item["required"]) for item in schema["anyOf"]} == {
        ("intent_understanding", "constraints_criteria"),
        ("user_query", "interpreted_constraints"),
    }
    assert "intent_understanding" in schema["properties"]
    assert "constraints_criteria" in schema["properties"]


def test_t01_contract_covers_materials_cs_explicit_constraints_and_unknown_ambiguity() -> None:
    payload = _json(
        OT
        / "problem-framing-generation"
        / "tests"
        / "t01_contract_cases.json"
    )
    categories = {row["category"] for row in payload["cases"]}
    assert {"materials", "cs", "explicit_constraints", "ambiguity_unknown"} <= categories

    by_id = {row["case_id"]: row for row in payload["cases"]}
    assert by_id["explicit_conflict"]["must_not_resolve_conflict"] is True
    assert by_id["unknown_vs_unrestricted"]["must_distinguish"] == [
        "unknown",
        "unrestricted",
    ]
    assert set(by_id["numeric_direction_unknown"]["must_not_infer"]) == {
        "上限",
        "下限",
        "截止电压",
    }
