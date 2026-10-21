import asyncio
import time

import httpx

from refactor.learning.connections import providers
from refactor.learning.connections.errors import ModelError
from refactor.learning.connections.gateway import BACKGROUND_PURPOSES, FOREGROUND_PURPOSES, BACKGROUND_TIMEOUT
from tests.refactor.providers.samples import anthro, anthro_frames, sse
from tests.refactor.gateway.test_gateway import configured, MESSAGES


_REAL_COMPLETE = providers.complete


def test_background187_first_text_after_sixty_seconds_is_not_discarded(tmp_path, monkeypatch):
    """The full SSE result remains usable even when its first frame is late."""
    gateway, _ = configured(tmp_path)
    wire = sse(anthro_frames(anthro(provider='deepseek')))
    first_byte_at = []

    def handle(_request):
        async def chunks():
            await asyncio.sleep(60)
            first_byte_at.append(time.monotonic())
            async for item in wire.stream:
                yield item
        class Delayed(httpx.AsyncByteStream):
            async def __aiter__(self):
                async for item in chunks():
                    yield item
        return httpx.Response(200, headers={'content-type': 'text/event-stream'}, stream=Delayed())

    provider_calls = []
    def call(provider, **kwargs):
        provider_calls.append(dict(provider=provider, on_text=kwargs.get('on_text'),
                                   first_event_timeout=kwargs.get('first_event_timeout')))
        return _REAL_COMPLETE(provider, http_client=httpx.MockTransport(handle), **kwargs)
    monkeypatch.setattr(providers, 'complete', call)

    started = time.monotonic()
    result = gateway.complete('point_materials', MESSAGES)
    elapsed = time.monotonic() - started

    assert result.text == '答案'
    assert elapsed >= 60
    assert provider_calls[0]['on_text'] is not None
    assert provider_calls[0]['first_event_timeout'] is None
    assert gateway.usage_log.summary('2020-01-01T00:00:00Z', '2099-01-01T00:00:00Z')['total']['succeeded'] == 1
    assert result.usage.raw['_mentorflow']['first_event_ms'] >= 59000


def test_background187_network_connect_failure_retries_safely(tmp_path, monkeypatch):
    from refactor.learning.connections import gateway as gateway_module

    gateway, _ = configured(tmp_path)
    requests, retry_delays = [], []
    good = sse(anthro_frames(anthro(provider='deepseek')))

    def handle(request):
        requests.append(request)
        if len(requests) < 3:
            raise httpx.ConnectError('offline mock', request=request)
        return good

    def wait(self, seconds=None):
        retry_delays.append(seconds)
        return self.is_set()

    monkeypatch.setattr(gateway_module._Cancellation, 'wait', wait)
    monkeypatch.setattr(providers, 'complete',
                        lambda provider, **kwargs: _REAL_COMPLETE(
                            provider, http_client=httpx.MockTransport(handle), **kwargs))

    result = gateway.complete('point_materials', MESSAGES)

    assert result.text == '答案'
    assert len(requests) == 3
    assert retry_delays == [1., 3.]
    total = gateway.usage_log.summary('2020-01-01T00:00:00Z', '2099-01-01T00:00:00Z')['total']
    assert total['calls'] == total['succeeded'] == 1
    assert total['failed'] == 0


def test_background187_disconnect_after_response_is_reported_without_unsafe_replay(tmp_path, monkeypatch):
    gateway, _ = configured(tmp_path)
    requests = []

    def handle(request):
        requests.append(request)
        class Interrupted(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield anthro_frames(anthro(provider='deepseek'))[0]
                raise httpx.ReadError('mock disconnect')
        return httpx.Response(200, headers={'content-type': 'text/event-stream'}, stream=Interrupted())

    monkeypatch.setattr(providers, 'complete',
                        lambda provider, **kwargs: _REAL_COMPLETE(
                            provider, http_client=httpx.MockTransport(handle), **kwargs))
    try:
        gateway.complete('point_materials', MESSAGES)
    except ModelError as exc:
        assert exc.code == 'MODEL_CONNECTION_FAILED'
    else:
        raise AssertionError('a truncated provider stream must fail')

    # A request that received HTTP 200 may already be billable; retrying it can
    # duplicate generation, so only pre-response connect failures are replayed.
    assert len(requests) == 1


def test_background187_only_background_purposes_disable_first_event_timeout():
    assert 'point_materials' in BACKGROUND_PURPOSES
    assert {'plan_intent', 'sufficiency'} <= BACKGROUND_PURPOSES
    assert FOREGROUND_PURPOSES == frozenset({
        'teacher', 'free_chat', 'rag_qa',
        'asr_correction', 'connection_test',
    })
    assert 'teacher' not in BACKGROUND_PURPOSES


def test_background187_unknown_new_purpose_defaults_to_backend_stream_and_long_budget(tmp_path, monkeypatch):
    from tests.refactor.gateway.test_gateway import answer

    gateway, _ = configured(tmp_path)
    calls = []
    monkeypatch.setattr(providers, 'complete', lambda provider, **kwargs: (calls.append(kwargs), answer())[1])
    gateway.complete('future_backend_purpose_187', MESSAGES)
    assert calls[0]['first_event_timeout'] is None
    assert calls[0]['on_text'] is not None
    assert calls[0]['timeout'] <= BACKGROUND_TIMEOUT and calls[0]['timeout'] > BACKGROUND_TIMEOUT - 1


def test_background187_known_foreground_retains_short_first_event_policy(tmp_path, monkeypatch):
    from tests.refactor.gateway.test_gateway import answer

    gateway, _ = configured(tmp_path)
    calls = []
    monkeypatch.setattr(providers, 'complete', lambda provider, **kwargs: (calls.append(kwargs), answer())[1])
    gateway.complete('teacher', MESSAGES)
    assert calls[0]['first_event_timeout'] == gateway.first_event_timeout
    assert calls[0]['on_text'] is None


def test_background187_structured_plan_decisions_use_complete_response_policy(tmp_path, monkeypatch):
    from tests.refactor.gateway.test_gateway import answer

    gateway, _ = configured(tmp_path)
    calls = []
    monkeypatch.setattr(providers, 'complete', lambda provider, **kwargs: (calls.append(kwargs), answer())[1])
    for purpose in ('plan_intent', 'sufficiency'):
        gateway.complete(purpose, MESSAGES)
    assert len(calls) == 2
    for kwargs in calls:
        assert kwargs['first_event_timeout'] is None
        assert kwargs['on_text'] is not None
        assert kwargs['timeout'] <= BACKGROUND_TIMEOUT and kwargs['timeout'] > BACKGROUND_TIMEOUT - 1
