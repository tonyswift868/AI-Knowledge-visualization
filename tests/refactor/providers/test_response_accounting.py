import asyncio
import copy
import json
import threading
import traceback

import httpx
import pytest

from refactor.learning.connections.providers import ModelError
from .failure_samples import OFFICIAL, STREAMED, expected_tokens, set_text, truncated, wire_frames
from .samples import KEY, ByteStream, anthro, call, chat, frame, ordinary, sse
from .test_safety import invoke


@pytest.mark.parametrize('provider', OFFICIAL)
@pytest.mark.parametrize('mode', ['parse', 'empty', 'cancel', 'timeout'])
def test_received_json_meters_survive_answer_failure(provider, mode):
    data, cancel = ordinary(provider), threading.Event()
    if mode == 'parse':
        set_text(data, provider, {'bad': KEY})
    elif mode == 'empty':
        set_text(data, provider, '')
    data['usageMetadata' if provider == 'gemini' else 'usage']['vendor_message'] = KEY

    class FailingStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield json.dumps(data).encode()
            raise httpx.ReadTimeout(KEY)

    def handle(_):
        if mode == 'cancel':
            cancel.set()
        return (httpx.Response(200, stream=FailingStream()) if mode == 'timeout'
                else httpx.Response(200, json=data))

    with pytest.raises(ModelError) as exc:
        invoke(httpx.MockTransport(handle), provider=provider, cancel=cancel)
    error = exc.value
    assert error.code == {'cancel': 'MODEL_CANCELLED', 'timeout': 'MODEL_TIMEOUT'}.get(mode, 'MODEL_RESPONSE_INVALID')
    assert error.response_received
    total, output = expected_tokens(provider)
    assert (error.usage.input_tokens, error.usage.cached_input_tokens, error.usage.output_tokens) == (total, 40, output)
    assert not error.usage.raw['_mentorflow'].get('partial_usage')
    assert KEY not in str(error.usage.raw)
    assert KEY not in ''.join(traceback.format_exception(error))


@pytest.mark.parametrize('provider', OFFICIAL)
@pytest.mark.parametrize('text', ['', '半截😀{'])
def test_output_limit_is_explicit_even_without_visible_text(provider, text):
    with pytest.raises(ModelError) as exc:
        call(provider, [truncated(provider, text=text)])
    error = exc.value
    assert error.code == 'MODEL_OUTPUT_TRUNCATED'
    assert error.generated_text_length == len(text)  # characters, not UTF-8 bytes
    assert error.usage.output_tokens == expected_tokens(provider)[1]
    assert error.response_received
    assert not hasattr(error, 'text')


@pytest.mark.parametrize('provider', STREAMED)
@pytest.mark.parametrize('split', [False, True])
def test_streamed_output_limit_retains_final_cumulative_usage(provider, split):
    data, chunks = truncated(provider), []
    with pytest.raises(ModelError) as exc:
        call(provider, [sse(wire_frames(data, provider), split=split)], on_text=chunks.append)
    error = exc.value
    assert error.code == 'MODEL_OUTPUT_TRUNCATED'
    assert ''.join(chunks) == '半截😀{'
    assert error.generated_text_length == 4
    assert error.usage.input_tokens == expected_tokens(provider)[0]
    assert error.usage.output_tokens == 20
    assert not error.usage.raw['_mentorflow'].get('partial_usage')


@pytest.mark.parametrize('provider', STREAMED)
@pytest.mark.parametrize('failure', ['callback', 'cancel'])
def test_already_received_terminal_frame_is_not_lost_on_callback_failure(provider, failure):
    data, cancel = ordinary(provider), threading.Event()
    def callback(_):
        if failure == 'cancel':
            cancel.set()
        else:
            raise RuntimeError(KEY)
    # All complete frames are in the same network chunk, including final usage.
    with pytest.raises(ModelError) as exc:
        call(provider, [sse(wire_frames(data, provider))], on_text=callback, cancel=cancel)
    assert exc.value.code == ('MODEL_CANCELLED' if failure == 'cancel' else 'MODEL_RESPONSE_INVALID')
    assert exc.value.usage.output_tokens == 20
    assert not exc.value.usage.raw['_mentorflow'].get('partial_usage')
    assert KEY not in ''.join(traceback.format_exception(exc.value))


@pytest.mark.parametrize('provider', ['deepseek', 'anthropic', 'kimi'])
@pytest.mark.parametrize('end', ['over_limit', 'bad_answer', 'timeout', 'no_usage'])
def test_tool_continuation_failure_keeps_each_round_once(provider, end):
    first = chat(provider='kimi', tool=True) if provider == 'kimi' else anthro(True, provider=provider, reason='pause_turn')
    second = copy.deepcopy(first) if end == 'over_limit' else ordinary(provider)
    if end == 'bad_answer':
        set_text(second, provider, 12)
    elif end == 'no_usage':
        del second['usage']
    sent = []
    def handle(_):
        sent.append(True)
        if len(sent) == 1:
            return httpx.Response(200, json=first)
        if end == 'timeout':
            raise httpx.ReadTimeout(KEY)
        return httpx.Response(200, json=second)
    with pytest.raises(ModelError) as exc:
        invoke(httpx.MockTransport(handle), provider=provider, enable_search=True,
               max_search_uses=1 if provider == 'kimi' else 2)
    assert exc.value.code == ('MODEL_TIMEOUT' if end == 'timeout' else 'MODEL_RESPONSE_INVALID')
    assert len(sent) == 2
    value = exc.value.usage
    rounds = value.raw['_mentorflow']['call_usages']
    assert len(rounds) == 2
    assert rounds[0]['input_tokens'] == expected_tokens(provider)[0]
    assert value.cached_input_tokens == (40 if end in {'timeout', 'no_usage'} else 80)
    assert value.raw['_mentorflow'].get('partial_usage', False) == (end == 'timeout')


@pytest.mark.parametrize('provider', ['deepseek', 'anthropic'])
def test_search_over_limit_preserves_all_reported_searches(provider):
    data = anthro(True, provider=provider)
    data['usage']['server_tool_use']['web_search_requests'] = 10
    with pytest.raises(ModelError) as exc:
        call(provider, [data], enable_search=True, max_search_uses=8)
    assert exc.value.code == 'MODEL_RESPONSE_INVALID'
    assert exc.value.usage.search_requests == 10  # never clamp to requested max
    assert exc.value.usage.cached_input_tokens == 40


@pytest.mark.parametrize('mode', ['cancel', 'timeout'])
def test_interrupted_stream_preserves_only_observed_not_final_meters(mode):
    cancel = threading.Event()
    class Pending(httpx.AsyncByteStream):
        closed = False
        async def __aiter__(self):
            data = anthro(provider='deepseek')
            data['usage']['output_tokens'] = 1
            yield frame({'type': 'message_start', 'message': data})
            if mode == 'cancel':
                cancel.set()
            await asyncio.sleep(10)
        async def aclose(self):
            self.closed = True
    stream = Pending()
    with pytest.raises(ModelError) as exc:
        call('deepseek', [httpx.Response(200, headers={'content-type': 'text/event-stream'}, stream=stream)],
             on_text=lambda _: None, cancel=cancel, timeout=.1 if mode == 'timeout' else 2)
    assert stream.closed
    assert exc.value.code == ('MODEL_CANCELLED' if mode == 'cancel' else 'MODEL_TIMEOUT')
    assert exc.value.usage.input_tokens == 150
    assert exc.value.usage.output_tokens == 1
    assert exc.value.usage.raw['_mentorflow']['partial_usage']


@pytest.mark.parametrize('provider', OFFICIAL)
def test_response_without_usage_is_unknown_but_not_no_response(provider):
    data = ordinary(provider)
    del data['usageMetadata' if provider == 'gemini' else 'usage']
    with pytest.raises(ModelError) as exc:
        call(provider, [data])
    assert exc.value.response_received and exc.value.usage is not None
    missing = exc.value.usage.raw['_mentorflow']['unreported_fields']
    assert {'input_tokens', 'cached_input_tokens', 'uncached_input_tokens', 'output_tokens'} <= set(missing)


@pytest.mark.parametrize('status', [401, 402, 429, 307])
def test_http_error_is_a_received_response_even_when_vendor_has_no_meter(status):
    with pytest.raises(ModelError) as exc:
        call('deepseek', [httpx.Response(status, json={'error': {'message': KEY}})])
    assert exc.value.response_received
    assert exc.value.usage is not None
    assert 'input_tokens' in exc.value.usage.raw['_mentorflow']['unreported_fields']


def test_connect_failure_without_any_response_has_no_usage():
    def fail(_):
        raise httpx.ConnectError(KEY)
    with pytest.raises(ModelError) as exc:
        invoke(httpx.MockTransport(fail))
    assert exc.value.code == 'MODEL_CONNECTION_FAILED'
    assert not exc.value.response_received and exc.value.usage is None


def test_context_window_limit_and_malformed_tool_json_keep_truncation_semantics():
    data = anthro(provider='deepseek', reason='model_context_window_exceeded')
    with pytest.raises(ModelError) as exc:
        call('deepseek', [data])
    assert exc.value.code == 'MODEL_OUTPUT_TRUNCATED'
    frames = wire_frames(anthro(True, provider='deepseek', reason='max_tokens'), 'deepseek')
    # Valid SSE envelope, but incomplete tool-argument JSON on an output limit.
    frames.insert(-2, frame({'type': 'content_block_delta', 'index': 0,
                             'delta': {'type': 'input_json_delta', 'partial_json': '{'}}))
    with pytest.raises(ModelError) as exc:
        call('deepseek', [sse(frames)], enable_search=True, on_text=lambda _: None)
    assert exc.value.code == 'MODEL_OUTPUT_TRUNCATED'
    assert exc.value.usage.output_tokens == 20


@pytest.mark.parametrize('provider,wire_provider', [('relay_openai', 'kimi'), ('relay_anthropic', 'anthropic')])
def test_relay_output_limits_share_native_error_contract(provider, wire_provider):
    data = truncated(wire_provider)
    with pytest.raises(ModelError) as exc:
        invoke(httpx.MockTransport(lambda _: httpx.Response(200, json=data)), provider=provider,
               base_url='https://relay.example.test/v1', model='custom-model')
    assert exc.value.code == 'MODEL_OUTPUT_TRUNCATED'
    assert exc.value.generated_text_length == 4
    assert exc.value.usage.output_tokens == 20


def test_failed_response_event_and_vendor_stream_error_keep_reported_usage():
    from .samples import responses
    data = responses()
    data.update(status='failed', error={'code': 'unknown', 'message': KEY})
    with pytest.raises(ModelError) as exc:
        call('openai', [sse([frame({'type': 'response.failed', 'response': data})])], on_text=lambda _: None)
    assert exc.value.code == 'MODEL_HTTP_ERROR'
    assert exc.value.usage.input_tokens == 100
    assert not exc.value.usage.raw['_mentorflow'].get('partial_usage')
    data = {'type': 'error', 'error': {'type': 'overloaded_error', 'message': KEY},
            'usage': anthro()['usage']}
    with pytest.raises(ModelError) as exc:
        call('anthropic', [sse([frame(data)])], on_text=lambda _: None)
    assert exc.value.code == 'MODEL_HTTP_ERROR'
    assert exc.value.usage.input_tokens == 150
    assert exc.value.usage.raw['_mentorflow']['partial_usage']
    assert KEY not in str(exc.value.usage.raw)


def test_malformed_output_metadata_cannot_prevent_metering_or_limit_detection():
    data = truncated('openai')
    data['output'][-1]['content'][0]['type'] = {'invalid': KEY}
    data['output'].insert(0, {'type': 'web_search_call', 'action': {'type': {'invalid': KEY}}})
    with pytest.raises(ModelError) as exc:
        call('openai', [data])
    assert exc.value.code == 'MODEL_OUTPUT_TRUNCATED'
    assert exc.value.usage.input_tokens == 100
    assert KEY not in str(exc.value.usage.raw)


@pytest.mark.parametrize('provider', STREAMED)
def test_stream_protocol_mismatch_keeps_received_json_usage(provider):
    response = httpx.Response(200, headers={'content-type': 'application/json'},
                              stream=ByteStream([json.dumps(ordinary(provider)).encode()]))
    with pytest.raises(ModelError) as exc:
        call(provider, [response], on_text=lambda _: None)
    assert exc.value.code == 'MODEL_RESPONSE_INVALID'
    assert exc.value.usage.input_tokens == expected_tokens(provider)[0]
    assert exc.value.usage.output_tokens == 20
    assert not exc.value.usage.raw['_mentorflow'].get('partial_usage')


@pytest.mark.parametrize('provider', STREAMED)
def test_missing_sse_delimiter_does_not_erase_valid_terminal_usage_json(provider):
    frames = wire_frames(truncated(provider), provider)
    # Messages has a separate message_stop; Chat has [DONE]. Remove them first.
    if provider in {'deepseek', 'anthropic', 'kimi', 'zhipu'}:
        frames = frames[:-1]
    frames[-1] = frames[-1].rstrip(b'\r\n')
    with pytest.raises(ModelError) as exc:
        call(provider, [sse(frames, split=True)], on_text=lambda _: None)
    assert exc.value.code == 'MODEL_OUTPUT_TRUNCATED'
    assert exc.value.usage.input_tokens == expected_tokens(provider)[0]
    assert exc.value.usage.output_tokens == 20
    assert not exc.value.usage.raw['_mentorflow'].get('partial_usage')


def test_failed_terminal_snapshot_without_meters_keeps_previous_reported_usage():
    from .samples import responses
    data = responses()
    data['status'] = 'in_progress'
    frames = [frame({'type': 'response.in_progress', 'response': data}),
              frame({'type': 'response.failed', 'response': {'status': 'failed', 'usage': None}})]
    with pytest.raises(ModelError) as exc:
        call('openai', [sse(frames)], on_text=lambda _: None)
    assert exc.value.code == 'MODEL_HTTP_ERROR'
    assert exc.value.usage.input_tokens == 100
    assert exc.value.usage.cached_input_tokens == 40
    assert exc.value.usage.output_tokens == 20
    assert exc.value.usage.raw['_mentorflow']['partial_usage']
