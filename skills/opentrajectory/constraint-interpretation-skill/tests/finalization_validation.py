from __future__ import annotations

from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sys

from jsonschema import Draft7Validator
import pytest


ROOT = Path(__file__).resolve().parents[1]


def _load_finalizer():
    path = ROOT / "scripts/finalize.py"
    spec = importlib.util.spec_from_file_location("constraint_finalizer_contract", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _input_payload() -> dict:
    return json.loads((ROOT / "examples/request.json").read_text(encoding="utf-8"))


def _current_draft() -> dict:
    draft = json.loads((ROOT / "examples/response.json").read_text(encoding="utf-8"))
    for row in draft["interpreted_constraints"]:
        row.pop("downstream_filter_rules", None)
    return draft


def test_current_provider_and_formal_schemas_exclude_deprecated_field() -> None:
    draft = _current_draft()
    for schema_name in ("model_output.schema.json", "output.schema.json"):
        schema = json.loads((ROOT / schema_name).read_text(encoding="utf-8"))
        serialized = json.dumps(schema, ensure_ascii=False)
        assert "downstream_filter_rules" not in serialized
        assert list(Draft7Validator(schema).iter_errors(draft)) == []


def test_skill_and_finalizer_have_no_semantic_filter_classifier() -> None:
    skill_text = (ROOT / "SKILL.md").read_text(encoding="utf-8")
    source = (ROOT / "scripts/finalize.py").read_text(encoding="utf-8")

    assert "downstream_filter_rules" not in skill_text
    assert "_is_hard_filter" not in source
    assert "re.search" not in source
    assert "re.findall" not in source


def test_current_draft_is_preserved_and_passes_formal_schema() -> None:
    draft = _current_draft()

    result = _load_finalizer().finalize(_input_payload(), deepcopy(draft))

    assert result.status == "succeeded"
    assert result.output == draft
    schema = json.loads((ROOT / "output.schema.json").read_text(encoding="utf-8"))
    assert list(Draft7Validator(schema).iter_errors(result.output)) == []


def test_exact_source_index_restores_only_the_input_owned_phrase() -> None:
    draft = _current_draft()
    expected_phrase = _input_payload()["raw_constraints"][0]["phrase"]
    first = draft["interpreted_constraints"][0]
    assert first.pop("original_phrase") == expected_phrase
    first["source_constraint_index"] = 0

    result = _load_finalizer().finalize(_input_payload(), draft)

    assert result.status == "succeeded"
    assert result.output is not None
    assert result.output["interpreted_constraints"][0]["original_phrase"] == expected_phrase
    assert "source_constraint_index" not in result.output["interpreted_constraints"][0]


@pytest.mark.parametrize(
    "legacy_value",
    [
        ["filter: rate >= 2C"],
        ["exclude everything"],
        ["must_include secret"],
        ["随便一段中文"],
        [{"action": "exclude", "value": "anything"}],
    ],
)
def test_legacy_filter_field_is_ignored_metamorphically(legacy_value: object) -> None:
    expected = _current_draft()
    draft = deepcopy(expected)
    draft["interpreted_constraints"][0]["downstream_filter_rules"] = legacy_value

    result = _load_finalizer().finalize(_input_payload(), draft)

    assert result.status == "degraded"
    assert result.errors == ("deprecated_field_ignored",)
    assert result.output == expected




def test_finalizer_does_not_reinterpret_scientific_text() -> None:
    draft = _current_draft()
    draft["interpreted_constraints"][0].update({
        "original_phrase": "temperature <= 500K + cycles >= 500",
        "interpreted_value": "模型正式判断保持原样",
        "domain_rationale": "   ",
        "evidence_basis": "self-described basis",
    })

    result = _load_finalizer().finalize(_input_payload(), draft)

    assert result.status == "succeeded"
    assert result.output is not None
    assert result.output["interpreted_constraints"][0]["interpreted_value"] == (
        "模型正式判断保持原样"
    )
    assert result.output["interpreted_constraints"][0]["domain_rationale"] == "   "


def test_formal_schema_remains_the_final_structure_gate() -> None:
    draft = _current_draft()
    draft["interpreted_constraints"][0].pop("constraint_name")
    model_schema = json.loads((ROOT / "model_output.schema.json").read_text(encoding="utf-8"))

    errors = list(Draft7Validator(model_schema).iter_errors(draft))

    assert errors
