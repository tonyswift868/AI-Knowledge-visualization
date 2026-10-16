"""BUG-106 connection probes: provider receipts and classification stay honest."""
from __future__ import annotations

import json

import httpx
import pytest

from refactor.learning.connections import providers
from refactor.learning.connections.errors import ModelError
from refactor.learning.connections.gateway import ModelGateway
from tests.refactor.gateway.test_gateway import KEY
from tests.refactor.providers.samples import frame, sse


REAL_COMPLETE = providers.complete
START, END = '2020-01-01T00:00:00Z', '2099-01-01T00:00:00Z'


def _gateway(tmp_path, account='connection153'):
    gateway = ModelGateway(account, tmp_path)
    gateway.save_preset('deepseek', api_key=KEY)
    return gateway


def _anthropic(*, stop='end_turn', text='', thinking='reasoning', usage=None):
    return {
        'id': 'msg_fixture',
        'type': 'message',
        'role': 'assistant',
        'model': 'deepseek-v4-flash',
        'content': ([{'type': 'thinking', 'thinking': thinking, 'signature': 'fixture-signature'}]
                    if thinking else []) + ([{'type': 'text', 'text': text}] if text else []),
        'stop_reason': stop,
        'stop_sequence': None,
        'usage': usage or {'input_tokens': 12, 'output_tokens': 8},
    }


def _connect_mocktransport(monkeypatch, responder):
    """Keep gateway/provider logic real; only replace the HTTP wire."""
    requests = []

    def routed(provider, **kwargs):
        def handle(request):
            requests.append(request)
            return responder(request)

        return REAL_COMPLETE(provider, http_client=httpx.MockTransport(handle), **kwargs)

    monkeypatch.setattr(providers, 'complete', routed)
    return requests


@pytest.mark.parametrize('stop,expected_finish', [
    ('max_tokens', 'max_tokens'),
    ('end_turn', 'end_turn'),
])
def test_connection153_thinking_only_complete_receipt_is_success(
        tmp_path, monkeypatch, stop, expected_finish):
    gateway = _gateway(tmp_path)
    requests = _connect_mocktransport(
        monkeypatch,
        lambda _request: httpx.Response(200, json=_anthropic(stop=stop, text='', thinking='thinking only')),
    )

    result = gateway.test_connection()

    assert result.text == ''
    assert result.finish_reason == expected_finish
    metadata = result.usage.raw['_mentorflow']
    assert metadata['response_received'] is True
    assert not metadata.get('partial_usage')
    assert metadata['thinking_characters'] > 0
    sent = json.loads(requests[0].content)
    assert sent['max_tokens'] == 32
    assert sent['thinking'] == {'type': 'disabled'}
    assert 'output_config' not in sent
    assert gateway.model_status()['state'] == 'ok'
    summary = gateway.usage_log.summary(START, END)['total']
    assert summary['calls'] == 1 and summary['succeeded'] == 1 and summary['failed'] == 0
    gateway.close()


def test_connection153_partial_truncation_is_not_treated_as_reachable_success(tmp_path, monkeypatch):
    gateway = _gateway(tmp_path)
    events = [
        {'type': 'message_start', 'message': {'usage': {'input_tokens': 12, 'output_tokens': 1}}},
        {'type': 'content_block_start', 'index': 0,
         'content_block': {'type': 'thinking', 'thinking': '', 'signature': ''}},
        {'type': 'content_block_delta', 'index': 0,
         'delta': {'type': 'thinking_delta', 'thinking': 'partial reasoning'}},
        {'type': 'content_block_stop', 'index': 0},
        # The provider ended at the output cap without a complete final usage receipt.
        {'type': 'message_delta', 'delta': {'stop_reason': 'max_tokens'}, 'usage': {}},
        {'type': 'message_stop'},
    ]

    def respond(_request):
        return sse([frame(event) for event in events])

    requests = _connect_mocktransport(monkeypatch, respond)
    with pytest.raises(ModelError) as caught:
        gateway.complete('connection_test', [{'role': 'user', 'content': 'Reply OK.'}],
                         reasoning_effort='none', max_tokens=32, on_text=lambda _part: None)

    error = caught.value
    assert error.code == 'MODEL_OUTPUT_TRUNCATED'
    assert error.response_received is True
    assert error.usage.raw['_mentorflow']['partial_usage'] is True
    assert error.usage.raw['_mentorflow']['thinking_characters'] > 0
    assert json.loads(requests[0].content)['thinking'] == {'type': 'disabled'}
    assert gateway.usage_log.summary(START, END)['total']['failed'] == 1
    gateway.close()


@pytest.mark.parametrize('bad_model', [123, KEY])
@pytest.mark.parametrize('stop,expected_code', [
    ('end_turn', 'MODEL_RESPONSE_INVALID'),
    ('max_tokens', 'MODEL_OUTPUT_TRUNCATED'),
])
def test_connection153_thinking_only_does_not_hide_invalid_or_key_echoed_model(
        tmp_path, monkeypatch, bad_model, stop, expected_code):
    gateway = _gateway(tmp_path, account='bad-model-' + str(type(bad_model).__name__))
    requests = _connect_mocktransport(
        monkeypatch,
        lambda _request: httpx.Response(200, json=dict(
            _anthropic(stop=stop, text='', thinking='only reasoning'), model=bad_model)),
    )

    with pytest.raises(ModelError) as caught:
        gateway.test_connection()

    error = caught.value
    assert error.code == expected_code
    if stop == 'end_turn':
        assert error.diagnostics['reason'] == 'empty_text_or_invalid_model'
    assert error.response_model_valid is False
    assert error.response_received is True
    assert error.usage.raw['_mentorflow']['thinking_characters'] > 0
    assert json.loads(requests[0].content)['thinking'] == {'type': 'disabled'}
    assert gateway.model_status()['state'] == 'error'
    assert gateway.usage_log.summary(START, END)['total']['failed'] == 1
    gateway.close()


def _incomplete_text_stream():
    events = [
        {'type': 'message_start', 'message': {'usage': {'input_tokens': 12, 'output_tokens': 1}}},
        {'type': 'content_block_start', 'index': 0, 'content_block': {'type': 'text', 'text': ''}},
        {'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'text_delta', 'text': 'partial visible'}},
        {'type': 'content_block_stop', 'index': 0},
        # Missing message_delta/message_stop: a response was received, but it is not complete.
    ]
    return sse([frame(event) for event in events])


@pytest.mark.parametrize('kind', ['balance', 'key', 'rate', 'busy', 'network', 'interrupted', 'software'])
def test_connection153_real_provider_fault_categories_are_not_swallowed(tmp_path, monkeypatch, kind):
    gateway = _gateway(tmp_path, account='fault-' + kind)

    def respond(request):
        if kind == 'balance':
            return httpx.Response(402, json={'error': {'type': 'insufficient_balance', 'message': 'fixture'}})
        if kind == 'key':
            return httpx.Response(401, json={'error': {'type': 'authentication_error', 'message': 'fixture'}})
        if kind == 'rate':
            return httpx.Response(429, json={'error': {'type': 'rate_limit_error', 'message': 'fixture'}})
        if kind == 'busy':
            return httpx.Response(503, json={'error': {'type': 'overloaded_error', 'message': 'fixture'}})
        if kind == 'network':
            raise httpx.ConnectError('offline fixture', request=request)
        if kind == 'interrupted':
            return _incomplete_text_stream()
        # A 200 JSON body where the streaming endpoint promised SSE is a protocol fault.
        return httpx.Response(200, json={'unexpected': 'fixture'})

    _connect_mocktransport(monkeypatch, respond)
    with pytest.raises(ModelError) as caught:
        gateway.complete('connection_test', [{'role': 'user', 'content': 'Reply OK.'}],
                         reasoning_effort='none', max_tokens=32,
                         on_text=(lambda _part: None) if kind in {'interrupted', 'software'} else None)

    expected = {
        'balance': 'balance', 'key': 'key', 'rate': 'rate', 'busy': 'busy',
        'network': 'network', 'interrupted': 'interrupted', 'software': 'software',
    }
    error = caught.value
    fault = error.model_fault
    assert fault['category'] == expected[kind]
    assert gateway.model_status()['state'] == 'error'
    assert error.code not in {'MODEL_OUTPUT_TRUNCATED', ''}
    assert gateway.usage_log.summary(START, END)['total']['failed'] == 1
    gateway.close()


def test_connection153_teacher_truncation_remains_a_failure(tmp_path, monkeypatch):
    gateway = _gateway(tmp_path)
    responses = iter([
        _anthropic(stop='end_turn', text='之前的一次正常回复', thinking=''),
        _anthropic(stop='max_tokens', text='partial answer'),
    ])
    _connect_mocktransport(monkeypatch, lambda _request: httpx.Response(200, json=next(responses)))

    gateway.complete('teacher', [{'role': 'user', 'content': '正常请求'}])
    assert gateway.model_status()['state'] == 'ok'

    with pytest.raises(ModelError) as caught:
        gateway.complete('teacher', [{'role': 'user', 'content': '课堂请求'}])

    assert caught.value.code == 'MODEL_OUTPUT_TRUNCATED'
    summary = gateway.usage_log.summary(START, END)['total']
    assert summary['calls'] == 2 and summary['succeeded'] == 1 and summary['failed'] == 1
    # Existing teacher flow owns truncation recovery; the gateway must not turn
    # its temporarily incomplete first candidate into a permanent fault state.
    assert gateway.model_status()['state'] == 'ok'
    gateway.close()


def test_connection153_reopened_gateway_starts_unknown_then_teacher_request_is_green(tmp_path, monkeypatch):
    first = _gateway(tmp_path, account='relogin')
    _connect_mocktransport(
        monkeypatch,
        lambda _request: httpx.Response(200, json=_anthropic(text='正常回复', thinking='')),
    )
    first.complete('teacher', [{'role': 'user', 'content': '登录前验证'}])
    assert first.model_status()['state'] == 'ok'
    first.close()

    reopened = ModelGateway('relogin', tmp_path)
    assert reopened.model_status()['state'] == 'unknown'
    assert reopened.model_status()['short'] == '模型待验证'
    # The first ordinary request after a new session establishes status; no explicit probe is required.
    reopened.complete('teacher', [{'role': 'user', 'content': '登录后第一次请求'}])
    assert reopened.model_status()['state'] == 'ok'
    assert reopened.model_status()['short'] == '模型正常'
    assert reopened.usage_log.summary(START, END)['total']['calls'] == 2
    reopened.close()

