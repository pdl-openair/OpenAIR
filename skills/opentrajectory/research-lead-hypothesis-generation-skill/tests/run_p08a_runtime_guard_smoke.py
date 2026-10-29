from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from jsonschema import validate

ROOT = Path(__file__).resolve().parents[4]
SKILL_DIR = Path(__file__).resolve().parents[1]
GUARD = ROOT / "services/api/app/execution/opentrajectory_structured_runtime_guard.py"

if not GUARD.is_file():
    print("P08A_RUNTIME_GUARD_SMOKE=SKIPPED_HANDOFF_PACKAGE_HAS_NO_RUNTIME")
    raise SystemExit(0)

spec = importlib.util.spec_from_file_location("ot_guard", GUARD)
assert spec and spec.loader
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
request = json.loads((SKILL_DIR / "examples/request.json").read_text(encoding="utf-8"))
draft = json.loads((SKILL_DIR / "examples/response.draft.json").read_text(encoding="utf-8"))
schema = json.loads((SKILL_DIR / "output.schema.json").read_text(encoding="utf-8"))
out, diag = m.apply_opentrajectory_runtime_guard(
    skill_id="research-lead-hypothesis-generation-skill",
    input_payload=request,
    output_payload=draft,
)
assert diag["status"] == "passed"
validate(out, schema)
assert out["direct_answer"]["answer"].strip()
assert len(out["research_leads"]) >= 1
assert len(out["hypotheses"]) >= 1
assert all(x["hypothesis_id"] and x["research_lead_id"] and x["falsifiable_prediction"] for x in out["hypotheses"])
expected_trace = schema["properties"]["audit"]["properties"]["traceability_mode"]["const"]
assert out["audit"]["traceability_mode"] == expected_trace
print("P08A_RUNTIME_GUARD_SMOKE=PASS")
print(f"DIRECT_ANSWER_CONFIDENCE={out['direct_answer']['confidence']}")
print(f"RESEARCH_LEAD_COUNT={len(out['research_leads'])}")
print(f"HYPOTHESIS_COUNT={len(out['hypotheses'])}")
print(f"TRACEABILITY_MODE={out['audit']['traceability_mode']}")
