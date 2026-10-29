import json
from pathlib import Path
import jsonschema
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from adapter.paper_source_resolution_adapter import _validated_input
SCHEMA = json.loads((ROOT / "input.schema.json").read_text(encoding="utf-8"))

def valid(payload):
    jsonschema.validate(payload, SCHEMA)

def invalid(payload):
    try:
        jsonschema.validate(payload, SCHEMA)
    except jsonschema.ValidationError:
        return
    raise AssertionError("payload unexpectedly accepted by Formal Schema")

def test_multiline_research_goal_is_formally_valid():
    payload={"research_goal":"first line\nsecond line", "repository":None, "revision":None}
    valid(payload)
    assert _validated_input(payload) is not None

def test_repository_protocol_is_formal_contract():
    invalid({"research_goal":"goal", "repository":"http://example.test/repo", "revision":"main"})
