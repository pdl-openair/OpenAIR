from __future__ import annotations

from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sys

SKILL = Path(__file__).resolve().parents[1]


def load(name: str):
    return json.loads((SKILL / "examples" / name).read_text(encoding="utf-8"))


def finalizer_module():
    path = SKILL / "scripts" / "finalize.py"
    spec = importlib.util.spec_from_file_location("research_record_finalize", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def main() -> None:
    f = finalizer_module()
    request = load("request.json")
    draft = load("model_semantic_output.json")

    result = f.finalize(deepcopy(request), deepcopy(draft))
    assert result.status == "partial", result
    assert result.output is not None
    output = result.output
    assert list(output)[0] == "research_records"
    assert output["schema_id"] == "schema_example_001"
    assert output["summary"]["status"] == "partial"
    assert output["summary"]["research_record_count"] == 3
    assert output["summary"]["uncovered_required_target_ids"] == ["target_003"]
    assert output["summary"]["dropped_invalid_count"] == 0
    assert output["extraction_warnings"] == ["required_target_uncovered:target_003"]
    assert [row["condition_status"] for row in output["research_records"]] == [
        "reported",
        "reported",
        "not_applicable",
    ]
    assert all(
        "condition_status" not in row for row in draft["research_records"]
    )
    assert [row["conditions"] for row in output["research_records"]] == [
        row["conditions"] for row in draft["research_records"]
    ]
    assert f._condition_status([]) == "not_applicable"
    assert f._condition_status([{"status": "reported"}]) == "reported"
    assert f._condition_status([{"status": "unknown"}]) == "partially_unknown"
    assert f._condition_status([
        {"status": "reported"},
        {"status": "unknown"},
    ]) == "partially_unknown"
    for row in output["research_records"]:
        assert row["record_id"].startswith("rr:")
        assert row["source"]["candidate_id"] == "cand_001"
        assert row["source"]["anchor_id"] == row["source_anchor_id"]
        assert len(row["source"]["content_hash"]) == 64
        assert "evidence_id" not in row

    repeated = f.finalize(deepcopy(request), deepcopy(draft))
    assert repeated.output == output

    duplicate = deepcopy(draft)
    duplicate["research_records"].append(deepcopy(duplicate["research_records"][0]))
    duplicate_result = f.finalize(deepcopy(request), duplicate)
    assert duplicate_result.output["summary"]["dropped_duplicate_count"] == 1
    assert duplicate_result.output["summary"]["research_record_count"] == 3

    paraphrased = deepcopy(draft)
    row = paraphrased["research_records"][0]
    row["research_object"] = "Conductivity measurement of the studied sample"
    row["source_quote"] = "A paraphrased source passage"
    row["result"]["values"][0]["value_text"] = "999"
    row["result"]["values"][0]["unit"] = "S cm−1"
    row["relevant_target_ids"] = ["target_999"]
    preserved = f.finalize(deepcopy(request), paraphrased)
    assert preserved.output is not None
    assert preserved.output["summary"]["research_record_count"] == 3
    assert preserved.output["summary"]["dropped_invalid_count"] == 0
    for key in ("research_object", "source_quote", "result", "relevant_target_ids"):
        assert preserved.output["research_records"][0][key] == row[key]

    missing_key = deepcopy(draft)
    del missing_key["research_records"][0]["research_object"]
    rejected = f.finalize(deepcopy(request), missing_key)
    assert rejected.status == "failed"
    assert rejected.output is None
    assert any("required" in str(error) for error in rejected.errors)

    unmatched = deepcopy(draft)
    for row in unmatched["research_records"]:
        row["source_anchor_id"] = "anchor_missing"
    retained = f.finalize(deepcopy(request), unmatched)
    assert retained.output["summary"]["research_record_count"] == 3
    assert all(row["source"]["source_locator"] == "" for row in retained.output["research_records"])

    changed_packet = deepcopy(request)
    changed_packet["paper_packets"][0]["sections"][0]["content"] += " changed"
    changed_packet["paper_packets"][0]["source_anchors"][0]["arxiv_id"] = "9999.99999"
    assert f.finalize(changed_packet, deepcopy(draft)).output is not None

    empty = f.finalize(deepcopy(request), {"research_records": []})
    assert empty.status == "succeeded"
    assert empty.output["summary"]["research_record_count"] == 0
    assert empty.output["summary"]["uncovered_required_target_ids"] == ["target_001", "target_003"]

    no_packets = deepcopy(request)
    no_packets["paper_packets"] = []
    no_material = f.finalize(no_packets, {"research_records": []})
    assert no_material.status == "succeeded"
    assert no_material.output["summary"]["input_paper_packet_count"] == 0

    print("RECORD_CONTENT_PRESERVED=PASS")
    print("EXISTING_REQUIRED_KEYS=PASS")
    print("DEDUPE_AND_DETERMINISM=PASS")
    print("SOURCE_BINDING_WITHOUT_FABRICATION=PASS")
    print("CONDITION_AGGREGATE_TRUTH_TABLE=PASS")
    print("EMPTY_RESULT=PASS")


if __name__ == "__main__":
    main()
