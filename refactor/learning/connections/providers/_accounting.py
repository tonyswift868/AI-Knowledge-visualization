"""Receipt accounting, independent of answer/tool/citation validation.

One receipt per HTTP response, with cumulative SSE meters replaced in place.
Only numeric usage, protocol metadata and visible text *lengths* survive errors;
no prompt, answer, reasoning, tool arguments or URL is attached to an exception.
"""
from __future__ import annotations
import time

from ..errors import (MODEL_CONTEXT_WINDOW_EXCEEDED, MODEL_OUTPUT_TRUNCATED,
                      CONTEXT_WINDOW_EXCEEDED_MESSAGE, ModelError)
from .base import Usage, combine_usage, usage


def mapping(value):
    return value if isinstance(value, dict) else {}


def items(value):
    return [v for v in value if isinstance(v, dict)] if isinstance(value, list) else []


def first(value):
    return next(iter(items(value)), {})


def number(value):
    return value if type(value) is int and 0 <= value <= 10**15 else None


def add(*values):
    return sum(values) if all(number(v) is not None for v in values) else None


def text_length(value):
    return len(value) if isinstance(value, str) else 0


def read_usage(protocol, provider, data, search_enabled, *, strict=True, **metadata):
    """Same meter normalization for successful answers and failed receipts.

    strict=False keeps independent valid counters when others are missing/bad.
    Unknown placeholders maintain Usage's arithmetic invariants, but are marked
    unreported and cannot become a zero-cost or complete summary.
    """
    raw = mapping(data.get('usageMetadata' if protocol == 'gemini_generate_content' else 'usage'))
    cached = output = reasoning = total = searches = None
    if protocol == 'anthropic_messages':
        # Native Messages input excludes cache reads/writes, including DeepSeek
        # (measured 2026-09-27). Preserve BUG-03's all-input accounting.
        cached = raw.get('cache_read_input_tokens', 0 if raw else None)
        total = add(raw.get('input_tokens'), cached, raw.get('cache_creation_input_tokens', 0))
        output = raw.get('output_tokens')
        searches = mapping(raw.get('server_tool_use')).get('web_search_requests')
        if searches is None:
            searches = (sum(b.get('type') == 'server_tool_use' and b.get('name') == 'web_search'
                            for b in items(data.get('content')))
                        if isinstance(data.get('content'), list) else None) if search_enabled else 0
        metadata.update(search_count_source='server_tool_use_or_blocks')
    elif protocol == 'openai_chat':
        total, output = raw.get('prompt_tokens'), raw.get('completion_tokens')
        cached = mapping(raw.get('prompt_tokens_details')).get('cached_tokens', raw.get('cached_tokens'))
        reasoning = mapping(raw.get('completion_tokens_details')).get('reasoning_tokens')
        message = mapping(first(data.get('choices')).get('message'))
        searches = (len(message.get('tool_calls') or []) if provider == 'kimi' and message
                    else None if search_enabled else 0)
        metadata.update(unreported_search_fields=(
            ['search_queries', 'search_hits', 'citations'] if provider == 'kimi' and search_enabled
            else ['search_queries'] if search_enabled else []))
    elif protocol == 'openai_responses':
        total, output = raw.get('input_tokens'), raw.get('output_tokens')
        cached = mapping(raw.get('input_tokens_details')).get('cached_tokens')
        reasoning = mapping(raw.get('output_tokens_details')).get('reasoning_tokens')
        actions = [mapping(o.get('action')) for o in items(data.get('output')) if o.get('type') == 'web_search_call']
        searches = (sum(a.get('type') == 'search' for a in actions)
                    if isinstance(data.get('output'), list) else None if search_enabled else 0)
        metadata.update(search_actions=actions, service_tier=data.get('service_tier'),
                        search_count_source='web_search_call.action.type=search')
    elif protocol == 'dashscope_generation':
        total, output = raw.get('input_tokens'), raw.get('output_tokens')
        cached = mapping(raw.get('prompt_tokens_details') or raw.get('input_tokens_details')).get('cached_tokens')
        reasoning = mapping(raw.get('output_tokens_details')).get('reasoning_tokens')
        searches = None if search_enabled else 0
        metadata.update(unreported_search_fields=['search_queries'] if search_enabled else [])
    elif protocol == 'gemini_generate_content':
        # Gemini reports thinking separately; reconcile tool input against its
        # reported total instead of double-counting it.
        reasoning = raw.get('thoughtsTokenCount', 0 if raw else None)
        output = add(raw.get('candidatesTokenCount', 0 if raw else None), reasoning)
        prompt, tool = raw.get('promptTokenCount'), raw.get('toolUsePromptTokenCount', 0)
        total = add(prompt, tool)
        if tool and raw.get('totalTokenCount') == add(prompt, output):
            total = prompt
        cached = raw.get('cachedContentTokenCount', 0 if raw else None)
        grounding = mapping(first(data.get('candidates')).get('groundingMetadata'))
        queries = grounding.get('webSearchQueries')
        searches = len(queries) if isinstance(queries, list) else None if search_enabled else 0
        reconciled = raw['totalTokenCount'] == add(total, output) if 'totalTokenCount' in raw else None
        metadata.update(grounding_metadata=grounding, token_total_reconciled=reconciled,
                        search_count_source='reported_webSearchQueries_not_billing_units')
    if strict:
        return usage(raw, total, cached, output, reasoning, searches, **metadata)
    values = dict(input_tokens=number(total), cached_input_tokens=number(cached),
                  output_tokens=number(output), reasoning_tokens=number(reasoning), search_requests=number(searches))
    missing = {k for k, v in values.items() if v is None}
    if number(total) is not None and number(cached) is not None and cached > total:
        missing.update(('cached_input_tokens', 'uncached_input_tokens'))
        values['cached_input_tokens'] = 0
    if number(output) is not None and number(reasoning) is not None and reasoning > output:
        missing.add('reasoning_tokens')
        values['reasoning_tokens'] = 0
    values = {k: v or 0 for k, v in values.items()}
    if 'input_tokens' in missing:
        values['input_tokens'] = values['cached_input_tokens']
    if missing & {'input_tokens', 'cached_input_tokens'}:
        missing.add('uncached_input_tokens')
    if 'output_tokens' in missing:
        values['output_tokens'] = values['reasoning_tokens']
    values['uncached_input_tokens'] = values['input_tokens'] - values['cached_input_tokens']
    return Usage(**values, raw=dict(raw, _mentorflow=dict(metadata, unreported_fields=sorted(missing))))


# Only numeric vendor usage is retained on exceptions. Metadata comes from our
# protocol reader; reply text, unknown vendor extensions and error messages do not.
_NUMBERS = {'input_tokens', 'output_tokens', 'total_tokens', 'prompt_tokens', 'completion_tokens',
            'cached_tokens', 'cache_read_input_tokens', 'cache_creation_input_tokens', 'cache_write_tokens',
            'promptTokenCount', 'cachedContentTokenCount', 'candidatesTokenCount', 'thoughtsTokenCount',
            'toolUsePromptTokenCount', 'totalTokenCount'}
_DETAILS = {'input_tokens_details': {'cached_tokens', 'cache_write_tokens'},
            'prompt_tokens_details': {'cached_tokens', 'cache_write_tokens'},
            'completion_tokens_details': {'reasoning_tokens'}, 'output_tokens_details': {'reasoning_tokens'},
            'cache_creation': {'ephemeral_5m_input_tokens', 'ephemeral_1h_input_tokens'},
            'server_tool_use': {'web_search_requests', 'web_fetch_requests'}}


def numeric_usage(raw):
    raw = mapping(raw)
    result = {k: number(v) for k, v in raw.items() if k in _NUMBERS}
    for key, allowed in _DETAILS.items():
        if isinstance(raw.get(key), dict):
            result[key] = {k: number(v) for k, v in raw[key].items() if k in allowed}
    return result


def covers(new, old):
    """Whether a cumulative frame reports every previously observed counter."""
    return all(v is None or (k in new and (covers(mapping(new[k]), v) if isinstance(v, dict)
                                          else number(new[k]) is not None)) for k, v in old.items())


def sent_reasoning(body):
    """Allowlisted values actually assembled on the HTTP request, no text."""
    body = mapping(body)
    result = {}
    efforts = {'none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max'}
    for key in ('reasoning', 'output_config'):
        effort = mapping(body.get(key)).get('effort')
        if isinstance(effort, str) and effort in efforts:
            result[key] = {'effort': effort}
    mode = mapping(body.get('thinking')).get('type')
    if isinstance(mode, str) and mode in {'disabled', 'enabled', 'adaptive'}:
        result['thinking'] = {'type': mode}
    effort = body.get('reasoning_effort')
    if isinstance(effort, str) and effort in efforts:
        result['reasoning_effort'] = effort
    if type(body.get('enable_thinking')) is bool:
        result['enable_thinking'] = body['enable_thinking']
    parameters = mapping(body.get('parameters'))
    nested = sent_reasoning({k: parameters[k] for k in ('enable_thinking', 'reasoning_effort') if k in parameters}) if parameters else {}
    if nested:
        result['parameters'] = nested
    thinking = mapping(mapping(body.get('generationConfig')).get('thinkingConfig'))
    level = thinking.get('thinkingLevel')
    if isinstance(level, str) and level in {'minimal', 'low', 'medium', 'high'}:
        result['generationConfig'] = {'thinkingConfig': {'thinkingLevel': level}}
    return result


class _Receipt:
    def __init__(self, config, body=None):
        self.protocol, self.provider = config['protocol'], config['provider_id']
        self.search = config['enable_search']
        self.model = config['model']
        self.data, self.length, self.reason = {}, 0, ''
        self.final_usage = False
        self.received = False
        self.tools = {}
        self.sent_reasoning = sent_reasoning(body or {})
        self.thinking_blocks = self.thinking_chars = 0

    def model_from(self, data):
        model = data.get('modelVersion' if self.protocol == 'gemini_generate_content' else 'model')
        if isinstance(model, str) and model and len(model) <= 200 and not any(ord(c) < 32 for c in model):
            self.model = model

    def remember_usage(self, raw, key='usage'):
        previous = self.data.setdefault(key, {})
        current = numeric_usage(raw)
        complete = covers(current, previous)
        for name, value in current.items():
            if isinstance(value, dict):
                previous.setdefault(name, {}).update(value)
            else:
                previous[name] = value
        return complete

    def snapshot(self, data):
        """Extract before validating the generated answer. Never store its text."""
        self.model_from(data)
        key = 'usageMetadata' if self.protocol == 'gemini_generate_content' else 'usage'
        self.final_usage = self.remember_usage(data.get(key), key)
        if self.protocol == 'anthropic_messages':
            blocks = items(data.get('content'))
            self.thinking_blocks = sum(b.get('type') == 'thinking' for b in blocks)
            self.thinking_chars = sum(text_length(b.get('thinking')) for b in blocks if b.get('type') == 'thinking')
            self.length = sum(text_length(b.get('text')) for b in blocks if b.get('type') == 'text')
            self.data['content'] = [{'type': 'server_tool_use', 'name': 'web_search'} for b in blocks
                                    if b.get('type') == 'server_tool_use' and b.get('name') == 'web_search']
            if not isinstance(data.get('content'), list):
                self.data.pop('content', None)
            self.reason = data.get('stop_reason')
        elif self.protocol in {'openai_chat', 'dashscope_generation'}:
            output = mapping(data.get('output')) if self.protocol == 'dashscope_generation' else data
            choice = first(output.get('choices'))
            message = mapping(choice.get('message'))
            self.length = text_length(message.get('content'))
            self.data['choices'] = [{'message': {'tool_calls': [{} for _ in items(message.get('tool_calls'))]}}] if message else []
            self.reason = choice.get('finish_reason')
        elif self.protocol == 'openai_responses':
            self.reason = mapping(data.get('incomplete_details')).get('reason', data.get('status'))
            output = items(data.get('output'))
            if isinstance(data.get('output'), list):
                self.length = sum(text_length(p.get('text', p.get('refusal'))) for o in output
                                  if o.get('type') == 'message' for p in items(o.get('content'))
                                  if p.get('type') in ('output_text', 'refusal'))
                self.data['output'] = []
                for item in output:
                    if item.get('type') == 'web_search_call':
                        kind = mapping(item.get('action')).get('type')
                        kind = kind if isinstance(kind, str) and kind in {'search', 'open_page', 'find_in_page'} else 'unknown'
                        self.data['output'].append({'type': 'web_search_call', 'action': {'type': kind}})
            tier = data.get('service_tier')
            self.data['service_tier'] = tier if isinstance(tier, str) and tier in {'default', 'auto', 'priority', 'flex', 'scale'} else None
        elif self.protocol == 'gemini_generate_content':
            candidate = first(data.get('candidates'))
            self.length = sum(text_length(p.get('text')) for p in items(mapping(candidate.get('content')).get('parts'))
                              if not p.get('thought'))
            queries = mapping(candidate.get('groundingMetadata')).get('webSearchQueries')
            self.data['candidates'] = [{'groundingMetadata': {'webSearchQueries': [''] * len(queries)}
                                       if isinstance(queries, list) else {}}]
            self.reason = candidate.get('finishReason')

    def event(self, event, data):
        if data is None:
            return
        kind = data.get('type', event)
        kind = kind if isinstance(kind, str) else ''
        if self.protocol == 'anthropic_messages':
            # An error/unknown event can also carry meters. Save them even if
            # answer parsing rejects this event; only a terminal delta proves
            # these are the final cumulative values.
            if isinstance(data.get('usage'), dict):
                self.data.setdefault('usage', {}).update(numeric_usage(data['usage']))
            if kind == 'message_start':
                message = mapping(data.get('message'))
                self.snapshot(message)
                self.final_usage = False
            elif kind == 'content_block_start':
                block = mapping(data.get('content_block'))
                if block.get('type') == 'thinking':
                    self.thinking_blocks += 1
                    self.thinking_chars += text_length(block.get('thinking'))
                if block.get('type') == 'text':
                    self.length += text_length(block.get('text'))
                if block.get('type') == 'server_tool_use' and block.get('name') == 'web_search':
                    self.data.setdefault('content', []).append({'type': 'server_tool_use', 'name': 'web_search'})
            elif kind == 'content_block_delta':
                delta = mapping(data.get('delta'))
                if delta.get('type') == 'text_delta':
                    self.length += text_length(delta.get('text'))
                elif delta.get('type') == 'thinking_delta':
                    self.thinking_chars += text_length(delta.get('thinking'))
            elif kind == 'message_delta':
                self.reason = mapping(data.get('delta')).get('stop_reason', self.reason)
                if self.reason and number(mapping(data.get('usage')).get('output_tokens')) is not None:
                    self.final_usage = True
        elif self.protocol in {'openai_chat', 'dashscope_generation'}:
            self.model_from(data)
            output = mapping(data.get('output')) if self.protocol == 'dashscope_generation' else data
            for choice in items(output.get('choices')):
                if choice.get('index', 0) != 0:
                    continue
                if isinstance(choice.get('finish_reason'), str) and choice['finish_reason'] not in {'', 'null'}:
                    self.reason = choice['finish_reason']
                message = mapping(choice.get('delta' if self.protocol == 'openai_chat' else 'message'))
                self.length += text_length(message.get('content'))
                for tool in items(message.get('tool_calls')):
                    if type(tool.get('index')) is int:
                        self.tools[tool['index']] = {}
            self.data['choices'] = [{'message': {'tool_calls': list(self.tools.values())}}]
            if isinstance(data.get('usage'), dict):
                complete = self.remember_usage(data['usage'])
                if self.reason:
                    self.final_usage = complete
        elif self.protocol == 'openai_responses':
            if kind in {'response.output_text.delta', 'response.refusal.delta'}:
                self.length += text_length(data.get('delta'))
            if isinstance(data.get('response'), dict):
                self.snapshot(data['response'])
                self.final_usage = self.final_usage and kind in {'response.completed', 'response.incomplete', 'response.failed'}

    def usage(self):
        value = read_usage(self.protocol, self.provider, self.data, self.search, strict=False)
        value.raw['_mentorflow'].update(request_attempted=True, sent_reasoning_parameters=self.sent_reasoning)
        if self.protocol == 'anthropic_messages' and self.received:
            value.raw['_mentorflow'].update(thinking_blocks=self.thinking_blocks, thinking_characters=self.thinking_chars)
        if not self.final_usage:
            value.raw['_mentorflow']['partial_usage'] = True
        if self.received:
            value.raw['_mentorflow']['response_received'] = True
        return value

    @property
    def truncated(self):
        reasons = {'anthropic_messages': {'max_tokens'},
                   'openai_chat': {'length'}, 'dashscope_generation': {'length'},
                   'openai_responses': {'max_output_tokens'}, 'gemini_generate_content': {'MAX_TOKENS'}}
        return isinstance(self.reason, str) and self.reason in reasons[self.protocol]

    @property
    def context_window_exceeded(self):
        return self.protocol == 'anthropic_messages' and self.reason == 'model_context_window_exceeded'


class Accounting:
    """Call-local state also survives cancellation of the async HTTP worker.

    Public ModelError metadata: usage (None only if no response),
    response_received, generated_text_length (Unicode characters, excluding
    reasoning/tool arguments), finish_reason and model. Metadata is not a partial
    answer and must not be saved as a successful turn. No automatic retry here.
    """
    def __init__(self, config):
        self.config, self.receipts = config, []
        self.first_event_timeout = config.get('first_event_timeout', 30.)

    def first_event(self):
        receipt = self.receipts[-1]
        if receipt.first_event_ms is None:
            receipt.first_event_ms = round((time.monotonic()-receipt.sent_at)*1000, 3)

    def first_expired(self):
        return bool(self.first_event_timeout is not None and self.receipts and self.receipts[-1].first_event_ms is None and
                    time.monotonic()-self.receipts[-1].sent_at >= self.first_event_timeout)

    def start(self, body=None):
        self.receipts.append(_Receipt(self.config, body))
        self.receipts[-1].sent_at = time.monotonic()
        self.receipts[-1].first_event_ms = None

    def annotate(self, usage):
        # Only program-defined parameter values/counts, never arbitrary body
        # content or vendor extensions. Keep the original numeric meters.
        meta = usage.raw.setdefault('_mentorflow', {})
        meta['request_attempted'] = bool(self.receipts)
        meta['first_event_ms'] = self.receipts[0].first_event_ms if self.receipts else None
        meta['first_event_ms_by_request'] = [r.first_event_ms for r in self.receipts]
        meta['sent_reasoning_parameters'] = self.receipts[-1].sent_reasoning if self.receipts else {}
        if self.config['protocol'] == 'anthropic_messages':
            meta['thinking_blocks'] = sum(r.thinking_blocks for r in self.receipts)
            meta['thinking_characters'] = sum(r.thinking_chars for r in self.receipts)
        return usage

    def begin(self):
        self.receipts[-1].received = True

    def snapshot(self, data):
        self.receipts[-1].snapshot(data)

    def event(self, event, data):
        self.receipts[-1].event(event, data)

    def check_truncated(self):
        if any(r.context_window_exceeded for r in self.receipts):
            raise self.attach(ModelError(MODEL_CONTEXT_WINDOW_EXCEEDED, CONTEXT_WINDOW_EXCEEDED_MESSAGE))
        if any(r.truncated for r in self.receipts):
            raise self.attach(ModelError(MODEL_OUTPUT_TRUNCATED, '模型输出达到上限，请拆小内容后重新生成。'))

    def attach(self, error):
        truncated = next((r for r in reversed(self.receipts) if r.truncated), None)
        if truncated and error.code == 'MODEL_RESPONSE_INVALID':
            error = ModelError(MODEL_OUTPUT_TRUNCATED, '模型输出达到上限，请拆小内容后重新生成。')
        error.response_received = any(r.received for r in self.receipts)
        error.usage = combine_usage([r.usage() for r in self.receipts]) if error.response_received else None
        error.generated_text_length = sum(r.length for r in self.receipts)
        error.finish_reason = truncated.reason if truncated else ''
        error.model = self.receipts[-1].model if self.receipts else self.config['model']
        error.request_attempted = bool(self.receipts)
        error.sent_reasoning_parameters = self.receipts[-1].sent_reasoning if self.receipts else {}
        if error.usage is not None:
            self.annotate(error.usage)
        return error
