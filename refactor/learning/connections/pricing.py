"""Decimal catalogue pricing, never a guessed invoice or currency conversion.

PriceCatalog(path).cost(provider, model, usage, at=aware_datetime, context={...})
returns Cost or None. Missing rates, billing conditions or billable meters make
the *whole* quote unavailable. Catalogue conditions include region/thinking mode;
cache writes and search are separate components. Free credits/tax are not inferred.

estimate_tokens is a heuristic, NOT usage: English prose averages ~4 characters
per token (https://developers.openai.com/api/docs/concepts/tokens). Chinese varies
widely by tokenizer (https://api-docs.deepseek.com/quick_start/token_usage/ and
https://platform.kimi.com/docs/pricing/chat). Use one token/Han character as a
neutral midpoint; estimates use 0.5..2 tokens/Han and ASCII 1/6..1/2. Other Unicode
uses UTF-8 bytes as a conservative bound. No tokenizer package or network request.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
import json
import math
from pathlib import Path
import re
from urllib.parse import urlsplit

from .errors import ModelError
from .providers import Usage

FIELDS = ('input_tokens', 'cached_input_tokens', 'uncached_input_tokens',
          'output_tokens', 'reasoning_tokens', 'search_requests')
MILLION = Decimal(1000000)


def _error():
    return ModelError('PRICE_CATALOG_INVALID', '价格表无效，未计算费用。')


def decimal(value):
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise _error()
    try:
        result = Decimal(str(value))
        if not result.is_finite() or not 0 <= result <= Decimal('1e15'):
            raise ValueError()
        return result
    except (InvalidOperation, ValueError):
        raise _error() from None


def timestamp(value=None):
    """UTC instant; naive dates/times are rejected rather than assigned a zone."""
    if value is None:
        return datetime.now(timezone.utc)
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00')) if isinstance(value, str) else value
        if not isinstance(result, datetime) or result.tzinfo is None or result.utcoffset() is None:
            raise ValueError()
        return result.astimezone(timezone.utc)
    except (ValueError, TypeError, OverflowError):
        raise ModelError('USAGE_TIME_INVALID', '请提供包含时区的用量时间。') from None


def validate_usage(value):
    if (not isinstance(value, Usage) or not isinstance(value.raw, dict)
            or any(type(getattr(value, key)) is not int or not 0 <= getattr(value, key) <= 10**15 for key in FIELDS)
            or value.input_tokens != value.cached_input_tokens + value.uncached_input_tokens
            or value.reasoning_tokens > value.output_tokens):
        raise ModelError('USAGE_INVALID', '模型用量数据无效，未将其记为零用量。')
    meta = value.raw.get('_mentorflow', {})
    if not isinstance(meta, dict) or not isinstance(meta.get('billing_context', {}), dict):
        raise ModelError('USAGE_INVALID', '模型用量元数据无效。')
    missing = meta.get('unreported_fields', [])
    if not isinstance(missing, list) or any(key not in FIELDS for key in missing):
        raise ModelError('USAGE_INVALID', '模型用量缺失标记无效。')
    return set(missing)


@dataclass(frozen=True)
class Cost:
    amount: Decimal
    currency: str
    components: dict[str, Decimal]
    catalog_version: str
    source_urls: tuple[str, ...]


@dataclass(frozen=True)
class CostEstimate:
    low: Decimal
    high: Decimal
    currency: str
    input_tokens_low: int
    input_tokens_high: int
    assumptions: tuple[str, ...]


def _counts(text):
    if not isinstance(text, str):
        raise ModelError('MODEL_MESSAGES_INVALID', '待估算内容必须为文字。')
    han = ascii_count = other = 0
    try:
        for char in text:
            n = ord(char)
            if 0x3400 <= n <= 0x9FFF or 0x20000 <= n <= 0x323AF:
                han += 1
            elif n < 128:
                ascii_count += 1
            else:
                other += len(char.encode('utf-8'))
    except UnicodeError:
        raise ModelError('MODEL_MESSAGES_INVALID', '待估算内容包含无效文字。') from None
    return han, ascii_count, other


def estimate_tokens(text):
    han, ascii_count, other = _counts(text)
    return math.ceil(han + ascii_count / 4 + other / 2)


def _bounds(text):
    han, ascii_count, other = _counts(text)
    return math.ceil(han / 2 + ascii_count / 6 + other / 4), math.ceil(han * 2 + ascii_count / 2 + other)


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise _error()
        result[key] = value
    return result


def _date(value):
    if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
        raise _error()
    return value


class PriceCatalog:
    def __init__(self, path):
        try:
            path = Path(path)
            if path.stat().st_size > 2 * 1024 * 1024:
                raise ValueError()
            data = json.loads(path.read_text(encoding='utf-8-sig'), parse_float=Decimal, object_pairs_hook=_pairs)
            self._load(data)
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            raise _error() from None

    @classmethod
    def from_dict(cls, data):
        obj = cls.__new__(cls)
        try:
            obj._load(copy.deepcopy(data))
        except (ValueError, TypeError, KeyError, AttributeError):
            raise _error() from None
        return obj

    @classmethod
    def empty(cls):
        return cls.from_dict({'version': 'unavailable', 'unit': 'per_million_tokens', 'models': []})

    def _load(self, data):
        if (not isinstance(data, dict) or data.get('unit') != 'per_million_tokens'
                or not isinstance(data.get('version'), str) or not data['version']
                or not isinstance(data.get('models'), list)):
            raise _error()
        self.version = data['version']
        self.periods = data.get('periods', {})
        if not isinstance(self.periods, dict):
            raise _error()
        calendar = self.periods.get('holiday_calendar')
        if calendar is not None:
            if (not isinstance(calendar, dict) or not isinstance(calendar.get('dates'), list)
                    or _date(calendar['start']) > _date(calendar['end'])
                    or any(not calendar['start'] <= _date(day) <= calendar['end']
                           for day in calendar['dates'])):
                raise _error()
        self._rows = data['models']
        for row in self._rows:
            if (not isinstance(row, dict) or type(row.get('verified')) is not bool
                    or any(not isinstance(row.get(k), str) or not row[k] for k in ('provider', 'model', 'currency'))
                    or not re.fullmatch('[A-Z]{3}', row['currency'])
                    or row.get('price_period', 'all') not in {'all', 'peak', 'off_peak'}
                    or not isinstance(row.get('aliases', []), list)
                    or any(not isinstance(a, str) or not a for a in row.get('aliases', []))
                    or not isinstance(row.get('conditions', {}), dict)):
                raise _error()
            if row['verified']:
                source = urlsplit(row.get('source_url', ''))
                if source.scheme != 'https' or not source.hostname or source.username or source.password:
                    raise _error()
                _date(row['checked_on'])
            for name in ('valid_from', 'valid_through', 'search_valid_through'):
                if name in row:
                    _date(row[name])
            if row.get('valid_from', '') > row.get('valid_through', '9999-12-31'):
                raise _error()
            for name in ('input_cache_hit_per_mtok', 'input_cache_miss_per_mtok', 'output_per_mtok', 'search_per_request'):
                if row.get(name) is not None:
                    row[name] = decimal(row[name])
            for key in ('input_tokens_min', 'input_tokens_max'):
                if key in row and (type(row[key]) is not int or row[key] < 0):
                    raise _error()
            if row.get('input_tokens_max', 10**15) <= row.get('input_tokens_min', -1):
                raise _error()
            writes = row.get('cache_write_rates', {})
            if not isinstance(writes, dict) or any(key not in {'5m', '1h'} for key in writes):
                raise _error()
            row['cache_write_rates'] = {k: decimal(v) if v is not None else None for k, v in writes.items()}
            searches = row.get('search_rates', {})
            if not isinstance(searches, dict) or any(k not in {'turbo', 'max', 'agent', 'agent_max'} for k in searches):
                raise _error()
            row['search_rates'] = {k: decimal(v) for k, v in searches.items()}
            if row.get('cache_write_mode', 'none') not in {'none', 'anthropic', 'openai', 'kimi'}:
                raise _error()
            if row.get('search_billing_unit', 'reported_requests') not in {'reported_requests', 'billable_queries', 'tool_calls'}:
                raise _error()
        # Overlapping rows are not silently resolved by file ordering.
        for i, a in enumerate(self._rows):
            for b in self._rows[i + 1:]:
                if (a['provider'] == b['provider'] and {a['model'], *a.get('aliases', [])} & {b['model'], *b.get('aliases', [])}
                        and a.get('price_period', 'all') == b.get('price_period', 'all')
                        and a.get('conditions', {}) == b.get('conditions', {})
                        and max(a.get('input_tokens_min', -1), b.get('input_tokens_min', -1)) <
                            min(a.get('input_tokens_max', 10**15), b.get('input_tokens_max', 10**15))
                        and max(a.get('valid_from', ''), b.get('valid_from', '')) <=
                            min(a.get('valid_through', '9999-12-31'), b.get('valid_through', '9999-12-31'))):
                    raise _error()

    def _period(self, at):
        # Chinese price schedule, no guessed public-holiday calendar.
        if self.periods.get('timezone') != 'Asia/Shanghai':
            return None
        local = at.astimezone(timezone(timedelta(hours=8)))
        clock = local.time()
        if local.weekday() >= 5 or not (time(9) <= clock < time(12) or time(14) <= clock < time(18)):
            return 'off_peak'
        calendar = self.periods.get('holiday_calendar')
        if not calendar or not calendar['start'] <= local.date().isoformat() <= calendar['end']:
            return None
        return 'off_peak' if local.date().isoformat() in calendar['dates'] else 'peak'

    def _candidates(self, provider, model, at, context, *, estimate=False):
        for row in self._rows:
            if row['provider'] != provider or model not in {row['model'], *row.get('aliases', [])} or not row['verified']:
                continue
            day = at.date().isoformat()
            if not row.get('valid_from', '') <= day <= row.get('valid_through', '9999-12-31'):
                continue
            if any(context.get(k) != v for k, v in row.get('conditions', {}).items()):
                continue
            period = row.get('price_period', 'all')
            chosen = context.get('price_period') or self._period(at)
            if period != 'all' and period != chosen and not (estimate and chosen is None):
                continue
            yield row

    @staticmethod
    def _write_counts(row, value, context):
        mode, raw = row.get('cache_write_mode', 'none'), value.raw
        if mode == 'none' or value.uncached_input_tokens == 0:
            return {'5m': 0, '1h': 0}
        if mode == 'anthropic':
            total = raw.get('cache_creation_input_tokens')
            details = raw.get('cache_creation') or {}
            if not isinstance(details, dict):
                return None
            counts = {'5m': details.get('ephemeral_5m_input_tokens'), '1h': details.get('ephemeral_1h_input_tokens')}
        else:
            details = raw.get('input_tokens_details' if mode == 'openai' else 'prompt_tokens_details') or {}
            if not isinstance(details, dict):
                return None
            total = details.get('cache_write_tokens')
            counts = {'5m': None, '1h': None}
        if total == 0:
            return {'5m': 0, '1h': 0}
        if type(total) is not int or not 0 <= total <= value.uncached_input_tokens:
            return None
        if mode == 'openai':
            return {'5m': total, '1h': 0}  # single published write price, not a TTL inference
        if all(type(n) is int and n >= 0 for n in counts.values()) and sum(counts.values()) == total:
            return counts
        ttl = context.get('cache_write_ttl')
        return {t: total if t == ttl else 0 for t in ('5m', '1h')} if ttl in {'5m', '1h'} else None

    def _quote(self, row, value, context):
        missing = validate_usage(value)
        if missing & {'input_tokens', 'cached_input_tokens', 'uncached_input_tokens', 'output_tokens', 'search_requests'}:
            return None
        if (value.raw.get('_mentorflow', {}).get('token_total_reconciled') is False
                or value.raw.get('_mentorflow', {}).get('partial_usage') is True):
            return None
        writes = self._write_counts(row, value, context)
        if writes is None:
            return None
        search_count = value.search_requests
        if row.get('search_billing_unit') == 'billable_queries' and search_count:
            # Monthly shared free quotas cannot be determined from a local log.
            search_count = context.get('billable_search_requests')
            if type(search_count) is not int or not 0 <= search_count <= value.search_requests:
                return None
        if row.get('search_billing_unit') == 'tool_calls':
            actions = value.raw.get('_mentorflow', {}).get('search_actions')
            if not isinstance(actions, list) or len(actions) < value.search_requests:
                return None
            search_count = len(actions)
        search_rate = row['search_rates'].get(context.get('search_strategy'), row.get('search_per_request'))
        counts = {'cached_input': (value.cached_input_tokens, row.get('input_cache_hit_per_mtok'), MILLION),
                  'uncached_input': (value.uncached_input_tokens - sum(writes.values()), row.get('input_cache_miss_per_mtok'), MILLION),
                  'cache_write_5m': (writes['5m'], row['cache_write_rates'].get('5m'), MILLION),
                  'cache_write_1h': (writes['1h'], row['cache_write_rates'].get('1h'), MILLION),
                  'output': (value.output_tokens, row.get('output_per_mtok'), MILLION),
                  'search': (search_count, search_rate, Decimal(1))}
        if any(n and rate is None for n, rate, _ in counts.values()):
            return None
        components = {k: Decimal(n) * (rate or Decimal(0)) / unit for k, (n, rate, unit) in counts.items()}
        return Cost(sum(components.values()), row['currency'], components, self.version, (row['source_url'],))

    def cost(self, provider, model, usage, *, at=None, context=None):
        validate_usage(usage)
        at = timestamp(at)
        context = dict(usage.raw.get('_mentorflow', {}).get('billing_context') or {}, **(context or {}))
        if 'finished_at' in context:
            end = timestamp(context['finished_at'])
            if self._period(at) != self._period(end) and provider == 'deepseek':
                return None
        if 'calls' in usage.raw:
            calls = usage.raw.get('_mentorflow', {}).get('call_usages')
            if not isinstance(calls, list) or not isinstance(usage.raw['calls'], list) or len(calls) != len(usage.raw['calls']):
                return None  # never price aggregate tokens against a per-request tier
            try:
                children = [Usage(**item) for item in calls]
                for child in children:
                    validate_usage(child)
            except (TypeError, ModelError):
                return None
            if any(sum(getattr(c, f) for c in children) != getattr(usage, f) for f in FIELDS):
                return None
            costs = [self.cost(provider, model, child, at=at, context=context) for child in children]
            if not costs or any(c is None for c in costs) or len({c.currency for c in costs}) != 1:
                return None
            return Cost(sum(c.amount for c in costs), costs[0].currency,
                        {k: sum(c.components[k] for c in costs) for k in costs[0].components}, self.version,
                        tuple(sorted({url for c in costs for url in c.source_urls})))
        rows = [r for r in self._candidates(provider, model, at, context)
                if r.get('input_tokens_min', -1) < usage.input_tokens <= r.get('input_tokens_max', 10**15)]
        if len(rows) == 1 and usage.search_requests and at.date().isoformat() > rows[0].get('search_valid_through', '9999-12-31'):
            return None
        return self._quote(rows[0], usage, context) if len(rows) == 1 else None

    def estimate_cost(self, provider, model, text='', *, input_tokens=None, output_tokens=(0, 4096),
                      search_requests=(0, 0), cache_hit_rate=(0, 0), at=None, context=None):
        """Range for the supplied token budget, not an entire plan or invoice.

        Search can add unbounded input/context: if search_requests.high > 0,
        supply input_tokens covering tool results and all rounds or return None.
        A tiered multi-round search needs per-round budgets; absent those the
        catalogue returns None rather than pricing the sum at one request's tier.
        No rate is inferred for unknown models. Currency conversions are absent.
        """
        def bounds(value):
            value = (value, value) if type(value) is int else value
            if (not isinstance(value, (tuple, list)) or len(value) != 2
                    or any(type(x) is not int or x < 0 for x in value) or value[0] > value[1]):
                raise ModelError('PRICE_ESTIMATE_INVALID', '费用估算区间无效。')
            return value
        lo, hi = bounds(input_tokens) if input_tokens is not None else _bounds(text)
        out_lo, out_hi = bounds(output_tokens)
        search_lo, search_hi = bounds(search_requests)
        if search_hi and input_tokens is None:
            return None
        if (not isinstance(cache_hit_rate, (list, tuple)) or len(cache_hit_rate) != 2
                or not Decimal(0) <= decimal(cache_hit_rate[0]) <= decimal(cache_hit_rate[1]) <= Decimal(1)):
            raise ModelError('PRICE_ESTIMATE_INVALID', '缓存命中估算区间无效。')
        quotes, when = [], timestamp(at)
        for row in self._candidates(provider, model, when, context or {}, estimate=True):
            if search_hi and when.date().isoformat() > row.get('search_valid_through', '9999-12-31'):
                return None
            if search_hi and ('input_tokens_max' in row or row.get('input_tokens_min', 0) > 0):
                return None
            lower = max(lo, row.get('input_tokens_min', -1) + 1)
            upper = min(hi, row.get('input_tokens_max', 10**15))
            if lower > upper:
                continue
            rates = [row.get(k) for k in ('input_cache_hit_per_mtok', 'input_cache_miss_per_mtok', 'output_per_mtok')]
            fee = row['search_rates'].get((context or {}).get('search_strategy'), row.get('search_per_request'))
            if any(v is None for v in rates) or (search_hi and fee is None):
                return None
            hit_rate, miss_rate, output_rate = rates
            write_rates = list(row['cache_write_rates'].values())
            if row.get('cache_write_mode', 'none') != 'none' and (not write_rates or None in write_rates):
                return None
            fractions = [decimal(v) for v in cache_hit_rate]
            input_low = min(hit_rate * f + miss_rate * (1-f) for f in fractions)
            input_high = max(hit_rate * f + max([miss_rate, *write_rates]) * (1-f) for f in fractions)
            fee = fee or Decimal(0)
            paid_low = 0 if row.get('search_billing_unit') == 'billable_queries' else search_lo
            quotes.append((Decimal(lower) * input_low / MILLION + Decimal(out_lo) * output_rate / MILLION + paid_low * fee,
                           Decimal(upper) * input_high / MILLION + Decimal(out_hi) * output_rate / MILLION + search_hi * fee,
                           row['currency'], lower, upper))
        covered = lo - 1
        for lower, upper in sorted((q[3], q[4]) for q in quotes):
            if lower > covered + 1:
                break
            covered = max(covered, upper)
        if not quotes or len({q[2] for q in quotes}) != 1 or covered < hi:
            return None
        return CostEstimate(min(q[0] for q in quotes), max(q[1] for q in quotes), quotes[0][2], lo, hi,
                            ('目录标价区间；不含税费或充值赠金抵扣。',
                             '包含所给输出预算与搜索次数；输入预算须覆盖全部往返及工具内容。',
                             '文字估算不是 tokenizer 实测；缓存写入按可用档位给出上下界。'))


def estimate_cost(catalog, provider, model, text='', **kwargs):
    return catalog.estimate_cost(provider, model, text, **kwargs)
