from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from jsonschema import Draft7Validator

# Full canonical-checkout smoke: production runtime must invoke the Skill-local finalizer.
SKILL = Path(__file__).resolve().parents[1]
ROOT = Path(__file__).resolve().parents[4]
if not os.environ.get("LLM_PROVIDER_API_KEY") or not os.environ.get("LLM_PROVIDER_BASE_URL"):
    raise RuntimeError("LIVE_LLM_NOT_CONFIGURED")

runtime = ROOT / "services/api/app/execution/run_single_structured_skill.py"
if not runtime.exists():
    raise RuntimeError("CANONICAL_STRUCTURED_SKILL_RUNTIME_NOT_AVAILABLE")

request = json.loads((SKILL / "examples" / "request.json").read_text(encoding="utf-8"))
env = dict(os.environ)
env["PYTHONPATH"] = str(ROOT / "services/api") + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
with tempfile.TemporaryDirectory(prefix="research_record_live_") as tmp:
    cmd = [
        sys.executable,
        str(runtime),
        "--skill-id",
        "evidence-node-extraction-skill",
        "--workspace-dir",
        tmp,
        "--task-id",
        "research-record-live",
    ]
    proc = subprocess.run(
        cmd,
        input=json.dumps(request, ensure_ascii=False),
        capture_output=True,
        text=True,
        env=env,
        cwd=str(ROOT),
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr or proc.stdout)
    payload = json.loads(proc.stdout)
    assert payload["ok"] is True, payload
    output = payload["output"]
    final_schema = json.loads((SKILL / "output.schema.json").read_text(encoding="utf-8"))
    errors = list(Draft7Validator(final_schema).iter_errors(output))
    assert not errors, errors
    assert output["audit"]["skill_id"] == "evidence_node_extraction"
    assert output["audit"]["traceability_mode"] == "research_record_source_anchor_quote_finalizer"
    assert output["summary"]["status"] in {"succeeded", "partial", "degraded"}
    assert "research_records" in output
    assert "evidence_items" not in output
    assert all("evidence_id" not in row for row in output["research_records"])
    print("LIVE_LLM_AND_PRODUCTION_FINALIZATION=PASS")
