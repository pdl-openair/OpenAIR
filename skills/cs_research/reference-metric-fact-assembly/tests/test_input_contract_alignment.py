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

def test_upstream_artifact_version_and_state_are_formal():
    payload={"source_package":{"schema_version":"wrong","state":"succeeded","source_kind":"official_repository","repository":"https://example.test/repo","resolved_commit_sha":"0"*40,"documents":[{"document_id":"d","path":"README.md","sha256":"0"*64,"content":"x"}]},"interpretation":{"reference_candidates":[],"unresolved":[]}}
    invalid(payload)
