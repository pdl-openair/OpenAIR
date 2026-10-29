from __future__ import annotations
import importlib.util
from pathlib import Path
ROOT = Path(__file__).resolve().parents[4]
GUARD = ROOT / "services/api/app/execution/opentrajectory_structured_runtime_guard.py"
spec = importlib.util.spec_from_file_location("ot_guard", GUARD); assert spec and spec.loader
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
out, diag = m.apply_opentrajectory_runtime_guard(skill_id="evidence-schema-design-skill", input_payload={"task_id":"x","research_question":"abc"}, output_payload={"evidence_schema":{},"design_warnings":[]})
assert out == {"evidence_schema":{},"design_warnings":[]}
assert diag["managed"] is False and diag["phase"] == "unmanaged"
try:
    m.apply_opentrajectory_runtime_guard(skill_id="evidence-schema-design-skill", input_payload={}, output_payload=[])
except m.RuntimeGuardRejected:
    pass
else:
    raise AssertionError("non-object structured output must be rejected")
print("P07F_RUNTIME_GUARD_SMOKE=PASS")
print("P07F_RUNTIME_GUARD_MODE=UNMANAGED_SCHEMA_VALIDATION_ONLY")
