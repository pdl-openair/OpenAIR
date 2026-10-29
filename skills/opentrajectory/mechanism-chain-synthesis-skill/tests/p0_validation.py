
from __future__ import annotations
import importlib.util, json
from pathlib import Path
from jsonschema import validate
SKILL_DIR=Path(__file__).resolve().parents[1]; ROOT=Path(__file__).resolve().parents[4]; GUARD=ROOT/"services/api/app/execution/opentrajectory_structured_runtime_guard.py"
def load_guard():
    spec=importlib.util.spec_from_file_location("ot_guard",GUARD); assert spec and spec.loader
    m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m
def read_json(p:Path): return json.loads(p.read_text(encoding="utf-8"))
def draft_from_example(resp):
    return {"mechanism_edges":[{**x,"edge_id":""} for x in resp["mechanism_edges"]],"mechanism_chains":[{**x,"chain_id":"","edge_ids":[]} for x in resp["mechanism_chains"]],"conflicts":[],"gaps":[],"summary":{},"audit":{},"synthesis_warnings":[]}
def rejected(g,req,out):
    try: g.apply_opentrajectory_runtime_guard(skill_id="mechanism-chain-synthesis-skill",input_payload=req,output_payload=out)
    except g.RuntimeGuardRejected: return
    raise AssertionError("expected RuntimeGuardRejected")
def main():
    g=load_guard(); req=read_json(SKILL_DIR/"examples/request.json"); resp=read_json(SKILL_DIR/"examples/response.json")
    validate(req,read_json(SKILL_DIR/"input.schema.json")); validate(resp,read_json(SKILL_DIR/"output.schema.json"))
    out,diag=g.apply_opentrajectory_runtime_guard(skill_id="mechanism-chain-synthesis-skill",input_payload=req,output_payload=draft_from_example(resp)); validate(out,read_json(SKILL_DIR/"output.schema.json")); assert diag["status"]=="passed"; assert out==resp
    out2,_=g.apply_opentrajectory_runtime_guard(skill_id="mechanism_chain_synthesis",input_payload=req,output_payload=draft_from_example(resp)); assert out2["mechanism_edges"][0]["edge_id"]==out["mechanism_edges"][0]["edge_id"]
    bad=draft_from_example(resp); bad["mechanism_edges"][0]["evidence_ids"]=["rr:ffffffffffffffffffffffffffffffff"]; rejected(g,req,bad)
    bad=draft_from_example(resp); bad["mechanism_edges"][0]["edge_status"]="conflict"; bad["mechanism_edges"][0]["relation"]={"predicate":"contradicts","description":"conflicts"}; bad["mechanism_edges"][0]["evidence_ids"]=["rr:11111111111111111111111111111111"]; rejected(g,req,bad)
    bad=draft_from_example(resp); bad["mechanism_chains"][0]["local_edge_keys"]=["edge-missing"]; rejected(g,req,bad)
    bad=draft_from_example(resp); bad["hypotheses"]=[]; rejected(g,req,bad)
    dup=draft_from_example(resp); clone=dict(dup["mechanism_edges"][0]); clone["local_edge_key"]="edge-duplicate"; dup["mechanism_edges"].append(clone); deduped,_=g.apply_opentrajectory_runtime_guard(skill_id="mechanism_chain_synthesis",input_payload=req,output_payload=dup); assert deduped["summary"]["dropped_duplicate_edge_count"]==1
    empty_req={"task_id":"empty","research_context":{"research_object":"x"},"research_records":[]}
    empty={"mechanism_edges":[],"mechanism_chains":[],"conflicts":[],"gaps":[],"summary":{},"audit":{},"synthesis_warnings":[]}
    empty_out,_=g.apply_opentrajectory_runtime_guard(skill_id="mechanism_chain_synthesis",input_payload=empty_req,output_payload=empty); validate(empty_out,read_json(SKILL_DIR/"output.schema.json")); assert empty_out["summary"]["mechanism_edge_count"]==0
    # P08B-1E-c: self-loop edge rejection (node_type + label)
    bad=draft_from_example(resp); bad["mechanism_edges"][0]["target_node"]=dict(bad["mechanism_edges"][0]["source_node"]); rejected(g,req,bad)
    # P08B-1E-c: self-loop via node_id (one side may omit node_id)
    bad=draft_from_example(resp); src=bad["mechanism_edges"][0]["source_node"]; bad["mechanism_edges"][0]["source_node"]={**src,"node_id":"node-self-loop"}; bad["mechanism_edges"][0]["target_node"]={**src,"node_id":"node-self-loop"}; rejected(g,req,bad)
    bad=draft_from_example(resp); src=bad["mechanism_edges"][0]["source_node"]; bad["mechanism_edges"][0]["source_node"]={**src,"node_id":"node-partial"}; bad["mechanism_edges"][0]["target_node"]=dict(src); rejected(g,req,bad)
    # P08B-1E-c: distinct node_ids with same (type, label) should NOT be rejected
    ok=draft_from_example(resp); src=ok["mechanism_edges"][0]["source_node"]; ok["mechanism_edges"][0]["source_node"]={**src,"node_id":"node-A"}; ok["mechanism_edges"][0]["target_node"]={**src,"node_id":"node-B"}
    ok_out,_=g.apply_opentrajectory_runtime_guard(skill_id="mechanism_chain_synthesis",input_payload=req,output_payload=ok); assert ok_out["summary"]["mechanism_edge_count"]>=1
    print("P07D_OFFLINE_VALIDATION=PASS")
    print("P07D_RESEARCH_RECORD_ID_GUARD=PASS")
    print("P07D_CONFLICT_MIN_TWO_RECORDS_GUARD=PASS")
    print("P07D_CHAIN_EDGE_REFERENCE_GUARD=PASS")
    print("P07D_NO_HYPOTHESIS_LEAK_GUARD=PASS")
    print("P07D_CANONICAL_EDGE_ID_STABILITY=PASS")
    print("P07D_DUPLICATE_EDGE_DEDUPE=PASS")
    print("P07D_ZERO_ONE_MULTI_MECHANISM_EDGES=PASS")
    print("P07D_SELF_LOOP_EDGE_REJECTED=PASS")
    print("P07D_DISTINCT_NODE_IDS_NOT_SELF_LOOP=PASS")
if __name__=="__main__": main()
