from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
import json
import sqlite3

import pytest

from refactor.learning.connections.errors import ModelError
from refactor.learning.connections.usage import UsageLog
from .helpers import AT, START, END, catalog, row, units


def test_idempotency_conflict_and_reopen(tmp_path):
    path = tmp_path / 'usage.sqlite3'
    log = UsageLog(path, catalog=catalog())
    assert log.record('test', 'model', 'plan', units(), request_id='same', at=AT) == 'same'
    log.record('test', 'model', 'plan', units(), request_id='same', at=AT)
    log.record('test', 'model', 'plan', units(), request_id='same')
    with pytest.raises(ModelError) as exc:
        log.record('test', 'model', 'teacher', units(), request_id='same', at=AT)
    assert exc.value.code == 'USAGE_RECORD_CONFLICT'
    reopened = UsageLog(path)
    assert reopened.summary(START, END)['total']['calls'] == 1
    assert reopened.summary(START, END)['total']['amount'] == Decimal('.000288')
    assert log.record('test', 'model', 'plan', units(), at=AT) != log.record('test', 'model', 'plan', units(), at=AT)


def test_parallel_sqlite_writers_idempotent(tmp_path):
    path = tmp_path / 'usage.sqlite3'
    UsageLog(path)
    def write(i):
        log = UsageLog(path)
        log.record('test', 'model', 'plan', units(), request_id='same', at=AT)
        log.record('test', 'model', 'plan', units(), request_id=str(i), at=AT)
    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(write, range(20)))
    assert UsageLog(path).summary(START, END)['total']['calls'] == 21


def test_dimensions_weighted_ratio_and_half_open_utc_days(tmp_path):
    log = UsageLog(tmp_path / 'usage.sqlite3', catalog=catalog())
    log.record('test', 'model', 'plan', units(input=100, cached=80), plan_id='plan-1', at='2026-09-01T00:00:00Z')
    log.record('test', 'model', 'teacher', units(input=900, cached=90), at='2026-09-02T08:59:00+09:00')
    log.record('test', 'other', 'teacher', units(), at=END)  # excluded
    total = log.summary(START, END)['total']
    assert total['calls'] == 2
    assert (total['input_tokens'], total['cached_input_tokens'], total['uncached_input_tokens'], total['output_tokens']) == (1000, 170, 830, 40)
    assert total['cache_hit_rate'] == Decimal('.17')
    assert [g['key'] for g in log.summary(START, END)['groups']] == ['plan', 'teacher']
    assert [g['key'] for g in log.summary(START, END, 'model')['groups']] == [('test', 'model')]
    days = log.summary(START, END, 'day')['groups']
    assert len(days) == 1 and days[0]['key'] == '2026-09-01'
    assert log.summary(START, END)['total']['amount_complete']


def test_failures_pending_unknowns_and_known_subtotals_are_distinct(tmp_path):
    log = UsageLog(tmp_path / 'usage.sqlite3', catalog=catalog())
    log.record('test', 'model', 'plan', units(), at=AT)
    log.record('test', 'model', 'plan', None, error_code='MODEL_TIMEOUT', at=AT)
    pending = log.begin('test', 'model', 'plan', at=AT)
    data = log.summary(START, END)['total']
    assert data['calls'] == 3 and data['succeeded'] == data['failed'] == data['in_progress'] == 1
    assert data['input_tokens'] is data['amount'] is data['cache_hit_rate'] is None
    assert not data['usage_complete'] and not data['amount_complete']
    assert data['known_tokens']['input_tokens'] == 100 and data['unpriced_calls'] == 2
    assert data['known_amounts'] == {'CNY': Decimal('.000288')}
    assert data['errors'] == {'MODEL_TIMEOUT': 1}
    log.finish(pending, units(), error_code='MODEL_CANCELLED')
    assert log.summary(START, END)['total']['cancelled'] == 1
    assert log.summary(START, END)['total']['known_tokens']['input_tokens'] == 200


def test_vendor_unreported_cache_does_not_become_zero_hit_rate(tmp_path):
    log = UsageLog(tmp_path / 'usage.sqlite3', catalog=catalog())
    log.record('test', 'model', 'plan', units(cached=0, raw={'_mentorflow': {'unreported_fields': ['cached_input_tokens', 'uncached_input_tokens']}}), at=AT)
    total = log.summary(START, END)['total']
    assert total['input_tokens'] == 100 and total['cached_input_tokens'] is None
    assert total['cache_hit_rate'] is total['amount'] is None


def test_unreconciled_token_total_is_not_reported_as_complete(tmp_path):
    log = UsageLog(tmp_path / 'usage.sqlite3', catalog=catalog())
    log.record('test', 'model', 'plan', units(raw={'_mentorflow': {'token_total_reconciled': False}}), at=AT)
    total = log.summary(START, END)['total']
    assert total['known_tokens']['input_tokens'] == 100
    assert total['input_tokens'] is total['amount'] is None and not total['usage_complete']
    assert total['search_requests'] == 0


def test_multicurrency_never_summed_and_prices_never_rewritten(tmp_path):
    other = dict(row(), model='usd', currency='USD')
    log = UsageLog(tmp_path / 'usage.sqlite3', catalog=catalog(row(), other))
    for model in ('model', 'usd'):
        log.record('test', model, 'plan', units(), at=AT, request_id=model)
    total = log.summary(START, END)['total']
    assert total['amount_complete'] and total['amount'] is total['currency'] is None
    assert total['amounts'] == {'CNY': Decimal('.000288'), 'USD': Decimal('.000288')}
    log.catalog = catalog(dict(row(), output_per_mtok=800))
    log.record('test', 'model', 'plan', units(), at=AT, request_id='model')
    assert log.summary(START, END)['total']['amounts'] == total['amounts']


def test_finish_once_real_response_model_and_storage_is_private(tmp_path):
    path = tmp_path / 'usage.sqlite3'
    log = UsageLog(path, catalog=catalog())
    request = log.begin('test', 'alias', 'plan', at=AT, plan_id='plan-1')
    secret = 'sk-DO-NOT-PERSIST'
    value = units(raw={'prompt': secret, 'query': secret, 'input_tokens': 100,
                       '_mentorflow': {'search_actions': [{'type': 'search', 'query': secret, 'sources': [secret]}],
                                      'billing_context': {'region': 'global', 'api_key': secret}, 'grounding_metadata': secret}})
    log.finish(request, value, model='model')
    log.finish(request, value, model='model')
    with pytest.raises(ModelError):
        log.finish(request, units(input=200))
    assert secret.encode() not in path.read_bytes()
    assert log.summary(START, END, 'model')['groups'][0]['key'] == ('test', 'model')
    with sqlite3.connect(path) as db:
        saved = json.loads(db.execute('SELECT usage_json FROM calls').fetchone()[0])
    assert saved['raw']['input_tokens'] == 100
    assert saved['raw']['_mentorflow']['search_actions'] == [{'type': 'search'}]


@pytest.mark.parametrize('payload', [b'not a database', None])
def test_storage_error_not_empty_success(tmp_path, payload):
    path = tmp_path / 'usage.sqlite3'
    if payload:
        path.write_bytes(payload)
    else:
        with sqlite3.connect(path) as db:
            db.execute('PRAGMA user_version=99')
    with pytest.raises(ModelError) as exc:
        UsageLog(path)
    assert exc.value.code == 'USAGE_STORAGE_ERROR'


def test_invalid_records_and_ranges(tmp_path):
    log = UsageLog(tmp_path / 'usage.sqlite3')
    for usage in [None, units(raw={'_mentorflow': 'bad'}), units(raw={'_mentorflow': {'unreported_fields': 'bad'}})]:
        with pytest.raises(ModelError):
            log.record('test', 'model', 'plan', usage, at=AT)
    with pytest.raises(ModelError):
        log.summary('2026-09-01', END)
    with pytest.raises(ModelError):
        log.summary(END, START)
    with pytest.raises(ModelError):
        log.summary(START, END, 'account')
    assert log.summary(START, END)['total']['calls'] == 0
