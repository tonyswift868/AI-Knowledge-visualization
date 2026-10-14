"""Pure study-time accounting; no clock, storage, UI or network dependencies.

Targets are fixed until an explicit replan. Closed segments are snapshots:
later confirmation/revocation changes live credits and overall remaining time,
but does not rewrite those snapshots or their recorded debt. Revoked historical
minutes become a synthetic target just before the current segment's first day,
filled before its fixed daily targets by all current credits. Credit IDs retain
their original submission even after confirmation/revocation, so retries cannot
resurrect revoked progress.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import date, datetime, time, timedelta, tzinfo
from fractions import Fraction
from math import isfinite, lcm
from types import MappingProxyType


def replan_preserves_past(cause: str | None) -> bool:
    """Shared policy for live commands, stored-event replay and report peaks.

    Missing/unknown causes retain the legacy segment-closing behavior.
    """
    return cause in ('point_minutes', 'restructure', 'window_changed', 'explicit_replan')


class LedgerError(ValueError):
    def __init__(self, code: str, message: str | None = None):
        self.code = code
        self.message = message or {
            "NO_STUDY_DAYS": "计划窗口内没有可学习的日期。",
            "NAIVE_DATETIME": "开课时间必须包含时区。",
            "CREDIT_CONFLICT": "同一进度标识不能提交不同的内容。",
        }.get(code, "时长账本参数无效。")
        super().__init__(self.message)


def _date(value: date) -> date:
    if not isinstance(value, date) or isinstance(value, datetime):
        raise LedgerError("INVALID_DATE", "日期必须是不含时间的日历日期。")
    return value


def _integer(value: int, *, positive: bool = False) -> int:
    if type(value) is not int or value < (1 if positive else 0):
        raise LedgerError("INVALID_MINUTES", "分钟数或数量必须是有效的整数。")
    return value


def _weight(value: float) -> float:
    if type(value) not in (int, float) or not 0 <= value <= 1 or not isfinite(value):
        raise LedgerError("INVALID_WEIGHT", "日期权重必须是 0 到 1 之间的有限数。")
    return float(value)


def _dates(start: date, end: date):
    # Ordinals also handle date.max without overflowing on the last increment.
    return (date.fromordinal(n) for n in range(start.toordinal(), end.toordinal() + 1))


@dataclass(frozen=True)
class PlanWindow:
    start: date
    end: date
    daily_cap_minutes: int
    weekday_weights: tuple[float, ...] = (1.0,) * 7
    date_weights: Mapping[date, float] = field(default_factory=dict)

    def __post_init__(self):
        if _date(self.end) < _date(self.start):
            raise LedgerError("INVALID_WINDOW", "计划结束日期不能早于开始日期。")
        _integer(self.daily_cap_minutes, positive=True)
        if not isinstance(self.weekday_weights, (tuple, list)) or len(self.weekday_weights) != 7:
            raise LedgerError("INVALID_WEIGHT", "必须提供周一到周日的七个权重。")
        if not isinstance(self.date_weights, Mapping):
            raise LedgerError("INVALID_WEIGHT", "特殊日期权重必须按日期填写。")
        object.__setattr__(self, "weekday_weights", tuple(_weight(w) for w in self.weekday_weights))
        object.__setattr__(self, "date_weights", MappingProxyType(
            {_date(day): _weight(weight) for day, weight in self.date_weights.items()}
        ))


def weight_of(window: PlanWindow, day: date) -> float:
    return window.date_weights.get(_date(day), window.weekday_weights[day.weekday()])


def _apportion(weighted_days: list[tuple[date, Fraction]], total: int) -> dict[date, int]:
    if not weighted_days:
        raise LedgerError("NO_STUDY_DAYS")
    # Scale decimal weights to integers: no floating-point remainder ties and
    # no loss of minutes even when total exceeds the exact range of floats.
    scale = lcm(*(weight.denominator for _, weight in weighted_days))
    weights = [int(weight * scale) for _, weight in weighted_days]
    denominator = sum(weights)
    quotients = [divmod(total * weight, denominator) for weight in weights]
    values = [floor for floor, _ in quotients]
    remainder = total - sum(values)
    order = sorted(range(len(values)), key=lambda i: (-quotients[i][1], weighted_days[i][0]))
    for index in order[:remainder]:
        values[index] += 1
    return {day: value for (day, _), value in zip(weighted_days, values)}


def distribute(window: PlanWindow, total_minutes: int, *, from_date: date | None = None) -> dict[date, int]:
    _integer(total_minutes)
    start = window.start if from_date is None else _date(from_date)
    targets = dict.fromkeys(_dates(start, window.end), 0)
    weighted = [(day, Fraction(str(weight_of(window, day))))
                for day in targets if weight_of(window, day) > 0]
    targets.update(_apportion(weighted, total_minutes))
    return targets


def full_day_minutes(targets: Mapping[date, int], window: PlanWindow) -> int:
    full = [minutes for day, minutes in targets.items() if weight_of(window, day) == 1]
    return max(full if full else targets.values(), default=0)


@dataclass(frozen=True)
class CapReport:
    ok: bool
    full_day_minutes: int
    cap: int
    suggested_end: date | None


def check_cap(window: PlanWindow, total_minutes: int) -> CapReport:
    current = full_day_minutes(distribute(window, total_minutes), window)
    cap = window.daily_cap_minutes
    if current <= cap:
        return CapReport(True, current, cap, None)
    limit_year = min(window.end.year + 5, date.max.year)
    try:
        limit = window.end.replace(year=limit_year)
    except ValueError:  # Five years after February 29 ends on February 28.
        limit = date(limit_year, 2, 28)
    weighted = [(day, Fraction(str(weight_of(window, day))))
                for day in _dates(window.start, window.end) if weight_of(window, day) > 0]
    weight_sum = sum((weight for _, weight in weighted), Fraction())
    largest = max(weight for _, weight in weighted)
    for ordinal in range(window.end.toordinal() + 1, limit.toordinal() + 1):
        day = date.fromordinal(ordinal)
        # Date overrides never extend beyond the original window.
        weight = Fraction(str(window.weekday_weights[day.weekday()]))
        if not weight:
            continue
        weighted.append((day, weight))
        weight_sum += weight
        largest = max(largest, weight)
        floor = total_minutes * largest // weight_sum
        if floor > cap:
            continue
        # The largest-weight days attain the maximum rounded target. Only a
        # quota on the cap boundary needs the exact remainder allocation.
        if floor < cap or max(_apportion(weighted, total_minutes).values()) <= cap:
            return CapReport(False, current, cap, day)
    return CapReport(False, current, cap, None)


def study_day(ts: datetime, refresh: time, tz: tzinfo) -> date:
    if not isinstance(ts, datetime) or ts.tzinfo is None or ts.utcoffset() is None:
        raise LedgerError("NAIVE_DATETIME")
    if not isinstance(refresh, time) or refresh.tzinfo is not None or not isinstance(tz, tzinfo):
        raise LedgerError("INVALID_REFRESH", "刷新时间应为本地钟表时间，并另行指定时区。")
    local = ts.astimezone(tz)
    if local.time() < refresh:
        if local.date() == date.min:
            raise LedgerError("INVALID_DATE", "学习日超出了可表示的日期范围。")
        return local.date() - timedelta(days=1)
    return local.date()


def _identifier(value: str):
    if not isinstance(value, str) or not value.strip():
        raise LedgerError("INVALID_CREDIT", "进度和学习单元标识不能为空。")


@dataclass(frozen=True)
class Credit:
    credit_id: str
    unit_id: str
    minutes: int
    day: date
    state: str
    seq: int

    def __post_init__(self):
        _identifier(self.credit_id)
        _identifier(self.unit_id)
        _integer(self.minutes, positive=True)
        _date(self.day)
        _integer(self.seq, positive=True)
        if self.state not in ("tentative", "confirmed"):
            raise LedgerError("INVALID_CREDIT", "进度状态必须是暂记或正式。")


@dataclass(frozen=True)
class LedgerStatus:
    today_target: int
    today_confirmed: int
    today_tentative: int
    debt_minutes: int
    needs_negotiation: bool
    ahead_minutes: int
    ahead_days: int
    remaining_minutes: int
    remaining_confirmed_minutes: int
    overdue_days: int
    completed: bool
    history_debt_minutes: int
    overflow_minutes: int
    today_repaid_confirmed: int = 0
    today_repaid_tentative: int = 0
    ahead_tentative: int = 0
    oldest_debt_day: str | None = None


@dataclass(frozen=True)
class _History:
    window: PlanWindow
    closed_on: date
    targets: dict[date, int]
    confirmed: dict[date, int]
    tentative: dict[date, int]
    relearning_minutes: int
    relearning_confirmed: int
    relearning_tentative: int
    debt_minutes: int


@dataclass(frozen=True)
class _Allocation:
    confirmed: dict[date, int]
    tentative: dict[date, int]
    relearning_confirmed: int
    relearning_tentative: int
    overflow: int


def _fill(targets: Mapping[date, int], credits: list[Credit], relearning_minutes: int):
    confirmed = dict.fromkeys(targets, 0)
    tentative = dict.fromkeys(targets, 0)
    days = [day for day in sorted(targets) if targets[day] > 0]
    index = overflow = relearning_confirmed = relearning_tentative = 0
    for credit in sorted(credits, key=lambda c: (c.state != "confirmed", c.seq)):
        left = credit.minutes
        filled = confirmed if credit.state == "confirmed" else tentative
        paid = min(left, relearning_minutes - relearning_confirmed - relearning_tentative)
        if credit.state == "confirmed":
            relearning_confirmed += paid
        else:
            relearning_tentative += paid
        left -= paid
        while left and index < len(days):
            day = days[index]
            free = targets[day] - confirmed[day] - tentative[day]
            amount = min(left, free)
            filled[day] += amount
            left -= amount
            if amount == free:
                index += 1
        overflow += left
    return _Allocation(confirmed, tentative, relearning_confirmed, relearning_tentative, overflow)


class Ledger:
    def __init__(self, window: PlanWindow, total_minutes: int):
        self._targets = distribute(window, total_minutes)
        self._window, self._total_minutes = window, total_minutes
        self._active_start = window.start
        self._credits: dict[str, Credit] = {}
        self._submissions: dict[str, Credit] = {}
        self._segments: dict[str, int] = {}
        self._history: list[_History] = []
        self._relearning_minutes = 0
        self._next_seq = 1
        self._released_minutes = self._active_released_minutes = 0
        self._estimate_start: date | None = None

    @property
    def window(self) -> PlanWindow:
        return self._window

    @property
    def total_minutes(self) -> int:
        return self._total_minutes

    @property
    def targets(self) -> dict[date, int]:
        return self._settled_targets()

    def scheduled_cap(self, today: date) -> CapReport:
        """Actual targets from today, excluding frozen past targets and debt.

        Keep the same full-weight-day convention as planning previews. Only
        the suggested extension is estimated; the displayed minutes and cap
        decision always come from the persisted target calendar.
        """
        targets = {day: minutes for day, minutes in self.targets.items() if day >= today}
        current = full_day_minutes(targets, self.window)
        cap = self.window.daily_cap_minutes
        suggested = None
        if current > cap and today <= self.window.end:
            future = replace(self.window, start=max(today, self.window.start))
            suggested = check_cap(future, sum(targets.values())).suggested_end
        return CapReport(current <= cap, current, cap, suggested)

    def release_unused(self, minutes: int) -> None:
        """Release unused estimates, never manufacture a learning credit.

        Release removes unfilled targets from the end of the active schedule.
        Historical snapshots remain frozen. Replans carry the released total
        forward, while actual credits and the original estimate stay intact.
        """
        _integer(minutes)
        self._released_minutes += minutes
        self._active_released_minutes += minutes

    def _settled_targets(self):
        targets = dict(self._targets)
        if self._active_released_minutes:
            allocation = self._allocation()
            left = self._active_released_minutes
            for day in sorted(targets, reverse=True):
                free = targets[day] - allocation.confirmed[day] - allocation.tentative[day]
                removed = min(left, free)
                targets[day] -= removed
                left -= removed
        return targets

    def _add(self, credit_id, unit_id, minutes, day, state):
        submitted = Credit(credit_id, unit_id, minutes, day, state, self._next_seq)
        previous = self._submissions.get(credit_id)
        if previous is not None:
            if replace(submitted, seq=previous.seq) != previous:
                raise LedgerError("CREDIT_CONFLICT")
            return
        self._credits[credit_id] = self._submissions[credit_id] = submitted
        self._segments[credit_id] = len(self._history)
        self._next_seq += 1

    def add_tentative(self, credit_id, unit_id, minutes, day):
        self._add(credit_id, unit_id, minutes, day, "tentative")

    def add_confirmed(self, credit_id, unit_id, minutes, day):
        self._add(credit_id, unit_id, minutes, day, "confirmed")

    def confirm(self, unit_id) -> int:
        _identifier(unit_id)
        matched = [c for c in self._credits.values() if c.unit_id == unit_id and c.state == "tentative"]
        for credit in matched:
            self._credits[credit.credit_id] = replace(credit, state="confirmed")
        return sum(c.minutes for c in matched)

    def revoke(self, unit_id) -> int:
        _identifier(unit_id)
        matched = [c for c in self._credits.values() if c.unit_id == unit_id and c.state == "tentative"]
        for credit in matched:
            if self._segments[credit.credit_id] < len(self._history):
                self._relearning_minutes += credit.minutes
            del self._credits[credit.credit_id]
            del self._segments[credit.credit_id]
        return sum(c.minutes for c in matched)

    def _allocation(self):
        return _fill(self._targets, [c for key, c in self._credits.items()
                                     if self._segments[key] == len(self._history)], self._relearning_minutes)

    def _today_repayment(self, today: date) -> tuple[int, int]:
        """Read-only attribution of today's credits to the FIFO past prefix.

        Uses precisely the allocator's confirmed-first, then seq order. The
        relearning target precedes all dates. Historical segments are frozen
        and cannot receive current credits. Release only removes unfilled tail
        targets, so it never changes these credited overlaps.
        """
        past = sum(value for day, value in self._targets.items() if day < today)
        if today >= self._active_start:
            past += self._relearning_minutes
        offset = confirmed = tentative = 0
        credits = [c for key, c in self._credits.items()
                   if self._segments[key] == len(self._history)]
        for credit in sorted(credits, key=lambda c: (c.state != "confirmed", c.seq)):
            if credit.day == today:
                amount = max(0, min(offset + credit.minutes, past) - offset)
                if credit.state == "confirmed": confirmed += amount
                else: tentative += amount
            offset += credit.minutes
        return confirmed, tentative

    def status(self, today: date) -> LedgerStatus:
        _date(today)
        allocation = self._allocation()
        confirmed, tentative = allocation.confirmed, allocation.tentative
        targets = self._settled_targets()
        filled = {day: confirmed[day] + tentative[day] for day in targets}
        debt = sum(target - filled[day] for day, target in targets.items() if day < today)
        if today >= self._active_start:
            debt += self._relearning_minutes - allocation.relearning_confirmed - allocation.relearning_tentative
        ahead = sum(minutes for day, minutes in filled.items() if day > today)
        ahead_days = 0
        for day in sorted(targets):
            if day <= today or targets[day] == 0:
                continue
            if filled[day] != targets[day]:
                break
            ahead_days += 1
        remaining = self.total_minutes - self._released_minutes - sum(c.minutes for c in self._credits.values())
        remaining_confirmed = self.total_minutes - self._released_minutes - sum(c.minutes for c in self._credits.values() if c.state == "confirmed")
        repaid_confirmed, repaid_tentative = self._today_repayment(today)
        oldest_debt = next((day.isoformat() for day in sorted(targets)
                            if day < today and filled[day] < targets[day]), None)
        return LedgerStatus(
            targets.get(today, 0), confirmed.get(today, 0), tentative.get(today, 0),
            debt, debt > 0 and debt >= 2 * full_day_minutes(targets, self.window), ahead, ahead_days,
            remaining, remaining_confirmed,
            (today - self.window.end).days if today > self.window.end and remaining_confirmed > 0 else 0,
            remaining_confirmed <= 0, sum(h.debt_minutes for h in self._history), allocation.overflow,
            repaid_confirmed, repaid_tentative,
            sum(minutes for day, minutes in tentative.items() if day > today), oldest_debt,
        )

    def replan(self, today: date, new_window: PlanWindow, new_total_minutes: int,
               *, preserve_past: bool = False) -> None:
        """Freeze days before today and redistribute within the new window.

        Initial allocation (__init__) covers the whole window. A replan starts
        allocating at max(today, window.start), while the history/credit segment
        boundary remains the actual operation day. Moving a start into the
        future must not freeze future targets as missed study days.

        preserve_past retains elapsed targets and their live FIFO debts. Course
        content estimates AND user-requested window changes use this mode.
        The default branch retains legacy segment-closing behavior for generated
        content operations and historical journals, not user date changes.
        """
        _date(today)
        _integer(new_total_minutes)
        if new_window.end < today:
            raise LedgerError("INVALID_REPLAN", "新的截止日期不能早于重排当天。")
        if self._history and today < self._history[-1].closed_on:
            raise LedgerError("INVALID_REPLAN", "不能重排已经冻结的历史日期。")
        past = sum(c.minutes for c in self._credits.values() if c.day < today)
        if new_total_minutes < past:
            raise LedgerError("INVALID_REPLAN", "新总时长不能少于重排当天之前已经记录的分钟数。")
        if preserve_past:
            # Rescheduling changes future work, not the learner's agreement to
            # past targets. Keep their FIFO segment, so today's
            # repayments cannot be counted in both a closed and a new segment.
            if self._estimate_start and today < self._estimate_start:
                raise LedgerError("INVALID_REPLAN", "不能重排已经冻结的日期。")
            if new_window.start != self.window.start and min(new_window.start, self.window.start) < today:
                raise LedgerError("INVALID_REPLAN", "不能改变已经开始的起始日期；只能调整未来部分。")
            prefix = {d: n for d, n in self._settled_targets().items() if d < today}
            active_start = self._active_start if self._history else new_window.start
            # Extending an expired plan must not invent debt for days outside
            # its former window, or leave holes in the serialized calendar.
            if today > date.min:
                for d in _dates(max(active_start, new_window.start), today - timedelta(days=1)):
                    prefix.setdefault(d, 0)
            historical = sum(c.minutes for key, c in self._credits.items()
                             if self._segments[key] < len(self._history))
            remaining = max(0, new_total_minutes - historical - self._released_minutes
                            - self._relearning_minutes - sum(prefix.values()))
            start = max(today, new_window.start)
            targets = (distribute(new_window, remaining, from_date=start) if remaining
                       else dict.fromkeys(_dates(start, new_window.end), 0))
            self._targets = {**prefix, **targets}
            self._window, self._total_minutes = new_window, new_total_minutes
            self._active_start = active_start
            self._active_released_minutes = 0
            self._estimate_start = today
            return
        remaining = max(0, new_total_minutes - past - self._released_minutes)
        allocation_start = max(today, new_window.start)
        targets = (distribute(new_window, remaining, from_date=allocation_start) if remaining > 0
                   else dict.fromkeys(_dates(allocation_start, new_window.end), 0))
        allocation = self._allocation()
        confirmed, tentative = allocation.confirmed, allocation.tentative
        frozen = {day: value for day, value in self._settled_targets().items() if day < today}
        relearning = (self._relearning_minutes, allocation.relearning_confirmed, allocation.relearning_tentative)
        if self._active_start > today:
            relearning = (0, 0, 0)
        history = _History(self.window, today, frozen,
                           {day: confirmed[day] for day in frozen},
                           {day: tentative[day] for day in frozen},
                           *relearning,
                           sum(target - confirmed[day] - tentative[day] for day, target in frozen.items())
                           + relearning[0] - relearning[1] - relearning[2])
        # All validation and calculations complete before any state is changed.
        self._history.append(history)
        for key, credit in self._credits.items():
            if credit.day >= today:
                self._segments[key] = len(self._history)
        self._window, self._total_minutes = new_window, new_total_minutes
        self._active_start, self._targets = today, targets
        self._relearning_minutes = 0
        self._active_released_minutes = 0
        self._estimate_start = None

    def to_dict(self) -> dict:
        return {
            **({'estimate_start': self._estimate_start.isoformat()} if self._estimate_start else {}),
            **({'settlement': {'released': self._released_minutes, 'active_released': self._active_released_minutes}}
               if self._released_minutes else {}),
            "version": 1,
            "window": _window_data(self.window),
            "total_minutes": self.total_minutes,
            "active_start": self._active_start.isoformat(),
            "targets": _calendar_data(self._targets),
            "relearning_minutes": self._relearning_minutes,
            "history": [{"window": _window_data(h.window), "closed_on": h.closed_on.isoformat(),
                         "targets": _calendar_data(h.targets), "confirmed": _calendar_data(h.confirmed),
                         "tentative": _calendar_data(h.tentative), "debt_minutes": h.debt_minutes,
                         "relearning_minutes": h.relearning_minutes, "relearning_confirmed": h.relearning_confirmed,
                         "relearning_tentative": h.relearning_tentative}
                        for h in self._history],
            "credits": [dict(_credit_data(c), segment=self._segments[c.credit_id])
                        for c in sorted(self._credits.values(), key=lambda c: c.seq)],
            "submissions": [_credit_data(c) for c in sorted(self._submissions.values(), key=lambda c: c.seq)],
            "next_seq": self._next_seq,
        }

    @classmethod
    def from_dict(cls, data) -> Ledger:
        try:
            return _restore(cls, data)
        except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
            raise LedgerError("INVALID_DATA", "账本数据格式或内容无效，无法恢复。") from None


def _window_data(window):
    return dict(start=window.start.isoformat(), end=window.end.isoformat(),
                daily_cap_minutes=window.daily_cap_minutes, weekday_weights=list(window.weekday_weights),
                date_weights={day.isoformat(): weight for day, weight in window.date_weights.items()})


def _window_from(data):
    _fields(data, {"start", "end", "daily_cap_minutes", "weekday_weights", "date_weights"})
    return PlanWindow(date.fromisoformat(data["start"]), date.fromisoformat(data["end"]),
                      data["daily_cap_minutes"], tuple(data["weekday_weights"]),
                      {date.fromisoformat(day): weight for day, weight in data["date_weights"].items()})


def _calendar_data(values):
    return {day.isoformat(): value for day, value in sorted(values.items())}


def _calendar_from(data):
    if not isinstance(data, dict):
        raise ValueError()
    result = {date.fromisoformat(day): _integer(value) for day, value in data.items()}
    if len(result) != len(data):
        raise ValueError()
    return result


def _credit_data(credit):
    return dict(credit_id=credit.credit_id, unit_id=credit.unit_id, minutes=credit.minutes,
                day=credit.day.isoformat(), state=credit.state, seq=credit.seq)


def _credit_from(data):
    _fields(data, {"credit_id", "unit_id", "minutes", "day", "state", "seq"})
    return Credit(data["credit_id"], data["unit_id"], data["minutes"], date.fromisoformat(data["day"]), data["state"], data["seq"])


def _fields(data, keys):
    if not isinstance(data, dict) or set(data) != keys:
        raise ValueError()


def _restore(cls, data):
    data = dict(data)
    estimate_start = data.pop('estimate_start', None)
    settlement = data.pop('settlement', {'released': 0, 'active_released': 0})
    _fields(settlement, {'released', 'active_released'})
    _fields(data, {"version", "window", "total_minutes", "active_start", "targets", "relearning_minutes", "history", "credits", "submissions", "next_seq"})
    if type(data["version"]) is not int or data["version"] != 1:
        raise ValueError()
    ledger = object.__new__(cls)
    ledger._released_minutes = _integer(settlement['released'])
    ledger._active_released_minutes = _integer(settlement['active_released'])
    if ledger._active_released_minutes > ledger._released_minutes: raise ValueError()
    ledger._window = _window_from(data["window"])
    ledger._total_minutes = _integer(data["total_minutes"])
    ledger._active_start = date.fromisoformat(data["active_start"])
    ledger._estimate_start = date.fromisoformat(estimate_start) if estimate_start is not None else None
    ledger._targets = _calendar_from(data["targets"])
    ledger._relearning_minutes = _integer(data["relearning_minutes"])
    budget = sum(ledger._targets.values())
    allocation_start = max(ledger._active_start, ledger.window.start)
    if ledger._estimate_start is not None:
        if ledger._estimate_start > ledger.window.end:
            raise ValueError()
        # Content estimates and date changes retain the elapsed calendar. Its
        # suffix is distributed; a smaller estimate can exhaust future work
        # without erasing already agreed (and possibly unpaid) past targets.
        start = max(allocation_start, ledger._estimate_start)
        suffix = {d: n for d, n in ledger._targets.items() if d >= start}
        amount = sum(suffix.values())
        expected = (distribute(ledger.window, amount, from_date=start) if amount
                    else dict.fromkeys(_dates(start, ledger.window.end), 0))
        if (set(ledger._targets) != set(_dates(allocation_start, ledger.window.end))
                or suffix != expected or budget > ledger.total_minutes and amount):
            raise ValueError()
    else:
        expected = (distribute(ledger.window, budget, from_date=allocation_start) if budget
                    else dict.fromkeys(_dates(allocation_start, ledger.window.end), 0))
        if expected != ledger._targets or budget > ledger.total_minutes:
            raise ValueError()
    if ledger._active_start > ledger.window.end:
        raise ValueError()
    if any(not isinstance(data[name], list) for name in ("history", "credits", "submissions")):
        raise ValueError()
    ledger._history = []
    for raw in data["history"]:
        _fields(raw, {"window", "closed_on", "targets", "confirmed", "tentative", "debt_minutes",
                      "relearning_minutes", "relearning_confirmed", "relearning_tentative"})
        h = _History(_window_from(raw["window"]), date.fromisoformat(raw["closed_on"]),
                     *(_calendar_from(raw[key]) for key in ("targets", "confirmed", "tentative")),
                     *(_integer(raw[key]) for key in ("relearning_minutes", "relearning_confirmed", "relearning_tentative")),
                     _integer(raw["debt_minutes"]))
        if set(h.targets) != set(h.confirmed) or set(h.targets) != set(h.tentative):
            raise ValueError()
        if any(day >= h.closed_on or day > h.window.end or h.confirmed[day] + h.tentative[day] > target
               for day, target in h.targets.items()):
            raise ValueError()
        relearning_debt = h.relearning_minutes - h.relearning_confirmed - h.relearning_tentative
        if relearning_debt < 0:
            raise ValueError()
        if h.debt_minutes != sum(target - h.confirmed[day] - h.tentative[day] for day, target in h.targets.items()) + relearning_debt:
            raise ValueError()
        if ledger._history and h.closed_on < ledger._history[-1].closed_on:
            raise ValueError()
        ledger._history.append(h)
    if ledger._history:
        if ledger._active_start != ledger._history[-1].closed_on:
            raise ValueError()
    elif ledger._active_start != ledger.window.start or ledger._estimate_start is None and budget != ledger.total_minutes:
        raise ValueError()
    ledger._submissions = {}
    for raw in data["submissions"]:
        credit = _credit_from(raw)
        if credit.credit_id in ledger._submissions:
            raise ValueError()
        ledger._submissions[credit.credit_id] = credit
    ledger._next_seq = _integer(data["next_seq"], positive=True)
    if (ledger._next_seq != len(ledger._submissions) + 1
            or {c.seq for c in ledger._submissions.values()} != set(range(1, ledger._next_seq))):
        raise ValueError()
    ledger._credits, ledger._segments = {}, {}
    for raw in data["credits"]:
        _fields(raw, {"credit_id", "unit_id", "minutes", "day", "state", "seq", "segment"})
        credit = _credit_from({key: value for key, value in raw.items() if key != "segment"})
        segment = _integer(raw["segment"])
        original = ledger._submissions[credit.credit_id]
        if (credit.credit_id in ledger._credits or replace(credit, state=original.state) != original
                or (original.state == "confirmed" and credit.state != "confirmed") or segment > len(ledger._history)):
            raise ValueError()
        if segment < len(ledger._history) and credit.day >= ledger._history[segment].closed_on:
            raise ValueError()
        ledger._credits[credit.credit_id], ledger._segments[credit.credit_id] = credit, segment
    return ledger


def estimate_check_minutes(point_count: int, *, minutes_per_question: float,
                           problem_ratio: float = 0.10, problem_questions: int = 4) -> int:
    """Final-check reserve, separate from one module-test question per point.

    The default budgets one correct answer for 90% of points and four for the
    difficult 10% (two initial level gains plus two recovered errors). Actual
    correct answers earn time; an estimate is not evidence of completed work.
    """
    _integer(point_count)
    _integer(problem_questions, positive=True)
    ratio = _weight(problem_ratio)
    if (type(minutes_per_question) not in (int, float) or not isfinite(minutes_per_question)
            or minutes_per_question <= 0):
        raise LedgerError("INVALID_MINUTES", "每题估算时长必须是大于零的有限数。")
    return round(point_count * (1 - ratio) * minutes_per_question
                 + point_count * ratio * problem_questions * minutes_per_question)
