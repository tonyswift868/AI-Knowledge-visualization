"""Synthetic wire responses from official schemas, checked 2026-09-27.

Sources are the corresponding REGISTRY doc_url plus:
https://platform.claude.com/docs/en/build-with-claude/streaming
https://platform.kimi.com/docs/api/chat
https://help.aliyun.com/zh/model-studio/qwen-api-via-dashscope
https://ai.google.dev/api/generate-content
Only fixtures/with_tool.json is real evidence (user-supplied DeepSeek probe).
No fabricated query/source fields for protocols which don't document them.
"""
import copy
import json

import httpx

from refactor.learning.connections.providers import REGISTRY, complete


KEY = 'unit-test-secret-never-display'
URL = 'https://materials.example.test/lesson'
MESSAGES = [{'role': 'user', 'content': '解释课程'}]


def call(provider, responses, *, check=None, **kwargs):
    responses = iter(response if isinstance(response, httpx.Response) else copy.deepcopy(response)
                     for response in responses)
    requests = []

    def handle(request):
        requests.append(request)
        if check:
            check(request, len(requests))
        response = next(responses)
        return response if isinstance(response, httpx.Response) else httpx.Response(200, json=response)

    spec = REGISTRY[provider]
    result = complete(provider, base_url=spec.default_base_url, model=spec.default_model,
                      api_key=KEY, messages=MESSAGES, http_client=httpx.MockTransport(handle), **kwargs)
    return result, requests


def anthro(search=False, *, provider='anthropic', reason='end_turn'):
    content = []
    if search:
        content.extend([
            {'type': 'server_tool_use', 'id': 'srv_1', 'name': 'web_search', 'input': {'query': '课程 官方'}},
            {'type': 'web_search_tool_result', 'tool_use_id': 'srv_1', 'content': [
                {'type': 'web_search_result', 'url': URL, 'title': '课程', 'encrypted_content': 'opaque'},
                {'type': 'web_search_result', 'url': URL, 'title': '课程', 'encrypted_content': 'opaque'},
            ]},
        ])
    content.append({'type': 'text', 'text': '答案', 'citations': [
        {'type': 'web_search_result_location', 'url': URL, 'title': '课程', 'cited_text': '原文'}
    ] if search else []})
    return {'id': 'msg_fixture', 'type': 'message', 'role': 'assistant', 'model': REGISTRY[provider].default_model,
            'content': content, 'stop_reason': reason,
            'usage': {'input_tokens': 100, 'cache_read_input_tokens': 40,
                      'cache_creation_input_tokens': 10, 'output_tokens': 20,
                      'server_tool_use': {'web_search_requests': int(search)}}}


def chat(search=False, *, provider='zhipu', tool=False):
    content = '答案 [ref_1]' if search and provider == 'zhipu' else '答案'
    message = {'role': 'assistant', 'content': content}
    if tool:
        message = {'role': 'assistant', 'content': None, 'reasoning_content': 'private reasoning',
                   'tool_calls': [{'id': 'call_1', 'type': 'function', 'function': {
                       'name': '$web_search',
                       'arguments': '{ "opaque_search_state" : "fixture", "usage": {"total_tokens":7} }'}}]}
    response = {'model': REGISTRY[provider].default_model,
                'choices': [{'index': 0, 'message': message, 'finish_reason': 'tool_calls' if tool else 'stop'}],
                'usage': {'prompt_tokens': 100, 'completion_tokens': 20,
                          'prompt_tokens_details': {'cached_tokens': 40},
                          'completion_tokens_details': {'reasoning_tokens': 5}}}
    if search and provider == 'zhipu':
        response['web_search'] = [
            {'title': '课程', 'link': URL, 'content': '摘要', 'refer': 'ref_1'},
            {'title': '课程', 'link': URL, 'content': '摘要', 'refer': 'ref_2'},
            {'title': '', 'link': '', 'content': '', 'refer': 'ref_3'},
        ]
    return response


def responses(search=False):
    output = []
    if search:
        output.append({'type': 'web_search_call', 'id': 'ws_1', 'status': 'completed', 'action': {
            'type': 'search', 'queries': ['课程 官方', '课程 入门'], 'sources': [
                {'type': 'url', 'url': URL}, {'type': 'url', 'url': URL}, {'type': 'url', 'url': ''}]}})
    output.append({'type': 'message', 'role': 'assistant', 'status': 'completed', 'content': [{
        'type': 'output_text', 'text': '答案', 'annotations': [
            {'type': 'url_citation', 'url': URL, 'title': '课程', 'start_index': 0, 'end_index': 2}
        ] if search else []}]})
    return {'model': REGISTRY['openai'].default_model, 'status': 'completed', 'output': output,
            'usage': {'input_tokens': 100, 'input_tokens_details': {'cached_tokens': 40},
                      'output_tokens': 20, 'output_tokens_details': {'reasoning_tokens': 5}}}


def qwen(search=False):
    output = {'choices': [{'message': {'role': 'assistant', 'content': '答案[ref_1]' if search else '答案'},
                            'finish_reason': 'stop'}]}
    if search:
        output['search_info'] = {'search_results': [
            {'index': 1, 'title': '课程', 'url': URL}, {'index': 2, 'title': '课程', 'url': URL},
            {'index': 3, 'title': '', 'url': ''}]}
    return {'output': output, 'usage': {'input_tokens': 100, 'output_tokens': 20,
            'prompt_tokens_details': {'cached_tokens': 40}, 'output_tokens_details': {'reasoning_tokens': 5}}}


def google(search=False):
    candidate = {'content': {'parts': [{'text': 'hidden reasoning', 'thought': True}, {'text': '答案'}], 'role': 'model'},
                 'finishReason': 'STOP'}
    if search:
        candidate['groundingMetadata'] = {
            'webSearchQueries': ['课程 官方', '课程 入门'],
            'groundingChunks': [{'web': {'uri': URL, 'title': '课程'}},
                                {'web': {'uri': URL, 'title': '课程'}}, {'web': {'uri': '', 'title': ''}}],
            'groundingSupports': [{'segment': {'startIndex': 0, 'endIndex': 2, 'text': '答案'}, 'groundingChunkIndices': [0, 1]}],
            'searchEntryPoint': {'renderedContent': '<div>provider attribution</div>'}}
    return {'modelVersion': REGISTRY['gemini'].default_model, 'candidates': [candidate],
            'usageMetadata': {'promptTokenCount': 100, 'cachedContentTokenCount': 40,
                              'candidatesTokenCount': 20, 'thoughtsTokenCount': 5, 'totalTokenCount': 125}}


def ordinary(provider):
    if provider in {'deepseek', 'anthropic'}:
        return anthro(provider=provider)
    return {'kimi': lambda: chat(provider='kimi'), 'zhipu': chat, 'openai': responses,
            'qwen': qwen, 'gemini': google}[provider]()


def frame(data, event=None):
    return ((f'event: {event}\r\n' if event else '') + 'data: ' +
            ('[DONE]' if data is None else json.dumps(data, ensure_ascii=False)) + '\r\n\r\n').encode()


class ByteStream(httpx.AsyncByteStream):
    def __init__(self, chunks):
        self.chunks, self.closed = chunks, False

    async def __aiter__(self):
        for chunk in self.chunks:
            yield chunk

    async def aclose(self):
        self.closed = True


def sse(frames, *, split=False):
    wire = b''.join(frames)
    # Exercise fragmented UTF-8 and SSE line/frame boundaries.
    chunks = [wire[i:i + 7] for i in range(0, len(wire), 7)] if split else [wire]
    return httpx.Response(200, headers={'content-type': 'text/event-stream; charset=utf-8'}, stream=ByteStream(chunks))


def anthro_frames(data):
    start = dict(data, content=[], stop_reason=None, usage=dict(data['usage'], output_tokens=1))
    frames = [frame({'type': 'message_start', 'message': start}, 'message_start')]
    for index, block in enumerate(data['content']):
        block = copy.deepcopy(block)
        if block['type'] == 'text':
            text, citations = block.pop('text'), block.pop('citations', [])
            frames.append(frame({'type': 'content_block_start', 'index': index,
                                 'content_block': dict(block, text='')}))
            for char in text:
                frames.append(frame({'type': 'content_block_delta', 'index': index,
                                     'delta': {'type': 'text_delta', 'text': char}}))
            for citation in citations:
                frames.append(frame({'type': 'content_block_delta', 'index': index,
                                     'delta': {'type': 'citations_delta', 'citation': citation}}))
        elif block['type'] == 'server_tool_use':
            arguments = json.dumps(block['input'], ensure_ascii=False)
            frames.append(frame({'type': 'content_block_start', 'index': index,
                                 'content_block': dict(block, input={})}))
            for part in (arguments[:5], arguments[5:]):
                frames.append(frame({'type': 'content_block_delta', 'index': index,
                                     'delta': {'type': 'input_json_delta', 'partial_json': part}}))
        else:
            frames.append(frame({'type': 'content_block_start', 'index': index, 'content_block': block}))
        frames.append(frame({'type': 'content_block_stop', 'index': index}))
    frames.extend([frame({'type': 'message_delta', 'delta': {'stop_reason': data['stop_reason']}, 'usage': data['usage']}),
                   frame({'type': 'message_stop'})])
    return frames


def chat_frames(data):
    message = data['choices'][0]['message']
    frames = []
    def delta(value, reason=None):
        return frame({'model': data['model'], 'choices': [{'index': 0, 'delta': value, 'finish_reason': reason}]})
    if message.get('reasoning_content'):
        frames.append(delta({'reasoning_content': message['reasoning_content']}))
    for text in message.get('content') or '':
        frames.append(delta({'content': text}))
    for index, tool in enumerate(message.get('tool_calls') or []):
        arguments = tool['function']['arguments']
        frames.append(delta({'tool_calls': [dict(tool, index=index, function={'name': tool['function']['name'], 'arguments': arguments[:9]})]}))
        frames.append(delta({'tool_calls': [{'index': index, 'function': {'arguments': arguments[9:]}}]}))
    frames.append(delta({}, data['choices'][0]['finish_reason']))
    frames.append(frame({'choices': [], 'usage': data['usage'], 'web_search': data.get('web_search', [])}))
    frames.append(frame(None))
    return frames
