"""Schema and contract validation for candidate-tiering Skill."""

import json
import pathlib
import sys
from jsonschema import Draft7Validator

if sys.platform == "win32":
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8")

ROOT = pathlib.Path(__file__).resolve().parents[1]
VALID_TIERS = {"core", "mechanism_support", "method_inspiration", "benchmark_or_evaluation", "background", "excluded"}


def load_json(rel: str) -> dict:
    return json.loads((ROOT / rel).read_text(encoding="utf-8"))


def main() -> int:
    errors_found = False
    checks = []

    try:
        input_schema = load_json("input.schema.json")
        output_schema = load_json("output.schema.json")
        req = load_json("examples/request.json")
        resp = load_json("examples/response.json")

        Draft7Validator.check_schema(input_schema)
        Draft7Validator.check_schema(output_schema)
        checks.append("✓ Schema definitions are valid Draft-07 JSON Schema")

        in_errors = list(Draft7Validator(input_schema).iter_errors(req))
        if in_errors:
            print("✗ Example request validation errors:")
            for e in in_errors:
                print(f"  - {e.message} at {'.'.join(str(p) for p in e.path)}")
            errors_found = True
        else:
            checks.append("✓ Example request validates against input schema")

        out_errors = list(Draft7Validator(output_schema).iter_errors(resp))
        if out_errors:
            print("✗ Example response validation errors:")
            for e in out_errors:
                print(f"  - {e.message} at {'.'.join(str(p) for p in e.path)}")
            errors_found = True
        else:
            checks.append("✓ Example response validates against output schema")

        # 4) source_tiers not empty
        tiers = resp.get("source_tiers", [])
        if not tiers:
            print("✗ source_tiers is empty")
            errors_found = True
        else:
            checks.append("✓ source_tiers is non-empty")

        # 5) each entry has valid tier
        for i, row in enumerate(tiers):
            t = row.get("tier")
            if t not in VALID_TIERS:
                print(f"✗ source_tiers[{i}].tier invalid: {t}")
                errors_found = True
        if not errors_found:
            checks.append("✓ all source_tiers entries use legal tier enum")

        # 6) tier_counts consistent with source_tiers counts
        calc = {k: 0 for k in VALID_TIERS}
        for row in tiers:
            calc[row["tier"]] += 1
        report_counts = resp.get("tiering_report", {}).get("tier_counts", {})
        for k in VALID_TIERS:
            if int(report_counts.get(k, -1)) != calc[k]:
                print(f"✗ tier_counts mismatch on {k}: report={report_counts.get(k)} calc={calc[k]}")
                errors_found = True
        if not errors_found:
            checks.append("✓ tier_counts consistent with source_tiers")

        # 7) degraded/facets flags enter risk_flags
        req_degraded = any(
            bool((c.get("facets_quality_flags") or {}).get("degraded_scoring")) or
            bool((c.get("facets_quality_flags") or {}).get("facets_incomplete"))
            for c in req.get("candidate_scores", [])
        )
        risk_flags = set(resp.get("tiering_report", {}).get("risk_flags", []))
        if req_degraded and "contains_degraded_scoring_inputs" not in risk_flags:
            print("✗ degraded input exists but risk flag missing")
            errors_found = True
        else:
            checks.append("✓ degraded inputs are reflected in tiering_report risk_flags")

        # 8) empty input fallback is explicit
        sys.path.insert(0, str(ROOT))
        from adapter.tiering_adapter import tier_candidates

        empty_resp = tier_candidates({"candidate_scores": []})
        if "missing_candidate_scores" not in empty_resp.get("tiering_report", {}).get("risk_flags", []):
            print("✗ Empty candidate_scores did not trigger missing_candidate_scores")
            errors_found = True
        else:
            checks.append("✓ empty candidate_scores triggers explicit fallback risk")

        # 8) no #11 fields
        forbidden = {"topk_selection", "selected_for_p03", "p03_acquisition_plan"}
        if forbidden & set(resp.keys()):
            print(f"✗ forbidden root fields present: {forbidden & set(resp.keys())}")
            errors_found = True
        for i, row in enumerate(tiers):
            overlap = forbidden & set(row.keys())
            if overlap:
                print(f"✗ source_tiers[{i}] has forbidden fields: {overlap}")
                errors_found = True
        if not errors_found:
            checks.append("✓ no #11 fields present")

        print("\nValidation Results:")
        for line in checks:
            print(f"  {line}")

        if errors_found:
            print("\n❌ Validation failed")
            return 1

        print("\n✅ All validations passed")
        return 0

    except Exception as e:
        print(f"✗ Validation error: {e}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
