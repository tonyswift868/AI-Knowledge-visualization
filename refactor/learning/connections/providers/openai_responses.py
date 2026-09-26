"""OpenAI Responses native web_search, complete sources and URL annotations."""
from ._http import endpoint, vendor_error
from .base import ModelResult, TextSink, hit, invalid, string, usage


def parse(data, model, search_enabled):
    if data.get('status') not in {'completed', 'incomplete'}:
        raise vendor_error('openai', 200, data)
    texts, queries, hits, citations, actions = [], [], [], [], []
    count = 0
    for output in data['output']:
        if output['type'] == 'web_search_call':
            if output.get('status') != 'completed':
                raise invalid()
            action = output['action']
            actions.append(action)
            if action['type'] == 'search':
                count += 1
                if 'queries' in action:
                    queries.extend(string(q) for q in action['queries'])
                elif 'query' in action:
                    queries.append(string(action['query']))
            for source in action.get('sources') or []:
                hits.append(hit(source))
        elif output['type'] == 'message':
            for part in output['content']:
                if part['type'] == 'output_text':
                    texts.append(string(part['text']))
                    citations.extend(hit(c) for c in part.get('annotations') or []
                                     if c.get('type') == 'url_citation')
                elif part['type'] == 'refusal':
                    texts.append(string(part['refusal']))
        elif output['type'] == 'function_call':
            raise invalid()
    raw = data['usage']
    details = raw.get('input_tokens_details') or {}
    finish = (data.get('incomplete_details') or {}).get('reason', data['status'])
    return ModelResult(''.join(texts), usage(
        raw, raw['input_tokens'], details.get('cached_tokens'), raw['output_tokens'],
        (raw.get('output_tokens_details') or {}).get('reasoning_tokens'), count,
        search_actions=actions, search_count_source='web_search_call.action.type=search'),
        'openai', data.get('model', model), tuple(queries), tuple(hits), tuple(citations), string(finish))


async def run(http, config):
    url = endpoint(config['base_url'], '/responses', version='v1')
    body = {'model': config['model'], 'input': config['messages'], 'store': False,
            'max_output_tokens': config['max_tokens']}
    if config['system'] is not None:
        body['instructions'] = config['system']
    if config['temperature'] is not None:
        body['temperature'] = config['temperature']
    if config['enable_search']:
        body.update(tools=[{'type': 'web_search'}], max_tool_calls=config['max_search_uses'],
                    include=['web_search_call.action.sources'])
    sink = TextSink(config['on_text'])
    if config['on_text']:
        data = None
        async for _, event in http.events(url, dict(body, stream=True)):
            if event is None:
                continue
            if event['type'] in {'response.output_text.delta', 'response.refusal.delta'}:
                sink.emit(event['delta'])
            elif event['type'] in {'response.completed', 'response.incomplete'}:
                if data is not None:
                    raise invalid()
                data = event['response']
            elif event['type'] in {'response.failed', 'error'}:
                raise vendor_error('openai', 200, event.get('response', event))
        if data is None:
            raise invalid()
    else:
        data = await http.json(url, body)
    result = parse(data, config['model'], config['enable_search'])
    sink.finish(result.text)
    return result
