from datetime import datetime, timezone
from decimal import Decimal
import json
import sqlite3

import pytest

from refactor.learning.connections import usage_view
from refactor.learning.connections.balance import BalanceHistory
from refactor.learning.connections.errors import ModelError
from refactor.learning.connections.pricing import PriceCatalog
from refactor.learning.connections.usage import UsageLog
from .helpers import catalog, row, units


NOW = datetime(2026, 11, 6, 20, tzinfo=timezone.utc)


def test_account_balance_drop_never_becomes_application_cost(tmp_path):
    balances = BalanceHistory('student', tmp_path)
    for at, amount in [('2026-11-02T18:39:00Z', '69.99'), ('2026-11-06T19:48:00Z', '48.61')]:
        balances.record('preset', dict(updated_at=at, balances=[dict(currency='CNY', amount=amount)]))
    assert balances.summary('preset', now=NOW, tz=timezone.utc)['currencies'][0]['today'] == '21.38'
    log = UsageLog(tmp_path / 'usage.sqlite3', catalog=catalog(dict(row(), input_cache_miss_per_mtok='1')))
    empty = usage_view.overview(log, now=NOW, timezone=timezone.utc)
    assert all(w['total']['calls'] == 0 and w['total']['known_amounts'] == {} for w in empty['periods'].values())
    log.record('test', 'model', 'connection_test', units(input=15, cached=0, output=0), at=NOW)
    # The query is half-open: advance the observation past the recorded call.
    result = usage_view.overview(log, now=NOW.replace(second=1), timezone=timezone.utc)
    for window in result['periods'].values():
        assert window['total']['calls'] == 1
        assert window['total']['known_amounts'] == {'CNY': Decimal('.000015')}


def test_failed_known_missing_usage_and_prices_remain_distinct(tmp_path):
    log = UsageLog(tmp_path / 'usage.sqlite3', catalog=catalog())
    at = NOW.replace(minute=59, hour=19)
    log.record('test', 'model', 'teacher', units(), error_code='MODEL_BAD_RESPONSE', at=at)
    log.record('test', 'model', 'teacher', None, error_code='MODEL_TIMEOUT', at=at)
    log.record('test', 'unpriced', 'teacher', units(), at=at)
    log.record('test', 'model', 'teacher', units(raw={'_mentorflow': {'partial_usage': True}}),
               error_code='MODEL_CANCELLED', at=at)
    log.begin('test', 'model', 'teacher', at=at)
    total = usage_view.overview(log, now=NOW, timezone=timezone.utc)['periods']['today']['total']
    assert (total['calls'], total['failed'], total['cancelled'], total['in_progress']) == (5, 2, 1, 1)
    assert total['known_amounts'] == {'CNY': Decimal('.000288')}
    assert total['unpriced_calls'] == 4 and total['priced_calls'] == 1
    assert total['input_tokens'] is None and not total['usage_complete'] and not total['amount_complete']
    assert total['transport_unknown_calls'] == total['cancelled_unknown_calls'] == total['partial_usage_calls'] == 1
    assert total['incomplete_reasons']['调用只返回部分用量'] == 1


def test_details_preserve_snapshots_and_only_backfill_absent_quotes(tmp_path):
    path = tmp_path / 'usage.sqlite3'
    log = UsageLog(path, catalog=catalog(row(), dict(row(), model='usd', currency='USD')))
    at = NOW.replace(hour=19)
    for model in ('model', 'usd'):
        log.record('test', model, 'connection_test', units(), request_id=model, at=at)
    log.catalog = PriceCatalog.empty()
    log.record('test', 'late', 'teacher', units(), request_id='late', at=at)
    log.catalog = catalog(dict(row(), input_cache_miss_per_mtok='9'), dict(row(), model='late'))
    before = path.read_bytes()
    data = usage_view.details(log, 1, now=NOW, timezone=timezone.utc)
    costs = {item['request_id']: item['cost'] for item in data['items']}
    assert costs == {'model': {'amount': '0.000288', 'currency': 'CNY'},
                     'usd': {'amount': '0.000288', 'currency': 'USD'},
                     'late': {'amount': '0.000288', 'currency': 'CNY'}}
    assert path.read_bytes() == before
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT cost_json FROM calls WHERE request_id='late'").fetchone()[0] is None
    total = usage_view.summary(log, 1, 'model', now=NOW, timezone=timezone.utc)['total']
    assert total['known_amounts'] == {'CNY': Decimal('.000576'), 'USD': Decimal('.000288')}
    assert total['amount'] is None and total['currency'] is None


def test_details_paginate_stably_and_expose_only_safe_fields(tmp_path):
    log = UsageLog(tmp_path / 'usage.sqlite3', catalog=catalog())
    value = units(raw={'prompt': 'secret-content', 'url': 'https://secret.invalid',
                       '_mentorflow': {'billing_context': {'region': 'global'},
                                      'partial_usage': True}})
    for request_id, at in [('b', '2026-11-06T18:00:00Z'), ('a', '2026-11-06T18:00:00Z'),
                           ('new', '2026-11-06T19:00:00Z')]:
        log.record('test', 'model', 'teacher', value, plan_id='private-plan', request_id=request_id, at=at)
    first = usage_view.details(log, 1, limit=2, now=NOW, timezone=timezone.utc)
    second = usage_view.details(log, 1, offset=first['next_offset'], limit=2, now=NOW, timezone=timezone.utc)
    assert first['total_calls'] == second['total_calls'] == 3
    assert [i['request_id'] for i in first['items'] + second['items']] == ['new', 'a', 'b']
    assert second['next_offset'] is None
    for item in first['items']:
        assert set(item) == {'request_id', 'at_utc', 'provider_id', 'model', 'purpose', 'state', 'error_code', 'usage', 'cost'}
        assert set(item['usage']) == {'input_tokens', 'cached_input_tokens', 'uncached_input_tokens',
                                     'output_tokens', 'reasoning_tokens', 'search_requests',
                                     'usage_complete', 'unknown_fields', 'partial_usage'}
        assert not item['usage']['usage_complete'] and item['usage']['partial_usage']
        assert item['cost'] is None
    text = json.dumps(first)
    assert all(word not in text for word in ('private-plan', 'secret-content', 'secret.invalid', 'billing_context', 'source_urls'))
    assert log.details(first['start'], first['end'], offset=10**50)['items'] == []


@pytest.mark.parametrize('offset,limit', [(-1, 1), (True, 1), (1.0, 1), (0, 0), (0, 101), (0, False), (0, '50')])
def test_details_reject_invalid_pagination(tmp_path, offset, limit):
    log = UsageLog(tmp_path / 'usage.sqlite3')
    with pytest.raises(ModelError, match='分页参数'):
        log.details('2026-11-06T00:00:00Z', NOW, offset=offset, limit=limit)


def test_details_missing_usage_is_null(tmp_path):
    log = UsageLog(tmp_path / 'usage.sqlite3')
    log.record('test', 'model', 'teacher', None, error_code='MODEL_TIMEOUT', at=NOW.replace(hour=19))
    item = usage_view.details(log, 1, now=NOW, timezone=timezone.utc)['items'][0]
    assert item['usage'] is item['cost'] is None


def test_details_money_uses_decimal_strings_for_small_amounts(tmp_path):
    log = UsageLog(tmp_path / 'usage.sqlite3', catalog=catalog())
    log.record('test', 'model', 'connection_test', units(input=1, cached=1, output=0), at=NOW.replace(hour=19))
    item = usage_view.details(log, 1, now=NOW, timezone=timezone.utc)['items'][0]
    assert item['cost'] == {'amount': '0.0000002', 'currency': 'CNY'}


def test_fixed_details_end_keeps_pages_stable_when_calls_arrive(tmp_path):
    log = UsageLog(tmp_path / 'usage.sqlite3')
    start = '2026-11-06T00:00:00Z'
    for minute in (1, 2, 3):
        log.record('test', 'model', 'teacher', units(), request_id=str(minute),
                   at=f'2026-11-06T19:0{minute}:00Z')
    first = log.details(start, NOW, limit=2)
    log.record('test', 'model', 'teacher', units(), request_id='new', at=NOW.replace(minute=1))
    second = log.details(start, NOW, offset=first['next_offset'], limit=2)
    assert [i['request_id'] for i in first['items'] + second['items']] == ['3', '2', '1']
    assert first['total_calls'] == second['total_calls'] == 3
    assert second['next_offset'] is None


def test_details_continuation_keeps_window_when_midnight_passes(tmp_path):
    log = UsageLog(tmp_path / 'usage.sqlite3')
    for hour in (18, 19):
        log.record('test', 'model', 'teacher', units(), request_id=str(hour),
                   at=f'2026-11-06T{hour}:00:00Z')
    first = usage_view.details(log, 1, limit=1, now=NOW, timezone=timezone.utc)
    log.record('test', 'model', 'teacher', units(), request_id='new', at='2026-11-07T01:00:00Z')
    second = usage_view.details(log, 1, offset=first['next_offset'], limit=1, end=first['end'],
                                now='2026-11-07T02:00:00Z', timezone=timezone.utc)
    assert (first['start'], first['end']) == (second['start'], second['end'])
    assert [i['request_id'] for i in first['items'] + second['items']] == ['19', '18']
    assert first['total_calls'] == second['total_calls'] == 2 and second['next_offset'] is None


@pytest.mark.parametrize('end', ['not-a-time', '2026-11-06T10:00:00', '2026-11-06T21:00:00Z'])
def test_details_reject_invalid_or_future_end(tmp_path, end):
    log = UsageLog(tmp_path / 'usage.sqlite3')
    with pytest.raises(ModelError) as error:
        usage_view.details(log, 1, end=end, now=NOW, timezone=timezone.utc)
    assert error.value.code == 'USAGE_TIME_INVALID'
