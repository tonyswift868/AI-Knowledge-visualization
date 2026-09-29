"""OpenAI Responses native web_search, complete sources and URL annotations."""
from ._http import endpoint, vendor_error
from ._accounting import read_usage
from .base import ModelResult, TextSink, hit, invalid, string


def parse(data, model, search_enabled):
    if data.get('status') not in {'completed', 'incomplete'}:
        raise vendor_error('openai', 200, data)
    texts, queries, hits, citations = [], [], [], []
    for output in data['output']:
        if output['type'] == 'web_search_call':
            if output.get('status') != 'completed':
                raise invalid()
            action = output['action']
            if action['type'] == 'search':
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
    finish = (data.get('incomplete_details') or {}).get('reason', data['status'])
    return ModelResult(''.join(texts), read_usage('openai_responses', 'openai', data, search_enabled),
        'openai', data.get('model', model), tuple(queries), tuple(hits), tuple(citations), string(finish))


async def run(http, config):
    url = endpoint(config['base_url'], '/responses', version='v1')
    body = {'model': config['model'], 'input': config['messages'], 'store': False,
            'max_output_tokens': config['max_tokens']}
    if config['system'] is not None:
        body['instructions'] = config['system']
    if config['temperature'] is not None:
        body['temperature'] = config['temperature']
    effort = config.get('reasoning_effort')
    # Match only model families whose supported effort values are documented.
    # Unknown or non-reasoning models retain their provider defaults.
    model = config['model']
    supported = ({'none', 'low', 'high', 'max'} if model.startswith('gpt-5.6') else
                 {'none', 'low', 'high'} if model.startswith('gpt-5.1') else set())
    if effort in supported:
        body['reasoning'] = {'effort': effort}
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
