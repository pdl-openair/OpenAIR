from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import jsonschema

ROOT = Path(__file__).resolve().parents[2]

def _module():
    spec=importlib.util.spec_from_file_location("pvd_retrieval_finalize", ROOT/"scripts/finalize.py")
    assert spec and spec.loader
    mod=importlib.util.module_from_spec(spec); sys.modules[spec.name] = mod; spec.loader.exec_module(mod); return mod

def _input(formal=("pulse_power","n2_flow"), pending=("substrate_temperature",)):
    return {
      "structured_context":{"research_goal":"Optimize the current PVD decision."},
      "recommendation_parameter_set":{"parameters":[{"parameter_key":k} for k in formal]},
      "parameter_admission_judgments":[{"status":"pending_evidence","user_intent":"evaluate_for_admission","parameter_key":k} for k in pending],
    }

def _core_draft(keys=("pulse_power","n2_flow","substrate_temperature")):
    return {"query_variants":[{"search_intent":"constraint_focused","query":"AlScN sputtering " + " ".join(keys),"covered_parameter_keys":list(keys),"rationale":"Covers current decision parameters.","priority":1}],"design_rationale":"Core retrieval design."}

def _shortage(parameter="n2_flow", sid="shortage-1"):
    return {"shortage_id":sid,"source_artifact_type":"coverage_report","source_artifact_ref":"artifact://coverage/node4/r1","reason_code":"coverage_gap","description":"Comparable evidence remains insufficient.","affected_parameter_keys":[parameter],"affected_target_ids":["target_002"]}

def _supplemental_draft(parameter="n2_flow", query="AlScN reactive sputtering nitrogen ratio comparable films"):
    return {"query_variants":[{"search_intent":"adjacent_but_relevant","query":query,"covered_parameter_keys":[parameter],"rationale":"Fill the documented evidence gap only.","priority":2}],"design_rationale":"Supplemental retrieval for documented shortage."}

def _validate(output):
    jsonschema.validate(output, json.loads((ROOT/"output.schema.json").read_text(encoding="utf-8")))

def test_core_without_shortage_stays_core():
    out=_module().finalize(_input(), _core_draft()).output; _validate(out)
    assert out["retrieval_design"]["mode"]=="core"
    assert "supplemental_trace" not in out["retrieval_design"]

def test_formal_shortage_triggers_supplemental_and_stable_merge():
    mod=_module(); core=mod.finalize(_input(), _core_draft()).output["retrieval_design"]
    inp=_input(); inp.update({"prior_retrieval_design":core,"evidence_shortage_facts":[_shortage("n2_flow")]})
    out=mod.finalize(inp,_supplemental_draft("n2_flow")).output; _validate(out)
    assert out["retrieval_design"]["mode"]=="core_plus_supplemental"
    assert [r["query_id"] for r in out["retrieval_design"]["query_variants"]]==["query_001","query_002"]
    assert out["retrieval_design"]["supplemental_trace"]["shortage_fact_ids"]==["shortage-1"]

def test_pending_shortage_does_not_promote_pending():
    mod=_module(); core=mod.finalize(_input(), _core_draft()).output["retrieval_design"]
    inp=_input(); inp.update({"prior_retrieval_design":core,"evidence_shortage_facts":[_shortage("substrate_temperature")]})
    out=mod.finalize(inp,_supplemental_draft("substrate_temperature")).output; _validate(out)
    assert out["coverage_target"]["formal_parameter_keys"]==["pulse_power","n2_flow"]
    assert out["coverage_target"]["pending_parameter_keys"]==["substrate_temperature"]



def test_dynamic_parameter_scope_is_not_fixed_to_four_parameters():
    formal=("bias_voltage","oxygen_flow","rotation_speed")
    pending=("substrate_temperature",)
    keys=formal+pending
    out=_module().finalize(_input(formal,pending),_core_draft(keys)).output; _validate(out)
    assert out["coverage_target"]["all_parameter_keys"]==list(keys)
