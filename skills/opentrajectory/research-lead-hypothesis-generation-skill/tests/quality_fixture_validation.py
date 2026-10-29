from __future__ import annotations

import json
from pathlib import Path

from jsonschema import validate

SKILL_DIR = Path(__file__).resolve().parents[1]

EXPECTED_CASES = {
    "01_high_consistency",
    "02_conflicting_evidence",
    "03_single_weak_record",
    "04_author_interpretation_only",
    "05_quantitative_comparable",
    "06_incomparable_conditions",
    "07_mechanism_packet_complete",
    "08_mechanism_packet_missing",
    "09_gap_heavy",
    "10_insufficient_bounded_answer",
}


def main() -> None:
    schema = json.loads((SKILL_DIR / "input.schema.json").read_text(encoding="utf-8"))
    suite = json.loads((SKILL_DIR / "evals/quality_cases.json").read_text(encoding="utf-8"))
    cases = suite["cases"]
    ids = {case["id"] for case in cases}
    assert ids == EXPECTED_CASES, (ids, EXPECTED_CASES)
    assert len(cases) == 10

    for case in cases:
        validate(case["input"], schema)
        assert case["oracle"]["must"].strip()
        assert case["oracle"]["must_not"].strip()
        assert case["oracle"]["expected_layers"].strip()

    assert "mechanism_packet" in next(x for x in cases if x["id"] == "07_mechanism_packet_complete")["input"]
    assert "mechanism_packet" not in next(x for x in cases if x["id"] == "08_mechanism_packet_missing")["input"]

    boundary = json.loads((SKILL_DIR / "evals/boundary_cases.json").read_text(encoding="utf-8"))["cases"]
    assert len(boundary) == 2
    for case in boundary:
        validate(case["input"], schema)
        assert case["oracle"]["must"].strip()
        assert case["oracle"]["must_not"].strip()

    print("QUALITY_EVAL_CASE_COUNT=10")
    print("QUALITY_EVAL_INPUT_SCHEMA=PASS")
    print("QUALITY_EVAL_REQUIRED_COVERAGE=PASS")
    print("QUALITY_BOUNDARY_CASE_COUNT=2")
    print("QUALITY_BOUNDARY_INPUT_SCHEMA=PASS")


if __name__ == "__main__":
    main()
