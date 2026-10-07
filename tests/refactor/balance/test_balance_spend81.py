"""Offline balance-delta accounting and window-lifetime scheduling contracts."""
from datetime import datetime, timedelta, timezone
import json
import threading
import time
from types import SimpleNamespace

import httpx
import pytest
from PySide6.QtWidgets import QApplication

from refactor.learning.connections.balance import BalanceHistory, BalanceClient, register
from refactor.learning.connections.gateway import ModelGateway
from refactor.learning.connections import credentials
from refactor.learning.app.bridge import AppBridge, CallContext

TZ = timezone(timedelta(hours=8))


def observation(amount, at, currency='CNY'):
    return dict(updated_at=at, balances=[dict(currency=currency, amount=amount)])


def test_spend_recharge_day_week_month_and_duplicate(tmp_path):
    history = BalanceHistory('student', tmp_path)
    # Sunday -> Monday crosses week and month; recharge never offsets spend.
    for amount, at in [('100', '2026-08-30T20:00:00+08:00'), ('97.25', '2026-08-31T00:10:00+08:00'),
                       ('150', '2026-08-31T09:00:00+08:00'), ('148.10', '2026-09-01T08:00:00+08:00')]:
        assert history.record('p', observation(amount, at))
    history.record('p', observation('148.10', '2026-09-01T08:00:00+08:00'))
    summary = history.summary('p', now=datetime(2026, 9, 1, 23, tzinfo=TZ), tz=TZ)
    c = summary['currencies'][0]
    assert summary['ready'] and summary['observations'] == 4
    assert c['today'] == '1.90' and c['week'] == '4.65' and c['month'] == '1.90'
    assert len(c['days']) == 14 and c['days'][-2] == dict(date='2026-08-31', amount='2.75')
    # Changing local date re-buckets without requiring another fetch.
    assert history.summary('p', now=datetime(2026, 9, 2, 1, tzinfo=TZ), tz=TZ)['currencies'][0]['today'] == '0'


def test_single_record_failure_account_preset_currency_and_series_isolation(tmp_path):
    history = BalanceHistory('student', tmp_path)
    assert history.summary('p')['observations'] == 0 and not history.path.exists()
    assert not history.record('p', None)
    history.record('p', observation('50', '2026-10-08T00:00:00Z'), series='key1')
    assert not history.summary('p', now=datetime(2026, 10, 8, 12, tzinfo=TZ))['ready']
    history.record('p', observation('10', '2026-10-08T01:00:00Z'), series='key2')
    history.record('p', observation('9.9999', '2026-10-08T02:00:00Z'), series='key2')
    history.record('p', observation('2', '2026-10-08T03:00:00Z', 'USD'), series='key2')
    history.record('q', observation('999', '2026-10-08T04:00:00Z'))
    result = history.summary('p', now=datetime(2026, 10, 8, 20, tzinfo=TZ), tz=TZ)
    assert result['currencies'][0]['today'] == '0.0001'
    assert not result['currencies'][1]['ready']
    assert BalanceHistory('other', tmp_path).summary('p')['observations'] == 0
    assert BalanceHistory('student', tmp_path).summary('p', now=datetime(2026, 10, 8, 20, tzinfo=TZ))['ready']


def test_query_writes_and_failure_leaves_history_and_settings(tmp_path, monkeypatch):
    class Vault:
        values = {}
        def get_password(self, service, name): return self.values.get((service, name))
        def set_password(self, service, name, value): self.values[service, name] = value
        def delete_password(self, service, name): self.values.pop((service, name), None)
    monkeypatch.setattr(credentials, '_get_keyring', lambda: Vault())
    gateway = ModelGateway('student', tmp_path)
    preset = gateway.save_preset('deepseek', api_key='fake-key')
    before = gateway._storage.path.read_bytes()
    gateway._balance_client = BalanceClient(http_client=httpx.MockTransport(lambda req: httpx.Response(200,
        json={'is_available': True, 'balance_infos': [{'currency': 'CNY', 'total_balance': '20.25'}]})))
    value = gateway.balance(preset['id'])
    assert value and gateway.balance_history.summary(preset['id'])['observations'] == 1
    gateway.balance(preset['id'])  # cache is one observation, not a fabricated sample
    assert gateway.balance_history.summary(preset['id'])['observations'] == 1
    gateway._balance_client = BalanceClient(http_client=httpx.MockTransport(lambda req: httpx.Response(503)))
    assert gateway.balance(preset['id'], refresh=True) is None
    assert gateway.balance_history.summary(preset['id'])['observations'] == 1
    assert gateway._storage.path.read_bytes() == before
    gateway.close()


def test_clock_completion_hooks_nonblocking_failure_and_close(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    bridge = AppBridge()
    seen = []
    done = threading.Event()
    preset = dict(id='p', has_api_key=True, provider_id='deepseek', base_url='https://api.deepseek.com')
    def query(identifier, **kwargs):
        seen.append((identifier, threading.get_ident(), kwargs['refresh']))
        done.set()
        return None
    gateway = SimpleNamespace(get_configuration=lambda: preset, balance=query,
                              list_presets=lambda: [preset], balance_history=BalanceHistory('student', tmp_path))
    current = SimpleNamespace(account_id='student', gateway=gateway, _closed=False)
    clock = [10.]
    monkeypatch.setattr('refactor.learning.connections.balance.time.monotonic', lambda: clock[0])
    tracker = register(bridge, lambda: current)
    def finish():
        assert done.wait(2)
        deadline = time.perf_counter() + 2
        while tracker.busy and time.perf_counter() < deadline: time.sleep(.001)
        done.clear()
    tracker.tick(); finish()
    assert len(seen) == 1 and seen[0][1] != threading.get_ident()
    clock[0] = 3609; tracker.tick(); assert len(seen) == 1
    clock[0] = 3610; tracker.tick(); finish(); assert len(seen) == 2
    for method in ['classroom.leave', 'plan.generate', 'plan.add_generate']:
        tracker.completed(method); finish()
    assert len(seen) == 5
    tracker.completed('classroom.reply'); assert len(seen) == 5
    def event(state):
        tracker.on_event(json.dumps(dict(type='event', name='plan_preparation',
                     data=dict(plan_id='plan', module_id='m', done=1, total=5, status=state))))
    event('running'); assert len(seen) == 5
    event('ready'); finish(); assert len(seen) == 6
    event('ready'); assert len(seen) == 6
    event('running'); event('failed'); finish(); assert len(seen) == 7
    result = bridge._handlers['usage.spend'].function({}, CallContext('spend', threading.Event(), lambda _: None))
    assert result['spend']['observations'] == 0
    with pytest.raises(Exception) as invalid:
        bridge._handlers['usage.spend'].function({'refresh': 'yes'}, None)
    assert invalid.value.code == 'PARAM_INVALID'
    bridge.close(); tracker.completed('plan.generate'); tracker.tick()
    assert len(seen) == 7 and not tracker.timer.isActive()
    app.processEvents()


def test_bridge_completion_signal_includes_failure_but_not_cancelled_calls():
    app = QApplication.instance() or QApplication([])
    bridge = AppBridge()
    seen = []
    bridge.request_completed.connect(seen.append)
    bridge.register('plan.generate', lambda p, c: {'ok': True})
    bridge.register('classroom.leave', lambda p, c: (_ for _ in ()).throw(ValueError()))
    for method in ['plan.generate', 'classroom.leave']:
        bridge._run('x', bridge._handlers[method], {}, CallContext('x', threading.Event(), lambda _: None))
    assert seen == ['plan.generate', 'classroom.leave']
    cancel = threading.Event(); cancel.set()
    bridge._run('x', bridge._handlers['plan.generate'], {}, CallContext('x', cancel, lambda _: None))
    assert seen == ['plan.generate', 'classroom.leave']
    bridge.close(); app.processEvents()
