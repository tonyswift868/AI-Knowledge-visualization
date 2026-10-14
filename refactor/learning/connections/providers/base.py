"""Provider-neutral results. Unreported counters are listed in raw['_mentorflow'].

Zero in an unreported field means unknown, not a promise of zero billable use.
Input is all input tokens; uncached includes cache writes. Output includes reasoning.
The original provider usage keys are retained; multi-request results keep each call.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Callable

from ..errors import ModelCancelled, ModelError


@dataclass(frozen=True)
class SearchSupport:
    kind: str
    verified: bool
    doc_url: str
    checked_on: str
    notes: str


@dataclass(frozen=True)
class ProviderSpec:
    id: str
    display_name: str
    protocol: str
    default_base_url: str
    default_model: str
    search: SearchSupport | None


@dataclass(frozen=True)
class Usage:
    input_tokens: int
    cached_input_tokens: int
    uncached_input_tokens: int
    output_tokens: int
    reasoning_tokens: int
    search_requests: int
    raw: dict


@dataclass(frozen=True)
class WebHit:
    url: str
    title: str
    snippet: str


@dataclass(frozen=True)
class ModelResult:
    text: str
    usage: Usage
    provider_id: str
    model: str
    search_queries: tuple[str, ...]
    search_hits: tuple[WebHit, ...]
    citations: tuple[WebHit, ...]
    finish_reason: str


def invalid(reason='protocol_validation', *, message='模型没有返回有效的完整协议响应。', **details):
    error = ModelError('MODEL_RESPONSE_INVALID', message)
    error.validation = dict(reason=reason, **details)
    return error


def integer(value):
    if type(value) is not int or value < 0:
        raise invalid('invalid_integer')
    return value


def string(value):
    if not isinstance(value, str):
        raise invalid('invalid_string')
    return value


def hit(item, *, url='url', title='title', snippet='snippet'):
    # Deliberately retain blank/duplicate results and original strings.
    return WebHit(string(item.get(url, '')), string(item.get(title, '')),
                  string(item.get(snippet, '')))


def usage(raw, input_tokens, cached, output, reasoning=None, searches=None, **metadata):
    if not isinstance(raw, dict):
        raise invalid('invalid_usage')
    missing = []
    for field, value in [('cached_input_tokens', cached), ('reasoning_tokens', reasoning),
                         ('search_requests', searches)]:
        if value is None:
            missing.append(field)
    total = integer(input_tokens)
    cached = integer(cached) if cached is not None else 0
    output = integer(output)
    reasoning = integer(reasoning) if reasoning is not None else 0
    if cached > total or reasoning > output:
        raise invalid('usage_counter_inconsistent')
    if 'cached_input_tokens' in missing:
        missing.append('uncached_input_tokens')
    details = dict(raw)
    details['_mentorflow'] = dict(metadata, unreported_fields=missing)
    return Usage(total, cached, total - cached, output, reasoning,
                 integer(searches) if searches is not None else 0, details)


def combine_usage(values):
    """Sum HTTP rounds, never cumulative frames within the same round."""
    if len(values) == 1:
        return values[0]
    names = ('input_tokens', 'cached_input_tokens', 'uncached_input_tokens',
             'output_tokens', 'reasoning_tokens', 'search_requests')
    missing = sorted({field for value in values
                      for field in value.raw['_mentorflow']['unreported_fields']})
    totals = {name: sum(getattr(value, name) for value in values) for name in names}
    totals['raw'] = {'calls': [value.raw for value in values],
                     '_mentorflow': {'unreported_fields': missing,
                                    'call_usages': [asdict(value) for value in values]}}
    meta = totals['raw']['_mentorflow']
    if any(v.raw['_mentorflow'].get('partial_usage') for v in values):
        meta['partial_usage'] = True
    if any(v.raw['_mentorflow'].get('response_received') for v in values):
        meta['response_received'] = True
    if any(v.raw['_mentorflow'].get('token_total_reconciled') is False for v in values):
        meta['token_total_reconciled'] = False
    return Usage(**totals)


def combine(results):
    if len(results) == 1:
        return results[0]
    last = results[-1]
    return ModelResult(''.join(r.text for r in results), combine_usage([r.usage for r in results]), last.provider_id,
                       last.model, tuple(q for r in results for q in r.search_queries),
                       tuple(h for r in results for h in r.search_hits),
                       tuple(c for r in results for c in r.citations), last.finish_reason)


class TextSink:
    """One sink per HTTP response, reconciled against its final text."""
    def __init__(self, callback: Callable[[str], None] | None):
        self.callback = callback
        self.parts = []

    def emit(self, delta):
        delta = string(delta)
        if not delta:
            return
        self.parts.append(delta)
        if self.callback:
            try:
                self.callback(delta)
            except ModelCancelled:
                raise
            except Exception as exc:
                raise invalid('text_callback', callback_exception=type(exc).__name__) from None

    def finish(self, text, *, block_types=()):
        if not self.callback:
            return
        current = ''.join(self.parts)
        if not current:
            self.emit(text)
        elif current != text:
            first = next((i for i, (a, b) in enumerate(zip(current, text)) if a != b),
                         min(len(current), len(text)))
            # Lengths and position suffice; no response or thinking text retained.
            raise invalid('text_mismatch', emitted_characters=len(current),
                          final_characters=len(text), first_difference=first,
                          block_types=list(block_types))
