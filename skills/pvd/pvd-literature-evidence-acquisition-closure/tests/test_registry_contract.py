from __future__ import annotations

from pathlib import Path
import yaml

REPO=Path(__file__).resolve().parents[4]


def load(path): return yaml.safe_load((REPO/path).read_text(encoding='utf-8'))


def test_t04_is_supporting_work_not_a_fixed_workflow_v2_node():
    stage=load(Path('schemas/pvd/stage-substage-map.yaml'))
    rows=[row for row in stage['execution_validation']['substages'] if row['key']=='literature_experiment_record_verification']
    assert len(rows)==1 and rows[0].get('enabled', True) is True
    skill_map=load(Path('schemas/pvd/substage-skill-map.yaml'))
    assert skill_map['literature_experiment_record_verification']==['pvd_literature_evidence_acquisition_closure']
    workflow=load(Path('schemas/workflows/pvd_process_optimization_workflow_v2.yaml'))
    assert 'literature_experiment_record_verification' not in {row['nodeKey'] for row in workflow['nodes']}
    support=next(row for row in workflow['supportingWork'] if row['supportKey']=='literature_evidence_closure')
    assert support['definitionId']=='pvd:literature_experiment_record_verification'
    assert support['supportsNodeKey']=='evidence_applicability_comparability_analysis'


def test_t04_registry_and_binding_use_t03_artifacts_as_formal_handoff():
    registry=load(Path('skills/registry.yaml'))['skills']
    by_id={row['skill_id']:row for row in registry}
    assert by_id['pvd_literature_evidence_acquisition_closure']['runtime_contract']=={
        'kind':'external_adapter','requires_external_action':True,
        'adapter_id':'python_adapter:pvd_literature_evidence_acquisition_closure',
        'evidence_contract_version':'external-action-evidence/v1',
    }
    assert by_id['pvd_evidence_node_extraction']['durable_model_profile']=='terra'
    assert by_id['pvd_research_record_structuring']['durable_model_profile']=='terra'

    bindings=load(Path('skills/skill-bindings.yaml'))['bindings']
    row=next(r for r in bindings if r['bindingId']=='bind:pvd:literature_experiment_record_verification:skill:pvd_literature_evidence_acquisition_closure')
    fields={x['targetField']:x for x in row['inputSources']['fields']}
    assert set(fields)=={'retrieval_design','evidence_schema','coverage_target','pending_parameter_evidence_identities'}
    assert fields['retrieval_design']['source']['bindingId']=='bind:pvd:research_retrieval_experiment_design:skill:pvd_evidence_retrieval_design'
    for key in ('evidence_schema','coverage_target','pending_parameter_evidence_identities'):
        assert fields[key]['source']['bindingId']=='bind:pvd:research_retrieval_experiment_design:skill:pvd_evidence_schema_design'
    runtime={x['targetField']:x['source'] for x in row['runtimeInputs']['fields']}
    assert runtime=={
        'user_query':{'kind':'invocationContext','field':'canonical_user_query'},
        'task_id':{'kind':'executionIdentity','field':'task_id'},
    }
