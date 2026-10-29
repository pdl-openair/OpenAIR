from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

from jsonschema import Draft7Validator

SKILL = Path(__file__).resolve().parents[1]
UPSTREAM = SKILL.parent


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    input_schema = load(SKILL / "input.schema.json")
    model_schema = load(SKILL / "model_output.schema.json")
    output_schema = load(SKILL / "output.schema.json")
    for schema in (input_schema, model_schema, output_schema):
        Draft7Validator.check_schema(schema)

    assert input_schema["required"] == ["task_id", "paper_packets", "evidence_schema"]
    assert input_schema["properties"]["paper_packets"]["items"]["$ref"] == (
        "../deepxiv-progressive-reading-skill/output.schema.json#/properties/paper_packets/items"
    )
    assert input_schema["properties"]["evidence_schema"]["$ref"] == (
        "../evidence-schema-design-skill/output.schema.json#/definitions/evidence_schema"
    )
    assert "research_context" not in input_schema["properties"]

    assert list(model_schema["properties"]) == ["research_records"]
    assert list(output_schema["properties"])[0] == "research_records"
    assert "evidence_items" not in json.dumps(
        {"model": model_schema, "output": output_schema}, ensure_ascii=False
    )

    model_item = model_schema["properties"]["research_records"]["items"]
    final_item = output_schema["properties"]["research_records"]["items"]
    deterministic = {"record_id", "source", "condition_status"}
    assert deterministic.isdisjoint(model_item["properties"])
    assert deterministic.issubset(set(final_item["required"]))
    assert model_item["properties"] == {
        key: value for key, value in final_item["properties"].items() if key not in deterministic
    }
    assert model_item["required"] == [
        key for key in final_item["required"] if key not in deterministic
    ]
    # Draft uses a single exhaustive record_type discriminator for Huiyan
    # transport; final schema keeps richer final-only conditionals.
    assert {clause["if"]["properties"]["record_type"]["const"] for clause in model_item["allOf"]} == set(
        model_item["properties"]["record_type"]["enum"]
    )
    assert any(
        clause.get("if", {}).get("properties", {}).get("assertion_mode") is not None
        for clause in final_item["allOf"]
    )
    assert "condition_status" not in json.dumps(model_item, ensure_ascii=False)
    assert '"contains"' not in json.dumps(model_schema, ensure_ascii=False)
    assert final_item["properties"]["condition_status"]["enum"] == [
        "reported",
        "partially_unknown",
        "not_applicable",
    ]

    semantic_draft = load(SKILL / "examples" / "model_semantic_output.json")
    assert not list(Draft7Validator(model_schema).iter_errors(semantic_draft))
    assert all("condition_status" not in row for row in semantic_draft["research_records"])

    bad = deepcopy(semantic_draft)
    bad["research_records"][0]["record_id"] = "rr:spoof"
    assert list(Draft7Validator(model_schema).iter_errors(bad))

    bad = deepcopy(semantic_draft)
    bad["research_records"][0]["source"] = {"candidate_id": "spoof"}
    assert list(Draft7Validator(model_schema).iter_errors(bad))

    bad = deepcopy(semantic_draft)
    bad["evidence_items"] = []
    assert list(Draft7Validator(model_schema).iter_errors(bad))

    bad = deepcopy(semantic_draft)
    bad["findings"] = []
    assert list(Draft7Validator(model_schema).iter_errors(bad))

    # Record identity and assertion identity remain explicit and non-overlapping.
    direct = semantic_draft["research_records"][0]
    assert direct["information_identity"] == "direct_source_fact"
    assert direct["assertion_mode"] == "direct_observation"
    mechanism = semantic_draft["research_records"][2]
    assert mechanism["record_type"] == "mechanism_statement"
    assert mechanism["information_identity"] == "author_interpretation"
    assert mechanism["assertion_mode"] == "author_interpretation"

    bad = deepcopy(semantic_draft)
    # Draft keeps mechanism assertion_mode narrowed for provider admission; the
    # assertion_mode↔information_identity coupling lives in output.schema.json.
    bad["research_records"][2]["assertion_mode"] = "direct_observation"
    assert list(Draft7Validator(model_schema).iter_errors(bad))

    bad = deepcopy(semantic_draft)
    bad["research_records"][0]["condition_status"] = "reported"
    assert list(Draft7Validator(model_schema).iter_errors(bad))

    bad = deepcopy(semantic_draft)
    bad["research_records"][0]["result"]["values"] = []
    assert list(Draft7Validator(model_schema).iter_errors(bad))

    finalizer_source = (SKILL / "scripts" / "finalize.py").read_text(encoding="utf-8")
    assert "services.api" not in finalizer_source
    assert "opentrajectory_structured_runtime_guard" not in finalizer_source
    assert "allowed_evidence_forms" not in finalizer_source

    # Validate the examples against the supplied latest read-only upstream contracts.
    request = load(SKILL / "examples" / "request.json")
    upstream = load(SKILL / "examples" / "upstream_progressive_reading_output.json")
    upstream_schema = load(UPSTREAM / "deepxiv-progressive-reading-skill" / "output.schema.json")
    evidence_schema_contract = load(
        UPSTREAM / "evidence-schema-design-skill" / "output.schema.json"
    )
    assert not list(Draft7Validator(upstream_schema).iter_errors(upstream))
    assert request["paper_packets"] == upstream["paper_packets"]
    wrapped_evidence_schema = {
        "status": "ready",
        "evidence_schema": request["evidence_schema"],
        "design_warnings": [],
    }
    assert not list(Draft7Validator(evidence_schema_contract).iter_errors(wrapped_evidence_schema))

    print("CONTRACT_TESTS=PASS")
    print("RESEARCH_RECORD_ONLY_PUBLIC_OUTPUT=PASS")
    print("LATEST_UPSTREAM_CONTRACTS=PASS")
    print("MODEL_DETERMINISTIC_FIELDS_REJECTED=PASS")
    print("MODEL_AGGREGATE_AUTHORITY_REMOVED=PASS")
    print("PROVIDER_SCHEMA_CONTAINS_DEPENDENCY_REMOVED=PASS")
    print("INFORMATION_IDENTITY_BOUNDARY=PASS")
    print("NO_SHARED_RUNTIME_DEPENDENCY=PASS")


if __name__ == "__main__":
    main()
