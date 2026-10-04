import asyncio
import json
import threading
import time
import traceback
from types import SimpleNamespace

import httpx
import pytest

from refactor.learning.connections.providers import REGISTRY, ModelError, complete
from refactor.learning.connections.providers import _http
from .samples import (KEY, MESSAGES, ByteStream, anthro, anthro_frames, call, chat,
                      chat_frames, frame, ordinary, responses, sse)


def invoke(transport, provider='openai', **kwargs):
    spec = REGISTRY[provider]
    values = dict(base_url=spec.default_base_url, model=spec.default_model, api_key=KEY,
                  messages=MESSAGES, http_client=transport)
    values.update(kwargs)
    return complete(provider, **values)


@pytest.mark.parametrize('status,expected', [(401, 'MODEL_AUTH_FAILED'), (402, 'MODEL_INSUFFICIENT_BALANCE'),
                                           (403, 'MODEL_PERMISSION_DENIED'), (404, 'MODEL_NOT_FOUND'),
                                           (429, 'MODEL_RATE_LIMITED'), (500, 'MODEL_HTTP_ERROR')])
def test_http_error_codes_safe_text_and_no_retry(status, expected):
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(status, json={'error': {'message': KEY, 'code': 'unknown'}})
    with pytest.raises(ModelError) as exc:
        invoke(httpx.MockTransport(handler), provider='deepseek')
    assert exc.value.code == expected
    assert exc.value.status == status
    assert len(requests) == 1
    assert KEY not in str(exc.value)
    assert KEY not in repr(exc.value)
    assert KEY not in ''.join(traceback.format_exception(exc.value))
    if status == 402:
        assert str(exc.value) == '模型账户余额不足'


@pytest.mark.parametrize('provider,status,body', [
    ('anthropic', 402, {'error': {'type': 'billing_error'}}),
    ('qwen', 400, {'code': 'Arrearage'}),
    ('qwen', 400, {'code': 'isv.OUT_OF_SERVICE'}),
    ('zhipu', 429, {'error': {'code': '1113'}}),
    ('openai', 429, {'error': {'code': 'credit_balance_exhausted'}}),
])
def test_documented_balance_codes(provider, status, body):
    body['message'] = KEY
    with pytest.raises(ModelError) as exc:
        invoke(httpx.MockTransport(lambda _: httpx.Response(status, json=body)), provider=provider)
    assert exc.value.code == 'MODEL_INSUFFICIENT_BALANCE'
    assert str(exc.value) == '模型账户余额不足'


@pytest.mark.parametrize('provider,body', [
    ('openai', {'error': {'code': 'rate_limit_exceeded'}}),
    ('openai', {'error': {'code': 'insufficient_quota'}}),
    ('gemini', {'error': {'status': 'RESOURCE_EXHAUSTED'}}),
    ('zhipu', {'error': {'code': '1302'}}),
    ('kimi', {'error': {'type': 'rate_limit_reached'}}),
])
def test_quota_or_rate_does_not_prove_balance(provider, body):
    with pytest.raises(ModelError) as exc:
        invoke(httpx.MockTransport(lambda _: httpx.Response(429, json=body)), provider=provider)
    assert exc.value.code == 'MODEL_RATE_LIMITED'


@pytest.mark.parametrize('status', [301, 302, 303, 307, 308])
def test_redirect_refused_even_with_following_injected_client(status):
    requests = []
    def handle(request):
        requests.append(request)
        return httpx.Response(status, headers={'location': 'https://elsewhere.example.test/' + KEY})
    client = httpx.AsyncClient(transport=httpx.MockTransport(handle), follow_redirects=True)
    try:
        with pytest.raises(ModelError) as exc:
            invoke(client)
        assert exc.value.code == 'MODEL_REDIRECT_REJECTED'
        assert len(requests) == 1
        assert KEY not in str(exc.value)
        assert not client.is_closed  # external ownership retained
    finally:
        asyncio.run(client.aclose())


@pytest.mark.parametrize('url', ['http://remote.example.test', 'https://user:pass@host.test',
                                'https://host.test?api_key=unsafe', 'https://host.test/#fragment',
                                'https://host.test\\evil', 'https://host.test/ bad'])
def test_unsafe_urls_never_send(url):
    sent = []
    with pytest.raises(ModelError) as exc:
        invoke(httpx.MockTransport(lambda req: sent.append(req)), base_url=url)
    assert exc.value.code == 'MODEL_URL_INVALID'
    assert sent == []


def test_loopback_exception_matches_existing_transport():
    seen = []
    def handle(request):
        seen.append(request)
        return httpx.Response(200, json=responses())
    result = invoke(httpx.MockTransport(handle), base_url='http://127.0.0.1:8000/v1')
    assert result.text == '答案'
    assert seen[0].url.path == '/v1/responses'


@pytest.mark.parametrize('exception,code', [(httpx.ReadTimeout, 'MODEL_TIMEOUT'),
                                         (httpx.ConnectError, 'MODEL_CONNECTION_FAILED')])
def test_transport_exceptions_sanitized(exception, code):
    def handler(request):
        raise exception('secret=' + KEY, request=request)
    with pytest.raises(ModelError) as exc:
        invoke(httpx.MockTransport(handler))
    assert exc.value.code == code
    assert KEY not in ''.join(traceback.format_exception(exc.value))


def test_pre_cancel_never_sends():
    cancel, sent = threading.Event(), []
    cancel.set()
    with pytest.raises(ModelError) as exc:
        invoke(httpx.MockTransport(lambda req: sent.append(req)), cancel=cancel)
    assert exc.value.code == 'MODEL_CANCELLED'
    assert sent == []


@pytest.mark.parametrize('mode', ['cancel', 'timeout'])
def test_cancel_or_deadline_during_pending_headers_cancels_task(mode):
    cancel = threading.Event()
    unwound = []
    async def handler(request):
        if mode == 'cancel':
            asyncio.get_running_loop().call_later(.04, cancel.set)
        try:
            await asyncio.sleep(10)
        finally:
            unwound.append(True)
    start = time.monotonic()
    with pytest.raises(ModelError) as exc:
        invoke(httpx.MockTransport(handler), cancel=cancel, timeout=.12 if mode == 'timeout' else 2)
    assert exc.value.code == ('MODEL_CANCELLED' if mode == 'cancel' else 'MODEL_TIMEOUT')
    assert unwound == [True]
    assert time.monotonic() - start < 1


@pytest.mark.parametrize('mode', ['cancel', 'timeout'])
def test_cancel_or_deadline_closes_inflight_stream(mode):
    cancel = threading.Event()
    class HangingStream(httpx.AsyncByteStream):
        closed = False
        async def __aiter__(self):
            yield frame({'type': 'response.output_text.delta', 'delta': '答'})
            if mode == 'cancel':
                asyncio.get_running_loop().call_later(.04, cancel.set)
            await asyncio.sleep(10)
        async def aclose(self):
            self.closed = True
    stream, chunks = HangingStream(), []
    with pytest.raises(ModelError) as exc:
        invoke(httpx.MockTransport(lambda _: httpx.Response(200, headers={'content-type': 'text/event-stream'}, stream=stream)),
               on_text=chunks.append, cancel=cancel, timeout=.12 if mode == 'timeout' else 2)
    assert exc.value.code == ('MODEL_CANCELLED' if mode == 'cancel' else 'MODEL_TIMEOUT')
    assert chunks == ['答']
    assert stream.closed


def test_callback_cancel_prevents_tool_roundtrip_even_in_one_network_chunk():
    cancel, sent = threading.Event(), []
    first = chat(provider='kimi', tool=True)
    first['choices'][0]['message']['content'] = '先搜索'
    def handler(request):
        sent.append(request)
        return sse(chat_frames(first))
    with pytest.raises(ModelError) as exc:
        invoke(httpx.MockTransport(handler), provider='kimi', enable_search=True,
               cancel=cancel, on_text=lambda _: cancel.set())
    assert exc.value.code == 'MODEL_CANCELLED'
    assert len(sent) == 1


def test_total_deadline_covers_all_tool_roundtrips(monkeypatch):
    # HTTP's deadline and asyncio's outer guard must share the same clock.
    # Advancing only HTTP's clock would leave a real 90 ms timer racing the test.
    elapsed_ms = 0
    monotonic = lambda: elapsed_ms / 1000
    monkeypatch.setattr(_http, 'time', SimpleNamespace(monotonic=monotonic))
    new_event_loop = asyncio.events.new_event_loop
    def controlled_loop():
        loop = new_event_loop()
        loop.time = monotonic
        return loop
    monkeypatch.setattr(asyncio.events, 'new_event_loop', controlled_loop)
    sent, starts = [], []
    async def handler(request):
        nonlocal elapsed_ms
        sent.append(request)
        starts.append(elapsed_ms)
        elapsed_ms += 55
        await asyncio.sleep(0)  # Yield to the actual timeout guard, without waiting.
        return httpx.Response(200, json=chat(provider='kimi', tool=len(sent) == 1))
    with pytest.raises(ModelError) as exc:
        invoke(httpx.MockTransport(handler), provider='kimi', enable_search=True, timeout=.09)
    assert exc.value.code == 'MODEL_TIMEOUT'
    assert len(sent) == 2
    assert starts == [0, 55] and elapsed_ms == 110
    assert any(m['role'] == 'tool' for m in json.loads(sent[1].content)['messages'])


def test_callback_error_never_exposes_secret_even_as_modelerror():
    def callback(_):
        raise ModelError('MODEL_HTTP_ERROR', KEY)
    with pytest.raises(ModelError) as exc:
        call('openai', [sse([frame({'type': 'response.output_text.delta', 'delta': '答案'})])], on_text=callback)
    assert exc.value.code == 'MODEL_RESPONSE_INVALID'
    assert KEY not in ''.join(traceback.format_exception(exc.value))


@pytest.mark.parametrize('provider,data', [('anthropic', {'type': 'error', 'error': {'type': 'authentication_error', 'message': KEY}}),
                                         ('openai', {'type': 'error', 'error': {'code': 'credit_balance_exhausted', 'message': KEY}}),
                                         ('qwen', {'code': 'Arrearage', 'message': KEY})])
def test_stream_error_event_is_safe(provider, data):
    with pytest.raises(ModelError) as exc:
        call(provider, [sse([frame(data)])], on_text=lambda _: None)
    assert exc.value.code in {'MODEL_AUTH_FAILED', 'MODEL_INSUFFICIENT_BALANCE'}
    assert KEY not in str(exc.value)


def test_oversized_response_is_closed(monkeypatch):
    monkeypatch.setattr(_http, 'MAX_BYTES', 100)
    stream = ByteStream([b'data: ' + b'x' * 101])
    with pytest.raises(ModelError) as exc:
        invoke(httpx.MockTransport(lambda _: httpx.Response(200, headers={'content-type': 'text/event-stream'}, stream=stream)),
               on_text=lambda _: None)
    assert exc.value.code == 'MODEL_RESPONSE_TOO_LARGE'
    assert stream.closed


@pytest.mark.parametrize('wire', [b'data: {invalid}\n\n', b'data: {"type":"ping"}', b'data: \xff\n\n'])
def test_malformed_sse_does_not_return_success(wire):
    with pytest.raises(ModelError) as exc:
        invoke(httpx.MockTransport(lambda _: httpx.Response(200, headers={'content-type': 'text/event-stream'}, content=wire)),
               on_text=lambda _: None)
    assert exc.value.code == 'MODEL_RESPONSE_INVALID'


@pytest.mark.parametrize('values,code', [
    ({'api_key': ''}, 'MODEL_KEY_REQUIRED'), ({'api_key': 'bad\nkey'}, 'MODEL_CONFIG_INVALID'),
    ({'messages': [{'role': 'system', 'content': 'wrong'}]}, 'MODEL_MESSAGES_INVALID'),
    ({'messages': [{'role': [], 'content': 'wrong'}]}, 'MODEL_MESSAGES_INVALID'),
    ({'messages': []}, 'MODEL_MESSAGES_INVALID'), ({'cancel': object()}, 'MODEL_CANCEL_INVALID'),
    ({'timeout': float('nan')}, 'MODEL_CONFIG_INVALID'), ({'max_search_uses': 0}, 'MODEL_CONFIG_INVALID'),
])
def test_invalid_inputs_fail_before_network(values, code):
    with pytest.raises(ModelError) as exc:
        invoke(httpx.MockTransport(lambda _: pytest.fail('unexpected request')), **values)
    assert exc.value.code == code
