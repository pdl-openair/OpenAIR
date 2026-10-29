
from __future__ import annotations
import importlib.util, json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[4]; SKILL_DIR=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location("ot_guard",ROOT/"services/api/app/execution/opentrajectory_structured_runtime_guard.py"); assert spec and spec.loader
m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
req=json.loads((SKILL_DIR/"examples/request.json").read_text(encoding="utf-8")); resp=json.loads((SKILL_DIR/"examples/response.json").read_text(encoding="utf-8"))
draft={"mechanism_edges":[{**x,"edge_id":""} for x in resp["mechanism_edges"]],"mechanism_chains":[{**x,"chain_id":"","edge_ids":[]} for x in resp["mechanism_chains"]],"conflicts":[],"gaps":[],"summary":{},"audit":{},"synthesis_warnings":[]}
out,diag=m.apply_opentrajectory_runtime_guard(skill_id="mechanism-chain-synthesis-skill",input_payload=req,output_payload=draft)
assert diag["status"]=="passed"; assert len(out["mechanism_edges"])>=1; assert len(out["mechanism_chains"])>=1
assert all(x["edge_id"] and x["evidence_ids"] for x in out["mechanism_edges"]); assert out["audit"]["traceability_mode"]=="evidence_id_guard"
print("P07D_RUNTIME_GUARD_SMOKE=PASS")
print(f"MECHANISM_EDGE_COUNT={len(out['mechanism_edges'])}")
print(f"MECHANISM_CHAIN_COUNT={len(out['mechanism_chains'])}")
print("CHAIN=evidence_node_extraction->mechanism_chain_synthesis")
