"""GenerateContent grounding. Google search URLs are returned, never fetched."""
from urllib.parse import quote

from .base import ModelResult, TextSink, WebHit, hit, integer, invalid, string, usage


def parse(data, model, search_enabled):
    candidate = data['candidates'][0]
    texts = [string(part['text']) for part in candidate.get('content', {}).get('parts', [])
             if 'text' in part and not part.get('thought')]
    grounding = candidate.get('groundingMetadata') or {}
    chunks = grounding.get('groundingChunks') or []
    indexed, hits, citations = {}, [], []
    for index, chunk in enumerate(chunks):
        if 'web' in chunk:
            value = hit(chunk['web'], url='uri')
            indexed[index] = value
            hits.append(value)
    for support in grounding.get('groundingSupports') or []:
        for index in support.get('groundingChunkIndices') or []:
            if index not in indexed:
                # Other grounding types have no WebHit; invalid web indices are errors.
                if type(index) is not int or index < 0 or index >= len(chunks):
                    raise invalid()
                continue
            value = indexed[index]
            citations.append(WebHit(value.url, value.title, string((support.get('segment') or {}).get('text', ''))))
    queries = tuple(string(q) for q in grounding.get('webSearchQueries') or [])
    raw = data['usageMetadata']
    thoughts = integer(raw.get('thoughtsTokenCount', 0))
    # Google reports thoughts separately from candidate tokens, unlike OpenAI.
    output = integer(raw.get('candidatesTokenCount', 0)) + thoughts
    prompt = integer(raw['promptTokenCount'])
    tool_prompt = integer(raw.get('toolUsePromptTokenCount', 0))
    # Include tool-use input, reconciling against total when the service already
    # included it in promptTokenCount. Keep all components for billing review.
    total_input = prompt + tool_prompt
    if tool_prompt and raw.get('totalTokenCount') == prompt + output:
        total_input = prompt
    reconciled = (raw['totalTokenCount'] == total_input + output) if 'totalTokenCount' in raw else None
    count = len(queries) if 'webSearchQueries' in grounding else (None if search_enabled else 0)
    return ModelResult(''.join(texts), usage(
        raw, total_input, raw.get('cachedContentTokenCount', 0), output, thoughts, count,
        grounding_metadata=grounding, token_total_reconciled=reconciled,
        search_count_source='reported_webSearchQueries_not_billing_units'),
        'gemini', data.get('modelVersion', model), queries, tuple(hits), tuple(citations),
        string(candidate['finishReason']))


async def run(http, config):
    base = config['base_url']
    model = config['model'].removeprefix('models/')
    url = base + '/models/' + quote(model, safe='-._') + ':generateContent'
    generation = {'maxOutputTokens': config['max_tokens']}
    if config['temperature'] is not None:
        generation['temperature'] = config['temperature']
    body = {'contents': [{'role': 'model' if m['role'] == 'assistant' else 'user',
                          'parts': [{'text': m['content']}]} for m in config['messages']],
            'generationConfig': generation}
    if config['system'] is not None:
        body['systemInstruction'] = {'parts': [{'text': config['system']}]}
    if config['enable_search']:
        body['tools'] = [{'googleSearch': {}}]
    data = await http.json(url, body)
    result = parse(data, config['model'], config['enable_search'])
    # GenerateContent is non-streaming; optional callback receives one final chunk.
    TextSink(config['on_text']).finish(result.text)
    return result
