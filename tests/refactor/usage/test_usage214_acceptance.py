"""Real bridge-to-account-journal acceptance checks for BUG-214."""
from datetime import datetime, timedelta, timezone
import json
import sqlite3
from types import SimpleNamespace

from refactor.learning.app import handlers
from refactor.learning.connections import usage_view
from refactor.learning.connections.balance import BalanceHistory
from refactor.learning.connections.pricing import timestamp
from refactor.learning.connections.providers import Usage
from refactor.learning.connections.usage import UsageLog
from tests.refactor.usage.helpers import catalog, row, units
from tests.refactor.app.test_app_window_layer import qapp, make_bridge, pump, _Window


UTC = timezone.utc
FROZEN = datetime(2026, 11, 6, 20, 0, tzinfo=UTC)


def _freeze_view_clock(monkeypatch):
    real_timestamp = timestamp
    monkeypatch.setattr(usage_view, 'timestamp',
                        lambda value=None: real_timestamp(FROZEN if value is None else value))
    return FROZEN.astimezone()


def _request(qapp, bridge, sent, method, params, request_id):
    bridge.call(json.dumps(dict(id=request_id, method=method, params=params)))
    return pump(qapp, bridge, sent, lambda message: message.get('id') == request_id)[0]


def _price_row(**changes):
    value = row()
    value.update(changes)
    return value


def test_real_bridge_keeps_cross_day_vendor_balance_out_of_today_usage(qapp, tmp_path, monkeypatch):
    local_now = _freeze_view_clock(monkeypatch)
    balances = BalanceHistory('current-account', tmp_path)
    for at, amount in [('2026-11-02T18:39:00Z', '69.99'), ('2026-11-06T19:48:00Z', '48.61')]:
        balances.record('preset', dict(updated_at=at, balances=[dict(currency='CNY', amount=amount)]))
    assert balances.summary('preset', now=FROZEN)['currencies'][0]['today'] == '21.38'
    balance_before = balances.path.read_bytes()
    today_call = local_now.replace(hour=11, minute=0, second=0, microsecond=0)
    log = UsageLog(tmp_path / 'current-account.sqlite3', catalog=catalog(
        _price_row(input_cache_hit_per_mtok='0', input_cache_miss_per_mtok='1.5', output_per_mtok='0', search_per_request='0')
    ))
    log.record('test', 'model', 'connection_test', Usage(10, 0, 10, 0, 0, 0, {}),
               request_id='only-today-connection-test', at=today_call.isoformat())

    # The account's balance history is deliberately external to the usage journal.
    # A handler that tries to consult it would fail this test immediately.
    gateway = SimpleNamespace(usage_log=log, balance_history=balances,
                              balance=lambda *args, **kwargs: (_ for _ in ()).throw(
        AssertionError('usage overview queried vendor balance')))
    window = _Window(SimpleNamespace(account_id='current-account', gateway=gateway))
    bridge, sent = make_bridge(qapp)
    handlers.register(bridge, window)
    try:
        reply = _request(qapp, bridge, sent, 'usage.overview', {}, 'cross-day-overview')
        assert reply['ok'], reply
        today = reply['result']['periods']['today']['total']
        assert today['calls'] == 1
        assert today['known_amounts'] == {'CNY': '0.000015'}
        assert today['estimated_amounts'] == {}
        assert today['unknown_usage_calls'] == 0
        assert today['unpriced_calls'] == 0
        assert all(w['total']['known_amounts'] == {'CNY': '0.000015'}
                   for w in reply['result']['periods'].values())
        details = _request(qapp, bridge, sent, 'usage.details', {'days': 1}, 'cross-day-details')
        assert details['ok'] and details['result']['total_calls'] == 1, details
        assert details['result']['items'][0]['cost'] == {'amount': '0.000015', 'currency': 'CNY'}
        assert '21.38' not in json.dumps([reply, details])
        assert balances.path.read_bytes() == balance_before
    finally:
        bridge.close()


def test_real_bridge_details_keep_account_scope_currencies_snapshots_and_safe_fields(
        qapp, tmp_path, monkeypatch):
    local_now = _freeze_view_clock(monkeypatch)
    today = local_now.replace(hour=11, minute=0, second=0, microsecond=0)
    prices = catalog(
        _price_row(input_cache_hit_per_mtok='0', input_cache_miss_per_mtok='1.5', output_per_mtok='0', search_per_request='0'),
        _price_row(model='usd-model', currency='USD', input_cache_hit_per_mtok='0',
                   input_cache_miss_per_mtok='3', output_per_mtok='0', search_per_request='0'),
    )
    current_log = UsageLog(tmp_path / 'current-account.sqlite3', catalog=prices)
    secret = 'sk-private-prompt-must-not-cross-bridge'
    current_log.record('test', 'model', 'connection_test',
                       units(input=10, cached=0, output=0, raw={'prompt': secret, 'url': secret,
                       '_mentorflow': {'api_key': secret}}), request_id='cny-known', at=today.isoformat())
    current_log.record('test', 'model', 'teacher', units(input=10, cached=0, output=0),
                       request_id='cny-failed-known', at=(today - timedelta(minutes=1)).isoformat(),
                       error_code='MODEL_TIMEOUT')
    current_log.record('test', 'usd-model', 'teacher', units(input=10, cached=0, output=0),
                       request_id='usd-known', at=(today - timedelta(minutes=2)).isoformat())
    current_log.record('test', 'model', 'teacher',
                       units(input=10, cached=0, output=0,
                             raw={'_mentorflow': {'partial_usage': True, 'unreported_fields': ['output_tokens']}}),
                       request_id='cny-partial', at=(today - timedelta(minutes=3)).isoformat())
    current_log.record('test', 'model', 'teacher', None, request_id='unknown-cost',
                       at=(today - timedelta(minutes=4)).isoformat(), error_code='MODEL_CONNECTION_FAILED')

    other_log = UsageLog(tmp_path / 'other-account.sqlite3', catalog=prices)
    other_log.record('test', 'model', 'teacher', units(input=100_000, cached=0, output=0),
                     request_id='other-account-call', at=today.isoformat())
    current = SimpleNamespace(account_id='current-account', _closed=True,
                              gateway=SimpleNamespace(usage_log=current_log))
    window = _Window(current)
    bridge, sent = make_bridge(qapp)
    handlers.register(bridge, window)
    try:
        overview = _request(qapp, bridge, sent, 'usage.overview', {}, 'current-overview')
        assert overview['ok'], overview
        total = overview['result']['periods']['today']['total']
        assert total['calls'] == 5 and total['failed'] == 2
        assert total['known_amounts'] == {'CNY': '0.000030', 'USD': '0.00003'}
        assert total['estimated_amounts'] == {}
        assert total['unpriced_calls'] == 2
        assert total['partial_usage_calls'] == 1

        summary = _request(qapp, bridge, sent, 'usage.summary',
                           {'days': 1, 'group_by': 'purpose'}, 'current-summary')
        assert summary['ok'], summary
        assert summary['result']['total']['calls'] == 5
        assert summary['result']['total']['known_amounts'] == {'CNY': '0.000030', 'USD': '0.00003'}
        assert summary['result']['total']['estimated_amounts'] == {}

        # A later account switch changes the selected journal; it never mixes files.
        window.services = SimpleNamespace(account_id='other-account', _closed=True,
                                          gateway=SimpleNamespace(usage_log=other_log))
        switched = _request(qapp, bridge, sent, 'usage.overview', {}, 'other-overview')
        assert switched['ok'], switched
        assert switched['result']['periods']['today']['total']['calls'] == 1
        assert switched['result']['periods']['today']['total']['known_amounts'] == {'CNY': '0.15'}

        # Switch back and inspect the real details response's strict field allowlist.
        window.services = current
        details = _request(qapp, bridge, sent, 'usage.details',
                           {'days': 1, 'offset': 0, 'limit': 10}, 'current-details')
        assert details['ok'], details
        result = details['result']
        assert result['total_calls'] == 5 and result['next_offset'] is None
        expected = {'request_id', 'at_utc', 'provider_id', 'model', 'purpose', 'state',
                    'error_code', 'usage', 'cost'}
        for call in result['items']:
            assert set(call) == expected
        assert secret not in json.dumps(result)
        by_id = {item['request_id']: item for item in result['items']}
        assert by_id['cny-failed-known']['state'] == 'failed'
        assert by_id['cny-failed-known']['cost'] == {'amount': '0.000015', 'currency': 'CNY'}
        assert by_id['unknown-cost']['cost'] is None and by_id['unknown-cost']['usage'] is None
        assert by_id['usd-known']['cost'] == {'amount': '0.00003', 'currency': 'USD'}
        partial = by_id['cny-partial']['usage']
        assert partial['partial_usage'] is True and not partial['usage_complete']
        assert 'output_tokens' in partial['unknown_fields']

        with sqlite3.connect(current_log.path) as db:
            snapshot_before = db.execute('SELECT request_id,cost_json FROM calls ORDER BY request_id').fetchall()
        current_log.catalog = catalog(_price_row(input_cache_hit_per_mtok='0', input_cache_miss_per_mtok='99',
                                                 output_per_mtok='0', search_per_request='0'))
        again = _request(qapp, bridge, sent, 'usage.details',
                         {'days': 1, 'offset': 0, 'limit': 10}, 'current-details-after-price-change')
        assert again['ok'], again
        assert {item['request_id']: item['cost'] for item in again['result']['items']} == {
            item['request_id']: item['cost'] for item in result['items']}
        with sqlite3.connect(current_log.path) as db:
            assert db.execute('SELECT request_id,cost_json FROM calls ORDER BY request_id').fetchall() == snapshot_before
    finally:
        bridge.close()
