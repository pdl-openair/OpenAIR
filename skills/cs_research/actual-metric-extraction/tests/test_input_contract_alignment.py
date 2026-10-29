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

def test_relative_result_path_and_json_pointer_grammar_are_formal():
    base={"execution_record_relative_path":"execution/run/execution-record.json","result_source":{"kind":"workspace_file","relative_path":"results/metrics.json","format":"json"},"metrics":[{"metric_name":"score","value_type":"number","selector":{"json_pointer":"/score"}}]}
    valid(base)
    bad=json.loads(json.dumps(base)); bad["result_source"]["relative_path"]="../metrics.json"; invalid(bad)
