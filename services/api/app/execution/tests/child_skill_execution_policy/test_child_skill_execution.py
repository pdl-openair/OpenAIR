from __future__ import annotations

import importlib.util
import json
from copy import deepcopy
from pathlib import Path
import socket
import sys
from unittest.mock import Mock

import httpx
import pytest

from app.execution import durable_skill_runtime as runtime
from app.execution.skill_finalization_runtime import SkillFinalizationError
from app.modules.repo_paths import repo_root


class AtProviderBoundary(BaseException):
    """Stop before any transport or registered finalizer execution."""




def _parameters():
    return {
        'task_id': 'child-contract-test',
        'user_query': 'Assess the supplied AlScN deposition literature.',
        'research_context': {'research_goal': 'Traceable process conditions'},
        'evidence_schema': {'evidence_targets': []},
        'candidates': [{
            'candidate_id': 'openalex_1_1',
            'title': 'Deposition of highly crystalline AlScN thin films using synchronized high-power impulse magnetron sputtering: From combinatorial screening to piezoelectric devices',
            'abstract': 'Study of AlScN deposition.',
            'doi': '10.1103/physrevmaterials.8.095001',
            'arxiv_id': '',
            'provenance': [{'source_name': 'openalex'}],
        }],
    }


@pytest.fixture(autouse=True)
def isolated_io(monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError('this suite permits no Provider/network/database calls')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect_ex', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    monkeypatch.setattr(httpx.Client, 'post', forbidden)
    monkeypatch.setattr(httpx.Client, 'send', forbidden)
    import psycopg
    monkeypatch.setattr(psycopg, 'connect', forbidden)
    monkeypatch.setenv('LLM_PROVIDER_API_KEY', 'offline-test-key')
    monkeypatch.setenv('LLM_PROVIDER_BASE_URL', 'https://blocked.invalid')
    monkeypatch.setenv('LLM_PROVIDER_MODEL', 'test-model')
    monkeypatch.setenv('LLM_PROVIDER_MODEL_TERRA', 'frozen-terra-A')
    monkeypatch.setenv('LLM_PROVIDER_MODEL_SOL', 'frozen-sol-A')
    monkeypatch.setenv('OPENAIR_PARENT_CAPABILITY_CALL_ID', 'parent-contract-test')
    monkeypatch.setattr(runtime, '_record_durable_skill_usage', forbidden)


def _call(skill_id='opentrajectory_source_screening_selection', parameters=None, **overrides):
    args = dict(skill_id=skill_id, parameters=_parameters() if parameters is None else parameters,
                parent_capability_call_id='parent-contract-test', adapter_name='child_contract_test')
    args.update(overrides)
    return runtime.execute_new_durable_llm_child(**args)




def test_missing_policy_is_specific_and_cannot_fall_back_to_legacy_model_profile():
    prepared = runtime.resolve_registered_durable_skill_runtime('opentrajectory_source_screening_selection')
    assert prepared is not None
    prepared['modelProfile'] = 'terra'
    result = runtime.execute_durable_skill({
        'skillId': prepared['skillId'], 'parameters': _parameters(),
        'logicalProviderId': 'irrelevant-before-transport',
    }, prepared_runtime=prepared)
    assert result['state'] == 'failed'
    assert result['failureClassification'] == 'provider_configuration'
    assert result['failedStage'] == 'execution_policy_precondition'
    assert result['firstMismatch'] == 'resolved_execution_policy_missing'


def test_shared_preparation_is_skill_independent_and_freezes_input(monkeypatch):
    actual = runtime.resolve_registered_durable_skill_runtime('opentrajectory_source_screening_selection')
    assert actual is not None
    actual['skillId'] = 'other_declared_reasoning_capability'
    actual['sourceContract']['durable_model_profile'] = 'sol'
    actual['sourceContract']['durable_llm_execution_policy']['max_output_tokens'] = 1234
    monkeypatch.setattr(runtime, 'resolve_registered_durable_skill_runtime', lambda _id: actual)
    seen = {}
    def execute(request, *, prepared_runtime):
        seen.update(request=request, runtime=prepared_runtime)
        return {'state': 'unknown', 'externalResultUnknown': True}
    spy = Mock(side_effect=execute)
    monkeypatch.setattr(runtime, 'execute_durable_skill', spy)
    parameters = _parameters()
    result = _call(actual['skillId'], parameters)
    assert result == {'state': 'unknown', 'externalResultUnknown': True}
    assert spy.call_count == 1  # an unknown sub-call adds no hidden recovery
    policy = seen['request']['resolvedExecutionPolicy']
    assert policy['model'] == 'frozen-sol-A'
    assert policy['maxOutputTokens'] == 1234
    parameters['candidates'][0]['candidate_id'] = 'changed-after-preparation'
    actual['sourceContract']['durable_model_profile'] = 'terra'
    assert seen['request']['parameters']['candidates'][0]['candidate_id'] == 'openalex_1_1'
    assert seen['runtime']['sourceContract']['durable_model_profile'] == 'sol'


@pytest.mark.parametrize('skill_id', ['pvd_evidence_node_extraction', 'pvd_research_record_structuring'])
def test_undeclared_policy_preserves_existing_child_contract(monkeypatch, skill_id):
    seen = {}
    def execute(request, *, prepared_runtime):
        seen.update(request=request, runtime=prepared_runtime)
        return {'state': 'unknown'}
    monkeypatch.setattr(runtime, 'execute_durable_skill', execute)
    _call(skill_id)
    assert 'resolvedExecutionPolicy' not in seen['request']
    assert seen['runtime']['modelProfile'] == 'terra'
    assert seen['request']['logicalProviderId']


def test_missing_parent_is_rejected_before_configuration_or_execution(monkeypatch):
    def forbidden(*_args, **_kwargs): raise AssertionError('preparation must stop first')
    monkeypatch.setattr(runtime, 'resolve_registered_durable_skill_runtime', forbidden)
    result = _call(parent_capability_call_id='')
    assert result['firstMismatch'] == 'child_call_evidence_identity_missing'
    assert result['failedStage'] == 'child_call_preparation'


def test_policy_resolution_failure_retains_safe_specific_code(monkeypatch):
    def reject(*_args, **_kwargs):
        raise ValueError('durable_llm_execution_policy_model_authority_ambiguous')
    monkeypatch.setattr(runtime, 'resolve_durable_llm_execution_policy', reject)
    result = _call()
    assert result['failureClassification'] == 'provider_configuration'
    assert result['firstMismatch'] == 'durable_llm_execution_policy_model_authority_ambiguous'
    assert result['diagnostics']['preparation']['exceptionType'] == 'ValueError'


def test_executor_keeps_frozen_policy_after_environment_changes(monkeypatch):
    seen = {}
    execute_original = runtime.execute_durable_skill
    def capture(request, *, prepared_runtime):
        seen.update(request=request, runtime=prepared_runtime)
        return {'state': 'unknown'}
    monkeypatch.setattr(runtime, 'execute_durable_skill', capture)
    _call()
    monkeypatch.setenv('LLM_PROVIDER_MODEL_TERRA', 'mutable-terra-B')
    def forbidden_resolve(*_args, **_kwargs): raise AssertionError('frozen execution must not re-resolve policy')
    monkeypatch.setattr(runtime, 'resolve_durable_llm_execution_policy', forbidden_resolve)
    captured = {}
    def boundary(**kwargs):
        captured.update(kwargs)
        raise AtProviderBoundary()
    monkeypatch.setattr(runtime, '_execute_structured_batch_requests', boundary)
    with pytest.raises(AtProviderBoundary):
        execute_original(seen['request'], prepared_runtime=seen['runtime'])
    assert captured['runtime_config'].model == 'frozen-terra-A'
    assert captured['execution_policy']['taskBudgetSeconds'] == 135


def test_all_batch_failures_remain_available_in_runtime_diagnostic(monkeypatch):
    failures = [{'batchIndex': 1, 'reason': 'http_401', 'inputIdentities': ['openalex_1_1']}]
    monkeypatch.setattr(runtime, '_execute_structured_batch_requests', lambda **_: {
        'draft': {'judgments': []}, 'successfulBatchCount': 0, 'failedBatches': failures,
        'usageWire': None, 'batchCount': 1, 'maxParallel': 1,
    })
    monkeypatch.setattr(runtime, '_record_durable_skill_usage', lambda **_: None)
    result = _call()
    assert result['failedStage'] == 'structured_batch_failure_evidence'
    assert result['firstMismatch'] == 'typed_batch_failure_missing'
    assert result['failureClassification'] == 'provider_configuration'
    assert result['diagnostics']['structuredBatch']['failedBatches'] == failures


def test_registered_finalizer_error_is_transported_without_running_finalizer(monkeypatch):
    from app.execution import skill_finalization_runtime
    monkeypatch.setattr(runtime, '_execute_structured_batch_requests', lambda **_: {
        'draft': {'judgments': []}, 'successfulBatchCount': 1, 'failedBatches': [],
        'usageWire': {'totalTokens': 1}, 'batchCount': 1, 'maxParallel': 1,
    })
    def reject(**_):
        raise SkillFinalizationError('draft_contract_rejected', errors=({'code': 'missing_judgment'},))
    monkeypatch.setattr(skill_finalization_runtime, 'finalize_and_validate_registered_output', reject)
    monkeypatch.setattr(runtime, '_record_durable_skill_usage', lambda **_: None)
    result = _call()
    assert result['failedStage'] == 'skill_finalization_failed'
    assert result['firstMismatch'] == 'draft_contract_rejected'
    assert result['diagnostics']['finalization'] == {
        'code': 'draft_contract_rejected', 'errors': [{'code': 'missing_judgment'}],
    }
