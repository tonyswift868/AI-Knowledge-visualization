"""Native Messages + server web search, including streamed pause_turn resumes."""
from __future__ import annotations

import json
from urllib.parse import urlsplit

from ._http import endpoint
from ._accounting import read_usage
from .base import ModelError, ModelResult, TextSink, combine, hit, invalid, string


def parse(data, provider, requested_model, search_enabled):
    queries, hits, citations, texts = [], [], [], []
    search_errors = []
    for block in data['content']:
        kind = block['type']
        if kind == 'text':
            texts.append(string(block['text']))
            for citation in block.get('citations') or []:
                if citation.get('type') == 'web_search_result_location':
                    citations.append(hit(citation, snippet='cited_text'))
        elif kind == 'server_tool_use' and block.get('name') == 'web_search':
            queries.append(string(block['input']['query']))
        elif kind == 'web_search_tool_result':
            content = block['content']
            if isinstance(content, dict):
                content = [content]
            for result in content:
                if result.get('type') == 'web_search_tool_result_error':
                    # The real DeepSeek fixture includes three error entries in
                    # its 44 raw results. Keep their positions as empty WebHits;
                    # preserve error details, never advertise 44 successful URLs.
                    search_errors.append({'result_index': len(hits), 'result': dict(result)})
                elif result.get('type') != 'web_search_result':
                    raise invalid()
                hits.append(hit(result))
    return ModelResult(''.join(texts), read_usage('anthropic_messages', provider, data, search_enabled,
                       search_errors=search_errors),
                       provider, data.get('model', requested_model), tuple(queries), tuple(hits),
                       tuple(citations), string(data['stop_reason']))


async def streamed(http, url, body, sink):
    message, blocks, partials = None, {}, {}
    ended = False
    async for event, data in http.events(url, dict(body, stream=True)):
        if data is None:
            continue
        kind = data.get('type', event)
        if kind == 'message_start':
            if message is not None:
                raise invalid()
            message = data['message']
        elif kind == 'content_block_start':
            index = data['index']
            if index in blocks or message is None:
                raise invalid()
            blocks[index] = data['content_block']
            if blocks[index]['type'] == 'text':
                sink.emit(blocks[index].get('text', ''))
        elif kind == 'content_block_delta':
            block = blocks[data['index']]
            delta = data['delta']
            if delta['type'] == 'text_delta':
                text = string(delta['text'])
                block['text'] = block.get('text', '') + text
                sink.emit(text)
            elif delta['type'] == 'input_json_delta':
                index = data['index']
                partials[index] = partials.get(index, '') + delta['partial_json']
            elif delta['type'] == 'citations_delta':
                block.setdefault('citations', []).append(delta['citation'])
            elif delta['type'] in {'thinking_delta', 'signature_delta'}:
                key = 'thinking' if delta['type'] == 'thinking_delta' else 'signature'
                block[key] = block.get(key, '') + delta[key]
        elif kind == 'message_delta':
            if message is None:
                raise invalid()
            message.update(data['delta'])
            # Usage fields are cumulative, not deltas, within one response.
            message.setdefault('usage', {}).update(data.get('usage', {}))
        elif kind == 'message_stop':
            ended = True
    if not ended or message is None or not message.get('stop_reason'):
        if getattr(http, 'evidence', None) is not None:
            http.evidence.stage = 'incomplete_anthropic_message'
        raise invalid()
    for index, value in partials.items():
        blocks[index]['input'] = json.loads(value)
    message['content'] = [blocks[index] for index in sorted(blocks)]
    return message


async def run(http, config):
    base = config['base_url']
    # The official DeepSeek root is chat-compatible; Messages lives under anthropic.
    if config['provider_id'] == 'deepseek' and urlsplit(base).hostname == 'api.deepseek.com':
        if urlsplit(base).path in {'', '/', '/v1'}:
            base = base.removesuffix('/v1').rstrip('/') + '/anthropic'
    url = endpoint(base, '/messages', version='v1')
    body = {'model': config['model'], 'messages': list(config['messages']),
            'max_tokens': config['max_tokens']}
    if config['system'] is not None:
        body['system'] = config['system']
    if config['temperature'] is not None:
        body['temperature'] = config['temperature']
    effort = config.get('reasoning_effort')
    if config['provider_id'] == 'deepseek' and effort is not None:
        # Anthropic compatibility docs + 2026-10-01 five-body probe:
        # reasoning.effort=none was ignored; thinking.type=disabled produced
        # no thinking block. Do not infer effectiveness from intent alone.
        # https://api-docs.deepseek.com/guides/anthropic_api/
        body['thinking'] = {'type': 'disabled' if effort == 'none' else 'enabled'}
        if effort != 'none':
            body['output_config'] = {'effort': effort}
    elif config['provider_id'] == 'anthropic' and effort in {'low', 'high', 'max'}:
        # https://platform.claude.com/docs/en/build-with-claude/effort
        model = config['model']
        supported = model.startswith(('claude-fable-5', 'claude-mythos-5',
            'claude-mythos-preview', 'claude-opus-5', 'claude-sonnet-5',
            'claude-opus-4-5', 'claude-opus-4-6', 'claude-opus-4-7',
            'claude-opus-4-8', 'claude-sonnet-4-6'))
        if supported and (effort != 'max' or not model.startswith('claude-opus-4-5')):
            body['output_config'] = {'effort': effort}
    # Claude's current models do not all permit thinking.type=disabled;
    # leaving unsupported "none" at the model default avoids a 400.
    results, used = [], 0
    # A bounded continuation handles server pause_turn, not arbitrary client tools.
    for _ in range(config['max_search_uses'] + 2):
        if config['enable_search']:
            remaining = config['max_search_uses'] - used
            if remaining <= 0 and results:
                raise ModelError('MODEL_RESPONSE_INVALID', '搜索次数已达上限，模型尚未完成回复。')
            body['tools'] = [{'type': 'web_search_20250305', 'name': 'web_search',
                              'max_uses': remaining}]
        sink = TextSink(config['on_text'])
        data = (await streamed(http, url, body, sink) if config['on_text']
                else await http.json(url, body))
        if getattr(http, 'evidence', None) is not None:
            http.evidence.stage = 'validating_anthropic_response'
        result = parse(data, config['provider_id'], config['model'], config['enable_search'])
        sink.finish(result.text)
        results.append(result)
        used += result.usage.search_requests
        if config['enable_search'] and used > config['max_search_uses']:
            raise invalid()
        if result.finish_reason == 'pause_turn':
            # Preserve encrypted search results, thinking and signatures verbatim.
            body['messages'].append({'role': 'assistant', 'content': data['content']})
            continue
        if result.finish_reason == 'tool_use':
            raise ModelError('MODEL_RESPONSE_INVALID', '模型请求了未配置的客户端工具。')
        if result.finish_reason not in {'end_turn', 'max_tokens', 'stop_sequence',
                                         'refusal', 'model_context_window_exceeded'}:
            raise invalid()
        return combine(results)
    raise ModelError('MODEL_RESPONSE_INVALID', '模型工具往返次数超限。')
