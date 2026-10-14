import json

import httpx
import pytest

from refactor.learning.connections import credentials, providers
from refactor.learning.connections.errors import ModelCancelled, ModelError
from refactor.learning.connections.gateway import ModelGateway
from refactor.learning.connections.providers.base import invalid
from tests.refactor.gateway.conftest import MemoryVault
from .samples import KEY, anthro, chat, frame, sse, anthro_frames


def invoke(response, provider='deepseek', **kw):
    return providers.complete(provider, base_url=providers.REGISTRY[provider].default_base_url,
        model=providers.REGISTRY[provider].default_model, api_key=KEY,
        messages=[dict(role='user', content='private request')],
        http_client=httpx.MockTransport(lambda _: response
            if isinstance(response, httpx.Response) else httpx.Response(200, json=response)), **kw)


def logged(tmp_path, monkeypatch, invoke_call, reason):
    with pytest.raises(ModelError) as caught:
        invoke_call()
    error = caught.value
    assert error.code == 'MODEL_RESPONSE_INVALID'
    assert error.diagnostics['reason'] == reason
    monkeypatch.setattr(credentials, '_get_keyring', lambda: MemoryVault())
    gateway = ModelGateway('proto131', tmp_path)
    gateway._log_failure(error, dict(provider_id='deepseek'), KEY,
                         [dict(role='user', content='private request')], '', 'request131', 'teacher')
    path = gateway.usage_log.path.with_name('model-failures.log')
    row = json.loads(path.read_text(encoding='utf-8').splitlines()[-1])
    assert row['reason'] == reason
    assert KEY not in path.read_text(encoding='utf-8')
    assert 'private request' not in path.read_text(encoding='utf-8')
    gateway.close()
    return row, error


@pytest.mark.parametrize('provider,reason', [('deepseek', 'unexpected'), ('zhipu', 'content_filter')])
def test_unknown_finish_reason131_is_logged_not_accepted(tmp_path, monkeypatch, provider, reason):
    data = anthro() if provider == 'deepseek' else chat()
    if provider == 'deepseek': data['stop_reason'] = reason
    else: data['choices'][0]['finish_reason'] = reason
    row, error = logged(tmp_path, monkeypatch, lambda: invoke(data, provider), 'stop_reason:' + reason)
    assert row['stop_reason'] == reason
    assert error.usage.output_tokens == 20  # Rejection must retain the paid receipt.


@pytest.mark.parametrize('mode,reason', [
    ('search_type', 'unknown_search_result:future_result'),
    ('search_limit', 'search_limit'),
    ('client_tool', 'unsupported_client_tool'),
    ('usage', 'invalid_integer'),
    ('malformed', 'parse_error:KeyError'),
])
def test_anthropic_validation_sites131(tmp_path, monkeypatch, mode, reason):
    data = anthro(mode in {'search_type', 'search_limit'})
    kw = {}
    if mode == 'search_type': data['content'][1]['content'][0]['type'] = 'future_result'
    if mode == 'search_limit':
        data['usage']['server_tool_use']['web_search_requests'] = 9
        kw['enable_search'] = True
    if mode == 'client_tool': data['stop_reason'] = 'tool_use'
    if mode == 'usage': data['usage']['output_tokens'] = -1
    if mode == 'malformed': del data['content'][0]['text']
    row, _ = logged(tmp_path, monkeypatch, lambda: invoke(data, **kw), reason)
    if mode == 'search_limit': assert row['search_requests'] == 9 and row['search_limit'] == 8


def wire(blocks, reason='end_turn'):
    events = [dict(type='message_start', message=dict(usage=dict(input_tokens=5, output_tokens=0)))]
    for index, kind, delta in blocks:
        events += [dict(type='content_block_start', index=index,
                        content_block=dict(type=kind, **({'text': ''} if kind == 'text' else {'thinking': ''}))),
                   dict(type='content_block_delta', index=index, delta=delta),
                   dict(type='content_block_stop', index=index)]
    events += [dict(type='message_delta', delta=dict(stop_reason=reason), usage=dict(output_tokens=10)),
               dict(type='message_stop')]
    return sse([frame(e) for e in events])


def test_thinking_text_delta131_never_leaks_to_visible_sink(tmp_path, monkeypatch):
    chunks = []
    response = wire([(0, 'thinking', dict(type='text_delta', text='private thinking'))])
    row, _ = logged(tmp_path, monkeypatch, lambda: invoke(response, on_text=chunks.append), 'block_delta_mismatch')
    assert chunks == []
    assert row['block_types'] == ['thinking'] and row['block_type'] == 'thinking'
    assert row['delta_type'] == 'text_delta' and row['block_index'] == 0


def test_text_mismatch131_logs_lengths_first_difference_and_blocks_only(tmp_path, monkeypatch):
    # Out-of-order block indices: emitted A,B but final index order B,A.
    chunks = []
    response = wire([(1, 'text', dict(type='text_delta', text='A'*200)),
                     (0, 'text', dict(type='text_delta', text='B'*201))])
    row, _ = logged(tmp_path, monkeypatch, lambda: invoke(response, on_text=chunks.append), 'text_mismatch')
    assert row['emitted_characters'] == row['final_characters'] == 401
    assert row['first_difference'] == 0 and row['block_types'] == ['text', 'text']
    assert 'A'*40 not in json.dumps(row) and 'B'*40 not in json.dumps(row)


def test_documented_thinking_text_signature131_streams_only_answer():
    data = anthro()
    data['content'].insert(0, dict(type='thinking', thinking='hidden', signature='opaque'))
    events = anthro_frames(data)
    events.insert(2, frame(dict(type='content_block_delta', index=0,
                               delta=dict(type='signature_delta', signature='sig'))))
    response = sse(events)
    chunks = []
    value = invoke(response, on_text=chunks.append, reasoning_effort='high')
    assert chunks == ['答', '案'] and value.text == '答案'
    assert value.usage.output_tokens == 20


def test_callback_failure131_has_reason_and_cancel_stays_cancelled(tmp_path, monkeypatch):
    def bad(_): raise RuntimeError(KEY)
    response = wire([(0, 'text', dict(type='text_delta', text='answer'))])
    row, _ = logged(tmp_path, monkeypatch, lambda: invoke(response, on_text=bad), 'text_callback')
    assert row['callback_exception'] == 'RuntimeError'
    def cancelled(_): raise ModelCancelled()
    with pytest.raises(ModelCancelled):
        invoke(wire([(0, 'text', dict(type='text_delta', text='answer'))]), on_text=cancelled)


@pytest.mark.parametrize('mode,reason', [
    ('search_limit', 'search_limit'), ('bad_tool', 'invalid_search_tool_call'),
    ('client_tool', 'unsupported_client_tool'), ('incomplete', 'incomplete_chat_message'),
    ('choice', 'choice_index'),
])
def test_chat_validation_sites131(tmp_path, monkeypatch, mode, reason):
    data = chat(provider='kimi', tool=True)
    kw = dict(enable_search=True)
    response = data
    if mode == 'search_limit': data['choices'][0]['message']['tool_calls'] *= 9
    if mode == 'bad_tool': data['choices'][0]['message']['tool_calls'][0]['function']['name'] = 'other'
    if mode == 'client_tool': kw['enable_search'] = False
    if mode in {'incomplete', 'choice'}:
        event = dict(choices=[dict(index=1 if mode == 'choice' else 0, delta=dict(content='x'))])
        response = sse([frame(event)])
        kw['on_text'] = lambda _: None
    logged(tmp_path, monkeypatch, lambda: invoke(response, 'kimi', **kw), reason)


def test_no_secret_in_vendor_controlled_reason131(tmp_path, monkeypatch):
    data = anthro(reason=KEY)
    with pytest.raises(ModelError) as caught: invoke(data)
    assert KEY not in json.dumps(caught.value.diagnostics)
    monkeypatch.setattr(credentials, '_get_keyring', lambda: MemoryVault())
    gateway = ModelGateway('proto131', tmp_path)
    gateway._log_failure(caught.value, dict(provider_id='deepseek'), KEY, [], '', 'request', 'teacher')
    text = gateway.usage_log.path.with_name('model-failures.log').read_text(encoding='utf-8')
    assert KEY not in text and '[redacted]' in text
    gateway.close()


@pytest.mark.parametrize('mode,reason', [
    ('duplicate', 'duplicate_message_start'), ('block_before', 'invalid_block_start'),
    ('delta_before', 'message_delta_before_start'), ('incomplete', 'incomplete_anthropic_message'),
    ('round_limit', 'tool_round_limit'), ('string', 'invalid_string'),
    ('empty', 'empty_text_or_invalid_model'),
])
def test_remaining_anthropic_paths131(tmp_path, monkeypatch, mode, reason):
    data = anthro()
    events = anthro_frames(data)
    kw = {}
    if mode == 'duplicate': events.insert(1, events[0])
    if mode == 'block_before': events = [events[1]]
    if mode == 'delta_before': events = [events[-2]]
    if mode == 'incomplete': events = events[:-1]
    if mode == 'round_limit':
        data['stop_reason'] = 'pause_turn'
        kw['max_search_uses'] = 1
        # Every continuation returns pause_turn without a tool: bounded at 3.
    if mode == 'string': data['content'][0]['text'] = 7
    if mode == 'empty': data['content'][0]['text'] = ''
    if mode in {'round_limit', 'string', 'empty'}:
        call = lambda: invoke(data, **kw)
    else:
        call = lambda: invoke(sse(events), on_text=lambda _: None)
    logged(tmp_path, monkeypatch, call, reason)


def test_end_to_end_gateway131_failure_receipt_and_log(tmp_path, monkeypatch):
    vault = MemoryVault()
    monkeypatch.setattr(credentials, '_get_keyring', lambda: vault)
    gateway = ModelGateway('proto131', tmp_path)
    gateway.save_preset('deepseek', api_key=KEY)
    original = providers.complete
    def fail(*args, **kwargs):
        kwargs['http_client'] = httpx.MockTransport(lambda _: httpx.Response(200, json=anthro(reason='unexpected')))
        return original(*args, **kwargs)
    monkeypatch.setattr(providers, 'complete', fail)
    with pytest.raises(ModelError) as caught:
        gateway.complete('teacher', [dict(role='user', content='private request')], on_text=None)
    row = json.loads(gateway.usage_log.path.with_name('model-failures.log').read_text(encoding='utf-8'))
    assert row['reason'] == 'stop_reason:unexpected' and row['request_id'] == caught.value.request_id
    summary = gateway.usage_log.summary('2020-01-01T00:00:00Z', '2099-01-01T00:00:00Z')['total']
    assert summary['calls'] == 1 and caught.value.usage.output_tokens == 20
    gateway.close()
