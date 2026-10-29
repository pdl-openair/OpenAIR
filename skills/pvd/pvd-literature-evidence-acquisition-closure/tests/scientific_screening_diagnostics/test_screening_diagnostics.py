from __future__ import annotations

from copy import deepcopy
import importlib.util
from pathlib import Path

import pytest

PATH = Path(__file__).resolve().parents[2] / 'adapter' / 'literature_evidence_closure.py'
spec = importlib.util.spec_from_file_location('screening_diagnostics_under_test', PATH)
assert spec and spec.loader
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def _select(child, candidates=None):
    return mod._select_scientific_sources(
        [{'candidate_id': 'source-1', 'title': 'Provided source', 'provenance': [{'source_name': 'provided'}]}] if candidates is None else candidates,
        task_id='diagnostic-contract-test', user_query='Assess the provided source.',
        retrieval_design={'query_variants': []},
        evidence_schema={'evidence_targets': [{'target_id': 'pending-target', 'requiredness': 'preferred'}]},
        llm_child=child,
    )


@pytest.mark.parametrize('failure', [
    {'state': 'failed', 'failureClassification': 'provider_configuration',
     'failedStage': 'execution_policy_precondition', 'firstMismatch': 'resolved_execution_policy_missing'},
    {'state': 'failed', 'failureClassification': 'provider_response_contract_invalid',
     'failedStage': 'skill_finalization_failed', 'firstMismatch': 'draft_contract_rejected',
     'diagnostics': {'finalization': {'code': 'draft_contract_rejected', 'errors': ['actual-error']}}},
    {'state': 'unknown', 'externalResultUnknown': True},
])
def test_original_failure_facts_survive_screening_fallback(failure):
    original = deepcopy(failure)
    def reject(*_, **__): raise mod.ChildSkillFailure(failure)
    selected, selection, warnings = _select(reject)
    assert selection['mode'] == 'retrieval_order_fallback'
    assert selection['degraded'] is True
    assert selection['diagnostics'] == original
    assert selected[0]['candidate_id'] == 'source-1'
    assert warnings == ['scientific_screening_unavailable:retrieval_order_preserved']
    assert failure == original


def test_screening_input_retains_canonical_candidates_and_pending_targets():
    seen = {}
    class Boundary(BaseException): pass
    def stop(skill_id, parameters, *, adapter_name):
        seen.update(skill_id=skill_id, parameters=parameters, adapter_name=adapter_name)
        raise Boundary()
    with pytest.raises(Boundary): _select(stop)
    assert seen['skill_id'] == 'opentrajectory_source_screening_selection'
    assert seen['adapter_name'] == 'pvd_t04_scientific_screening'
    assert seen['parameters']['candidates'][0]['provenance'] == [{'source_name': 'provided'}]
    assert seen['parameters']['research_context']['formal_evidence_targets'][0]['requiredness'] == 'preferred'


def test_absent_candidates_do_not_start_any_child():
    def forbidden(*_, **__): raise AssertionError('no child expected')
    selected, selection, warnings = _select(forbidden, candidates=[])
    assert selected == []
    assert selection['mode'] == 'no_candidates'
    assert warnings == ['retrieval_returned_no_candidates']
