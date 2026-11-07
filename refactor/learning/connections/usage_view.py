"""Calendar windows over the current account's local usage journal."""
from datetime import datetime, time, timedelta

from .errors import ModelError
from .pricing import timestamp


def _midnight(day, timezone):
    # Resolving each naive local midnight via the OS retains its historical DST
    # rules. Reusing local.tzinfo here would freeze the current UTC offset.
    midnight = datetime.combine(day, time())
    return midnight.astimezone() if timezone is None else midnight.replace(tzinfo=timezone)


def _window(now, timezone, days=1):
    local = timestamp(now).astimezone(timezone)
    return _midnight(local.date() - timedelta(days=days - 1), timezone), local


def overview(log, *, now=None, timezone=None):
    now = timestamp(now)
    end = now.astimezone(timezone)
    starts = {'today': end.date(), 'week': end.date() - timedelta(days=end.weekday()),
              'month': end.date().replace(day=1)}
    periods = {}
    for name, day in starts.items():
        start = _midnight(day, timezone)
        periods[name] = dict(start=start.isoformat(), end=end.isoformat(),
                             total=log.summary(start, end, timezone=timezone)['total'])
    return dict(timezone=getattr(timezone, 'key', None) or end.tzname(), periods=periods)


def summary(log, days, group_by, *, now=None, timezone=None):
    start, end = _window(now, timezone, days)
    result = log.summary(start, end, group_by, timezone=timezone)
    result.update(start=start.isoformat(), end=end.isoformat())
    return result


def details(log, days, *, offset=0, limit=50, end=None, now=None, timezone=None):
    now = timestamp(now)
    finish = timestamp(end) if end is not None else now
    if finish > now:
        raise ModelError('USAGE_TIME_INVALID', '用量详情截止时间不能晚于当前时间。')
    start, finish = _window(finish, timezone, days)
    return dict(start=start.isoformat(), end=finish.isoformat(),
                timezone=getattr(timezone, 'key', None) or finish.tzname(),
                **log.details(start, finish, offset=offset, limit=limit))
