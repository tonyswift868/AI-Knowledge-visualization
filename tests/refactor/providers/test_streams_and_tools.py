import copy
import json

import pytest

from refactor.learning.connections.providers import ModelError
from .samples import (anthro, anthro_frames, call, chat, chat_frames, frame,
                      qwen, responses, sse)


@pytest.mark.parametrize('provider', ['deepseek', 'anthropic'])
@pytest.mark.parametrize('search', [False, True])
def test_messages_sse_matches_final_text_usage_and_tools(provider, search):
    data = anthro(search, provider=provider)
    chunks = []
    result, requests = call(provider, [sse(anthro_frames(data), split=True)], on_text=chunks.append, enable_search=search)
    assert chunks == ['答', '案']
    assert ''.join(chunks) == result.text
    assert result.usage.output_tokens == 20  # cumulative message_delta, not 1 + 20
    assert result.usage.search_requests == int(search)
    assert len(result.search_hits) == (2 if search else 0)
    assert len(result.citations) == int(search)
    assert result.search_queries == (('课程 官方',) if search else ())
    assert json.loads(requests[0].content)['stream'] is True


@pytest.mark.parametrize('provider', ['kimi', 'zhipu'])
def test_chat_stream_with_usage_after_finish(provider):
    chunks = []
    data = chat(provider=provider, search=provider == 'zhipu')
    result, requests = call(provider, [sse(chat_frames(data), split=True)], on_text=chunks.append, enable_search=True)
    assert ''.join(chunks) == result.text == data['choices'][0]['message']['content']
    assert result.usage.cached_input_tokens == 40
    assert result.usage.output_tokens == 20
    if provider == 'kimi':
        assert json.loads(requests[0].content)['stream_options'] == {'include_usage': True}
    else:
        assert len(result.search_hits) == 3
        assert len(result.citations) == 1


def test_kimi_stream_fragmented_tool_arguments_round_trip():
    first, last = chat(provider='kimi', tool=True), chat(provider='kimi')
    first['choices'][0]['message']['content'] = '先搜索。'
    chunks = []
    result, requests = call('kimi', [sse(chat_frames(first)), sse(chat_frames(last))],
                            on_text=chunks.append, enable_search=True)
    assert ''.join(chunks) == result.text == '先搜索。答案'
    body = json.loads(requests[1].content)
    assert body['messages'][-2]['reasoning_content'] == 'private reasoning'
    assert body['messages'][-1]['content'] == first['choices'][0]['message']['tool_calls'][0]['function']['arguments']
    assert result.usage.search_requests == 1
    assert result.usage.input_tokens == 200


def test_openai_responses_stream_grounding_and_completion_snapshot():
    data = responses(True)
    frames = [frame({'type': 'response.output_text.delta', 'delta': char}) for char in '答案']
    frames.append(frame({'type': 'response.completed', 'response': data}))
    chunks = []
    result, _ = call('openai', [sse(frames, split=True)], on_text=chunks.append, enable_search=True)
    assert chunks == ['答', '案']
    assert result.text == ''.join(chunks)
    assert result.usage.search_requests == 1
    assert len(result.search_hits) == 3
    assert result.search_queries == ('课程 官方', '课程 入门')


def test_dashscope_stream_incremental_output_and_cumulative_usage():
    data = qwen(True)
    frames = []
    for char in '答案[ref_1]':
        part = qwen()
        part['output']['choices'][0]['message']['content'] = char
        part['output']['choices'][0]['finish_reason'] = 'null'
        frames.append(frame(part))
    data['output']['choices'][0]['message']['content'] = ''
    frames.append(frame(data))
    chunks = []
    result, requests = call('qwen', [sse(frames, split=True)], enable_search=True, on_text=chunks.append)
    assert ''.join(chunks) == result.text == '答案[ref_1]'
    assert result.usage.input_tokens == 100
    assert result.usage.output_tokens == 20
    assert len(result.search_hits) == 3
    assert len(result.citations) == 1
    assert requests[0].headers['X-DashScope-SSE'] == 'enable'
    assert json.loads(requests[0].content)['parameters']['incremental_output'] is True


@pytest.mark.parametrize('stream', [False, True])
def test_messages_pause_turn_continuation_preserves_tool_content(stream):
    first, last = anthro(True, reason='pause_turn'), anthro()
    first['content'].insert(0, {'type': 'thinking', 'thinking': 'private', 'signature': 'signed'})
    chunks = []
    inputs = [sse(anthro_frames(item)) for item in [first, last]] if stream else [first, last]
    result, requests = call('anthropic', inputs, enable_search=True, max_search_uses=3,
                            on_text=chunks.append if stream else None)
    second = json.loads(requests[1].content)
    assert second['messages'][-1] == {'role': 'assistant', 'content': first['content']}
    assert second['tools'][0]['max_uses'] == 2
    assert result.text == '答案答案'
    assert result.finish_reason == 'end_turn'
    assert result.usage.input_tokens == 300
    assert result.usage.cached_input_tokens == 80
    assert result.usage.search_requests == 1
    if stream:
        assert ''.join(chunks) == result.text


def test_kimi_stops_at_tool_budget_without_extra_request():
    with pytest.raises(ModelError) as exc:
        call('kimi', [chat(provider='kimi', tool=True)] * 2, enable_search=True, max_search_uses=1)
    assert exc.value.code == 'MODEL_RESPONSE_INVALID'
    assert '上限' in str(exc.value)


@pytest.mark.parametrize('mutation', ['unknown_tool', 'duplicate_id', 'tool_without_search', 'wrong_finish'])
def test_unapproved_client_tools_never_execute(mutation):
    data = chat(provider='kimi', tool=True)
    message = data['choices'][0]['message']
    if mutation == 'unknown_tool':
        message['tool_calls'][0]['function']['name'] = 'shell'
    elif mutation == 'duplicate_id':
        message['tool_calls'].append(copy.deepcopy(message['tool_calls'][0]))
    elif mutation == 'wrong_finish':
        data['choices'][0]['finish_reason'] = 'stop'
    with pytest.raises(ModelError) as exc:
        call('kimi', [data], enable_search=mutation != 'tool_without_search')
    assert exc.value.code == 'MODEL_RESPONSE_INVALID'


@pytest.mark.parametrize('provider,wire', [
    ('anthropic', sse(anthro_frames(anthro())[:-1])),
    ('kimi', sse(chat_frames(chat(provider='kimi'))[:-1])),
    ('openai', sse([frame({'type': 'response.output_text.delta', 'delta': '答案'})])),
])
def test_truncated_stream_is_not_a_success(provider, wire):
    chunks = []
    with pytest.raises(ModelError) as exc:
        call(provider, [wire], on_text=chunks.append)
    assert exc.value.code == 'MODEL_RESPONSE_INVALID'


def test_stream_final_text_disagreement_is_reported():
    frames = [frame({'type': 'response.output_text.delta', 'delta': '错误'}),
              frame({'type': 'response.completed', 'response': responses()})]
    with pytest.raises(ModelError) as exc:
        call('openai', [sse(frames)], on_text=lambda _: None)
    assert exc.value.code == 'MODEL_RESPONSE_INVALID'


def test_messages_tool_error_is_preserved_not_counted_as_successful_url():
    data = anthro(True)
    data['content'][1]['content'] = {'type': 'web_search_tool_result_error', 'error_code': 'unavailable'}
    result, _ = call('anthropic', [data], enable_search=True)
    assert len(result.search_hits) == 1
    assert result.search_hits[0].url == ''
    assert result.usage.raw['_mentorflow']['search_errors'][0]['result']['error_code'] == 'unavailable'


def test_responses_incomplete_reason_is_not_completed():
    data = responses()
    data.update(status='incomplete', incomplete_details={'reason': 'max_output_tokens'})
    with pytest.raises(ModelError) as exc:
        call('openai', [data])
    assert exc.value.code == 'MODEL_OUTPUT_TRUNCATED'
    assert exc.value.finish_reason == 'max_output_tokens'
    assert exc.value.generated_text_length == 2
    assert exc.value.usage.output_tokens == 20
