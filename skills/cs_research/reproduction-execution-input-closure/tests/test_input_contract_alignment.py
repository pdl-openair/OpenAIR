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

def test_readiness_and_plan_state_are_formal():
    jsonschema.validate({"status":"ready","blocking_gaps":[],"executable_scope":["run"]}, SCHEMA["properties"]["execution_readiness_record"]);
    try: jsonschema.validate({"status":"blocked","blocking_gaps":[],"executable_scope":["run"]}, SCHEMA["properties"]["execution_readiness_record"])
    except jsonschema.ValidationError: return
    raise AssertionError("blocked readiness accepted")
