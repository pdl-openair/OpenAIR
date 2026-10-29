import importlib.util
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
def load(name,path):
 s=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m
m5=load('h5',ROOT/'pvd-process-factor-candidate-experiment-analysis/scripts/finalize.py');m6=load('h6',ROOT/'pvd-parameter-experiment-plan-feasibility-validation/scripts/finalize.py');m7=load('h7',ROOT/'pvd-round-experiment-plan-parameter-recommendation/scripts/finalize.py')
CTX={'structured_context':{},'run_preferences':{},'recommendation_parameter_set':{'parameters':[{'parameter_key':'power','display_name':'Power','value_type':'continuous','unit':'W','allowed_values':[],'rationale':'scope','basis_refs':['q']}]}}
def cap5(direction='increase'):
 d={'analysis_status':'complete','factor_assessments':[{'factor':'power','candidate_direction':direction,'priority_role':'secondary_lever' if direction!='uncertain' else 'uncertain','evidence_refs':[],'diagnosis_refs':[],'rationale':'basis','uncertainty':'high' if direction=='uncertain' else 'medium'}],'recommended_next_experiment_focus':{'factor_refs':['power'],'rationale':'focus'},'limitations':[]}
 return m5.finalize({'current_context':CTX,'evidence_applicability':None,'feedback_analysis':None},d).output
def cap6(a,feas='feasible',conditions=None,conflicts=None,missing=None):
 d={'direction_assessments':[{'candidate_ref':a['factor_assessments'][0]['candidate_ref'],'factor':'power','feasibility':feas,'rationale':'gate','conditions':conditions or [],'conflicts':conflicts or [],'risks':[],'missing_information':missing or []}],'cross_parameter_assessment':{'feasibility':'feasible','rationale':'cross','conditions':[],'conflicts':[],'missing_information':[]},'shared_risks':[],'important_unknowns':[],'limitations':[]}
 return m6.finalize({'current_context':CTX,'direction_analysis':a},d).output
def draft(v=100,conds=None):return {'decision_status':'recommendation','experiments':[{'experiment_id':'e','priority':1,'parameters':[{'parameter_key':'power','recommended_value':v,'confidence':'medium','rationale':'cap7 value','basis_refs':[]}],'design_rationale':'test','validation_goal':'goal','failure_signal':'fail','feasibility_conditions':conds or [],'recommendation_risks':[]}],'limited_resource_first_to_run_experiment_ids':[],'direction_change_signals':[],'next_step':'measure','important_unknowns':[],'portfolio_note':'bounded'}
def test_m5_to_m6_to_m7_without_mapper_or_candidate():
 a=cap5();b=cap6(a);r=m7.finalize({'current_context':CTX,'direction_analysis':a,'feasibility_validation':b},draft(123));assert r.status=='succeeded';assert r.output['experiments'][0]['parameters'][0]['recommended_value']==123
def test_uncertain_defer_does_not_force_value():
 a=cap5('uncertain');b=cap6(a,'insufficient_information',missing=['need fact']);d=draft();d['decision_status']='no_recommendation';d['experiments']=[];r=m7.finalize({'current_context':CTX,'direction_analysis':a,'feasibility_validation':b},d);assert r.status=='succeeded';assert r.output['experiment_count']==0
def test_conditional_flows_to_experiment():
 a=cap5();b=cap6(a,'feasible_with_conditions',conditions=['confirm cooling']);r=m7.finalize({'current_context':CTX,'direction_analysis':a,'feasibility_validation':b},draft(140,['confirm cooling']));assert r.status=='succeeded';assert 'confirm cooling' in r.output['experiments'][0]['feasibility_conditions']
def test_dynamic_scope_and_no_mapper_files():
 assert cap5()['audit']['parameter_keys']==['power'];names=[p.name.lower() for p in ROOT.rglob('*') if p.is_file()];assert not any('mapper' in n or 'shim' in n for n in names)
