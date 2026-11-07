"""Account-local, durable usage journal. No prompts, replies, keys or URLs logged.

summary uses the half-open UTC interval [start, end). A missing meter makes that
aggregate field None, never zero. known_* fields are explicitly partial subtotals.
Amounts are Decimal, separated by currency and snapshotted at record time; changing
the catalogue does not reprice determined snapshots. Previously unpriced calls
can be calculated read-only with the current catalogue and are counted separately.
Pending/crashed calls stay visible.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict
from datetime import timezone as datetime_timezone
from decimal import Decimal
import json
import math
from pathlib import Path
import re
import sqlite3
import uuid

from .errors import ModelError
from .pricing import FIELDS, PriceCatalog, timestamp, validate_usage
from .providers import Usage
from .providers._accounting import sent_reasoning

_RAW_NUMBERS = {'input_tokens', 'output_tokens', 'total_tokens', 'prompt_tokens', 'completion_tokens',
                'cached_tokens', 'cache_read_input_tokens', 'cache_creation_input_tokens', 'cache_write_tokens',
                'promptTokenCount', 'cachedContentTokenCount', 'candidatesTokenCount', 'thoughtsTokenCount',
                'toolUsePromptTokenCount', 'totalTokenCount'}
_DETAILS = {'input_tokens_details': {'cached_tokens', 'cache_write_tokens'},
            'prompt_tokens_details': {'cached_tokens', 'cache_write_tokens'},
            'output_tokens_details': {'reasoning_tokens'}, 'completion_tokens_details': {'reasoning_tokens'},
            'cache_creation': {'ephemeral_5m_input_tokens', 'ephemeral_1h_input_tokens'},
            'server_tool_use': {'web_search_requests', 'web_fetch_requests'}}
_CONTEXT_VALUES = {'region': {'cn-beijing', 'global', 'international', 'unknown'},
                   'requested_reasoning_effort': {'none', 'low', 'high', 'max'},
                   'thinking_mode': {'thinking', 'non_thinking'}, 'cache_mode': {'implicit', 'explicit'},
                   'service_tier': {'standard', 'default', 'batch', 'flex', 'fast', 'priority'},
                   'search_strategy': {'turbo', 'max', 'agent', 'agent_max'},
                   'price_period': {'peak', 'off_peak'}, 'cache_write_ttl': {'5m', '1h'}}


def _clean_usage(value, depth=0):
    validate_usage(value)
    if depth > 8:
        raise ModelError('USAGE_INVALID', '用量往返记录层级无效。')
    raw = {k: v for k, v in value.raw.items() if k in _RAW_NUMBERS and type(v) is int and v >= 0}
    for key, fields in _DETAILS.items():
        if isinstance(value.raw.get(key), dict):
            raw[key] = {k: v for k, v in value.raw[key].items() if k in fields and type(v) is int and v >= 0}
    meta = value.raw.get('_mentorflow', {})
    cleaned = {'unreported_fields': sorted(set(meta.get('unreported_fields', [])))}
    if meta.get('partial_usage') is True:
        cleaned['partial_usage'] = True
    if meta.get('response_received') is True:
        cleaned['response_received'] = True
    if type(meta.get('request_attempted')) is bool:
        cleaned['request_attempted'] = meta['request_attempted']
    if 'sent_reasoning_parameters' in meta:
        cleaned['sent_reasoning_parameters'] = sent_reasoning(meta['sent_reasoning_parameters'])
    def latency(value):
        return value is None or (type(value) in (int,float) and math.isfinite(value) and value >= 0)
    if 'first_event_ms' in meta and latency(meta['first_event_ms']):
        cleaned['first_event_ms'] = meta['first_event_ms']
    values=meta.get('first_event_ms_by_request')
    if isinstance(values,list) and all(latency(v) for v in values):
        cleaned['first_event_ms_by_request'] = values[:1024]
    for field in ('thinking_blocks', 'thinking_characters'):
        if type(meta.get(field)) is int and meta[field] >= 0:
            cleaned[field] = meta[field]
    if 'token_total_reconciled' in meta:
        if meta['token_total_reconciled'] is not None and type(meta['token_total_reconciled']) is not bool:
            raise ModelError('USAGE_INVALID', '用量对账标记无效。')
        cleaned['token_total_reconciled'] = meta['token_total_reconciled']
    context = meta.get('billing_context') or {}
    cleaned['billing_context'] = {k: v for k, v in context.items()
                                   if k in _CONTEXT_VALUES and isinstance(v, str) and v in _CONTEXT_VALUES[k]}
    for key in ('billable_search_requests',):
        if type(context.get(key)) is int and context[key] >= 0:
            cleaned['billing_context'][key] = context[key]
    if 'finished_at' in context:
        cleaned['billing_context']['finished_at'] = timestamp(context['finished_at']).isoformat(timespec='microseconds')
    if isinstance(meta.get('search_actions'), list):
        cleaned['search_actions'] = [{'type': a.get('type')} for a in meta['search_actions']
                                      if isinstance(a, dict) and a.get('type') in {'search', 'open_page', 'find_in_page'}]
        if len(cleaned['search_actions']) != len(meta['search_actions']):
            cleaned['unreported_fields'] = sorted(set(cleaned['unreported_fields']) | {'search_requests'})
    if 'calls' in value.raw:
        children = meta.get('call_usages')
        if not isinstance(value.raw['calls'], list) or (isinstance(children, list) and len(children) != len(value.raw['calls'])):
            raise ModelError('USAGE_INVALID', '用量往返数量不一致。')
        raw['calls'] = []
        if isinstance(children, list):
            cleaned['call_usages'] = []
            for item in children:
                try:
                    child = _clean_usage(Usage(**item), depth + 1)
                except TypeError:
                    raise ModelError('USAGE_INVALID', '用量往返数据无效。') from None
                cleaned['call_usages'].append(child)
                raw['calls'].append(child['raw'])
        else:
            # Keep a count only; old aggregated usage cannot safely price tiers.
            raw['calls'] = [{} for _ in value.raw['calls']]
    raw['_mentorflow'] = cleaned
    return dict({key: getattr(value, key) for key in FIELDS}, raw=raw)


def _label(value, *, limit=200, empty=False):
    if (not isinstance(value, str) or len(value) > limit or (not empty and not value)
            or any(ord(c) < 32 for c in value)):
        raise ModelError('USAGE_INVALID', '用量记录标识无效。')
    return value


def _known_counters(data):
    """A later round missing a meter must not hide earlier reported subtotals."""
    meta = data['raw'].get('_mentorflow', {})
    children = meta.get('call_usages')
    if isinstance(children, list):
        parts = [_known_counters(child) for child in children]
        return {key: sum(part[key] for part in parts) for key in FIELDS}
    missing = set(meta.get('unreported_fields', []))
    return {key: data[key] if key not in missing else 0 for key in FIELDS}


class UsageLog:
    def __init__(self, path, *, catalog=None):
        self.path = Path(path).absolute()
        self.catalog = catalog if catalog is not None else PriceCatalog.empty()
        self._check()
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self._db(write=True) as db:
                version = db.execute('PRAGMA user_version').fetchone()[0]
                if version not in {0, 1}:
                    raise ModelError('USAGE_STORAGE_ERROR', '用量数据库版本不兼容。')
                db.execute('''CREATE TABLE IF NOT EXISTS calls (
                    request_id TEXT PRIMARY KEY, provider_id TEXT NOT NULL, model TEXT NOT NULL,
                    purpose TEXT NOT NULL, plan_id TEXT NOT NULL, at_utc TEXT NOT NULL,
                    state TEXT NOT NULL CHECK(state IN ('in_progress','succeeded','failed','cancelled')),
                    error_code TEXT NOT NULL, usage_json TEXT, cost_json TEXT)''')
                db.execute('CREATE INDEX IF NOT EXISTS calls_at ON calls(at_utc)')
                db.execute('PRAGMA user_version=1')
        except OSError:
            raise ModelError('USAGE_STORAGE_ERROR', '无法创建当前账号的用量记录。') from None

    def _check(self):
        for path in (self.path, *self.path.parents,
                     *(Path(str(self.path) + suffix) for suffix in ('-wal', '-shm', '-journal'))):
            if path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction()):
                raise ModelError('USAGE_STORAGE_ERROR', '用量记录路径不能是链接。')

    @contextmanager
    def _db(self, write=False):
        self._check()
        db = None
        try:
            db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA synchronous=FULL')
            if write:
                db.execute('BEGIN IMMEDIATE')
            yield db
            if write:
                db.commit()
        except sqlite3.Error:
            if db is not None:
                db.rollback()
            raise ModelError('USAGE_STORAGE_ERROR', '用量记录无法读写；请勿重复发起已发送的模型请求。') from None
        except BaseException:
            if db is not None:
                db.rollback()
            raise
        finally:
            if db is not None:
                db.close()

    def _values(self, provider_id, model, purpose, usage, plan_id, request_id, at, error_code, state):
        _label(provider_id, limit=80, empty=True)
        _label(model, limit=256, empty=True)
        _label(purpose, limit=80)
        _label(plan_id, empty=True)
        identifier = _label(request_id or uuid.uuid4().hex, limit=200)
        when = timestamp(at)
        if error_code and (not isinstance(error_code, str) or not re.fullmatch('[A-Z][A-Z0-9_]{0,79}', error_code)):
            raise ModelError('USAGE_INVALID', '用量错误分类无效。')
        data = _clean_usage(usage) if usage is not None else None
        quote = self.catalog.cost(provider_id, model, Usage(**data), at=when) if data is not None else None
        cost = (dict(amount=str(quote.amount), currency=quote.currency,
                     components={k: str(v) for k, v in quote.components.items()},
                     catalog_version=quote.catalog_version, source_urls=quote.source_urls,
                     estimated=quote.estimated, estimation_reason=quote.estimation_reason) if quote else None)
        dump = lambda obj: json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False) if obj is not None else None
        return (identifier, provider_id, model, purpose, plan_id, when.isoformat(timespec='microseconds'),
                state, error_code or '', dump(data), dump(cost))

    @staticmethod
    def _same(existing, values, *, ignore_time=False):
        # Cost is a snapshot, not part of idempotency: changing prices cannot
        # turn a retry of the same event into a new charge.
        indices = [1, 2, 3, 4, 6, 7, 8] + ([] if ignore_time else [5])
        return all(tuple(existing)[i] == values[i] for i in indices)

    def record(self, provider_id, model, purpose, usage: Usage | None, plan_id='', request_id='', at=None, *, error_code=''):
        """Return request_id. Repeated identical IDs are no-ops; conflicts fail.

        usage=None requires an error code. Unknown usage never becomes a zero
        success. begin/finish additionally protect calls interrupted by a crash.
        """
        if usage is None and not error_code:
            raise ModelError('USAGE_INVALID', '缺失用量的调用必须记录失败原因。')
        state = 'cancelled' if error_code == 'MODEL_CANCELLED' else 'failed' if error_code else 'succeeded'
        values = self._values(provider_id, model, purpose, usage, plan_id, request_id, at, error_code, state)
        with self._db(write=True) as db:
            old = db.execute('SELECT * FROM calls WHERE request_id=?', (values[0],)).fetchone()
            if old is not None:
                if not self._same(old, values, ignore_time=at is None):
                    raise ModelError('USAGE_RECORD_CONFLICT', '相同请求标识已对应另一条用量记录。')
                return values[0]
            db.execute('INSERT INTO calls VALUES (?,?,?,?,?,?,?,?,?,?)', values)
        return values[0]

    def begin(self, provider_id, model, purpose, *, plan_id='', request_id='', at=None):
        values = self._values(provider_id, model, purpose, None, plan_id, request_id, at, '', 'in_progress')
        with self._db(write=True) as db:
            old = db.execute('SELECT * FROM calls WHERE request_id=?', (values[0],)).fetchone()
            if old is not None:
                raise ModelError('USAGE_RECORD_CONFLICT', '请求标识已存在，未重复发送模型请求。')
            db.execute('INSERT INTO calls VALUES (?,?,?,?,?,?,?,?,?,?)', values)
        return values[0]

    def finish(self, request_id, usage: Usage | None, *, error_code='', model=None):
        if usage is None and not error_code:
            raise ModelError('USAGE_INVALID', '缺失用量的调用不能记为成功。')
        with self._db(write=True) as db:
            old = db.execute('SELECT * FROM calls WHERE request_id=?', (request_id,)).fetchone()
            if old is None:
                raise ModelError('USAGE_RECORD_CONFLICT', '调用记录不存在，不能跳过记账。')
            state = 'cancelled' if error_code == 'MODEL_CANCELLED' else 'failed' if error_code else 'succeeded'
            values = self._values(old['provider_id'], old['model'] if model is None else model, old['purpose'], usage,
                                  old['plan_id'], request_id, old['at_utc'], error_code, state)
            if old['state'] != 'in_progress':
                if not self._same(old, values):
                    raise ModelError('USAGE_RECORD_CONFLICT', '调用结果已记账，不能覆盖。')
                return
            db.execute('UPDATE calls SET model=?,state=?,error_code=?,usage_json=?,cost_json=? WHERE request_id=?',
                       (values[2], state, values[7], values[8], values[9], request_id))

    def _quote(self, row):
        """Use a determined snapshot, or calculate an absent quote read-only."""
        data = json.loads(row['usage_json']) if row['usage_json'] else None
        quote = json.loads(row['cost_json']) if row['cost_json'] else None
        recalculated = False
        if quote is None and data is not None:
            cost = self.catalog.cost(row['provider_id'], row['model'], Usage(**data), at=row['at_utc'])
            if cost is not None:
                quote = asdict(cost)
                recalculated = True
        return data, quote, recalculated

    def summary(self, start, end, group_by='purpose', *, tokens_only=False, timezone=datetime_timezone.utc):
        """Group days in timezone (None follows the OS); default remains UTC."""
        if group_by not in {'purpose', 'model', 'day'}:
            raise ModelError('USAGE_INVALID', '用量汇总维度无效。')
        start, end = timestamp(start), timestamp(end)
        if start > end:
            raise ModelError('USAGE_TIME_INVALID', '汇总结束时间不能早于开始时间。')
        with self._db() as db:
            rows = db.execute('SELECT * FROM calls WHERE at_utc>=? AND at_utc<? ORDER BY at_utc,request_id',
                              (start.isoformat(timespec='microseconds'), end.isoformat(timespec='microseconds'))).fetchall()
        # Never UPDATE historical accounting. Preserve determined amounts even
        # when catalogue rates change; fill only absent quotes for this view.
        rows = [dict(row) for row in rows]
        for row in rows:
            if tokens_only:
                row['quote'], row['recalculated'], row['unpriced_reason'] = None, False, ''
                continue
            data, row['quote'], row['recalculated'] = self._quote(row)
            row['unpriced_reason'] = (self.catalog.unpriced_reason(row['provider_id'], row['model'],
                                      Usage(**data) if data else None, at=row['at_utc'])
                                      if row['quote'] is None else '')
        groups = {}
        for row in rows:
            key = ((row['provider_id'], row['model']) if group_by == 'model' else
                   timestamp(row['at_utc']).astimezone(timezone).date().isoformat()
                   if group_by == 'day' else row['purpose'])
            groups.setdefault(key, []).append(row)
        def aggregate(items):
            known = {key: 0 for key in FIELDS}
            unknown, amounts, unpriced, errors = set(), {}, 0, {}
            estimated_amounts, reasons = {}, {}
            priced, estimated, recalculated = 0, 0, 0
            incomplete = {}
            transport_unknown = cancelled_unknown = partial_calls = 0
            def note(reason):
                incomplete[reason] = incomplete.get(reason, 0) + 1
            states = {s: 0 for s in ('in_progress', 'succeeded', 'failed', 'cancelled')}
            for row in items:
                data = json.loads(row['usage_json']) if row['usage_json'] else None
                meta = data['raw'].get('_mentorflow', {}) if data else {}
                missing_meters = not data or bool(set(meta.get('unreported_fields', [])) & {'input_tokens', 'output_tokens'})
                uncertain = missing_meters or meta.get('partial_usage') is True
                if uncertain and meta.get('request_attempted') is not False:
                    transport_unknown += row['error_code'] in {'MODEL_TIMEOUT', 'MODEL_CONNECTION_FAILED'}
                    cancelled_unknown += row['error_code'] == 'MODEL_CANCELLED'
                partial_calls += meta.get('partial_usage') is True
                states[row['state']] += 1
                if row['error_code']:
                    errors[row['error_code']] = errors.get(row['error_code'], 0) + 1
                if row['usage_json']:
                    data = json.loads(row['usage_json'])
                    missing = set(data['raw'].get('_mentorflow', {}).get('unreported_fields', []))
                    if missing:
                        note('厂商未返回部分用量字段')
                    unknown.update(missing)
                    if data['raw'].get('_mentorflow', {}).get('partial_usage'):
                        note('调用只返回部分用量')
                        unknown.update(FIELDS)
                    if data['raw'].get('_mentorflow', {}).get('token_total_reconciled') is False:
                        note('厂商返回的 token 总数不一致')
                        unknown.update(set(FIELDS) - {'search_requests'})
                    reported = _known_counters(data)
                    for key in FIELDS:
                        known[key] += reported[key]
                else:
                    unknown.update(FIELDS)
                    note('调用尚未结束' if row['state'] == 'in_progress' else '调用未返回用量')
                if row['quote'] is not None:
                    cost = row['quote']
                    is_estimated = cost.get('estimated', False)
                    target = estimated_amounts if is_estimated else amounts
                    target[cost['currency']] = target.get(cost['currency'], Decimal(0)) + Decimal(cost['amount'])
                    estimated += int(is_estimated)
                    priced += int(not is_estimated)
                    recalculated += int(row['recalculated'])
                else:
                    unpriced += 1
                    reason = row['unpriced_reason']
                    reasons[reason] = reasons.get(reason, 0) + 1
            counters = {key: None if key in unknown else value for key, value in known.items()}
            ratio = (Decimal(known['cached_input_tokens']) / Decimal(known['input_tokens'])
                     if not unknown & {'input_tokens', 'cached_input_tokens'} and known['input_tokens'] else None)
            if tokens_only:
                return dict(calls=len(items), **states, **counters, cache_hit_rate=ratio,
                            unknown_usage_calls=transport_unknown+cancelled_unknown,
                            transport_unknown_calls=transport_unknown, cancelled_unknown_calls=cancelled_unknown,
                            partial_usage_calls=partial_calls,
                            usage_complete=not unknown, unknown_fields=sorted(unknown),
                            incomplete_reasons=incomplete, errors=errors)
            return dict(calls=len(items), **states, **counters, cache_hit_rate=ratio,
                        unknown_usage_calls=transport_unknown+cancelled_unknown,
                        transport_unknown_calls=transport_unknown, cancelled_unknown_calls=cancelled_unknown,
                        partial_usage_calls=partial_calls,
                        usage_complete=not unknown, unknown_fields=sorted(unknown), known_tokens=known,
                        incomplete_reasons=incomplete,
                        amount=next(iter(amounts.values())) if not (unpriced or estimated) and len(amounts) == 1 else None,
                        currency=next(iter(amounts)) if not (unpriced or estimated) and len(amounts) == 1 else None,
                        amounts=dict(amounts) if not (unpriced or estimated) else None, known_amounts=amounts,
                        priced_calls=priced, estimated_calls=estimated, estimated_amounts=estimated_amounts,
                        unpriced_reasons=reasons, recalculated_calls=recalculated,
                        unpriced_calls=unpriced, amount_complete=not (unpriced or estimated), errors=errors)
        local_end = end.astimezone(timezone)
        return {'start': start.isoformat(), 'end': end.isoformat(),
                'timezone': getattr(timezone, 'key', None) or local_end.tzname(), 'group_by': group_by,
                'total': aggregate(rows), 'groups': [dict(key=key, **aggregate(items)) for key, items in sorted(groups.items())]}

    def details(self, start, end, *, offset=0, limit=50):
        """Page through safe call fields without exposing plans or billing context."""
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100:
            raise ModelError('USAGE_INVALID', '用量详情分页参数无效。')
        start, end = timestamp(start), timestamp(end)
        if start > end:
            raise ModelError('USAGE_TIME_INVALID', '详情结束时间不能早于开始时间。')
        bounds = (start.isoformat(timespec='microseconds'), end.isoformat(timespec='microseconds'))
        with self._db() as db:
            # Count and page share a read snapshot if a model call finishes meanwhile.
            db.execute('BEGIN')
            total = db.execute('SELECT COUNT(*) FROM calls WHERE at_utc>=? AND at_utc<?', bounds).fetchone()[0]
            rows = db.execute('''SELECT * FROM calls WHERE at_utc>=? AND at_utc<?
                                 ORDER BY at_utc DESC,request_id LIMIT ? OFFSET ?''',
                              (*bounds, limit, offset)).fetchall() if offset < total else []
        items = []
        for row in rows:
            data, quote, _ = self._quote(row)
            usage = None
            if data is not None:
                meta = data['raw'].get('_mentorflow', {})
                unknown = set(meta.get('unreported_fields', []))
                if meta.get('partial_usage'):
                    unknown.update(FIELDS)
                if meta.get('token_total_reconciled') is False:
                    unknown.update(set(FIELDS) - {'search_requests'})
                usage = {key: data[key] for key in FIELDS}
                usage.update(usage_complete=not unknown, unknown_fields=sorted(unknown),
                             partial_usage=meta.get('partial_usage') is True)
            item = {key: row[key] for key in ('request_id', 'at_utc', 'provider_id', 'model',
                                              'purpose', 'state', 'error_code')}
            item.update(usage=usage, cost=({'amount': format(Decimal(quote['amount']), 'f'), 'currency': quote['currency']}
                                          if quote is not None else None))
            items.append(item)
        return dict(total_calls=total, items=items,
                    next_offset=offset + len(items) if offset + len(items) < total else None)
