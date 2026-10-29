import json
import pathlib
from jsonschema import Draft7Validator

ROOT = pathlib.Path(__file__).resolve().parents[1]


def load_json(rel):
    return json.loads((ROOT / rel).read_text(encoding="utf-8"))


def assert_valid(data, schema, label):
    errors = list(Draft7Validator(schema).iter_errors(data))
    if errors:
        raise AssertionError(label + " schema errors: " + "; ".join(e.message for e in errors))


def assert_invalid(schema, payload, label):
    errors = list(Draft7Validator(schema).iter_errors(payload))
    if not errors:
        raise AssertionError(label + " must be rejected")


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

    assert_valid(response, output_schema, "example response")
    checks.append("example response validates against output.schema.json")

    expected = {
        "original_user_query": 4000,
        "research_object": 512,
        "target_metrics": 240,
        "boundary_conditions": 320,
        "comparison_baseline": 320,
        "requested_source_scope": 480,
        "ambiguities": 320,
    }
    props = output_schema["properties"]
    actual = {
        "original_user_query": props["original_user_query"]["maxLength"],
        "research_object": props["research_object"]["maxLength"],
        "target_metrics": props["target_metrics"]["items"]["maxLength"],
        "boundary_conditions": props["boundary_conditions"]["items"]["maxLength"],
        "comparison_baseline": props["comparison_baseline"]["anyOf"][0]["maxLength"],
        "requested_source_scope": props["requested_source_scope"]["anyOf"][0]["maxLength"],
        "ambiguities": props["ambiguities"]["items"]["maxLength"],
    }
    if actual != expected:
        raise AssertionError(f"unexpected Phase1 budgets: {actual}")
    checks.append("Phase1 schema budgets match expected limits")

    near = json.loads(json.dumps(response))
    near["target_metrics"] = ["m" * 240]
    near["boundary_conditions"] = ["b" * 320]
    near["ambiguities"] = ["a" * 320]
    assert_valid(near, output_schema, "Phase1 near-limit output")
    oversized = json.loads(json.dumps(response))
    oversized["target_metrics"] = ["m" * 241]
    assert_invalid(output_schema, oversized, "Phase1 oversized target metric")
    checks.append("Phase1 near-limit output passes and oversized target metric is rejected")

    if any(item.strip().lower() in {"25c", "0c to 45c", "nmc"} for item in response.get("boundary_conditions", [])):
        raise AssertionError("example response boundary_conditions contains force-filled values")
    checks.append("boundary_conditions avoid force-filled defaults in example")

    print("P0 validation passed:")
    for i, check in enumerate(checks, 1):
        print(f"{i}. {check}")


if __name__ == "__main__":
    main()
