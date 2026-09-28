"""Synthetic failure variants of the documented T02 wire fixtures."""
import copy

from .samples import anthro_frames, chat_frames, frame, ordinary

OFFICIAL = ('deepseek', 'qwen', 'kimi', 'zhipu', 'openai', 'anthropic', 'gemini')
STREAMED = tuple(p for p in OFFICIAL if p != 'gemini')


def set_text(data, provider, text):
    if provider in {'deepseek', 'anthropic'}:
        data['content'][-1]['text'] = text
    elif provider in {'kimi', 'zhipu'}:
        data['choices'][0]['message']['content'] = text
    elif provider == 'qwen':
        data['output']['choices'][0]['message']['content'] = text
    elif provider == 'openai':
        data['output'][-1]['content'][0]['text'] = text
    else:
        data['candidates'][0]['content']['parts'][-1]['text'] = text


def truncated(provider, *, text='半截😀{'):
    data = ordinary(provider)
    set_text(data, provider, text)
    if provider in {'deepseek', 'anthropic'}:
        data['stop_reason'] = 'max_tokens'
    elif provider in {'kimi', 'zhipu'}:
        data['choices'][0]['finish_reason'] = 'length'
    elif provider == 'qwen':
        data['output']['choices'][0]['finish_reason'] = 'length'
    elif provider == 'openai':
        data.update(status='incomplete', incomplete_details={'reason': 'max_output_tokens'})
    else:
        data['candidates'][0]['finishReason'] = 'MAX_TOKENS'
    return data


def wire_frames(data, provider):
    if provider in {'deepseek', 'anthropic'}:
        return anthro_frames(data)
    if provider in {'kimi', 'zhipu'}:
        return chat_frames(data)
    if provider == 'openai':
        text = data['output'][-1]['content'][0]['text']
        return [frame({'type': 'response.output_text.delta', 'delta': text}),
                frame({'type': 'response.' + data['status'], 'response': data})]
    first = copy.deepcopy(data)
    first['output']['choices'][0]['finish_reason'] = 'null'
    last = copy.deepcopy(data)
    last['output']['choices'][0]['message']['content'] = ''
    return [frame(first), frame(last)]


def expected_tokens(provider):
    return (150 if provider in {'deepseek', 'anthropic'} else 100,
            25 if provider == 'gemini' else 20)
