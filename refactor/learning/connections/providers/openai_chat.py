"""Chat Completions for Kimi builtin_function and Zhipu server-side search.

Kimi's tool arguments are opaque server state: echo the exact string, do not
perform an external search or add its usage.total_tokens to prompt usage again.
"""
from __future__ import annotations

import re

from ._http import endpoint
from .base import ModelError, ModelResult, TextSink, combine, hit, invalid, string, usage


def parse(data, provider, model, search_enabled):
    choice = data['choices'][0]
    message = choice['message']
    text = message.get('content') or ''
    string(text)
    tools = message.get('tool_calls') or []
    raw = data['usage']
    detail = raw.get('prompt_tokens_details') or {}
    cached = detail.get('cached_tokens', raw.get('cached_tokens'))
    reasoning = (raw.get('completion_tokens_details') or {}).get('reasoning_tokens')
    hits, citations = [], []
    for source in data.get('web_search') or []:
        value = hit(source, url='link', snippet='content')
        hits.append(value)
        marker = source.get('refer')
        # Only match the provider's explicit reference marker, never a plain URL.
        if isinstance(marker, str) and marker and re.search(
                r'(?<![\w])' + re.escape(marker) + r'(?![\w])', text):
            citations.append(value)
    count = len(tools) if provider == 'kimi' else (None if search_enabled else 0)
    return ModelResult(text, usage(raw, raw['prompt_tokens'], cached, raw['completion_tokens'],
                       reasoning, count, unreported_search_fields=(
                           ['search_queries', 'search_hits', 'citations'] if provider == 'kimi'
                           and search_enabled else ['search_queries'] if search_enabled else [])),
                       provider, data.get('model', model), (), tuple(hits), tuple(citations),
                       string(choice['finish_reason']))


async def streamed(http, url, body, sink):
    message = {'role': 'assistant', 'content': ''}
    tool_calls, sources, raw_usage = {}, [], None
    reason, model, done = None, body['model'], False
    async for _, data in http.events(url, dict(body, stream=True)):
        if data is None:
            done = True
            continue
        model = data.get('model', model)
        if data.get('usage') is not None:
            raw_usage = data['usage']
        sources.extend(data.get('web_search') or [])
        for choice in data.get('choices') or []:
            if choice.get('index', 0) != 0:
                raise invalid()
            if choice.get('finish_reason'):
                reason = choice['finish_reason']
            delta = choice.get('delta') or {}
            if delta.get('content'):
                text = string(delta['content'])
                message['content'] += text
                sink.emit(text)
            if delta.get('reasoning_content'):
                message['reasoning_content'] = message.get('reasoning_content', '') + delta['reasoning_content']
            for fragment in delta.get('tool_calls') or []:
                index = fragment['index']
                call = tool_calls.setdefault(index, {'id': '', 'type': 'function',
                                                      'function': {'name': '', 'arguments': ''}})
                if fragment.get('id'):
                    call['id'] += fragment['id']
                if fragment.get('type'):
                    call['type'] = fragment['type']
                function = fragment.get('function') or {}
                for key in ('name', 'arguments'):
                    call['function'][key] += function.get(key, '')
    if not done or not reason or raw_usage is None:
        raise invalid()
    if tool_calls:
        message['tool_calls'] = [tool_calls[i] for i in sorted(tool_calls)]
    return {'model': model, 'usage': raw_usage, 'web_search': sources,
            'choices': [{'message': message, 'finish_reason': reason}]}


async def run(http, config):
    url = endpoint(config['base_url'], '/chat/completions')
    messages = list(config['messages'])
    if config['system'] is not None:
        messages.insert(0, {'role': 'system', 'content': config['system']})
    body = {'model': config['model'], 'messages': messages, 'max_tokens': config['max_tokens']}
    if config['temperature'] is not None:
        body['temperature'] = config['temperature']
    provider = config['provider_id']
    if config['enable_search']:
        body['tools'] = ([{'type': 'builtin_function', 'function': {'name': '$web_search'}}]
                         if provider == 'kimi' else
                         [{'type': 'web_search', 'web_search': {'search_result': True}}])
    if config['on_text'] and provider == 'kimi':
        body['stream_options'] = {'include_usage': True}
    results, used = [], 0
    for _ in range(config['max_search_uses'] + 1):
        sink = TextSink(config['on_text'])
        data = (await streamed(http, url, body, sink) if config['on_text']
                else await http.json(url, body))
        choice = data['choices'][0]
        calls = choice['message'].get('tool_calls') or []
        reason = choice.get('finish_reason')
        if calls or reason == 'tool_calls':
            if provider != 'kimi' or not config['enable_search'] or not calls or reason != 'tool_calls':
                raise ModelError('MODEL_RESPONSE_INVALID', '模型请求了未配置的客户端工具。')
            if used + len(calls) > config['max_search_uses']:
                raise ModelError('MODEL_RESPONSE_INVALID', '搜索次数已达上限，模型尚未完成回复。')
            ids = set()
            for call in calls:
                if (call.get('type') != 'function' or call['function']['name'] != '$web_search'
                        or not isinstance(call['function']['arguments'], str)
                        or not call.get('id') or call['id'] in ids):
                    raise invalid()
                ids.add(call['id'])
        elif reason not in {'stop', 'length'}:
            raise invalid()
        result = parse(data, provider, config['model'], config['enable_search'])
        sink.finish(result.text)
        results.append(result)
        if not calls:
            return combine(results)
        used += len(calls)
        # Retain reasoning_content and all original assistant fields for Kimi.
        body['messages'].append(choice['message'])
        for call in calls:
            body['messages'].append({'role': 'tool', 'tool_call_id': call['id'],
                                      'name': '$web_search', 'content': call['function']['arguments']})
    raise ModelError('MODEL_RESPONSE_INVALID', '模型工具往返次数超限。')
