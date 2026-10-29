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

def test_source_package_identity_is_formal():
    source=SCHEMA["properties"]["source_package"]
    jsonschema.validate({"schema_version":"openair-reference-source-package/v1","state":"succeeded","source_kind":"official_repository","repository":"https://example.test/repo","resolved_commit_sha":"0"*40,"workspace_relative_path":"repository","documents":[{}]}, source)
    try: jsonschema.validate({"schema_version":"openair-reference-source-package/v1","state":"succeeded","source_kind":"official_repository","repository":"x","resolved_commit_sha":"main","workspace_relative_path":"repository","documents":[{}]}, source)
    except jsonschema.ValidationError: return
    raise AssertionError("non-commit identity accepted")
