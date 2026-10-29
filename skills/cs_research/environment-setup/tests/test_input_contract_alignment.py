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

def test_requirement_https_and_probe_path_grammar_are_formal():
    valid({"python_version":"3.11","requirements":["torch==2.4.0"],"verify_imports":["torch"],"find_links":["https://example.test/wheels"],"runtime_readiness_probe":{"executable":"bin/probe","argv":[],"working_directory":"repository","timeout_seconds":10}})
    invalid({"python_version":"3.11","requirements":["not a requirement"],"verify_imports":["torch"]})
    invalid({"python_version":"3.11","requirements":["torch==2.4.0"],"verify_imports":["torch"],"find_links":["http://example.test/wheels"]})
