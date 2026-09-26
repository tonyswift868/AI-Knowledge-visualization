"""In-process Qt clock; no persistence, background process or account writes.

Construct on the Qt application thread. ``now`` returns an aware datetime;
``timezone=None`` follows the OS zone on every poll, or supply a tzinfo / IANA
name for a fixed account setting. Callbacks must be quick local bool queries.
The precise 15-second poll recomputes wall time, including after sleep or clock
changes; it never relies on a 24-hour timer or reimplements ledger.study_day.

Signals carry the new study day, not instructions to reassign classroom credit.
The caller retains the entry-day of an ongoing class (TEACH-05).

Reminders belong to study days: e.g. refresh 04:00 + reminder 02:00 means 02:00
near the END of that study day. Only a due time crossed while running is caught
up, and only for the current study day, never historical days. A pending reminder
waits until class ends. Each day's unfinished check is made at most once, even
after clock rollback, timezone/config changes or repeated DST hours. Nothing is
backfilled across stop/start. Configuration changes take effect immediately.
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, time, timedelta, timezone as datetime_timezone, tzinfo
from zoneinfo import ZoneInfo

from PySide6.QtCore import QObject, Qt, QTimer, Signal

from ..course import ledger


_UNCHANGED = object()


def _wall_time(value, name: str) -> time:
    if not isinstance(value, time) or value.tzinfo is not None:
        raise ValueError(f"{name} must be a local datetime.time without tzinfo")
    return value


def _timezone(value) -> tzinfo | None:
    if isinstance(value, str):
        return ZoneInfo(value)
    if value is not None and not isinstance(value, tzinfo):
        raise ValueError("timezone must be None, tzinfo, or an IANA timezone name")
    return value


class DailyClock(QObject):
    study_day_changed = Signal(object)  # datetime.date
    reminder_due = Signal()
    late_night_notice = Signal()

    def __init__(self, parent=None, *, refresh: time = time(4),
                 timezone: tzinfo | str | None = None, reminder: time | None = None,
                 today_unfinished: Callable[[], bool] = lambda: False,
                 in_class: Callable[[], bool] = lambda: False,
                 now: Callable[[], datetime] | None = None):
        super().__init__(parent)
        self._refresh = _wall_time(refresh, "refresh")
        self._timezone = _timezone(timezone)
        self._reminder = None if reminder is None else _wall_time(reminder, "reminder")
        self._today_unfinished, self._in_class = today_unfinished, in_class
        self._now = now or (lambda: datetime.now(datetime_timezone.utc))
        self._handled_reminders: set[date] = set()
        self._noticed_days: set[date] = set()
        self._pending_reminder: date | None = None
        self._disposed = False
        self._checking = False
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.setInterval(15_000)
        self._timer.timeout.connect(self.check_now)
        instant, local = self._read_time()
        self._day = ledger.study_day(instant, self._refresh, local.tzinfo)
        self._last_wall = local.replace(tzinfo=None)
        self._timer.start()

    @property
    def current_study_day(self) -> date:
        return self._day

    @property
    def refresh(self) -> time:
        return self._refresh

    @property
    def timezone(self) -> tzinfo | None:
        return self._timezone

    @property
    def reminder(self) -> time | None:
        return self._reminder

    def _read_time(self) -> tuple[datetime, datetime]:
        instant = self._now()
        if not isinstance(instant, datetime) or instant.tzinfo is None or instant.utcoffset() is None:
            raise ValueError("now() must return an aware datetime")
        # astimezone(None) resolves the OS timezone afresh, not just at startup.
        return instant, instant.astimezone(self._timezone)

    def configure(self, *, refresh=_UNCHANGED, timezone=_UNCHANGED, reminder=_UNCHANGED) -> None:
        """Atomically change settings; None disables reminders / follows OS zone."""
        new_refresh = self._refresh if refresh is _UNCHANGED else _wall_time(refresh, "refresh")
        new_timezone = self._timezone if timezone is _UNCHANGED else _timezone(timezone)
        new_reminder = self._reminder if reminder is _UNCHANGED else (
            None if reminder is None else _wall_time(reminder, "reminder"))
        if self._disposed:
            return
        self._refresh, self._timezone, self._reminder = new_refresh, new_timezone, new_reminder
        self._pending_reminder = None
        self.check_now()

    def check_now(self) -> None:
        """Poll immediately (also useful with a fake source); timer calls this."""
        if self._disposed or not self._timer.isActive() or self._checking:
            return
        self._checking = True
        try:
            instant, local = self._read_time()
            wall = local.replace(tzinfo=None)
            day = ledger.study_day(instant, self._refresh, local.tzinfo)
            previous_day, previous_wall = self._day, self._last_wall
            self._day, self._last_wall = day, wall
            if day != previous_day:
                self._pending_reminder = None
                self.study_day_changed.emit(day)
                if self._disposed or not self._timer.isActive():
                    return
                if (day > previous_day and day not in self._noticed_days and self._in_class()):
                    self._noticed_days.add(day)
                    self.late_night_notice.emit()
            if self._disposed or not self._timer.isActive() or self._reminder is None:
                return
            due_date = day + timedelta(days=int(self._reminder < self._refresh))
            due = datetime.combine(due_date, self._reminder)
            if previous_wall < due <= wall and day not in self._handled_reminders:
                self._pending_reminder = day
            if (self._pending_reminder != day or wall < due or day in self._handled_reminders):
                return
            if self._in_class():
                return
            self._pending_reminder = None
            self._handled_reminders.add(day)
            if self._today_unfinished():
                self.reminder_due.emit()
        finally:
            self._checking = False

    def stop(self) -> None:
        """Pause while the owner is inactive; no catch-up across this interval."""
        self._timer.stop()
        self._pending_reminder = None

    def start(self) -> None:
        if self._disposed or self._timer.isActive():
            return
        _, local = self._read_time()
        self._last_wall = local.replace(tzinfo=None)
        self._timer.start()
        self.check_now()

    def dispose(self) -> None:
        self.stop()
        self._disposed = True
