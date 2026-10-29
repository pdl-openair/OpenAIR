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

def test_repository_protocol_and_revision_grammar_are_formal():
    valid({"repository":"https://example.test/repo.git", "revision":"main"})
    invalid({"repository":"http://example.test/repo.git", "revision":"main"})
    invalid({"repository":"https://example.test/repo.git", "revision":" main "})
