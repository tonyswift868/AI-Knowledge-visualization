from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from PySide6.QtCore import QCoreApplication, Qt
import pytest

from refactor.learning.course import ledger
from refactor.learning.services import daily_clock as module
from refactor.learning.services.daily_clock import DailyClock


UTC = timezone.utc
SHANGHAI = timezone(timedelta(hours=8))


class FakeTime:
    def __init__(self, value="2026-09-27T03:59:50+08:00"):
        self.set(value)

    def set(self, value):
        self.value = datetime.fromisoformat(value)

    def __call__(self):
        return self.value


@pytest.fixture
def factory():
    app = QCoreApplication.instance() or QCoreApplication([])
    clocks = []
    def create(source=None, **kwargs):
        kwargs.setdefault("timezone", SHANGHAI)
        source = source or FakeTime()
        clock = DailyClock(now=source, **kwargs)
        events = {"days": [], "reminders": [], "late": []}
        clock.study_day_changed.connect(events["days"].append)
        clock.reminder_due.connect(lambda: events["reminders"].append(clock.current_study_day))
        clock.late_night_notice.connect(lambda: events["late"].append(clock.current_study_day))
        clocks.append(clock)
        return clock, source, events
    yield create
    for clock in clocks:
        clock.dispose()
    app.processEvents()


def test_refresh_boundary_directly_uses_ledger(factory, monkeypatch):
    calls = []
    real = ledger.study_day
    def observed(*args):
        calls.append(args)
        return real(*args)
    monkeypatch.setattr(ledger, "study_day", observed)
    clock, source, events = factory()
    assert clock.current_study_day == date(2026, 9, 26)
    source.set("2026-09-27T03:59:59+08:00")
    clock.check_now()
    assert not events["days"]
    source.set("2026-09-27T04:00:00+08:00")
    clock.check_now()
    clock.check_now()
    assert events["days"] == [date(2026, 9, 27)]
    assert len(calls) == 4
    assert clock._timer.timerType() == Qt.TimerType.PreciseTimer
    assert 0 < clock._timer.interval() <= 30_000


@pytest.mark.parametrize("unfinished", [True, False])
def test_reminder_once_only_when_unfinished_even_after_clock_rollback(factory, unfinished):
    queries = []
    clock, source, events = factory(FakeTime("2026-09-27T19:59:55+08:00"), reminder=time(20),
                                    today_unfinished=lambda: queries.append(1) or unfinished)
    source.set("2026-09-27T20:00:00+08:00")
    clock.check_now()
    for value in ("2026-09-27T21:00:00+08:00", "2026-09-27T19:00:00+08:00", "2026-09-27T20:00:01+08:00"):
        source.set(value)
        clock.check_now()
    assert queries == [1]
    assert events["reminders"] == ([date(2026, 9, 27)] if unfinished else [])
    source.set("2026-09-28T20:00:00+08:00")
    clock.check_now()
    assert len(queries) == 2


def test_refresh_in_class_emits_day_and_one_rest_notice_without_reassigning_credit(factory):
    clock, source, events = factory(in_class=lambda: True)
    source.set("2026-09-27T04:00:00+08:00")
    clock.check_now()
    assert events["days"] == events["late"] == [date(2026, 9, 27)]
    source.set("2026-09-27T03:00:00+08:00")
    clock.check_now()
    source.set("2026-09-27T04:00:00+08:00")
    clock.check_now()
    assert events["days"] == [date(2026, 9, 27), date(2026, 9, 26), date(2026, 9, 27)]
    assert events["late"] == [date(2026, 9, 27)]


def test_reminder_waits_until_class_ends(factory):
    state = {"in_class": True}
    clock, source, events = factory(FakeTime("2026-09-27T19:59:55+08:00"), reminder=time(20),
                                    today_unfinished=lambda: True, in_class=lambda: state["in_class"])
    source.set("2026-09-27T20:00:00+08:00")
    clock.check_now()
    assert not events["reminders"]
    state["in_class"] = False
    source.set("2026-09-27T20:00:15+08:00")
    clock.check_now()
    clock.check_now()
    assert events["reminders"] == [date(2026, 9, 27)]


def test_sleep_wake_skips_old_days_but_catches_current_due_reminder(factory):
    clock, source, events = factory(reminder=time(20), today_unfinished=lambda: True)
    source.set("2026-09-30T21:00:00+08:00")
    clock.check_now()
    assert events["days"] == [date(2026, 9, 30)]
    assert events["reminders"] == [date(2026, 9, 30)]
    clock.check_now()
    assert len(events["reminders"]) == 1


def test_refresh_change_is_immediate_and_invalid_config_is_atomic(factory):
    clock, source, events = factory(FakeTime("2026-09-27T03:00:00+08:00"))
    clock.configure(refresh=time(2))
    assert events["days"] == [date(2026, 9, 27)]
    clock.configure(refresh=time(5))
    assert events["days"][-1] == date(2026, 9, 26)
    with pytest.raises(ValueError):
        clock.configure(refresh=time(1), reminder=time(20, tzinfo=UTC))
    assert clock.refresh == time(5) and clock.reminder is None


def test_timezone_change_recomputes_study_day_immediately(factory):
    clock, source, events = factory(FakeTime("2026-09-27T01:00:00+00:00"))
    assert clock.current_study_day == date(2026, 9, 27)
    clock.configure(timezone=UTC)
    assert clock.current_study_day == date(2026, 9, 26)
    clock.configure(timezone="Asia/Shanghai")
    assert clock.current_study_day == date(2026, 9, 27)
    assert events["days"] == [date(2026, 9, 26), date(2026, 9, 27)]


def test_follow_os_timezone_is_resolved_on_each_poll(factory):
    system = {"zone": UTC}
    class TravelingDatetime(datetime):
        def astimezone(self, tz=None):
            return super().astimezone(system["zone"] if tz is None else tz)
    source = lambda: TravelingDatetime(2026, 9, 27, 1, tzinfo=UTC)
    clock, _, events = factory(source, timezone=None)
    assert clock.current_study_day == date(2026, 9, 26)
    system["zone"] = SHANGHAI
    clock.check_now()
    assert events["days"] == [date(2026, 9, 27)]


@pytest.mark.parametrize("zone,before,after,reminder", [
    ("America/New_York", "2026-03-08T06:59:50+00:00", "2026-03-08T07:00:00+00:00", time(2, 30)),
    ("America/New_York", "2026-11-01T05:29:50+00:00", "2026-11-01T05:30:00+00:00", time(1, 30)),
])
def test_dst_gap_or_repeat_uses_wall_clock_and_once_per_day(factory, zone, before, after, reminder):
    clock, source, events = factory(FakeTime(before), timezone=ZoneInfo(zone), reminder=reminder,
                                    today_unfinished=lambda: True)
    source.set(after)
    clock.check_now()
    source.value += timedelta(hours=1)
    clock.check_now()
    assert len(events["reminders"]) == 1


def test_early_morning_reminder_belongs_to_previous_study_day(factory):
    clock, source, events = factory(FakeTime("2026-09-28T01:59:59+08:00"), reminder=time(2),
                                    today_unfinished=lambda: True)
    source.set("2026-09-28T02:00:00+08:00")
    clock.check_now()
    assert events["reminders"] == [date(2026, 9, 27)]


def test_reminder_setting_enable_disable_and_same_day_dedup(factory):
    clock, source, events = factory(FakeTime("2026-09-27T19:00:00+08:00"), today_unfinished=lambda: True)
    clock.configure(reminder=time(20))
    source.set("2026-09-27T20:00:00+08:00")
    clock.check_now()
    clock.configure(reminder=None)
    clock.configure(reminder=time(21))
    source.set("2026-09-27T21:00:00+08:00")
    clock.check_now()
    assert events["reminders"] == [date(2026, 9, 27)]
    clock.configure(reminder=None)
    source.set("2026-09-28T21:00:00+08:00")
    clock.check_now()
    assert len(events["reminders"]) == 1


def test_closed_app_does_not_backfill_reminders_and_dispose_stops(factory):
    clock, source, events = factory(FakeTime("2026-09-27T21:00:00+08:00"), reminder=time(20),
                                    today_unfinished=lambda: True)
    clock.check_now()
    assert not events["reminders"]
    clock.stop()
    source.set("2026-09-29T21:00:00+08:00")
    clock.check_now()
    assert not events["days"]
    clock.start()
    assert events["days"] == [date(2026, 9, 29)]
    assert not events["reminders"]
    clock.dispose()
    source.set("2026-09-30T21:00:00+08:00")
    clock.start()
    clock.check_now()
    assert not clock._timer.isActive()
    assert events["days"] == [date(2026, 9, 29)]


def test_naive_clock_is_rejected(factory):
    with pytest.raises(ValueError, match="aware"):
        factory(lambda: datetime(2026, 9, 27))
