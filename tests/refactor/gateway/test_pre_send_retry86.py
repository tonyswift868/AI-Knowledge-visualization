"""Real provider adapters with offline transports; no clock-speed assertions."""
import json
import threading
from types import SimpleNamespace

import httpx
import pytest

from refactor.learning.connections import providers
from refactor.learning.connections import gateway as gateway_module
from refactor.learning.connections.errors import ModelError
from tests.refactor.providers.failure_samples import OFFICIAL, wire_frames
from tests.refactor.providers.samples import ordinary, anthro
from .test_failed_provider_billing import gateway, ledger_row
from .test_gateway import MESSAGES, START, END, KEY

REAL_COMPLETE = providers.complete


@pytest.fixture
def delays(monkeypatch):
    waits = []
    def wait(token, seconds):
        waits.append(seconds)
        return token.is_set()
    monkeypatch.setattr(gateway_module._Cancellation, 'wait', wait)
    return waits


def wire(monkeypatch, handler):
    calls = []
    def complete(provider, **kwargs):
        calls.append(kwargs)
        return REAL_COMPLETE(provider, http_client=httpx.MockTransport(
            lambda request: handler(request, kwargs, len(calls))), **kwargs)
    monkeypatch.setattr(providers, 'complete', complete)
    return calls


@pytest.mark.parametrize('provider', OFFICIAL)
@pytest.mark.parametrize('fault', [httpx.ConnectError, httpx.ConnectTimeout])
def test_retry86_connect_recovers_all_protocols_one_usage_row(tmp_path, monkeypatch, delays, provider, fault):
    g = gateway(tmp_path, provider)
    def handle(request, kwargs, n):
        if n < 3:
            raise fault(KEY, request=request)
        return httpx.Response(200, json=ordinary(provider))
    calls = wire(monkeypatch, handle)
    result = g.complete('teacher', MESSAGES)
    assert result.text and len(calls) == 3 and delays == [1., 3.]
    total = g.usage_log.summary(START, END)['total']
    assert total['calls'] == total['succeeded'] == 1 and total['failed'] == 0
    assert total['input_tokens'] == result.usage.input_tokens
    assert not g.usage_log.path.with_name('model-failures.log').exists()
    rows = [json.loads(s) for s in g.usage_log.path.with_name('model-retries.log').read_text(encoding='utf-8').splitlines()]
    assert [r['retry'] for r in rows] == [1, 2]
    assert all(r['purpose'] == 'teacher' for r in rows) and KEY not in str(rows)


def test_retry86_exhaustion_preserves_public_error_and_proven_zero_usage(tmp_path, monkeypatch, delays):
    g = gateway(tmp_path)
    def fail(request, *_):
        raise httpx.ConnectError(KEY, request=request)
    calls = wire(monkeypatch, fail)
    with pytest.raises(ModelError) as caught:
        g.complete('minute_recalculation', MESSAGES)
    assert caught.value.code == 'MODEL_CONNECTION_FAILED'
    assert str(caught.value) == '无法连接模型服务，请检查地址和网络。'
    assert len(calls) == 3 and delays == [1., 3.]
    state, code, stored, _ = ledger_row(g)
    meta = json.loads(stored)['raw']['_mentorflow']
    assert state == 'failed' and code == caught.value.code
    assert meta['request_attempted'] is False and meta['unreported_fields'] == []
    assert g.usage_log.summary(START, END)['total']['input_tokens'] == 0
    assert len(g.usage_log.path.with_name('model-failures.log').read_text(encoding='utf-8').splitlines()) == 1


def test_retry86_probe_does_not_retry(tmp_path, monkeypatch, delays):
    g = gateway(tmp_path)
    def fail(*_):
        raise httpx.ConnectError(KEY)
    calls = wire(monkeypatch, fail)
    with pytest.raises(ModelError):
        g.test_connection()
    assert len(calls) == 1 and delays == []
    assert not g.usage_log.path.with_name('model-retries.log').exists()


@pytest.mark.parametrize('fault', [httpx.ReadError, httpx.ReadTimeout, httpx.WriteError, httpx.RemoteProtocolError])
def test_retry86_uncertain_send_is_not_replayed_or_priced_zero(tmp_path, monkeypatch, delays, fault):
    g = gateway(tmp_path)
    def fail(*_):
        raise fault(KEY)
    calls = wire(monkeypatch, fail)
    with pytest.raises(ModelError):
        g.complete('plan_intent', MESSAGES)
    assert len(calls) == 1 and delays == []
    assert json.loads(ledger_row(g)[2])['raw']['_mentorflow']['request_attempted'] is True
    assert g.usage_log.summary(START, END)['total']['input_tokens'] is None


def test_retry86_cancel_during_backoff_stops_without_another_send(tmp_path, monkeypatch):
    g, cancel = gateway(tmp_path), threading.Event()
    def wait(token, seconds):
        cancel.set()
        return token.is_set()
    monkeypatch.setattr(gateway_module._Cancellation, 'wait', wait)
    def fail(*_):
        raise httpx.ConnectError(KEY)
    calls = wire(monkeypatch, fail)
    with pytest.raises(ModelError) as caught:
        g.complete('teacher', MESSAGES, cancel=cancel)
    assert caught.value.code == 'MODEL_CANCELLED' and len(calls) == 1
    assert json.loads(ledger_row(g)[2])['raw']['_mentorflow']['request_attempted'] is False


def test_retry86_cancel_before_call_sends_nothing(tmp_path, monkeypatch, delays):
    g, cancel = gateway(tmp_path), threading.Event()
    cancel.set()
    calls = wire(monkeypatch, lambda *_: pytest.fail('sent after cancellation'))
    with pytest.raises(ModelError) as caught:
        g.complete('teacher', MESSAGES, cancel=cancel)
    assert caught.value.code == 'MODEL_CANCELLED' and calls == delays == []


@pytest.mark.parametrize('moment', ['after_key', 'end_of_wait'])
def test_retry86_cancel_race_before_send_is_still_proven_zero(tmp_path, monkeypatch, moment):
    g, cancel = gateway(tmp_path), threading.Event()
    if moment == 'after_key':
        def secret(config):
            cancel.set()
            return KEY
        monkeypatch.setattr(g, '_secret', secret)
    else:
        def wait(token, seconds):
            cancel.set()
            return False  # Cancellation races completion of the delay.
        monkeypatch.setattr(gateway_module._Cancellation, 'wait', wait)
    def fail(*_):
        raise httpx.ConnectError(KEY)
    calls = wire(monkeypatch, fail)
    with pytest.raises(ModelError) as caught:
        g.complete('teacher', MESSAGES, cancel=cancel)
    assert caught.value.code == 'MODEL_CANCELLED'
    assert len(calls) == (0 if moment == 'after_key' else 1)
    meta = json.loads(ledger_row(g)[2])['raw']['_mentorflow']
    assert meta['request_attempted'] is False and meta['unreported_fields'] == []


def test_retry86_streamed_text_then_disconnect_is_not_replayed(tmp_path, monkeypatch, delays):
    g, shown = gateway(tmp_path), []
    class Broken(httpx.AsyncByteStream):
        async def __aiter__(self):
            # The adapter emits visible text before losing the stream.
            for frame in wire_frames(anthro(provider='deepseek'), 'deepseek')[:-2]:
                yield frame
            raise httpx.ReadError(KEY)
    calls = wire(monkeypatch, lambda *_: httpx.Response(200, headers={'content-type': 'text/event-stream'}, stream=Broken()))
    with pytest.raises(ModelError) as caught:
        g.complete('teacher', MESSAGES, on_text=shown.append)
    assert shown and caught.value.response_received and len(calls) == 1 and delays == []


def test_retry86_prior_tool_receipt_blocks_replay_on_next_connect_failure(tmp_path, monkeypatch, delays):
    g, sends = gateway(tmp_path), []
    def handle(*_):
        sends.append(True)
        if len(sends) == 1:
            return httpx.Response(200, json=anthro(True, provider='deepseek', reason='pause_turn'))
        raise httpx.ConnectError(KEY)
    calls = wire(monkeypatch, handle)
    with pytest.raises(ModelError) as caught:
        g.complete('search', MESSAGES, enable_search=True)
    assert caught.value.usage is not None and caught.value.response_received
    assert len(sends) == 2 and len(calls) == 1 and delays == []


def test_retry86_callback_guard_even_when_bad_adapter_claims_safe(tmp_path, monkeypatch, delays):
    g, calls = gateway(tmp_path), []
    def fail(provider, **kwargs):
        calls.append(True)
        kwargs['on_text']('')
        error = ModelError('MODEL_CONNECTION_FAILED', 'offline')
        error.safe_to_retry = True
        raise error
    monkeypatch.setattr(providers, 'complete', fail)
    with pytest.raises(ModelError):
        g.complete('teacher', MESSAGES, on_text=lambda _: None)
    assert len(calls) == 1 and delays == []


def test_retry86_backoff_consumes_existing_total_deadline(tmp_path, monkeypatch):
    g, clock, waits = gateway(tmp_path), [10.], []
    monkeypatch.setattr(gateway_module, 'time', SimpleNamespace(monotonic=lambda: clock[0]))
    def wait(token, seconds):
        waits.append(seconds)
        clock[0] += seconds
        return False
    monkeypatch.setattr(gateway_module._Cancellation, 'wait', wait)
    def handle(request, kwargs, n):
        clock[0] += 2
        if n == 1:
            raise httpx.ConnectError(KEY)
        return httpx.Response(200, json=ordinary('deepseek'))
    calls = wire(monkeypatch, handle)
    g.complete('teacher', MESSAGES)
    assert waits == [1.] and calls[1]['timeout'] == calls[0]['timeout'] - 3
