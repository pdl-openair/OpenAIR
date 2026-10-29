import json
import pathlib
from jsonschema import Draft7Validator

ROOT = pathlib.Path(__file__).resolve().parents[1]


INTENTS = {
    "core_topic",
    "constraint_focused",
    "mechanism_focused",
    "method_or_strategy",
    "metric_or_performance",
    "adjacent_but_relevant",
    "noise_exclusion",
}


def load_json(rel):
    return json.loads((ROOT / rel).read_text(encoding="utf-8"))


def assert_valid(data, schema, label):
    errors = list(Draft7Validator(schema).iter_errors(data))
    if errors:
        raise AssertionError(label + " schema errors: " + "; ".join(e.message for e in errors))


def main():
    input_schema = load_json("input.schema.json")
    output_schema = load_json("output.schema.json")
    request = load_json("examples/request.json")
    response = load_json("examples/response.json")

    checks = []

    Draft7Validator.check_schema(input_schema)
    Draft7Validator.check_schema(output_schema)
    checks.append("schema definitions are valid Draft-07 JSON Schema")

    assert_valid(request, input_schema, "example request")
    checks.append("example request validates against input.schema.json")

    legacy_rule_request = json.loads(json.dumps(request))
    legacy_rule_request["interpreted_constraints"][0]["downstream_filter_rules"] = [
        {
            "type": "hard_filter",
            "field": "voltage_max",
            "operator": "<=",
            "value": 4.2,
            "unit": "V",
            "description": "voltage_max <= 4.2 V"
        }
    ]
    assert_valid(legacy_rule_request, input_schema, "deprecated filter-rule compatibility request")
    checks.append("deprecated filter-rule input is accepted only at the legacy boundary")

    assert_valid(response, output_schema, "example response")
    checks.append("example response validates against output.schema.json")

    empty_query = dict(request)
    empty_query["user_query"] = ""
    errors = list(Draft7Validator(input_schema).iter_errors(empty_query))
    if not errors:
        raise AssertionError("empty user_query should be rejected by input.schema.json")
    checks.append("empty user_query is rejected by input.schema.json")

    matrix = response["search_matrix"]
    matrix_intents = [row["search_intent"] for row in matrix]
    if set(matrix_intents) != INTENTS or len(matrix_intents) != len(set(matrix_intents)):
        raise AssertionError(f"search_matrix must cover 7 unique intents, got {matrix_intents}")
    checks.append("search_matrix covers exactly 7 unique intents")

    variants = response["expanded_query_variants"]
    if len(variants) != 8:
        raise AssertionError(f"example must contain 8 query variants, got {len(variants)}")
    checks.append("example contains the expected structural query inventory")

    protocol = response["result_protocol"]
    if protocol["status"] != "ok":
        raise AssertionError(f"canonical example must be ok, got {protocol['status']!r}")
    if protocol["quarantined_items"] or protocol["repaired_items"] or protocol["warnings"]:
        raise AssertionError("canonical example must not report degraded protocol entries")
    if protocol["coverage"].get("accepted_query_count") != len(variants):
        raise AssertionError("result protocol query coverage must match the artifact")
    checks.append("example result protocol reports a complete non-degraded artifact")

    if "variant_type" not in output_schema["properties"]["expanded_query_variants"]["items"]["required"]:
        raise AssertionError("variant_type must be required in QueryVariant")
    checks.append("QueryVariant requires variant_type")


    budget_expectations = {
        "query_terms": output_schema["properties"]["search_matrix"]["items"]["properties"]["query_terms"]["items"]["maxLength"],
        "matrix_rationale": output_schema["properties"]["search_matrix"]["items"]["properties"]["rationale"]["maxLength"],
        "variant_query": output_schema["properties"]["expanded_query_variants"]["items"]["properties"]["query"]["maxLength"],
        "variant_rationale": output_schema["properties"]["expanded_query_variants"]["items"]["properties"]["rationale"]["maxLength"],
        "facet_term": output_schema["properties"]["assembled_facets"]["properties"]["target_metrics_en"]["items"]["maxLength"],
    }
    if budget_expectations != {
        "query_terms": 160,
        "matrix_rationale": 512,
        "variant_query": 512,
        "variant_rationale": 512,
        "facet_term": 160,
    }:
        raise AssertionError(f"unexpected Phase3 schema budgets: {budget_expectations}")
    near = json.loads(json.dumps(response))
    near["search_matrix"][0]["rationale"] = "r" * 512
    near["expanded_query_variants"][0]["query"] = "q" * 512
    near["expanded_query_variants"][0]["rationale"] = "v" * 512
    near["assembled_facets"]["target_metrics_en"] = ["m" * 160]
    assert_valid(near, output_schema, "Phase3 near-limit output")
    oversized = json.loads(json.dumps(response))
    oversized["expanded_query_variants"][0]["query"] = "q" * 513
    if not list(Draft7Validator(output_schema).iter_errors(oversized)):
        raise AssertionError("Phase3 Boolean query above 512 characters must remain rejected")
    checks.append("Phase3 retrieval/prose schema budgets keep near-limit headroom and reject overflow")

    model_schema = load_json("model_output.schema.json")
    for code_owned_field in ("search_matrix", "quality_report", "result_protocol"):
        if code_owned_field in model_schema.get("properties", {}):
            raise AssertionError(f"model schema must not own {code_owned_field}")
    checks.append("model schema leaves formal artifact and result protocol ownership to code")

    print("Skill 2 P0/P0.1 validation passed:")
    for i, check in enumerate(checks, 1):
        print(f"{i}. {check}")


if __name__ == "__main__":
    main()
