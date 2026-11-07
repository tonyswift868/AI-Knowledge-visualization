"""Independent bridge acceptance: native local dates and anchored continuation."""
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace

import pytest

from refactor.learning.app import handlers
from refactor.learning.connections import usage_view
from refactor.learning.connections.usage import UsageLog
from tests.refactor.app.test_app_window_layer import qapp, make_bridge, pump, _Window
from .helpers import catalog, units


@pytest.mark.parametrize('day', ['2026-03-08', '2026-11-01', '2026-11-06'])
def test_native_calendar_and_bridge_paging_survive_midnight(qapp, tmp_path, monkeypatch, day):
    # Use native OS conversion for each boundary, without changing its timezone
    # or clock. This exercises the production timezone=None path on Windows too.
    local_day = datetime.fromisoformat(day)
    start = local_day.astimezone()
    next_start = (local_day + timedelta(days=1)).astimezone()
    clock = [(local_day + timedelta(hours=23, minutes=59)).astimezone()]
    real_timestamp = usage_view.timestamp
    monkeypatch.setattr(usage_view, 'timestamp',
                        lambda value=None: real_timestamp(clock[0] if value is None else value))
    log = UsageLog(tmp_path / 'journal.sqlite3', catalog=catalog())
    for request_id, at in [('outside', start.astimezone(timezone.utc) - timedelta(seconds=1)),
                           ('old', start), ('b', clock[0] - timedelta(minutes=1)),
                           ('a', clock[0] - timedelta(minutes=1))]:
        log.record('test', 'model', 'connection_test', units(), request_id=request_id, at=at)
    bridge, sent = make_bridge(qapp)
    handlers.register(bridge, _Window(SimpleNamespace(gateway=SimpleNamespace(usage_log=log))))

    def ask(method, **params):
        call_id = f'calendar-{len(sent)}'
        bridge.call(json.dumps(dict(id=call_id, method=method, params=params)))
        reply = pump(qapp, bridge, sent,
                     lambda m: m.get('id') == call_id and m['type'] == 'response')[0]
        assert reply['ok'], reply
        return reply['result']

    try:
        overview = ask('usage.overview')['periods']
        summary = ask('usage.summary', days=1, group_by='day')
        first = ask('usage.details', days=1, limit=2)
        assert first['start'] == summary['start'] == overview['today']['start'] == start.isoformat()
        assert first['end'] == summary['end'] == overview['today']['end'] == clock[0].isoformat()
        assert summary['total']['calls'] == first['total_calls'] == overview['today']['total']['calls'] == 3
        assert [(g['key'], g['calls']) for g in summary['groups']] == [(day, 3)]
        assert overview['week']['start'] == (local_day - timedelta(days=local_day.weekday())).astimezone().isoformat()
        assert overview['month']['start'] == local_day.replace(day=1).astimezone().isoformat()
        assert len({w['end'] for w in overview.values()}) == 1

        # A new call after page one's end must not displace any old row, even
        # when the next request happens on the following local calendar day.
        log.record('test', 'model', 'teacher', units(), request_id='new-day', at=next_start)
        clock[0] = next_start + timedelta(minutes=2)
        second = ask('usage.details', days=1, limit=2, offset=first['next_offset'], end=first['end'])
        assert (second['start'], second['end']) == (first['start'], first['end'])
        assert first['total_calls'] == second['total_calls'] == 3
        assert [r['request_id'] for r in first['items'] + second['items']] == ['a', 'b', 'old']
        assert second['next_offset'] is None
        fresh = ask('usage.details', days=1, limit=2)
        assert fresh['start'] == next_start.isoformat()
        assert fresh['total_calls'] == 1 and fresh['items'][0]['request_id'] == 'new-day'
        assert ask('usage.overview')['periods']['today']['total']['calls'] == 1
    finally:
        bridge.close()
