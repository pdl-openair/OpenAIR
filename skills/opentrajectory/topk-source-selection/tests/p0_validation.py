"""Schema and contract validation for topk-source-selection Skill."""

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


def load_json(rel: str):
    return json.loads((ROOT / rel).read_text(encoding="utf-8"))


def main() -> int:
    errors = False
    checks: list[str] = []

    input_schema = load_json("input.schema.json")
    output_schema = load_json("output.schema.json")
    req = load_json("examples/request.json")
    resp = load_json("examples/response.json")

    Draft7Validator.check_schema(input_schema)
    Draft7Validator.check_schema(output_schema)
    checks.append("✓ Schema definitions are valid Draft-07 JSON Schema")

    in_err = list(Draft7Validator(input_schema).iter_errors(req))
    if in_err:
        errors = True
        print("✗ Request schema errors")
        for e in in_err:
            print(f"  - {e.message}")
    else:
        checks.append("✓ Example request validates against input schema")

    out_err = list(Draft7Validator(output_schema).iter_errors(resp))
    if out_err:
        errors = True
        print("✗ Response schema errors")
        for e in out_err:
            print(f"  - {e.message}")
    else:
        checks.append("✓ Example response validates against output schema")

    topk = resp.get("topk_selection", {})
    selected = topk.get("selected_for_p03", [])
    cfg = req.get("topk_config", {})
    dynamic_cap = int(cfg.get("dynamic_cap", 60))

    if len(selected) <= dynamic_cap:
        checks.append("✓ selected_for_p03 count <= dynamic_cap")
    else:
        errors = True
        print(f"✗ selected_for_p03 count {len(selected)} > dynamic_cap {dynamic_cap}")

    # basic descending order
    scores = [float(x.get("final_score", 0)) for x in selected]
    if all(scores[i] >= scores[i + 1] - 1e-9 for i in range(len(scores) - 1)):
        checks.append("✓ selected_for_p03 is basic descending by final_score")
    else:
        errors = True
        print("✗ selected_for_p03 is not descending by final_score")

    if all(str(x.get("tier")) != "excluded" for x in selected):
        checks.append("✓ selected_for_p03 contains no excluded tier")
    else:
        errors = True
        print("✗ selected_for_p03 contains excluded tier")

    # degraded case
    sys.path.insert(0, str(ROOT))
    from adapter.topk_adapter import build_topk_selection
    degraded_resp = build_topk_selection({"source_tiers": []})
    degraded = degraded_resp.get("topk_selection", {}).get("degraded") is True
    if degraded:
        checks.append("✓ empty source_tiers triggers degraded=true")
    else:
        errors = True
        print("✗ empty source_tiers did not trigger degraded=true")

    # stable order + duplicate tier removal + low-pool degradation
    runtime_rows = [
        {"candidate_id": "b", "dedupe_key": "b", "title": "Alpha two", "final_score": 0.9, "tier": "core", "pool": "core_pool"},
        {"candidate_id": "a", "dedupe_key": "a", "title": "Alpha one", "final_score": 0.9, "tier": "core", "pool": "core_pool"},
        {"candidate_id": "s", "dedupe_key": "s", "title": "Support", "final_score": 0.7, "tier": "mechanism_support", "pool": "support_pool"},
    ]
    cfg = {"core_min": 2, "dynamic_target": 2, "dynamic_cap": 3, "score_min": 0.4, "extended_score_min": 0.6}
    forward = build_topk_selection({"source_tiers": runtime_rows, "topk_config": cfg})["topk_selection"]
    reverse = build_topk_selection({"source_tiers": list(reversed(runtime_rows)), "topk_config": cfg})["topk_selection"]
    forward_keys = [x.get("dedupe_key") for x in forward.get("selected_for_p03", [])]
    reverse_keys = [x.get("dedupe_key") for x in reverse.get("selected_for_p03", [])]
    if forward_keys != reverse_keys:
        errors = True
        print("✗ Top-K output order changed under equivalent input permutation")
    else:
        checks.append("✓ Top-K order is stable under input permutation")

    duplicate = build_topk_selection({"source_tiers": runtime_rows + [dict(runtime_rows[0])], "topk_config": cfg})["topk_selection"]
    if duplicate.get("selection_stats", {}).get("duplicate_tier_rows_removed", 0) != 1:
        errors = True
        print("✗ Duplicate tier row was not deterministically removed")
    else:
        checks.append("✓ duplicate tier rows are removed deterministically")

    fallback = build_topk_selection({"source_tiers": runtime_rows[:1], "topk_config": {"core_min": 3, "dynamic_target": 3, "dynamic_cap": 3}})["topk_selection"]
    if fallback.get("degraded") is not True:
        errors = True
        print("✗ Low candidate pool fallback did not set degraded=true")
    else:
        checks.append("✓ low candidate pool fallback sets degraded=true")

    forbidden = {"candidate_scores", "source_tiers", "tiering_report"}
    if forbidden & set(resp.keys()):
        errors = True
        print(f"✗ root has forbidden fields: {forbidden & set(resp.keys())}")
    elif forbidden & set(topk.keys()):
        errors = True
        print(f"✗ topk_selection has forbidden fields: {forbidden & set(topk.keys())}")
    else:
        checks.append("✓ no #9/#10 boundary fields in output")

    print("\nValidation Results:")
    for c in checks:
        print(f"  {c}")

    if errors:
        print("\n❌ Validation failed")
        return 1
    print("\n✅ All validations passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
