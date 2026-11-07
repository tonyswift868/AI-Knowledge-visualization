from datetime import timezone
from zoneinfo import ZoneInfo

from refactor.learning.connections.usage import UsageLog
from refactor.learning.connections import usage_view
from .helpers import catalog, units


DENVER = ZoneInfo('America/Denver')


def record(log, request_id, at):
    log.record('test', 'model', 'plan', units(), request_id=request_id, at=at)


def test_day_summary_defaults_to_utc_and_accepts_timezone_tzinfo(tmp_path):
    log = UsageLog(tmp_path / 'usage.sqlite3', catalog=catalog())
    record(log, 'before-denver-midnight', '2026-03-08T06:59:00Z')
    record(log, 'after-denver-midnight', '2026-03-08T07:01:00Z')

    default_days = log.summary('2026-03-08T00:00:00Z', '2026-03-09T00:00:00Z', 'day')['groups']
    utc_days = log.summary('2026-03-08T00:00:00Z', '2026-03-09T00:00:00Z',
                           'day', timezone=timezone.utc)['groups']
    denver_days = log.summary('2026-03-08T00:00:00Z', '2026-03-09T00:00:00Z',
                              'day', timezone=DENVER)['groups']

    assert [(g['key'], g['calls']) for g in default_days] == [('2026-03-08', 2)]
    assert [(g['key'], g['calls']) for g in utc_days] == [('2026-03-08', 2)]
    assert [(g['key'], g['calls']) for g in denver_days] == [
        ('2026-03-07', 1), ('2026-03-08', 1),
    ]


def test_spring_dst_local_day_grouping_uses_23_hour_day(tmp_path):
    log = UsageLog(tmp_path / 'usage.sqlite3', catalog=catalog())
    # Denver's 2026-03-08 starts at 07:00Z and ends at 06:00Z the next day.
    record(log, 'start', '2026-03-08T07:00:00Z')
    record(log, 'inside', '2026-03-09T05:59:59Z')
    record(log, 'end', '2026-03-09T06:00:00Z')

    groups = log.summary('2026-03-08T07:00:00Z', '2026-03-09T06:00:00Z',
                         'day', timezone=DENVER)['groups']

    assert [(g['key'], g['calls']) for g in groups] == [('2026-03-08', 2)]


def test_fall_dst_local_day_grouping_uses_25_hour_day(tmp_path):
    log = UsageLog(tmp_path / 'usage.sqlite3', catalog=catalog())
    # Denver's 2026-11-01 starts at 06:00Z and ends at 07:00Z the next day.
    record(log, 'start', '2026-11-01T06:00:00Z')
    record(log, 'inside', '2026-11-02T06:59:59Z')
    record(log, 'end', '2026-11-02T07:00:00Z')

    groups = log.summary('2026-11-01T06:00:00Z', '2026-11-02T07:00:00Z',
                         'day', timezone=DENVER)['groups']

    assert [(g['key'], g['calls']) for g in groups] == [('2026-11-01', 2)]


def test_summary_windows_cover_requested_number_of_local_calendar_days(tmp_path):
    log = UsageLog(tmp_path / 'usage.sqlite3', catalog=catalog())
    now = '2026-03-08T18:00:00Z'  # noon in Denver after the spring transition.

    for days, expected_start in (
        (1, '2026-03-08T00:00:00-07:00'),
        (7, '2026-03-02T00:00:00-07:00'),
        (30, '2026-02-07T00:00:00-07:00'),
        (365, '2025-03-09T00:00:00-07:00'),
    ):
        result = usage_view.summary(log, days, 'day', now=now, timezone=DENVER)
        assert result['start'] == expected_start
        assert result['end'] == '2026-03-08T12:00:00-06:00'
        assert result['total']['calls'] == 0


def test_overview_windows_use_local_midnights_across_dst(tmp_path):
    log = UsageLog(tmp_path / 'usage.sqlite3', catalog=catalog())
    result = usage_view.overview(log, now='2026-03-08T18:00:00Z', timezone=DENVER)

    assert result['timezone'] == 'America/Denver'
    assert (result['periods']['today']['start'], result['periods']['today']['end']) == (
        '2026-03-08T00:00:00-07:00', '2026-03-08T12:00:00-06:00')
    assert (result['periods']['week']['start'], result['periods']['week']['end']) == (
        '2026-03-02T00:00:00-07:00', '2026-03-08T12:00:00-06:00')
    assert (result['periods']['month']['start'], result['periods']['month']['end']) == (
        '2026-03-01T00:00:00-07:00', '2026-03-08T12:00:00-06:00')
    assert all(p['total']['calls'] == 0 for p in result['periods'].values())

    fall = usage_view.overview(log, now='2026-11-01T18:00:00Z', timezone=DENVER)
    assert (fall['periods']['today']['start'], fall['periods']['today']['end']) == (
        '2026-11-01T00:00:00-06:00', '2026-11-01T11:00:00-07:00')
    assert (fall['periods']['week']['start'], fall['periods']['week']['end']) == (
        '2026-10-26T00:00:00-06:00', '2026-11-01T11:00:00-07:00')
    assert (fall['periods']['month']['start'], fall['periods']['month']['end']) == (
        '2026-11-01T00:00:00-06:00', '2026-11-01T11:00:00-07:00')
    assert all(p['total']['calls'] == 0 for p in fall['periods'].values())


def test_exact_local_midnight_summary_is_empty(tmp_path):
    log = UsageLog(tmp_path / 'usage.sqlite3', catalog=catalog())
    result = usage_view.summary(log, 1, 'day', now='2026-03-08T07:00:00Z', timezone=DENVER)

    assert result['start'] == result['end'] == '2026-03-08T00:00:00-07:00'
    assert result['total']['calls'] == 0


def test_timezone_none_resolves_each_local_midnight_with_os_rules(monkeypatch):
    class FakeLocalTime:
        def astimezone(self, tz=None):
            assert tz is None
            return end

    class FakeMidnight:
        def __init__(self, day):
            self.day = day

        def astimezone(self):
            resolved_midnights.append(self.day)
            return datetime(2026, self.day.month, self.day.day, tzinfo=DENVER)

    class DatetimeShim:
        @staticmethod
        def combine(day, _time):
            return FakeMidnight(day)

    class LogStub:
        def summary(self, start, finish, group_by='purpose', **kwargs):
            captured.append((start, finish, kwargs['timezone']))
            return {'total': {'calls': 0}}

    from datetime import datetime

    end = datetime(2026, 11, 1, 12, tzinfo=DENVER)
    resolved_midnights = []
    captured = []
    monkeypatch.setattr(usage_view, 'datetime', DatetimeShim)
    monkeypatch.setattr(usage_view, 'timestamp', lambda _value: FakeLocalTime())

    usage_view.summary(LogStub(), 7, 'day', now='ignored', timezone=None)

    assert resolved_midnights == [datetime(2026, 10, 26).date()]
    start, finish, tz = captured[0]
    assert start == datetime(2026, 10, 26, tzinfo=DENVER)
    assert finish == end and tz is None
