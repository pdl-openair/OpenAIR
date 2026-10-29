import json
from pathlib import Path
import jsonschema

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = json.loads((ROOT / "input.schema.json").read_text(encoding="utf-8"))

def valid(payload):
    jsonschema.validate(payload, SCHEMA)

def invalid(payload):
    try:
        jsonschema.validate(payload, SCHEMA)
    except jsonschema.ValidationError:
        return
    raise AssertionError("payload unexpectedly accepted by Formal Schema")

def test_comparison_kind_and_tolerance_condition_are_formal():
    rule=SCHEMA["properties"]["reference_metrics"]["items"]["properties"]["comparison_rule"]
    jsonschema.validate({"kind":"absolute_tolerance","tolerance":0.1}, rule)
    for bad in ({"kind":"absolute_tolerance"},{"kind":"exact","tolerance":0.1},{"kind":"invented"}):
        try: jsonschema.validate(bad, rule)
        except jsonschema.ValidationError: continue
        raise AssertionError(f"invalid rule accepted: {bad}")
