from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft7Validator

from app.execution.structured_output_provider_adapter import (
    PROVIDER_SCHEMA_UNSUPPORTED,
    ProviderSchemaCapabilityError,
    apply_provider_schema_transport,
)


ROOT = Path(__file__).resolve().parents[3]


def _json(path: str) -> dict:
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def _skill(path: str) -> dict:
    return yaml.safe_load((ROOT / path).read_text(encoding="utf-8"))


def _assert_huiyan_rejects(schema: dict) -> None:
    with pytest.raises(ProviderSchemaCapabilityError) as caught:
        apply_provider_schema_transport(
            "huiyan_chat_completions",
            payload={"response_format": {"type": "json_object"}},
            response_format={"type": "json_object"},
            provider_schema=schema,
        )
    assert caught.value.code == PROVIDER_SCHEMA_UNSUPPORTED


def test_cs_deterministic_classes_remain_code_owned_and_formally_strict() -> None:
    exploratory_skill = _skill("skills/cs_research/exploratory-run/skill.yaml")
    comparison_skill = _skill("skills/cs_research/reference-metric-comparison/skill.yaml")
    assert exploratory_skill["runtime"] == "python_adapter"
    assert exploratory_skill["runtime_contract"]["kind"] == "external_adapter"
    assert comparison_skill["runtime"] == "python_adapter"
    assert comparison_skill["runtime_contract"]["kind"] == "deterministic"
    assert "finalization" not in exploratory_skill
    assert "finalization" not in comparison_skill

    exploratory = _json("skills/cs_research/exploratory-run/output.schema.json")
    unverified_gate = exploratory["definitions"]["resourceUsage"]["allOf"][0]["then"]
    gate_validator = Draft7Validator(unverified_gate)
    assert gate_validator.is_valid({})
    assert not gate_validator.is_valid({"actual_usage": {}})
    assert not gate_validator.is_valid({"consumption_evidence": {}})

    comparison = _json("skills/cs_research/reference-metric-comparison/input.schema.json")
    rule_schema = comparison["properties"]["reference_metrics"]["items"]["properties"]["comparison_rule"]
    rule_validator = Draft7Validator(rule_schema)
    assert rule_validator.is_valid({"kind": "exact"})
    assert not rule_validator.is_valid({"kind": "exact", "tolerance": 0})
    assert rule_validator.is_valid({"kind": "absolute_tolerance", "tolerance": 0})
    assert not rule_validator.is_valid({"kind": "absolute_tolerance"})

    input_choice = Draft7Validator({"oneOf": comparison["oneOf"]})
    assert input_choice.is_valid({"actual_metrics": {}})
    assert input_choice.is_valid({"actual_metric_records": {}})
    assert not input_choice.is_valid(
        {"actual_metrics": {}, "actual_metric_records": {}}
    )


def test_ot_model_semantics_keep_not_pattern_and_fail_closed() -> None:
    schema = _json(
        "skills/opentrajectory/retrieval-matrix-generation/model_output.schema.json"
    )
    query = schema["properties"]["expanded_query_variants"]["items"]["properties"]["query"]
    term = schema["definitions"]["term"]
    assert set(query["not"]) == {"pattern"}
    assert set(term["not"]) == {"pattern"}
    assert "pattern" not in query
    assert "pattern" not in term
    _assert_huiyan_rejects(schema)


def test_pvd_model_semantics_keep_both_not_classes_and_fail_closed() -> None:
    provider = _json("skills/pvd/pvd-research-record-structuring/provider.schema.json")
    model = _json("skills/pvd/pvd-research-record-structuring/model_output.schema.json")
    formal = _json("skills/pvd/pvd-research-record-structuring/output.schema.json")

    model_group = model["properties"]["record_groups"]["items"]
    provider_group = provider["properties"]["record_groups"]["items"]
    ambiguous_not = {
        "anyOf": [
            {"required": ["study_id"]},
            {"required": ["series_id"]},
            {"required": ["sample_id"]},
        ]
    }
    assert model_group["allOf"][1]["then"]["not"] == ambiguous_not
    assert provider_group["allOf"][1]["then"]["not"] == ambiguous_not

    formal_fact_not = formal["$defs"]["fact"]["allOf"][0]["else"]["not"]
    provider_fact_not = provider_group["properties"]["material"]["properties"][
        "target_system"
    ]["allOf"][0]["else"]["not"]
    assert formal_fact_not == {"required": ["value"]}
    assert provider_fact_not == formal_fact_not
    _assert_huiyan_rejects(provider)


def test_native_positive_pattern_remains_unchanged() -> None:
    positive = "^[A-Z]+$"
    schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["term"],
        "properties": {"term": {"type": "string", "pattern": positive}},
    }
    adapted = apply_provider_schema_transport(
        "huiyan_chat_completions",
        payload={"response_format": {"type": "json_object"}},
        response_format={"type": "json_object"},
        provider_schema=schema,
    )
    transported = adapted["response_format"]["json_schema"]["schema"]
    assert transported["properties"]["term"]["pattern"] == positive
