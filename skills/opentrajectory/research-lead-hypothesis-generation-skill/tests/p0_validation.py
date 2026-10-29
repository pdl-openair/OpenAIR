from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path

from jsonschema import validate

SKILL_DIR = Path(__file__).resolve().parents[1]
ROOT = Path(__file__).resolve().parents[4]
GUARD = ROOT / "services/api/app/execution/opentrajectory_structured_runtime_guard.py"


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def allowed_ids(request: dict) -> tuple[set[str], set[str]]:
    record_ids = {x["record_id"] for x in request.get("research_records", [])}
    packet = request.get("mechanism_packet") or {}
    edge_ids = {x["edge_id"] for x in packet.get("mechanism_edges", [])}
    return record_ids, edge_ids


def assert_grounded(request: dict, output: dict) -> None:
    record_ids, edge_ids = allowed_ids(request)

    def subset(values, allowed, label):
        assert set(values).issubset(allowed), f"{label}: unknown ids {set(values) - allowed}"

    subset(output["direct_answer"]["supporting_record_ids"], record_ids, "direct_answer")
    for row in output["evidence_supported_conclusions"]:
        subset(row["supporting_record_ids"], record_ids, "conclusion records")
        subset(row["supporting_mechanism_edge_ids"], edge_ids, "conclusion mechanism edges")
    for row in output["conflicts"]:
        subset(row["record_ids"], record_ids, "conflict records")
        subset(row["mechanism_context_edge_ids"], edge_ids, "conflict mechanism edges")
    for row in output["evidence_gaps"]:
        subset(row["related_record_ids"], record_ids, "gap records")
        subset(row["mechanism_context_edge_ids"], edge_ids, "gap mechanism edges")
    lead_keys = {row["local_lead_key"] for row in output["research_leads"]}
    for row in output["research_leads"]:
        subset(row["supporting_record_ids"], record_ids, "lead records")
        subset(row["mechanism_context_edge_ids"], edge_ids, "lead mechanism edges")
    for row in output["hypotheses"]:
        subset(row["supporting_record_ids"], record_ids, "hypothesis records")
        subset(row["mechanism_context_edge_ids"], edge_ids, "hypothesis mechanism edges")
        assert row["source_local_lead_key"] in lead_keys


def load_guard():
    if not GUARD.is_file():
        return None
    spec = importlib.util.spec_from_file_location("ot_guard", GUARD)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> None:
    request = read_json(SKILL_DIR / "examples/request.json")
    draft = read_json(SKILL_DIR / "examples/response.draft.json")
    final_example = read_json(SKILL_DIR / "examples/response.json")
    input_schema = read_json(SKILL_DIR / "input.schema.json")
    output_schema = read_json(SKILL_DIR / "output.schema.json")

    validate(request, input_schema)
    validate(final_example, output_schema)
    assert_grounded(request, final_example)
    assert final_example["direct_answer"]["answer"].strip()
    assert final_example["audit"]["traceability_mode"] == output_schema["properties"]["audit"]["properties"]["traceability_mode"]["const"]
    assert "validation_plans" not in final_example

    quality_cases = read_json(SKILL_DIR / "evals/quality_cases.json")["cases"]
    assert len(quality_cases) == 10
    for case in quality_cases:
        validate(case["input"], input_schema)

    guard = load_guard()
    if guard is None:
        print("P08A_RUNTIME_GUARD=SKIPPED_HANDOFF_PACKAGE_HAS_NO_RUNTIME")
    else:
        out, diag = guard.apply_opentrajectory_runtime_guard(
            skill_id="research-lead-hypothesis-generation-skill",
            input_payload=request,
            output_payload=copy.deepcopy(draft),
        )
        assert diag["status"] == "passed"
        validate(out, output_schema)
        assert_grounded(request, out)
        assert out["direct_answer"]["answer"].strip()

        bad = copy.deepcopy(draft)
        bad["direct_answer"]["supporting_record_ids"] = ["rr:" + "0" * 32]
        try:
            guard.apply_opentrajectory_runtime_guard(
                skill_id="research-lead-hypothesis-generation-skill",
                input_payload=request,
                output_payload=bad,
            )
        except guard.RuntimeGuardRejected:
            pass
        else:
            raise AssertionError("runtime guard accepted an unknown ResearchRecord id")

    print("P08A_INPUT_SCHEMA=PASS")
    print("P08A_FORMAL_OUTPUT_EXAMPLE=PASS")
    print("P08A_RECORD_AND_MECHANISM_REFERENCE_GROUNDING=PASS")
    print("P08A_DIRECT_ANSWER_PRESENT=PASS")
    print("P08A_QUALITY_CASES_10=PASS")


if __name__ == "__main__":
    main()
