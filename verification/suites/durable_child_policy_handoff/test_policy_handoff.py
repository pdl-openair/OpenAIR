from __future__ import annotations

import importlib.util
import socket
import httpx
import pytest
from app.execution import durable_skill_runtime as runtime
from app.modules.repo_paths import repo_root


class AtProviderBoundary(BaseException):
    """Stop before any transport or registered finalizer execution."""


def _closure():
    path = repo_root() / 'skills/pvd/pvd-literature-evidence-acquisition-closure/adapter/literature_evidence_closure.py'
    spec = importlib.util.spec_from_file_location('pvd_child_call_wiring_under_test', path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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


def test_pvd_child_reaches_screening_transport_boundary_with_own_declared_policy(monkeypatch):
    """Regression: on the original source this raises ChildSkillFailure BEFORE the boundary."""
    seen = {}
    def stop(**kwargs):
        seen.update(kwargs)
        raise AtProviderBoundary()
    monkeypatch.setattr(runtime, '_execute_structured_batch_requests', stop)
    with pytest.raises(AtProviderBoundary):
        _closure()._llm_child('opentrajectory_source_screening_selection', _parameters(), adapter_name='pvd_t04_scientific_screening')
    policy = seen['execution_policy']
    assert policy['model'] == 'frozen-terra-A'
    assert policy['reasoningEffort'] == 'none'
    assert policy['maxOutputTokens'] == 6000
    assert policy['readTimeoutSeconds'] == 120
    assert policy['taskBudgetSeconds'] == 135
    assert policy['structuredOutputRecoveryAttempts'] == 0
    assert seen['base_payload']['model'] == policy['model']
    assert seen['base_payload']['max_tokens'] == policy['maxOutputTokens']
    assert 'task_id' not in seen['provider_parameters']
    assert 'provenance' not in seen['provider_parameters']['candidates'][0]
    assert seen['request']['parameters']['candidates'][0]['provenance']
    assert seen['request']['providerEvidence']['evidenceKind'] == 'sub_call'
