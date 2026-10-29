from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('pvd_evidence_finalize_test', ROOT/'scripts/finalize.py')
assert spec and spec.loader
mod=importlib.util.module_from_spec(spec); sys.modules[spec.name]=mod; spec.loader.exec_module(mod)


def payload():
    content='Power was set to 500 W while nitrogen flow was 20 sccm for the reported film.'
    return {
      'task_id':'task-1',
      'paper_packets':[{
        'candidate_id':'paper-1','arxiv_id':'','sections':[{'section_name':'Methods','content':content,'content_hash':'a'*64}],
        'source_anchors':[{'anchor_id':'anchor-1','candidate_id':'paper-1','arxiv_id':'','section_name':'Methods','content_hash':'a'*64}]
      }],
      'evidence_schema':{'contract':'pvd-evidence-schema/v1','evidence_targets':[{'target_id':'target_001','requiredness':'required','covered_parameter_keys':['power']}]}
    }


def item(anchor='anchor-1', quote='Power was set to 500 W while nitrogen flow was 20 sccm for the reported film.'):
    return {
      'source_anchor_id':anchor,'evidence_type':'experimental_condition','assertion_mode':'direct_observation',
      'claim':'The reported sputtering power was 500 W.','evidence_quote':quote,
      'conditions':[{'name':'power','value_text':'500','unit':'W'}], 'metric':None,'mechanism':None,'comparison':None,
      'qualifiers':[],'confidence':0.95,'matched_target_ids':['target_001'],'target_match_status':'matched'
    }


def test_finalizer_preserves_grounded_item_and_assigns_stable_identity():
    result=mod.finalize(payload(),{'evidence_items':[item()]})
    assert result.status=='succeeded'
    assert result.output is not None
    row=result.output['evidence_items'][0]
    assert row['candidate_id']=='paper-1'
    assert row['evidence_id'].startswith('ev:')
    assert result.output['summary']['uncovered_required_target_ids']==[]




def test_schema_identity_is_deterministic_when_t03_schema_has_no_explicit_id():
    first=mod.finalize(payload(),{'evidence_items':[item()]})
    second=mod.finalize(payload(),{'evidence_items':[item()]})
    assert first.output is not None and second.output is not None
    assert first.output['schema_id'].startswith('pvd-evidence-schema:')
    assert first.output['schema_id']==second.output['schema_id']
