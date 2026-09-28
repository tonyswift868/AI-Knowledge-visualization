"""DashScope native Generation: search sources are not guaranteed by chat mode."""
import re

from ._http import endpoint, vendor_error
from ._accounting import read_usage
from .base import ModelResult, TextSink, hit, invalid, string


def parse(data, model, search_enabled):
    output = data['output']
    choice = output['choices'][0]
    text = string(choice['message']['content'])
    sources = (output.get('search_info') or {}).get('search_results') or []
    hits = tuple(hit(source) for source in sources)
    by_index = {str(source['index']): value for source, value in zip(sources, hits) if 'index' in source}
    citations = tuple(by_index[index] for index in re.findall(r'\[ref_(\d+)\]', text) if index in by_index)
    return ModelResult(text, read_usage('dashscope_generation', 'qwen', data, search_enabled),
        'qwen', data.get('model', model), (), hits, citations, string(choice['finish_reason']))


async def run(http, config):
    url = endpoint(config['base_url'], '/services/aigc/text-generation/generation')
    messages = list(config['messages'])
    if config['system'] is not None:
        messages.insert(0, {'role': 'system', 'content': config['system']})
    parameters = {'result_format': 'message', 'max_tokens': config['max_tokens'],
                  'enable_search': config['enable_search']}
    if config['enable_search']:
        parameters['search_options'] = {'enable_source': True, 'enable_citation': True,
                                         'citation_format': '[ref_<number>]'}
    if config['temperature'] is not None:
        parameters['temperature'] = config['temperature']
    body = {'model': config['model'], 'input': {'messages': messages}, 'parameters': parameters}
    sink = TextSink(config['on_text'])
    if config['on_text']:
        parameters['incremental_output'] = True
        chunks, sources, raw, finish = [], [], None, None
        async for _, data in http.events(url, body, {'X-DashScope-SSE': 'enable'}):
            if data is None:
                continue
            if data.get('code'):
                raise vendor_error('qwen', 200, data)
            if data.get('usage') is not None:
                raw = data['usage']
            output = data.get('output') or {}
            sources.extend((output.get('search_info') or {}).get('search_results') or [])
            for choice in output.get('choices') or []:
                delta = choice.get('message', {}).get('content', '')
                chunks.append(string(delta))
                sink.emit(delta)
                if choice.get('finish_reason') not in {None, '', 'null'}:
                    finish = choice['finish_reason']
        if finish is None or raw is None:
            raise invalid()
        data = {'usage': raw, 'output': {'choices': [{'message': {'content': ''.join(chunks)},
                                                    'finish_reason': finish}],
                                       'search_info': {'search_results': sources}}}
    else:
        data = await http.json(url, body)
    result = parse(data, config['model'], config['enable_search'])
    sink.finish(result.text)
    return result
