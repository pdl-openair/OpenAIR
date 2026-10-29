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

def test_executable_and_working_directory_must_be_relative():
    valid({"executable":"python","argv":["-V"],"working_directory":"repository","timeout_seconds":10})
    invalid({"executable":"../python","argv":["-V"],"working_directory":"repository","timeout_seconds":10})
