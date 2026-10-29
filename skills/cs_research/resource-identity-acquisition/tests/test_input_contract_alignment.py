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

def test_huggingface_identity_grammar_is_formal():
    valid({"resource_kind":"huggingface_file","repo_id":"org/model","repo_type":"model","revision":"main","filename":"weights/model.bin"})
    invalid({"resource_kind":"huggingface_file","repo_id":"https://huggingface.co/org/model","repo_type":"model","revision":"main","filename":"weights/model.bin"})
    invalid({"resource_kind":"huggingface_file","repo_id":"org/model","repo_type":"model","revision":"main","filename":"../secret"})
