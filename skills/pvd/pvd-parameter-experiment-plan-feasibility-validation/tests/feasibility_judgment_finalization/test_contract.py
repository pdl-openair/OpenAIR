import importlib.util
import json
import sys
from pathlib import Path

import pytest
from jsonschema.validators import validator_for

R=Path(__file__).resolve().parents[2]
s=importlib.util.spec_from_file_location('m6f',R/'scripts/finalize.py')
m=importlib.util.module_from_spec(s)
sys.modules[s.name]=m
s.loader.exec_module(m)

OUTPUT_SCHEMA=json.loads((R/'output.schema.json').read_text(encoding='utf-8'))


def inp():
 return {'current_context':{'recommendation_parameter_set':{'parameters':[{'parameter_key':'power','display_name':'Power','value_type':'continuous','unit':'W'}]}},'direction_analysis':{'factor_assessments':[{'candidate_ref':'node6:factor_assessment:power','factor':'power','display_name':'Power','candidate_direction':'increase'}]}}


def assessment(feasibility, *, conditions=None, conflicts=None, missing_information=None):
 return {'candidate_ref':'node6:factor_assessment:power','factor':'power','feasibility':feasibility,'rationale':'model judgment','conditions':conditions or [],'conflicts':conflicts or [],'risks':[],'missing_information':missing_information or []}


def draft(direction, cross=None):
 cross_feasibility=direction['feasibility']
 return {
  'direction_assessments':[direction],
  'cross_parameter_assessment':cross if cross is not None else {
   'feasibility':cross_feasibility,
   'rationale':'model cross-parameter judgment',
   'conditions':list(direction['conditions']),
   'conflicts':list(direction['conflicts']),
   'missing_information':list(direction['missing_information']),
  },
  'shared_risks':[],
  'important_unknowns':[],
  'limitations':[],
 }


@pytest.mark.parametrize(
 ('feasibility','decision','details'),
 [
  ('feasible','release',{}),
  ('feasible_with_conditions','conditional_release',{'conditions':['hold pressure stable']}),
  ('insufficient_information','defer',{'missing_information':['chamber limit unknown']}),
  ('infeasible','block',{'conflicts':['exceeds chamber limit']}),
 ],
)
def test_explicit_model_judgments_are_preserved(feasibility,decision,details):
 r=m.finalize(inp(),draft(assessment(feasibility,**details)))
 assert r.status=='succeeded'
 assert r.output['direction_assessments'][0]['feasibility']==feasibility
 assert r.output['direction_assessments'][0]['decision']==decision
 assert r.output['direction_assessments'][0]['candidate_direction']=='increase'
 assert r.output['recommendation_gate']==decision
 validator_cls=validator_for(OUTPUT_SCHEMA)
 validator_cls.check_schema(OUTPUT_SCHEMA)
 validator_cls(OUTPUT_SCHEMA).validate(r.output)










def test_unavailable_not_infeasible():
 r=m.build_unavailable_result(inp(),failure_kind='provider_timeout')
 assert r.status=='succeeded'
 assert r.output['availability']=='unavailable'
 assert r.output['validation_status']=='partial'
 assert r.output['direction_assessments'][0]['feasibility']=='insufficient_information'
 assert r.output['direction_assessments'][0]['rationale'].startswith('Feasibility execution unavailable (provider_timeout)')
 assert r.output['recommendation_gate']=='defer'
 validator_cls=validator_for(OUTPUT_SCHEMA)
 validator_cls.check_schema(OUTPUT_SCHEMA)
 validator_cls(OUTPUT_SCHEMA).validate(r.output)
