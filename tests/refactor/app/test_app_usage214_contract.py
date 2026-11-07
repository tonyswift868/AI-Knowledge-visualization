import json
from types import SimpleNamespace

import pytest

from refactor.learning.app import handlers
from refactor.learning.connections import usage_view
from refactor.learning.connections.pricing import timestamp
from refactor.learning.connections.usage import UsageLog
from tests.refactor.usage.helpers import catalog, units
from .test_app_window_layer import qapp, make_bridge, pump, _Window


@pytest.mark.parametrize('method,params', [
    ('usage.summary', {'days': True}), ('usage.summary', {'days': 7.0}),
    ('usage.summary', {'days': 2}), ('usage.summary', {'group_by': []}),
    ('usage.details', {'days': '7'}), ('usage.details', {'offset': -1}),
    ('usage.details', {'offset': True}), ('usage.details', {'offset': 1.5}),
    ('usage.details', {'limit': 0}), ('usage.details', {'limit': 101}),
    ('usage.details', {'limit': False}), ('usage.details', {'limit': '50'}),
    ('usage.details', {'end': None}), ('usage.details', {'end': 123}),
])
def test_usage_bridge_validates_before_accessing_account(qapp, method, params):
    bridge, sent = make_bridge(qapp)
    handlers.register(bridge, _Window(None))
    try:
        bridge.call(json.dumps(dict(id='invalid-usage', method=method, params=params)))
        reply = pump(qapp, bridge, sent, lambda m: m.get('id') == 'invalid-usage')[0]
        assert reply['error']['code'] == 'PARAM_INVALID'
    finally:
        bridge.close()


def test_usage_methods_round_trip_json_safe_money(qapp, tmp_path, monkeypatch):
    now = timestamp('2026-11-06T20:00:00Z')
    actual = usage_view.timestamp
    monkeypatch.setattr(usage_view, 'timestamp', lambda value=None: actual(now if value is None else value))
    log = UsageLog(tmp_path / 'usage.sqlite3', catalog=catalog())
    log.record('test', 'model', 'connection_test', units(), at='2026-11-06T19:00:00Z')
    window = _Window(SimpleNamespace(gateway=SimpleNamespace(usage_log=log)))
    bridge, sent = make_bridge(qapp)
    handlers.register(bridge, window)
    try:
        for method, params in [('usage.overview', {}), ('usage.summary', {'days': 1, 'group_by': 'day'}),
                               ('usage.details', {'days': 1, 'offset': 0, 'limit': 1})]:
            call_id = method.replace('.', '-')
            bridge.call(json.dumps(dict(id=call_id, method=method, params=params)))
            reply = pump(qapp, bridge, sent, lambda m: m.get('id') == call_id)[0]
            assert reply['ok'], reply
            result = reply['result']
            assert result['timezone']
            if method == 'usage.overview':
                assert set(result) == {'timezone', 'periods'}
                assert set(result['periods']) == {'today', 'week', 'month'}
                assert len({w['end'] for w in result['periods'].values()}) == 1
                totals = [w['total'] for w in result['periods'].values()]
            elif method == 'usage.summary':
                totals = [result['total'], *result['groups']]
                assert result['start'].split('T')[1].startswith('00:00:00')
            else:
                assert result['total_calls'] == 1 and result['next_offset'] is None
                assert result['items'][0]['cost'] == {'amount': '0.000288', 'currency': 'CNY'}
                continue
            for total in totals:
                assert total['calls'] == 1
                assert total['known_amounts'] == {'CNY': '0.000288'}
                assert total['incomplete_reasons'] == {}
    finally:
        bridge.close()


@pytest.mark.parametrize('end', ['bad-time', '2026-11-06T10:00:00', '2099-01-01T00:00:00Z'])
def test_details_bridge_rejects_invalid_end(qapp, tmp_path, end):
    log = UsageLog(tmp_path / 'usage.sqlite3')
    bridge, sent = make_bridge(qapp)
    handlers.register(bridge, _Window(SimpleNamespace(gateway=SimpleNamespace(usage_log=log))))
    try:
        bridge.call(json.dumps(dict(id='end-check', method='usage.details', params=dict(days=1, end=end))))
        reply = pump(qapp, bridge, sent, lambda m: m.get('id') == 'end-check')[0]
        assert reply['error']['code'] == 'PARAM_INVALID'
    finally:
        bridge.close()
