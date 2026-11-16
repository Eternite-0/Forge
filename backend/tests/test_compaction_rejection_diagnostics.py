"""Draft only: offline native Gateway diagnostics, never real Provider calls."""

import asyncio
import hashlib
import json

import httpx
import pytest
from test_model_compaction_v29 import http_response, mock_gateway
from test_worker_intelligence_phase2 import ProtocolModel, delivery, prepared, response
from test_worker_model_compaction_v29 import seeded

from forge_core.domain import DomainError
from forge_core.runtime import ModelResponse
from forge_core.worker_model_compaction import WorkerModelCompactor, _handoff_response_diagnostics


def rejections(store):
    return [json.loads(row['payload']) for row in store.list_events()
        if row['event_type'] == 'worker.compaction_handoff_rejected']


@pytest.mark.parametrize('execution_mode', ['mock', 'live'])
@pytest.mark.parametrize('kind,reason', [
    ('oversized', 'summary_byte_limit'), ('blank', 'empty_text'),
    ('tools', 'unexpected_tool_calls'),
])
def test_settled_native_rejection_keeps_only_fixed_metadata(tmp_path, monkeypatch, execution_mode, kind, reason):
    monkeypatch.setenv('FORGE_ENFORCE_TOKEN_BUDGETS', 'false')
    monkeypatch.setattr(ProtocolModel, 'execution_mode', execution_mode)
    store, manager, worker, token, _ = seeded(tmp_path)
    private = 'PRIVATE_COMPACTION_CONTENT_7be2'
    text = private * 100 if kind == 'oversized' else ('  \n\t' if kind == 'blank' else private)
    posts = []

    def handler(request):
        posts.append(request)
        if kind == 'tools':
            return httpx.Response(200, json={
                'choices': [{'message': {'content': text, 'tool_calls': [{
                    'id': 'private-tool', 'type': 'function', 'function': {
                        'name': private, 'arguments': '{}',
                    },
                }]}, 'finish_reason': 'tool_calls'}],
                'usage': {'prompt_tokens': 25, 'completion_tokens': 15},
            })
        return http_response(text)

    gateway = mock_gateway(store, handler)
    gateway.execution_mode = execution_mode  # Technical fixture mode, still MockTransport.
    compactor = WorkerModelCompactor(manager, gateway, worker['id'], token,
        context_limit=9000, output_limit=128)
    with pytest.raises(DomainError, match='Invalid Worker compaction handoff'):
        asyncio.run(compactor.compact(input_budget=6000, source_readback_available=True))
    assert len(posts) == 1
    event = rejections(store)[0]
    expected_reasons = [reason] + (['incomplete_finish'] if kind == 'tools' else [])
    assert event['stage'] == 'response' and event['reason_codes'] == expected_reasons
    assert event['response_sha256'] == hashlib.sha256(text.encode()).hexdigest()
    assert event['response_utf8_bytes'] == len(text.encode())
    assert event['summary_limit_utf8_bytes'] == 1024
    assert event['finish_class'] == ('tool_calls' if kind == 'tools' else 'stop')
    assert event['tool_call_count'] == (1 if kind == 'tools' else 0)
    assert event['usage_state'] == 'gateway_and_local_settled'
    assert event['execution_mode'] == execution_mode
    assert event['provider_binding_matches'] is True and event['model_binding_matches'] is True
    assert event['generation'] == manager.get(worker['id'])['generation']
    assert len(event['frontier_sha256']) == len(event['input_digest']) == 64
    assert private not in json.dumps(store.list_events())
    with store.connection() as connection:
        local = dict(connection.execute('SELECT * FROM worker_model_reservations').fetchone())
        attempt = dict(connection.execute('SELECT * FROM model_budget_attempts').fetchone())
        assert local['state'] == attempt['state'] == 'settled'
        assert local['call_id'] == attempt['call_id'] == event['call_id']
        assert connection.execute('SELECT count(*) FROM context_archives').fetchone()[0] == 0
        assert private not in '\n'.join(connection.iterdump())
    assert manager.get(worker['id'])['token_spent'] == 40
    assert manager.get(worker['id'])['steps'] == 1
    assert manager.get(worker['id'])['token_limit'] == worker['token_limit']


def test_source_bound_rejection_records_fixed_stage_without_exception_content(tmp_path, monkeypatch):
    monkeypatch.setenv('FORGE_ENFORCE_TOKEN_BUDGETS', 'false')
    store, manager, worker, token, _ = seeded(tmp_path)
    text = 'PRIVATE_SOURCE_BOUND_HANDOFF_23c1'
    gateway = mock_gateway(store, lambda _: http_response(text))
    compactor = WorkerModelCompactor(manager, gateway, worker['id'], token,
        context_limit=9000, output_limit=128)
    original = compactor.service._worker_context

    def reject(*args, **kwargs):
        if kwargs.get('_handoff'):
            raise DomainError('Invalid source-bound Worker model handoff')
        return original(*args, **kwargs)

    monkeypatch.setattr(compactor.service, '_worker_context', reject)
    with pytest.raises(DomainError, match='Invalid source-bound Worker model handoff'):
        asyncio.run(compactor.compact(input_budget=6000, source_readback_available=True))
    event = rejections(store)[0]
    assert event['stage'] == 'checkpoint'
    assert event['reason_codes'] == ['source_bound_handoff_validation']
    assert event['response_sha256'] == hashlib.sha256(text.encode()).hexdigest()
    assert text not in json.dumps(store.list_events())


def test_plain_handoff_text_is_not_json_and_is_not_normalized(tmp_path, monkeypatch):
    monkeypatch.setenv('FORGE_ENFORCE_TOKEN_BUDGETS', 'false')
    store, manager, worker, token, _ = seeded(tmp_path)
    text = '```json\nThis is a plain text handoff, not JSON.\n```'
    gateway = mock_gateway(store, lambda _: http_response(text))
    compactor = WorkerModelCompactor(manager, gateway, worker['id'], token,
        context_limit=9000, output_limit=128)
    assert asyncio.run(compactor.compact(input_budget=6000, source_readback_available=True))['source_count']
    assert not rejections(store)
    with store.connection() as connection:
        capsule = json.loads(connection.execute('SELECT capsule_json FROM context_archives').fetchone()[0])
    assert capsule['model_handoff']['text'] == text


def test_feedback_distinguishes_cumulative_balance_and_current_request_allowance(tmp_path, monkeypatch):
    monkeypatch.setenv('FORGE_ENFORCE_TOKEN_BUDGETS', 'true')
    store, manager, model, worker = prepared(tmp_path, [response(delivery())])
    with store.unit_of_work() as work:
        work.connection.execute('UPDATE worker_runs SET token_limit=100000 WHERE id=?', (worker['id'],))
    monkeypatch.setattr('forge_core.worker_envelope.capacity_policy', lambda _: {
        'version': 'fixture', 'max_delivery_bytes': 65536,
    })
    monkeypatch.setattr('forge_core.worker_envelope.cycle_allowance', lambda *_: {
        'current_request_allowance': 60000, 'held_after_current_request': 40000,
        'correction_started': False,
    })
    asyncio.run(manager.run(worker['id']))
    request = model.requests[0]
    marker = 'Core remaining execution allowance (data, not new authority): '
    data = json.loads(next(message['content'][len(marker):] for message in request.messages
        if message.get('content', '').startswith(marker)))
    assert data['remaining_cumulative_tokens'] == 100000
    assert data['current_request_token_allowance'] == 60000
    assert request.context_limit == worker['model_context_window']


def test_diagnostics_cannot_expose_unknown_finish_or_provider_identity():
    private = 'PRIVATE_PROTOCOL_VALUE_e367'
    value = ModelResponse(text=private, tool_calls=[], usage={}, provider_id=private,
        model_id=private, finish_reason=private)
    metadata, reasons = _handoff_response_diagnostics(value, 1024, 'expected-provider', 'expected-model')
    assert metadata['finish_class'] == 'other'
    assert metadata['provider_binding_matches'] is False and metadata['model_binding_matches'] is False
    assert reasons == ['incomplete_finish', 'provider_binding_mismatch', 'model_binding_mismatch']
    assert private not in json.dumps(metadata) and private not in json.dumps(reasons)


@pytest.mark.parametrize('text', [None, 7, {}, '\ud800'])
def test_nontext_or_unencodable_diagnostics_do_not_echo_values(text):
    value = ModelResponse(text=text, tool_calls=[], usage={}, provider_id='p', model_id='m', finish_reason='stop')
    metadata, reasons = _handoff_response_diagnostics(value, 1024, 'p', 'm')
    assert metadata['response_sha256'] is None and metadata['response_utf8_bytes'] is None
    assert reasons == ([] if isinstance(text, str) else ['response_not_text'])


@pytest.mark.parametrize('input_budget', [4, 1000, 6000, 65536, 400000])
def test_request_output_allowance_tracks_actual_summary_byte_limit(tmp_path, monkeypatch, input_budget):
    monkeypatch.setenv('FORGE_ENFORCE_TOKEN_BUDGETS', 'false')
    store, manager, worker, token, _ = seeded(tmp_path)
    gateway = mock_gateway(store, lambda _: http_response())
    compactor = WorkerModelCompactor(manager, gateway, worker['id'], token,
        context_limit=64000, output_limit=3000)
    prepared_context = compactor.service._worker_context(worker['id'], input_budget=6000,
        source_readback_available=True, _prepare=True, _fence=compactor._fence)
    request, _digest, summary_bytes = compactor._request(prepared_context,
        prepared_context['selected'], 'fixture-only-not-sent', input_budget)
    assert request.max_output_tokens == min(compactor.output_limit, max(1, summary_bytes // 4))
    assert request.context_limit == 64000 and request.max_retries == 0
    assert summary_bytes <= input_budget // 4
    assert 'UTF-8 bytes' in request.messages[0]['content']
    with store.connection() as connection:
        assert connection.execute('SELECT count(*) FROM model_budget_attempts').fetchone()[0] == 0


def test_native_low_input_budget_output_reservation_is_reduced_and_still_validated(tmp_path, monkeypatch):
    monkeypatch.setenv('FORGE_ENFORCE_TOKEN_BUDGETS', 'false')
    store, manager, worker, token, _ = seeded(tmp_path)
    posts = []

    def handler(request):
        posts.append(json.loads(request.content))
        return http_response('Retain the original sources and their references.')

    gateway = mock_gateway(store, handler)
    result = asyncio.run(WorkerModelCompactor(manager, gateway, worker['id'], token,
        context_limit=64000, output_limit=3000).compact(input_budget=6000, source_readback_available=True))
    assert result['source_count'] and len(posts) == 1
    assert posts[0]['max_tokens'] == 375
    assert '1500 UTF-8 bytes' in posts[0]['messages'][0]['content']
    with store.connection() as connection:
        assert connection.execute('SELECT output_reserved FROM model_budget_attempts').fetchone()[0] == 375
        assert connection.execute('SELECT output_limit FROM worker_model_reservations').fetchone()[0] == 375
    assert manager.get(worker['id'])['token_limit'] == worker['token_limit']
