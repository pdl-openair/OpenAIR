from __future__ import annotations

from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sys
from typing import Any

from jsonschema import Draft7Validator
import pytest


ROOT = Path(__file__).resolve().parents[1]
SEARCH_INTENTS = (
    "core_topic",
    "constraint_focused",
    "mechanism_focused",
    "method_or_strategy",
    "metric_or_performance",
    "adjacent_but_relevant",
    "noise_exclusion",
)


def _load_finalizer():
    path = ROOT / "scripts" / "finalize.py"
    spec = importlib.util.spec_from_file_location("test_retrieval_matrix_finalizer", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _input() -> dict[str, Any]:
    return {
        "intent_understanding": {
            "original_user_query": "Compare long-horizon scientific agents by result reliability.",
            "research_object": "long-horizon scientific agents",
        },
        "constraints_criteria": {
            "interpreted_constraints": [
                {"constraint_name": "agent_scope", "semantic_state": "confirmed"},
                {"constraint_name": "reliability_metric", "semantic_state": "confirmed"},
            ],
            "missing_information": [],
            "research_risks": [
                {
                    "risk": "metric definition varies",
                    "downstream_impact": "Incompatible reliability metrics could be compared.",
                    "affected_constraint_names": ["reliability_metric"],
                }
            ],
        },
    }


def _strategy(intent: str, index: int) -> dict[str, Any]:
    return {
        "search_intent": intent,
        "query_terms": [f"retrieval concept {index}"],
        "must_include_terms": ["scientific agent"],
        "optional_terms": [f"reliability view {index}"],
        "exclude_terms": [],
        "expected_paper_type": "research article",
        "target_database": "",
        "source_constraints": ["agent_scope"],
        "rationale": f"Covers the supported retrieval role {index}.",
        "priority": 1 if index < 2 else 2 if index < 5 else 3,
    }


def _draft() -> dict[str, Any]:
    return {
        "strategies": [_strategy(intent, index) for index, intent in enumerate(SEARCH_INTENTS)],
        "expanded_query_variants": [
            {
                "query": f'"scientific agent" AND "reliability view {index}"',
                "variant_type": "fallback" if index == 7 else "broad",
                "search_intent": SEARCH_INTENTS[index % len(SEARCH_INTENTS)],
                "source_constraints": ["agent_scope", "reliability_metric"],
                "rationale": f"Provides a distinct supported query role {index}.",
                "priority": 1 if index < 3 else 2 if index < 6 else 3,
            }
            for index in range(8)
        ],
        "facets": {
            "core_entities": ["scientific agent"],
            "research_object_en": ["long-horizon scientific agent"],
            "mechanisms": [],
            "target_metrics_en": ["result reliability"],
            "task_context": ["long-horizon research"],
            "must_have_terms": ["scientific agent"],
            "nice_to_have_terms": ["long-horizon research"],
            "negative_contexts": [],
            "evaluation_terms": ["result reliability"],
        },
    }


def _assert_formal_schema(output: dict[str, Any]) -> None:
    schema = json.loads((ROOT / "output.schema.json").read_text(encoding="utf-8"))
    assert list(Draft7Validator(schema).iter_errors(output)) == []


def test_model_schema_contains_only_model_owned_fields() -> None:
    model_schema = json.loads((ROOT / "model_output.schema.json").read_text(encoding="utf-8"))
    formal_schema = json.loads((ROOT / "output.schema.json").read_text(encoding="utf-8"))
    input_schema = json.loads((ROOT / "input.schema.json").read_text(encoding="utf-8"))
    serialized = json.dumps(model_schema, ensure_ascii=False)

    assert list(Draft7Validator(model_schema).iter_errors(_draft())) == []
    for code_owned_field in (
        '"search_matrix"',
        '"quality_report"',
        '"search_matrix_count"',
        '"search_intent_coverage"',
        '"query_facets"',
        '"result_protocol"',
    ):
        assert code_owned_field not in serialized
    assert len(serialized) < len(json.dumps(formal_schema, ensure_ascii=False))
    constraint = input_schema["properties"]["interpreted_constraints"]["items"]
    assert "downstream_filter_rules" not in constraint["required"]
    assert "downstream_filter_rules" not in constraint["properties"]


def test_complete_draft_succeeds_without_rewriting_research_content() -> None:
    finalizer = _load_finalizer()
    draft = _draft()

    result = finalizer.finalize(_input(), deepcopy(draft))

    assert result.status == "succeeded"
    assert result.output is not None
    _assert_formal_schema(result.output)
    assert result.output["expanded_query_variants"] == draft["expanded_query_variants"]
    assert result.output["result_protocol"]["status"] == "ok"
    assert result.output["result_protocol"]["quarantined_items"] == []
    assert result.output["quality_report"]["search_matrix_count"] == 7
    assert result.output["quality_report"]["expanded_query_variants_count"] == 8


def test_content_is_preserved_without_runtime_semantic_reclassification() -> None:
    finalizer = _load_finalizer()
    payload = _input()
    payload["constraints_criteria"]["interpreted_constraints"].append(
        {
            "constraint_name": "unconfirmed_scope",
            "candidate_values": ["unconfirmed chemistry"],
            "semantic_state": "ambiguous",
        }
    )
    draft = _draft()
    draft["strategies"][0]["must_include_terms"] = ["unconfirmed chemistry"]
    draft["strategies"][0]["exclude_terms"] = ["scientific agent"]
    draft["facets"]["negative_contexts"] = ["invented exclusion"]

    result = finalizer.finalize(payload, draft)

    assert result.status == "succeeded"
    assert result.output is not None
    assert result.output["search_matrix"][0]["must_include_terms"] == ["unconfirmed chemistry"]
    assert result.output["search_matrix"][0]["exclude_terms"] == ["scientific agent"]
    assert result.output["assembled_facets"]["negative_contexts"] == ["invented exclusion"]


@pytest.mark.parametrize(
    "query",
    [
        '"research and development" OR "innovation"',
        '"risk or benefit" AND "decision making"',
        '"do not resuscitate" AND "patient outcomes"',
        '"中文科研代理" AND "结果可靠性"',
        '"[EMIM][TFSI]" AND "result reliability"',
    ],
)
def test_language_and_quoted_research_text_do_not_trigger_extra_gate(query: str) -> None:
    finalizer = _load_finalizer()
    draft = _draft()
    draft["expanded_query_variants"][0]["query"] = query

    result = finalizer.finalize(_input(), draft)

    assert result.status == "succeeded", (query, result.errors)
    assert result.output is not None
    assert result.output["expanded_query_variants"][0]["query"] == query


@pytest.mark.parametrize(
    "term",
    [
        "[100] crystallographic direction",
        "Miller direction [110]",
        "concentration [0.1, 0.2] M",
        '["alpha", "beta"]',
        "任意中文术语?",
    ],
)
def test_term_content_is_not_semantically_screened(term: str) -> None:
    finalizer = _load_finalizer()
    draft = _draft()
    draft["strategies"][0]["query_terms"] = [term]

    result = finalizer.finalize(_input(), draft)

    assert result.status == "succeeded", (term, result.errors)
    assert result.output is not None
    assert result.output["search_matrix"][0]["query_terms"] == [term]


@pytest.mark.parametrize(
    "legacy_value",
    [
        ["filter: rate >= 2C"],
        ["exclude everything"],
        ["must_include secret"],
        ["随便一段中文"],
        [{"action": "exclude", "value": "anything"}],
        "random scalar",
    ],
)
def test_deprecated_filter_field_is_ignored_metamorphically(legacy_value: object) -> None:
    finalizer = _load_finalizer()
    baseline = finalizer.finalize(_input(), _draft())
    payload = _input()
    payload["constraints_criteria"]["interpreted_constraints"][0][
        "downstream_filter_rules"
    ] = legacy_value

    result = finalizer.finalize(payload, _draft())

    assert baseline.output is not None
    assert result.status == "succeeded"
    assert result.output == baseline.output


def test_exclusion_variant_is_not_semantically_reauthorized_by_code() -> None:
    finalizer = _load_finalizer()
    draft = _draft()
    draft["expanded_query_variants"][7].update(
        {
            "query": '"scientific agent" AND "unrelated benchmark"',
            "variant_type": "exclusion",
        }
    )

    result = finalizer.finalize(_input(), draft)

    assert result.status == "succeeded"
    assert result.output is not None
    assert result.output["expanded_query_variants"][7]["query"] == (
        '"scientific agent" AND "unrelated benchmark"'
    )
