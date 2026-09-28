"""Pure, JSON-ready daily projection shared by home and classroom callers.

The caller supplies an aware instant, account refresh time, timezone and course
completion evidence. No wall clock, settings, database, UI or network is read.
Daily reminders and the placement of historical debt are deliberately not policy
decisions here; historical debt is retained separately from current debt.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import date, datetime, time, tzinfo

from .ledger import Ledger, LedgerError, study_day


def build_today_status(ledger: Ledger, *, now: datetime, refresh: time, tz: tzinfo,
                       plan_state: str, check_complete: bool,
                       classroom_day: date | None = None) -> dict:
    """Project one plan without changing its ledger or lifecycle.

    ``study_day`` is the current account day; ``progress_day`` is the date used
    for target/fill/debt/ahead figures. A classroom may pin the latter to its
    entry day until exit, even when a clock/settings/zone change moves the
    current day backwards. Overdue days still use the current account day.

    ``completed`` requires both confirmed time and complete course checks.
    ``time_completed`` preserves Ledger.status's time-only completion flag.
    The caller must pass check_complete=False for a partially loaded course.

    ``is_late_night`` means local wall time is strictly before refresh.
    ``needs_rest`` is also true for a pinned class that has crossed refresh.
    These are conditions, not notification events; the host owns delivery,
    deduplication, and whether a classroom is open.
    """
    current = study_day(now, refresh, tz)
    if plan_state not in ('creating', 'ready', 'active', 'checking', 'completed'):
        raise ValueError('Unknown plan state')
    if type(check_complete) is not bool:
        raise ValueError('check_complete must be a boolean')
    if classroom_day is not None and (
        not isinstance(classroom_day, date) or isinstance(classroom_day, datetime)
    ):
        raise LedgerError('INVALID_DATE', '开课学习日必须是不含时间的日期。')
    progress_day = classroom_day if classroom_day is not None else current
    result = asdict(ledger.status(progress_day))
    result['time_completed'] = result['completed']
    running = plan_state in ('active', 'checking', 'completed')
    result['completed'] = running and result['time_completed'] and check_complete
    result['overdue_days'] = max(0, (current - ledger.window.end).days) if running and not result['completed'] else 0
    late = now.astimezone(tz).time() < refresh
    result.update(study_day=current.isoformat(), progress_day=progress_day.isoformat(),
                  is_late_night=late, needs_rest=late or progress_day < current,
                  check_complete=check_complete)
    return result
