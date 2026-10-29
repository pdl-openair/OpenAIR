"""Engineering and contract validation for candidate-source-scoring."""

from __future__ import annotations

import copy
import json
import pathlib
import sys

from jsonschema import Draft7Validator, validators

if sys.platform == "win32":
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8")

ROOT = pathlib.Path(__file__).resolve().parents[1]
UPSTREAM_SCHEMA_PATH = ROOT.parent / "title-abstract-screening-skill" / "output.schema.json"
DOWNSTREAM_SCHEMA_PATH = ROOT.parent / "candidate-tiering" / "input.schema.json"


def load_json(path: pathlib.Path | str) -> dict:
    target = pathlib.Path(path)
    if not target.is_absolute():
        target = ROOT / target
    return json.loads(target.read_text(encoding="utf-8"))


def schema_validator(schema: dict):
    validator_cls = validators.validator_for(schema)
    validator_cls.check_schema(schema)
    return validator_cls(schema)


def assert_valid(schema: dict, value: object, label: str) -> None:
    errors = sorted(schema_validator(schema).iter_errors(value), key=lambda err: list(err.path))
    if errors:
        joined = "\n".join(f"- {err.message} at {list(err.path)}" for err in errors)
        raise AssertionError(f"{label} validation failed:\n{joined}")


def upstream_artifact_from_request(request: dict) -> dict:
    include_count = sum(row["screening_decision"] == "include" for row in request["screened_candidates"])
    maybe_count = sum(row["screening_decision"] == "maybe" for row in request["screened_candidates"])
    exclude_count = sum(row["screening_decision"] == "exclude" for row in request["screened_candidates"])
    return {
        "task_id": "task-contract-direct",
        "skill_id": "title-abstract-screening-skill",
        "screened_candidates": request["screened_candidates"],
        "summary": {
            "total_candidates": len(request["screened_candidates"]),
            "include_count": include_count,
            "maybe_count": maybe_count,
            "exclude_count": exclude_count,
        },
        "warnings": [],
        "errors": [],
    }


def main() -> int:
    sys.path.insert(0, str(ROOT))
    from adapter.scoring_adapter import score_candidates

    input_schema = load_json("input.schema.json")
    output_schema = load_json("output.schema.json")
    request = load_json("examples/request.json")

    Draft7Validator.check_schema(input_schema)
    Draft7Validator.check_schema(output_schema)
    assert_valid(input_schema, request, "example request")

    original_candidates = copy.deepcopy(request["screened_candidates"])
    actual = score_candidates(copy.deepcopy(request))
    assert request["screened_candidates"] == original_candidates, "production scoring mutated screened candidates"
    assert_valid(output_schema, actual, "production output")

    upstream_schema = load_json(UPSTREAM_SCHEMA_PATH)
    upstream_item = upstream_schema["$defs"]["ScreenedCandidate"]
    scorer_item = input_schema["properties"]["screened_candidates"]["items"]
    assert set(scorer_item["required"]).issubset(set(upstream_item["required"]))
    assert scorer_item["properties"]["screening_decision"]["enum"] == upstream_item["properties"]["screening_decision"]["enum"]
    assert scorer_item["properties"]["access_status"]["enum"] == upstream_item["properties"]["access_status"]["enum"]

    upstream_artifact = upstream_artifact_from_request(request)
    assert_valid(upstream_schema, upstream_artifact, "upstream screening artifact")
    direct_request = {
        "screened_candidates": upstream_artifact["screened_candidates"],
        "research_context": request["research_context"],
    }
    assert direct_request["screened_candidates"] is upstream_artifact["screened_candidates"]
    assert_valid(input_schema, direct_request, "upstream-to-scorer direct input")
    direct_output = score_candidates(direct_request)
    assert_valid(output_schema, direct_output, "direct scoring output")

    downstream_schema = load_json(DOWNSTREAM_SCHEMA_PATH)
    assert_valid(
        downstream_schema,
        {"candidate_scores": direct_output["candidate_scores"]},
        "scorer-to-tiering direct handoff",
    )

    legacy_request = {
        "deduped_candidates": [],
        "facets": {},
    }
    assert list(Draft7Validator(input_schema).iter_errors(legacy_request)), "old input unexpectedly remains valid"
    assert "deduped_candidates" not in input_schema["properties"]
    assert "facets" not in input_schema["properties"]
    for old_contract_key in ("research_evidence_contract", "evidence_contract", "contract"):
        assert old_contract_key not in input_schema["properties"]

    assert all(0 <= row["final_score"] <= 1 for row in actual["candidate_scores"])
    assert actual["candidate_scores"][0]["candidate_id"] == "cand_001"
    assert actual["candidate_scores"][0]["final_score"] > actual["candidate_scores"][1]["final_score"]
    assert "access_degraded:metadata_only" in actual["candidate_scores"][1]["degradation_reasons"]
    assert actual["candidate_scores"][1]["score_breakdown"]["off_domain_penalty"] >= 0.6

    forbidden = {"tier", "pool", "topk_selection", "selected_for_p03"}
    for row in actual["candidate_scores"]:
        assert not (forbidden & set(row))
    assert not (forbidden & set(actual))

    expected_response = load_json("examples/response.json")
    if actual != expected_response:
        raise AssertionError("examples/response.json is not the current production output for examples/request.json")

    print("ENGINEERING_VALIDATION = PASS")
    print("UPSTREAM_DIRECT_CONTRACT = PASS")
    print("DOWNSTREAM_DIRECT_CONTRACT = PASS")
    print("OLD_INPUT_CONTRACT_REMOVED = PASS")
    print("PRODUCTION_OUTPUT_SCHEMA = PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
