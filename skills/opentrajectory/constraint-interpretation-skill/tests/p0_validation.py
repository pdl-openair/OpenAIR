import json
import pathlib
import re
from jsonschema import Draft7Validator

ROOT = pathlib.Path(__file__).resolve().parents[1]
CJK_RE = re.compile(r"[\u3400-\u4DBF\u4E00-\u9FFF\uF900-\uFAFF]")
SNAKE_CASE_RE = re.compile(r"^[a-z][a-z0-9_]*$")
LONG_EXPLANATION_HINT_RE = re.compile(r"\b(may refer to|not specified|for example|e\.g\.|i\.e\.|because|which means)\b", re.IGNORECASE)


def load_json(rel):
    return json.loads((ROOT / rel).read_text(encoding="utf-8"))


def main():
    input_schema = load_json("input.schema.json")
    output_schema = load_json("output.schema.json")
    example_request = load_json("examples/request.json")
    example_response = load_json("examples/response.json")

    checks = []

    # 1. JSON Schema validity
    Draft7Validator.check_schema(input_schema)
    Draft7Validator.check_schema(output_schema)
    checks.append("schema definitions are valid Draft-07 JSON Schema")

    # 2. Example input validates
    list(Draft7Validator(input_schema).iter_errors(example_request)) or checks.append("example request validates against input.schema.json")
    errors = list(Draft7Validator(input_schema).iter_errors(example_request))
    if errors:
        raise AssertionError("example request schema errors: " + "; ".join(e.message for e in errors))

    # 3. Legacy original_phrase input accepted
    legacy_request = {
        "user_query": "比较快充条件下循环稳定性",
        "raw_constraints": [{"original_phrase": "温度范围25-45°C", "source": "legacy"}],
    }
    errors = list(Draft7Validator(input_schema).iter_errors(legacy_request))
    if errors:
        raise AssertionError("legacy raw_constraints input not accepted: " + "; ".join(e.message for e in errors))
    checks.append("legacy raw_constraints[].original_phrase is accepted")

    # 4. Output example validates
    errors = list(Draft7Validator(output_schema).iter_errors(example_response))
    if errors:
        raise AssertionError("example response schema errors: " + "; ".join(e.message for e in errors))
    checks.append("example response validates against output.schema.json")

    # 5. No CJK in downstream_search_terms
    for c in example_response["interpreted_constraints"]:
        for term in c["downstream_search_terms"]:
            if CJK_RE.search(term):
                raise AssertionError(f"CJK found in downstream_search_terms: {term!r}")
    checks.append("example downstream_search_terms contain no CJK characters")

    # 6. Explicit numerical constraints use high-confidence range in example
    explicit_names = {"voltage_window", "temperature_range"}
    bad = []
    for c in example_response["interpreted_constraints"]:
        if c["constraint_name"] in explicit_names and not (0.85 <= c["confidence"] <= 0.92):
            bad.append((c["constraint_name"], c["confidence"]))
    if bad:
        raise AssertionError(f"explicit numerical constraints outside 0.85-0.92: {bad}")
    checks.append("explicit numerical example constraints use 0.85-0.92 confidence")

    # 7. Old 0.62 confidence rule not present in example response
    if any(abs(c["confidence"] - 0.62) < 1e-9 for c in example_response["interpreted_constraints"]):
        raise AssertionError("old confidence=0.62 rule appears in example response")
    checks.append("old confidence=0.62 rule absent from example response")

    # 8. Fuzzy fast charging remains uncertain without inventing retrieval policy.
    fast = next(c for c in example_response["interpreted_constraints"] if c["constraint_name"] == "fast_charging_condition")
    if "downstream_filter_rules" in fast:
        raise AssertionError("current constraint output must not contain deprecated filter rules")

    if fast["candidate_values"]:
        raise AssertionError(f"fuzzy fast charging must not invent candidate values: {fast['candidate_values']!r}")
    guessed_fast_values = re.compile(r"(?:^|[^0-9])(?:1C|2C|3C|>=?\s*1C|>=?\s*2C)", re.IGNORECASE)
    fast_text = " ".join([fast["domain_rationale"], *fast["downstream_search_terms"], *fast["candidate_values"]])
    if guessed_fast_values.search(fast_text):
        raise AssertionError("fuzzy fast charging example contains domain-guessed C-rate values")
    checks.append("fuzzy fast charging stays uncertain without filter rules or guessed C-rate")

    # 9. Phase2 rationale budget accepts concise English explanations while staying bounded.
    rationale_schema = output_schema["properties"]["interpreted_constraints"]["items"]["properties"]["domain_rationale"]
    if rationale_schema.get("maxLength") != 512:
        raise AssertionError(f"domain_rationale maxLength must be 512, got: {rationale_schema.get('maxLength')!r}")
    representative_english_rationale = (
        "Restricts included studies to bench-scale (laboratory) batch reactor configurations "
        "rather than continuous flow or pilot/field scales because reactor type affects "
        "irradiance distribution and kinetics."
    )
    representative_constraint = dict(example_response["interpreted_constraints"][0])
    representative_constraint["domain_rationale"] = representative_english_rationale
    representative_response = dict(example_response)
    representative_response["interpreted_constraints"] = [representative_constraint]
    errors = list(Draft7Validator(output_schema).iter_errors(representative_response))
    if errors:
        raise AssertionError("representative concise English domain_rationale must validate: " + "; ".join(e.message for e in errors))
    checks.append("domain_rationale schema budget accepts concise English one-sentence explanations")

    oversized_constraint = dict(representative_constraint)
    oversized_constraint["domain_rationale"] = "x" * 513
    oversized_response = dict(example_response)
    oversized_response["interpreted_constraints"] = [oversized_constraint]
    errors = list(Draft7Validator(output_schema).iter_errors(oversized_response))
    if not errors:
        raise AssertionError("domain_rationale above 512 characters must remain rejected")
    checks.append("domain_rationale schema hard limit rejects explanations above 512 characters")

    # 10. Phase2 schema budgets separate compact machine fields from bounded explanations.
    constraint_props = output_schema["properties"]["interpreted_constraints"]["items"]["properties"]
    budget_expectations = {
        "candidate_values": constraint_props["candidate_values"]["items"]["maxLength"],
        "domain_rationale": constraint_props["domain_rationale"]["maxLength"],
        "downstream_search_terms": constraint_props["downstream_search_terms"]["items"]["maxLength"],
        "risk_flags": constraint_props["risk_flags"]["items"]["maxLength"],
    }
    if budget_expectations != {
        "candidate_values": 180,
        "domain_rationale": 512,
        "downstream_search_terms": 160,
        "risk_flags": 240,
    }:
        raise AssertionError(f"unexpected Phase2 schema budgets: {budget_expectations}")
    near_constraint = dict(example_response["interpreted_constraints"][0])
    near_constraint["domain_rationale"] = "r" * 512
    near_constraint["risk_flags"] = ["x" * 240]
    near_response = dict(example_response)
    near_response["interpreted_constraints"] = [near_constraint]
    errors = list(Draft7Validator(output_schema).iter_errors(near_response))
    if errors:
        raise AssertionError("Phase2 near-limit response must validate: " + "; ".join(e.message for e in errors))
    deprecated_constraint = dict(near_constraint)
    deprecated_constraint["downstream_filter_rules"] = ["exclude everything"]
    deprecated_response = dict(example_response)
    deprecated_response["interpreted_constraints"] = [deprecated_constraint]
    if not list(Draft7Validator(output_schema).iter_errors(deprecated_response)):
        raise AssertionError("deprecated filter-rule field must be rejected by the current output contract")
    checks.append("Phase2 budgets retain headroom and the current output rejects deprecated filter rules")

    # 11. Anti-pollution guard for battery query phrasing
    battery_query = "高电压快充条件下，如何设计锂金属电池电解液以提高循环稳定性并降低界面副反应？"
    synthetic = {
        "user_query": battery_query,
        "raw_constraints": [
            {"phrase": "高电压", "source": "user_query"},
            {"phrase": "快充", "source": "user_query"},
            {"phrase": "锂金属电池电解液", "source": "user_query"},
        ],
    }
    errors = list(Draft7Validator(input_schema).iter_errors(synthetic))
    if errors:
        raise AssertionError("synthetic battery query input should validate: " + "; ".join(e.message for e in errors))
    forbidden_defaults = {"0c to 45c", "25c", "nmc", "solid-state electrolyte", "liquid electrolyte", "hybrid electrolyte"}
    observed = set()
    for c in example_response["interpreted_constraints"]:
        observed.update(t.strip().lower() for t in c.get("downstream_search_terms", []))
        observed.update(v.strip().lower() for v in c.get("candidate_values", []))
    if observed & forbidden_defaults:
        raise AssertionError(f"example response contains forbidden default-injected terms: {sorted(observed & forbidden_defaults)}")
    checks.append("anti-pollution defaults are absent from interpreted constraint terms/candidates")

    # 12. risk_flags contract guard (machine-readable short codes only)
    risk_flag_maxlen = output_schema["properties"]["interpreted_constraints"]["items"]["properties"]["risk_flags"]["items"]["maxLength"]

    def _validate_risk_flag(flag, where):
        if not isinstance(flag, str):
            raise AssertionError(f"risk flag must be string in {where}: {flag!r}")
        if len(flag) > risk_flag_maxlen:
            raise AssertionError(f"risk flag too long in {where}: len={len(flag)} > {risk_flag_maxlen}, value={flag!r}")
        if ": " in flag:
            raise AssertionError(f"risk flag must not include explanation separator ': ' in {where}: {flag!r}")
        if "(" in flag or ")" in flag:
            raise AssertionError(f"risk flag must not include parenthetical notes in {where}: {flag!r}")
        if LONG_EXPLANATION_HINT_RE.search(flag):
            raise AssertionError(f"risk flag appears to contain explanatory sentence in {where}: {flag!r}")
        if any(ch.isspace() for ch in flag):
            raise AssertionError(f"risk flag must not contain spaces in {where}: {flag!r}")
        if not SNAKE_CASE_RE.match(flag):
            if not re.fullmatch(r"[a-z0-9_\-]+", flag):
                raise AssertionError(f"risk flag should be machine-readable short code in {where}: {flag!r}")

    for idx, c in enumerate(example_response["interpreted_constraints"]):
        for rf in c.get("risk_flags", []):
            _validate_risk_flag(rf, f"interpreted_constraints[{idx}].risk_flags")

    for rf in example_response.get("quality_report", {}).get("risk_flags", []):
        _validate_risk_flag(rf, "quality_report.risk_flags")

    bad_flag = "term_interpretation_varies: 'interface side reactions' may refer to different measured proxies (gaseous products, parasitic capacity, SEI composition)"
    rejected = False
    try:
        _validate_risk_flag(bad_flag, "synthetic.bad")
    except AssertionError:
        rejected = True
    if not rejected:
        raise AssertionError("bad explanatory risk flag should be rejected")
    _validate_risk_flag("interface_side_reaction_proxy_undefined", "synthetic.good")
    checks.append("risk_flags guard rejects explanatory strings and accepts compact machine-readable codes")

    # 13. Wave1-03 near-mismatch eval was fixed before prompt edits and validates as legal input.
    near_eval = load_json("tests/replay/wave1_03_near_mismatch_eval/replay_cases.json")
    cases = near_eval.get("cases", [])
    if len(cases) < 6:
        raise AssertionError(f"near-mismatch eval requires at least 6 cases, got {len(cases)}")
    for case in cases:
        errors = list(Draft7Validator(input_schema).iter_errors(case["input"]))
        if errors:
            raise AssertionError(f"near-mismatch input invalid for {case.get('case_id')}: " + "; ".join(e.message for e in errors))
    checks.append(f"near-mismatch eval contains {len(cases)} schema-valid boundary cases")

    print("P0 validation passed:")
    for i, check in enumerate(checks, 1):
        print(f"{i}. {check}")


if __name__ == "__main__":
    main()
