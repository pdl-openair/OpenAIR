
from __future__ import annotations
import json, os, subprocess, sys, tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[4]; SKILL_DIR=Path(__file__).resolve().parents[1]
request=json.loads((SKILL_DIR/"examples/request.json").read_text(encoding="utf-8"))
if not os.environ.get("LLM_PROVIDER_API_KEY") or not os.environ.get("LLM_PROVIDER_BASE_URL"):
    raise RuntimeError("P0.7d live smoke requires LLM_PROVIDER_API_KEY and LLM_PROVIDER_BASE_URL environment variables; secret values are never printed")
env=dict(os.environ); env["PYTHONPATH"]=str(ROOT/"services/api")+(os.pathsep+env["PYTHONPATH"] if env.get("PYTHONPATH") else ""); env["PYTHONIOENCODING"]="utf-8"; env["PYTHONUTF8"]="1"
with tempfile.TemporaryDirectory(prefix="p07d_live_") as tmp:
    cmd=[sys.executable,str(ROOT/"services/api/app/execution/run_single_structured_skill.py"),"--skill-id","mechanism-chain-synthesis-skill","--workspace-dir",tmp,"--task-id","p07d-live"]
    proc=subprocess.run(cmd,input=json.dumps(request,ensure_ascii=False),capture_output=True,text=True,encoding="utf-8",errors="strict",env=env,cwd=str(ROOT))
    if proc.returncode!=0: raise RuntimeError(f"P0.7d live smoke failed exit={proc.returncode} stdout={proc.stdout} stderr={proc.stderr}")
    payload=json.loads(proc.stdout); assert payload["ok"] is True,payload; out=payload["output"]
    assert len(out["mechanism_edges"])>=1,out; assert len(out["mechanism_chains"])>=1,out
    assert all(x.get("edge_id") and x.get("evidence_ids") for x in out["mechanism_edges"]),out
    assert out["audit"]["traceability_mode"]=="evidence_id_guard",out
    print("P07D_LIVE_SMOKE=PASS")
    print(f"LIVE_MECHANISM_EDGE_COUNT={len(out['mechanism_edges'])}")
    print(f"LIVE_MECHANISM_CHAIN_COUNT={len(out['mechanism_chains'])}")
    print(f"LIVE_CONFLICT_COUNT={len(out['conflicts'])}")
    print(f"LIVE_GAP_COUNT={len(out['gaps'])}")
    print(f"LIVE_TRACEABILITY_MODE={out['audit']['traceability_mode']}")
