import copy
import json
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import httpx
import pytest

from refactor.learning.connections.providers import (
    REGISTRY, ModelError, SearchSupport, WebHit, complete, detect_provider,
)
from .samples import (KEY, MESSAGES, URL, anthro, call, chat, google, ordinary,
                      qwen, responses)


OFFICIAL = [k for k in REGISTRY if not k.startswith('relay_')]

@pytest.mark.parametrize('provider', OFFICIAL)
def test_ordinary_answers_and_usage(provider):
    result, requests = call(provider, [ordinary(provider)], system='固定系统前缀', temperature=.3)
    assert result.text == '答案'
    assert result.provider_id == provider
    assert result.model == REGISTRY[provider].default_model
    assert result.search_queries == result.search_hits == result.citations == ()
    assert result.usage.input_tokens == (150 if provider == 'anthropic' else 100)
    assert result.usage.cached_input_tokens == 40
    assert result.usage.uncached_input_tokens == (110 if provider == 'anthropic' else 60)
    assert result.usage.output_tokens == (25 if provider == 'gemini' else 20)
    assert result.usage.reasoning_tokens == (0 if provider in {'deepseek', 'anthropic'} else 5)
    assert result.usage.search_requests == 0
    assert len(requests) == 1
    assert requests[0].method == 'POST'
    assert not requests[0].url.query
    body = json.loads(requests[0].content)
    assert KEY not in json.dumps(body)
    if provider in {'deepseek', 'anthropic'}:
        assert requests[0].url.path.endswith('/v1/messages')
        assert requests[0].headers['x-api-key'] == KEY
        assert body['system'] == '固定系统前缀'
        assert body['messages'] == MESSAGES
    elif provider == 'gemini':
        assert requests[0].headers['x-goog-api-key'] == KEY
        assert body['systemInstruction']['parts'][0]['text'] == '固定系统前缀'
        assert body['generationConfig']['temperature'] == .3
    else:
        assert requests[0].headers['authorization'] == 'Bearer ' + KEY
        if provider == 'openai':
            assert requests[0].url.path == '/v1/responses'
            assert body['instructions'] == '固定系统前缀'
            assert body['input'] == MESSAGES
            assert body['store'] is False
        elif provider == 'qwen':
            assert requests[0].url.path == '/api/v1/services/aigc/text-generation/generation'
            assert body['input']['messages'][0] == {'role': 'system', 'content': '固定系统前缀'}
        else:
            assert body['messages'][0] == {'role': 'system', 'content': '固定系统前缀'}
            assert requests[0].url.path.endswith('/chat/completions')


@pytest.mark.parametrize('provider', ['deepseek', 'anthropic'])
def test_messages_native_search(provider):
    result, requests = call(provider, [anthro(True, provider=provider)], enable_search=True, max_search_uses=3)
    body = json.loads(requests[0].content)
    assert body['tools'] == [{'type': 'web_search_20250305', 'name': 'web_search', 'max_uses': 3}]
    assert result.text == '答案'
    assert result.search_queries == ('课程 官方',)
    assert result.search_hits == (WebHit(URL, '课程', ''),) * 2
    assert result.citations == (WebHit(URL, '课程', '原文'),)
    assert result.usage.search_requests == 1
    assert result.usage.raw['cache_read_input_tokens'] == 40


def test_real_deepseek_evidence_preserves_all_44_entries_and_truncation():
    raw = json.loads((Path(__file__).parent / 'fixtures/with_tool.json').read_text(encoding='utf-8'))
    assert raw['status'] == 200
    result, _ = call('deepseek', [raw['response']], enable_search=True)
    assert result.usage.search_requests == 8
    assert len(result.search_queries) == 8
    assert len(result.search_hits) == 44
    assert sum(bool(hit.url) for hit in result.search_hits) == 41
    assert result.usage.cached_input_tokens == 38656
    assert result.usage.input_tokens == 45629
    assert result.usage.uncached_input_tokens == 6973
    assert result.usage.output_tokens == 4618
    assert result.finish_reason == 'max_tokens'
    assert result.citations == ()
    assert result.text == ''.join(b['text'] for b in raw['response']['content'] if b['type'] == 'text')
    errors = result.usage.raw['_mentorflow']['search_errors']
    assert len(errors) == 3
    for item in errors:
        assert item['result']['error_code'] == 'max_uses_exceeded'
        assert result.search_hits[item['result_index']] == WebHit('', '', '')


def test_qwen_search_native_wire_sources_and_citation_markers():
    result, requests = call('qwen', [qwen(True)], enable_search=True, max_search_uses=2)
    params = json.loads(requests[0].content)['parameters']
    assert params['enable_search'] is True
    assert params['search_options'] == {'enable_source': True, 'enable_citation': True, 'citation_format': '[ref_<number>]'}
    assert 'max_search_uses' not in params
    assert result.text == '答案[ref_1]'
    assert result.search_queries == ()
    assert result.search_hits == (WebHit(URL, '课程', ''), WebHit(URL, '课程', ''), WebHit('', '', ''))
    assert result.citations == (result.search_hits[0],)
    assert result.usage.input_tokens == 100
    assert result.usage.cached_input_tokens == 40
    assert 'search_requests' in result.usage.raw['_mentorflow']['unreported_fields']


def test_kimi_opaque_builtin_roundtrip_keeps_original_arguments_and_reasoning():
    first, last = chat(provider='kimi', tool=True), chat(provider='kimi')
    last['usage']['prompt_tokens'] = 140
    last['usage']['prompt_tokens_details']['cached_tokens'] = 60
    original = copy.deepcopy(first)
    result, requests = call('kimi', [first, last], enable_search=True)
    assert first == original
    body = json.loads(requests[0].content)
    assert body['tools'] == [{'type': 'builtin_function', 'function': {'name': '$web_search'}}]
    second = json.loads(requests[1].content)
    assert second['messages'][-2] == first['choices'][0]['message']
    assert second['messages'][-1] == {'role': 'tool', 'tool_call_id': 'call_1', 'name': '$web_search',
                                      'content': first['choices'][0]['message']['tool_calls'][0]['function']['arguments']}
    assert result.text == '答案'
    assert result.usage.input_tokens == 240
    assert result.usage.cached_input_tokens == 100
    assert result.usage.uncached_input_tokens == 140
    assert result.usage.output_tokens == 40
    assert result.usage.reasoning_tokens == 10
    assert result.usage.search_requests == 1
    assert len(result.usage.raw['calls']) == 2
    assert result.search_queries == result.search_hits == result.citations == ()
    assert result.usage.raw['calls'][0]['_mentorflow']['unreported_search_fields'] == ['search_queries', 'search_hits', 'citations']


def test_zhipu_search_results_keep_duplicates_blank_rows_and_references():
    result, requests = call('zhipu', [chat(True)], enable_search=True)
    assert json.loads(requests[0].content)['tools'] == [{'type': 'web_search', 'web_search': {'search_result': True}}]
    assert result.text == '答案 [ref_1]'
    assert result.search_queries == ()
    assert result.search_hits == (WebHit(URL, '课程', '摘要'),) * 2 + (WebHit('', '', ''),)
    assert result.citations == (result.search_hits[0],)
    assert result.usage.cached_input_tokens == 40
    assert 'search_requests' in result.usage.raw['_mentorflow']['unreported_fields']


def test_openai_responses_all_sources_and_actual_queries():
    result, requests = call('openai', [responses(True)], enable_search=True, max_search_uses=3)
    body = json.loads(requests[0].content)
    assert body['tools'] == [{'type': 'web_search'}]
    assert body['include'] == ['web_search_call.action.sources']
    assert body['max_tool_calls'] == 3
    assert result.text == '答案'
    assert result.search_queries == ('课程 官方', '课程 入门')
    assert result.search_hits == (WebHit(URL, '', ''),) * 2 + (WebHit('', '', ''),)
    assert result.citations == (WebHit(URL, '课程', ''),)
    assert result.usage.search_requests == 1
    assert result.usage.reasoning_tokens == 5
    assert result.usage.cached_input_tokens == 40


def test_gemini_grounding_and_separate_reasoning_tokens():
    result, requests = call('gemini', [google(True)], enable_search=True, on_text=lambda _: None)
    body = json.loads(requests[0].content)
    assert body['tools'] == [{'googleSearch': {}}]
    assert body['contents'] == [{'role': 'user', 'parts': [{'text': '解释课程'}]}]
    assert result.text == '答案'
    assert result.search_queries == ('课程 官方', '课程 入门')
    assert result.search_hits == (WebHit(URL, '课程', ''),) * 2 + (WebHit('', '', ''),)
    assert result.citations == (WebHit(URL, '课程', '答案'),) * 2
    assert result.usage.output_tokens == 25
    assert result.usage.reasoning_tokens == 5
    assert result.usage.cached_input_tokens == 40
    assert result.usage.search_requests == 2
    assert result.usage.raw['_mentorflow']['grounding_metadata']['searchEntryPoint']['renderedContent']


@pytest.mark.parametrize('total,expected_input,reconciled', [(132, 107, True), (125, 100, True), (140, 107, False)])
def test_gemini_tool_prompt_accounting_reconciles_original_total(total, expected_input, reconciled):
    data = google(True)
    data['usageMetadata'].update(toolUsePromptTokenCount=7, totalTokenCount=total)
    result, _ = call('gemini', [data], enable_search=True)
    assert result.usage.input_tokens == expected_input
    assert result.usage.uncached_input_tokens == expected_input - 40
    assert result.usage.raw['toolUsePromptTokenCount'] == 7
    assert result.usage.raw['_mentorflow']['token_total_reconciled'] is reconciled


def test_deepseek_root_alias_preserves_explicit_origin_port():
    seen = []
    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=anthro(provider='deepseek'))
    complete('deepseek', base_url='https://api.deepseek.com:8443/v1', model='deepseek-v4-flash',
             api_key=KEY, messages=MESSAGES, http_client=httpx.MockTransport(handler))
    assert str(seen[0].url) == 'https://api.deepseek.com:8443/anthropic/v1/messages'


@pytest.mark.parametrize('provider', OFFICIAL)
def test_registry_detection_and_evidence_level(provider):
    spec = REGISTRY[provider]
    assert detect_provider(spec.default_base_url + '/') == provider
    assert spec.search.verified is (provider == 'deepseek')
    assert spec.search.checked_on == '2026-09-27'
    assert spec.search.doc_url.startswith('https://')
    assert isinstance(spec.search, SearchSupport)
    with pytest.raises(FrozenInstanceError):
        spec.id = 'changed'


@pytest.mark.parametrize('url', [
    'https://api.openai.com.evil.test/v1', 'https://api.openai.com@evil.test/v1',
    'http://api.openai.com/v1', 'https://evil.test?url=api.openai.com',
    'https://api.openai.com%2fevil.test', 'https://other.aliyuncs.com',
    'https://api.openai.com/v1#fragment', 'not a url',
])
def test_detect_does_not_guess_or_accept_spoofed_hosts(url):
    assert detect_provider(url) is None


def test_regional_and_workspace_hosts():
    assert detect_provider('https://api.moonshot.ai/v1') == 'kimi'
    assert detect_provider('https://workspace.cn-beijing.maas.aliyuncs.com/api/v1') == 'qwen'
    assert detect_provider('https://workspace.cn-beijing.maas.aliyuncs.com.evil.test') is None


def test_search_unsupported_before_http(monkeypatch):
    monkeypatch.setitem(REGISTRY, 'qwen', replace(REGISTRY['qwen'], search=None))
    with pytest.raises(ModelError) as exc:
        call('qwen', [], enable_search=True)
    assert exc.value.code == 'MODEL_SEARCH_UNSUPPORTED'


def test_caller_messages_are_not_mutated_and_no_random_prefix():
    before = copy.deepcopy(MESSAGES)
    _, requests = call('kimi', [chat(provider='kimi', tool=True), chat(provider='kimi')], enable_search=True, system='stable')
    assert MESSAGES == before
    first, second = [json.loads(req.content) for req in requests]
    assert first['messages'] == second['messages'][:2]


def test_missing_usage_does_not_succeed_with_zero_accounting():
    data = ordinary('openai')
    del data['usage']
    with pytest.raises(ModelError, match='模型') as exc:
        call('openai', [data])
    assert exc.value.code == 'MODEL_RESPONSE_INVALID'


@pytest.mark.parametrize('provider', ['kimi', 'zhipu', 'qwen', 'openai'])
def test_missing_cache_counter_is_explicitly_unknown(provider):
    data = ordinary(provider)
    raw = data['usage']
    raw.pop('prompt_tokens_details', None)
    raw.pop('input_tokens_details', None)
    result, _ = call(provider, [data])
    assert result.usage.cached_input_tokens == 0
    assert {'cached_input_tokens', 'uncached_input_tokens'} <= set(result.usage.raw['_mentorflow']['unreported_fields'])
