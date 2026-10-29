from __future__ import annotations

from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sys

from jsonschema import Draft7Validator


SKILL = Path(__file__).resolve().parents[1]


def _json(name: str) -> dict:
    return json.loads((SKILL / name).read_text(encoding="utf-8"))


def _finalizer():
    path = SKILL / "scripts" / "finalize.py"
    spec = importlib.util.spec_from_file_location("d5_condition_status_finalize", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _example() -> tuple[dict, dict]:
    return _json("examples/request.json"), _json("examples/model_semantic_output.json")


def test_provider_draft_has_detail_authority_only() -> None:
    schema = _json("model_output.schema.json")
    item = schema["properties"]["research_records"]["items"]

    assert "condition_status" not in item["properties"]
    assert "condition_status" not in item["required"]
    assert '"contains"' not in json.dumps(schema, ensure_ascii=False)

    _request, draft = _example()
    assert not list(Draft7Validator(schema).iter_errors(draft))
    duplicate_authority = deepcopy(draft)
    duplicate_authority["research_records"][0]["condition_status"] = "reported"
    assert list(Draft7Validator(schema).iter_errors(duplicate_authority))


def test_condition_status_truth_table_is_deterministic() -> None:
    finalizer = _finalizer()

    assert finalizer._condition_status([]) == "not_applicable"
    assert finalizer._condition_status([{"status": "reported"}]) == "reported"
    assert finalizer._condition_status([{"status": "unknown"}]) == "partially_unknown"
    assert finalizer._condition_status([
        {"status": "reported"},
        {"status": "unknown"},
    ]) == "partially_unknown"


def test_model_details_form_complete_strict_formal_result_without_supplement() -> None:
    finalizer = _finalizer()
    request, draft = _example()
    original_details = deepcopy([
        row["conditions"] for row in draft["research_records"]
    ])

    result = finalizer.finalize(request, draft)

    assert result.output is not None
    assert [row["condition_status"] for row in result.output["research_records"]] == [
        "reported",
        "reported",
        "not_applicable",
    ]
    assert [row["conditions"] for row in result.output["research_records"]] == original_details
    assert not list(
        Draft7Validator(_json("output.schema.json")).iter_errors(result.output)
    )


def test_unknown_detail_controls_aggregate_without_code_created_fact() -> None:
    finalizer = _finalizer()
    request, draft = _example()
    condition = draft["research_records"][0]["conditions"][0]
    condition.update({
        "name": "ionic conductivity",
        "status": "unknown",
        "value_text": None,
        "unit": None,
    })

    result = finalizer.finalize(request, draft)

    assert result.output is not None
    row = result.output["research_records"][0]
    assert row["condition_status"] == "partially_unknown"
    assert row["conditions"] == [condition]


def test_direct_and_durable_merge_form_the_same_formal_result() -> None:
    finalizer = _finalizer()
    request, draft = _example()
    direct = finalizer.finalize(request, deepcopy(draft))

    provider_subresults = [
        {"research_records": deepcopy(draft["research_records"][:2])},
        {"research_records": deepcopy(draft["research_records"][2:])},
    ]
    merged = {"research_records": []}
    for subresult in provider_subresults:
        merged["research_records"].extend(subresult["research_records"])
    nested = finalizer.finalize(request, merged)

    assert direct.output == nested.output
