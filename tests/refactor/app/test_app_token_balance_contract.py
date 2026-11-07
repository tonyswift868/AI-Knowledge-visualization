import json
from types import SimpleNamespace

from refactor.learning.app import handlers
from refactor.learning.connections.usage import UsageLog
from refactor.learning.connections.providers import Usage
from refactor.learning.connections.pricing import timestamp
from .test_app_window_layer import qapp, make_bridge, pump, _Window


def test_bridge_usage_amounts_and_vendor_balance_separate(qapp, tmp_path):
    log = UsageLog(tmp_path / 'usage.sqlite3')
    log.record('deepseek', 'flash', 'teacher', Usage(100, 40, 60, 20, 0, 3, {}), at=timestamp())
    seen = []
    def balance(preset_id, **kwargs):
        seen.append((preset_id, kwargs['refresh'], kwargs['cancel']))
        return dict(label='账户余额', balances=[dict(currency='CNY', amount='0')], updated_at='2026-09-28T00:00:00Z')
    window = _Window(SimpleNamespace(gateway=SimpleNamespace(usage_log=log, balance=balance)))
    bridge, sent = make_bridge(qapp)
    handlers.register(bridge, window)
    try:
        bridge.call(json.dumps(dict(id='tokens', method='usage.summary', params=dict(days=7, group_by='day'))))
        reply = pump(qapp, bridge, sent, lambda m: m.get('id') == 'tokens')[0]
        assert reply['ok'] and reply['result']['total']['uncached_input_tokens'] == 60
        assert reply['result']['total']['unpriced_calls'] == 1
        assert reply['result']['total']['known_amounts'] == {}
        assert reply['result']['total']['amount_complete'] is False
        bridge.call(json.dumps(dict(id='balance', method='models.balance', params=dict(preset_id='p1', refresh=True))))
        reply = pump(qapp, bridge, sent, lambda m: m.get('id') == 'balance')[0]
        assert reply['result']['balance']['balances'][0]['amount'] == '0'
        assert seen[0][:2] == ('p1', True) and hasattr(seen[0][2], 'is_set')
        bridge.call(json.dumps(dict(id='bad', method='models.balance', params=dict(preset_id='p1', refresh='true'))))
        reply = pump(qapp, bridge, sent, lambda m: m.get('id') == 'bad')[0]
        assert reply['error']['code'] == 'PARAM_INVALID'
        assert len(seen) == 1
    finally:
        bridge.close()
