from __future__ import annotations

import json
import socket
import time
from copy import deepcopy
from types import SimpleNamespace

import httpx
import pytest

from app.execution import durable_skill_runtime as runtime
from app.execution import skill_finalization_runtime
from app.modules.provider_transport_evidence import ProviderHttpAttempt

SKILL = 'opentrajectory_source_screening_selection'


def parameters():
    # Deterministic protocol fixture, never a scientific acceptance result.
    return {'task_id': 'batch-failure-contract-test', 'user_query': 'Assess supplied material.',
            'research_context': {}, 'evidence_schema': {'evidence_targets': []},
            'candidates': [{'candidate_id': f'candidate_{i}', 'title': f'Supplied material {i}',
                            'abstract': 'Supplied test text.', 'arxiv_id': '',
                            'provenance': [{'source_name': 'protocol_fixture'}]} for i in range(4)]}


@pytest.fixture(autouse=True)
def no_external_io(monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError('Real Provider/network/DB/finalizer IO is forbidden in this suite')
    for name in ['connect', 'connect_ex']:
        monkeypatch.setattr(socket.socket, name, forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    monkeypatch.setattr(httpx.Client, 'send', forbidden)
    import psycopg
    monkeypatch.setattr(psycopg, 'connect', forbidden)
    monkeypatch.setattr(psycopg.Connection, 'connect', forbidden)
    monkeypatch.setattr(psycopg.AsyncConnection, 'connect', forbidden)
    monkeypatch.setattr(skill_finalization_runtime, 'finalize_and_validate_registered_output', forbidden)
    # These tests start after provider-schema capability selection; they exercise
    # only batch failure facts and no-replay transport behavior.
    monkeypatch.setattr(
        runtime, 'apply_provider_schema_transport',
        lambda _provider, *, payload, **_kwargs: dict(payload),
    )
    monkeypatch.setattr(runtime, '_record_durable_skill_usage', lambda **_: None)
    monkeypatch.setenv('LLM_PROVIDER_API_KEY', 'offline-test-key')
    monkeypatch.setenv('LLM_PROVIDER_BASE_URL', 'https://blocked.invalid')
    monkeypatch.setenv('LLM_PROVIDER_MODEL', 'test-model')
    monkeypatch.setenv('LLM_PROVIDER_MODEL_TERRA', 'test-model')


def inject(monkeypatch, outcomes):
    seen = {'http': [], 'attempts': [], 'io_started': [], 'finished': []}

    class Attempts:
        def begin_sub_attempt(self, **kw):
            attempt = ProviderHttpAttempt('offline', kw['capability_call_id'], len(seen['attempts']) + 1,
                                          'offline-hash', 'offline-endpoint', 'sub_call', kw['adapter_name'])
            seen['attempts'].append({'attempt': attempt, **kw})
            return attempt
        def begin(self, **_):
            raise AssertionError('Expected attributed child attempt')
        def mark_io_started(self, attempt):
            assert attempt in [item['attempt'] for item in seen['attempts']]
            assert attempt not in seen['io_started']
            seen['io_started'].append(attempt)
        def finish(self, attempt, **kw):
            assert attempt in seen['io_started']
            seen['finished'].append(kw)

    class Client:
        def __init__(self, **_): pass
        def __enter__(self): return self
        def __exit__(self, *_): return False
        def post(self, url, *, headers, content):
            assert len(seen['io_started']) > len(seen['http'])
            seen['http'].append(json.loads(content))
            index = len(seen['http']) - 1
            assert index < len(outcomes), 'Unexpected additional submission'
            value = outcomes[index]
            if isinstance(value, BaseException):
                raise value
            status, body = value
            kwargs = {'json': body} if not isinstance(body, bytes) else {'content': body}
            return httpx.Response(status, **kwargs, headers={'x-request-id': 'test-request-id'},
                                  request=httpx.Request('POST', 'https://blocked.invalid'))

    monkeypatch.setattr(runtime, 'ProviderHttpAttemptRepository', Attempts)
    monkeypatch.setattr(runtime.httpx, 'Client', Client)
    return seen


def child():
    return runtime.execute_new_durable_llm_child(skill_id=SKILL, parameters=parameters(),
              parent_capability_call_id='offline-parent', adapter_name='batch-failure-test')


@pytest.mark.parametrize('status', [400, 401, 403, 422, 408, 429, 500, 502, 503, 504])
def test_http_failure_keeps_actual_stage_and_never_becomes_draft_error(monkeypatch, status):
    seen = inject(monkeypatch, [(status, {'error': {'message': 'DO_NOT_COPY_SECRET',
                       'type': 'bad_response_status_code', 'code': 'bad_response_status_code'}})])
    result = child()
    transient = status in {408, 429} or status >= 500
    assert result['state'] == 'failed'
    assert result['failureClassification'] == ('provider_http_error' if transient else 'provider_rejected_request')
    assert result['failedStage'] == 'provider_http_response'
    assert result['firstMismatch'] == f'http_{status}'
    assert result['diagnostics']['providerHttp'] == {'httpStatus': status, 'providerRequestId': 'test-request-id'}
    assert len(seen['http']) == len(seen['io_started']) == len(seen['attempts']) == 1
    assert 'DO_NOT_COPY_SECRET' not in json.dumps(result)
    record = result['diagnostics']['structuredBatch']['failedBatches'][0]
    assert record['inputIdentities'] == [f'candidate_{i}' for i in range(4)]
    assert record['failure']['firstMismatch'] == result['firstMismatch']


@pytest.mark.parametrize('exc_type,phase,known_unsent', [
    (httpx.ConnectTimeout, 'connect', True), (httpx.ConnectError, 'connect', True),
    (httpx.PoolTimeout, 'pool', True), (httpx.ReadTimeout, 'read', False),
    (httpx.WriteTimeout, 'write', False), (httpx.RemoteProtocolError, 'protocol', False),
])
def test_transport_phase_survives_without_repeated_submission(monkeypatch, exc_type, phase, known_unsent):
    seen = inject(monkeypatch, [exc_type('DO_NOT_COPY_SECRET')])
    result = child()
    assert result['state'] == ('failed' if known_unsent else 'unknown')
    assert result['failedStage'] == f'provider_transport_{phase}'
    assert result['firstMismatch'] == exc_type.__name__
    assert result['diagnostics']['transport']['submissionKnownNotSent'] is known_unsent
    assert len(seen['http']) == len(seen['io_started']) == 1
    assert 'DO_NOT_COPY_SECRET' not in json.dumps(result)


@pytest.mark.parametrize('body,stage', [(b'not JSON', 'provider_body_decode'), ({'choices': [{'message': {'content': '{'}}]}, 'structured_output_parse')])
def test_completed_invalid_body_remains_output_contract_failure(monkeypatch, body, stage):
    seen = inject(monkeypatch, [(200, body)])
    result = child()
    assert result['state'] == 'failed'
    assert result['failureClassification'] == 'provider_response_contract_invalid'
    assert len(seen['http']) == len(seen['io_started']) == 1
    assert 'providerHttp' not in result['diagnostics']
    assert result['failedStage'] == stage


def batch(monkeypatch, outcomes, *, deadline=None):
    seen = inject(monkeypatch, outcomes)
    original = parameters()
    schema = {'type': 'object', 'required': ['judgments'], 'additionalProperties': False,
              'properties': {'judgments': {'type': 'array', 'items': {'type': 'object'}}}}
    base = {'model': 'offline-model', 'messages': [{'role': 'system', 'content': 'Protocol fixture only.'}, {'role': 'user', 'content': json.dumps({
                    'scientificInput': original, 'outputSchema': schema})}]}
    result = runtime._execute_structured_batch_requests(
        request={'providerEvidence': {'capabilityCallId': 'offline', 'evidenceKind': 'sub_call'}},
        skill_id='any_registered_batch_capability', provider_parameters=original,
        batch_policy={'input_array_field': 'candidates', 'output_array_field': 'judgments',
                      'identity_field': 'candidate_id', 'max_items_per_batch': 2, 'max_parallel': 1},
        provider_message_contract='scientific_minimal_v1', base_payload=base, draft_schema=schema,
        runtime_config=SimpleNamespace(model='offline-model', adapter='huiyan_chat_completions'),
        endpoint='https://blocked.invalid', base_headers={}, provider_id='offline',
        execution_policy={'readTimeoutSeconds': 120}, task_deadline=deadline, max_output_chars=50_000)
    return result, seen


def test_partial_success_is_preserved_and_not_resubmitted(monkeypatch):
    draft = {'judgments': [{'candidate_id': 'candidate_0'}, {'candidate_id': 'candidate_1'}]}
    response = {'choices': [{'message': {'content': json.dumps(draft)}}],
                'usage': {'prompt_tokens': 1, 'completion_tokens': 1, 'total_tokens': 2}}
    result, seen = batch(monkeypatch, [(200, response), (504, {'error': {}})])
    assert result['successfulBatchCount'] == 1
    assert result['draft'] == draft
    assert result['failedBatches'][0]['inputIdentities'] == ['candidate_2', 'candidate_3']
    assert result['failedBatches'][0]['failure']['firstMismatch'] == 'http_504'
    assert len(seen['http']) == len(seen['io_started']) == 2  # two different batches, no retries


def test_uncertain_batch_cannot_be_hidden_by_another_known_failure(monkeypatch):
    value, seen = batch(monkeypatch, [(401, {'error': {}}), httpx.ReadTimeout('response lost')])
    result = runtime._structured_batch_failure_result(value['failedBatches'])
    assert result['state'] == 'unknown'
    assert result['firstMismatch'] == 'ReadTimeout'
    assert len(result['diagnostics']['structuredBatch']['failedBatches']) == 2
    assert len(seen['http']) == len(seen['io_started']) == 2


def test_soft_budget_expiry_does_not_terminate_running_provider_observation(monkeypatch):
    responses = []
    for start in (0, 2):
        draft = {'judgments': [
            {'candidate_id': f'candidate_{start}'},
            {'candidate_id': f'candidate_{start + 1}'},
        ]}
        responses.append((200, {'choices': [{'message': {'content': json.dumps(draft)}}]}))
    value, seen = batch(monkeypatch, responses, deadline=time.monotonic() - 1)
    assert value['successfulBatchCount'] == 2
    assert value['failedBatches'] == []
    assert len(seen['http']) == len(seen['io_started']) == 2


def test_local_batch_array_gap_preserves_response_and_valid_sibling(monkeypatch):
    gap = {"summary": "Evidence is incomplete", "limitations": ["missing measurement"]}
    valid = {"judgments": [{"candidate_id": "candidate_2"}]}
    responses = [(200, {"choices": [{"message": {"content": json.dumps(d)}}]}) for d in (gap, valid)]
    value, seen = batch(monkeypatch, responses)
    assert value["successfulBatchCount"] == 2
    assert value["failedBatches"] == []
    assert value["draft"]["judgments"] == valid["judgments"]
    assert value["draft"]["unmerged_batch_outputs"] == [gap]
    assert len(seen["http"]) == 2


def test_batch_merge_retains_extra_fields_and_existing_array_items():
    value = {"judgments": []}
    original = {"judgments": [{"id": "a"}], "limitations": ["unknown"]}
    runtime._merge_structured_batch_draft(value, original, "judgments")
    assert value == {"judgments": [{"id": "a"}], "unmerged_batch_outputs": [original]}
