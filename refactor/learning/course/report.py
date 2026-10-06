"""Versioned learning-report data, built and saved from one course transaction.

No model, prose evaluation, wall clock, or authoritative progress cache is used.
Counts come from MasteryBook's canonical replay; timing comes from T03 Ledger.

Time conventions (also exposed as machine-readable fields in the result):
* Daily target fills are FIFO allocations, with historical segments frozen at
  replan time. Credited minutes instead group the *current live* credits by their
  original study day. These columns need not sum to the same value after revoke.
* Learning days include original submissions, even subsequently revoked ones,
  and judgement dates in each event's supplied timezone. No account refresh
  timezone is guessed for judgements, which have no study-day field.
* Peaks sample before/after each ledger operation and across idle days. Replay
  follows (operation_seq, id, op), exactly as CourseStore does. Undated legacy
  confirm/revoke operations use the preceding replay day; backdated operations
  keep causal order. Both limitations are listed, never presented as exact dates.
* All stored replans count, including automatic generation/reorder/delete calls.
* First detection means the first closed module_test OR skip_test. One retest
  judgement counts once, even if it contains several points. Weakness is the two
  error counters plus check errors before the associated questions pass;
  ties use plan order. The top ten include zero scores when fewer points exist.

Exact start/finish dates use plans.started_on/completed_on. Without a recorded
start, the first activity is labelled as an inference. Missing historical
completion dates and their early/late day counts remain null, not report.today.
"""
from __future__ import annotations

from datetime import date, datetime

from .ledger import Ledger, PlanWindow
from .store import StoreError


SCHEMA = 'mentorflow.course.report.v1'
WEAKEST_LIMIT = 10


def _day(value):
    if isinstance(value, str):
        value = date.fromisoformat(value)
    if type(value) is not date:
        raise StoreError('INVALID_DATE', '报告日期必须是不含时间的学习日。')
    return value


def _window(raw):
    return PlanWindow(_day(raw['start']), _day(raw['end']), raw['daily_cap_minutes'],
                      tuple(raw['weekday_weights']), {_day(k): v for k, v in raw['date_weights'].items()})


def _operations(credits, commands):
    return sorted([(r['operation_seq'], r['id'], 'credit', r) for r in credits]
                  + [(r['operation_seq'], r['id'], r['op'], r) for r in commands])


def _operation_day(op, raw):
    value = raw['credit']['day'] if op == 'credit' else raw.get('today')
    return _day(value) if value is not None else None


def _peaks(plan, operations, through):
    ledger = Ledger(_window(plan['initial_window']), plan['initial_total_minutes'])
    known_days = [_operation_day(op, raw) for _, _, op, raw in operations]
    current = min([ledger.window.start, through] + [d for d in known_days if d is not None])
    debt_peak = ahead_peak = 0
    undated, backdated = [], []

    def sample(day):
        nonlocal debt_peak, ahead_peak
        status = ledger.status(day)
        debt_peak = max(debt_peak, status.debt_minutes)
        ahead_peak = max(ahead_peak, status.ahead_minutes)

    sample(current)
    for (_, identifier, op, raw), recorded_day in zip(operations, known_days):
        if recorded_day is None:
            undated.append(identifier)
        elif recorded_day < current:
            backdated.append(identifier)
        current = max(current, recorded_day or current)
        # Between operations, debt only increases and ahead only decreases.
        # Sampling endpoints therefore includes extrema on all idle days.
        sample(current)
        if op == 'credit':
            credit = raw['credit']
            method = ledger.add_confirmed if credit['state'] == 'confirmed' else ledger.add_tentative
            method(credit['credit_id'], credit['unit_id'], credit['minutes'], _day(credit['day']))
        elif op == 'replan':
            ledger.replan(_day(raw['today']), _window(raw['window']), raw['total_minutes'])
        elif op == 'release':
            ledger.release_unused(raw['minutes'])
        else:
            getattr(ledger, op)(raw['unit_id'])
        sample(current)
    sample(max(current, through))
    return dict(debt_peak_minutes=debt_peak, ahead_peak_minutes=ahead_peak,
                peak_basis='ledger_operation_boundaries_and_idle_days',
                peak_dates_exact=not undated and not backdated,
                undated_operation_ids=undated, backdated_operation_ids=backdated)


def _time_series(ledger, judgements, through):
    snapshot = ledger.to_dict()
    days = {}

    def row(day):
        return days.setdefault(day, dict(day=day, segment=None, frozen=False, target_minutes=0,
            confirmed_minutes=0, tentative_minutes=0, credited_confirmed_minutes=0,
            credited_tentative_minutes=0, submitted_minutes=0, judgement_count=0))

    segments = []
    for index, history in enumerate(snapshot['history']):
        for day, target in history['targets'].items():
            row(day).update(segment=index, frozen=True, target_minutes=target,
                            confirmed_minutes=history['confirmed'][day], tentative_minutes=history['tentative'][day])
        segments.append(dict(segment=index, closed_on=history['closed_on'],
            history_debt_minutes=history['debt_minutes'], relearning_minutes=history['relearning_minutes'],
            relearning_filled_minutes=history['relearning_confirmed'] + history['relearning_tentative']))
    for day, target in ledger.targets.items():
        status = ledger.status(day)
        row(day.isoformat()).update(segment=len(segments), target_minutes=target,
                                   confirmed_minutes=status.today_confirmed, tentative_minutes=status.today_tentative)
    start_status = ledger.status(_day(snapshot['active_start']))
    segments.append(dict(segment=len(segments), closed_on=None, history_debt_minutes=0,
                         relearning_minutes=snapshot['relearning_minutes'],
                         relearning_filled_minutes=snapshot['relearning_minutes'] - start_status.debt_minutes))
    for credit in snapshot['credits']:
        row(credit['day'])['credited_' + credit['state'] + '_minutes'] += credit['minutes']
    for credit in snapshot['submissions']:
        row(credit['day'])['submitted_minutes'] += credit['minutes']
    for judgement in judgements:
        day = datetime.fromisoformat(judgement['event']['at']).date().isoformat()
        row(day)['judgement_count'] += 1
    # Include idle and overdue days, and retain future targets when finished early.
    first = min([ledger.window.start, through] + [_day(d) for d in days])
    last = max([ledger.window.end, through] + [_day(d) for d in days])
    for ordinal in range(first.toordinal(), last.toordinal() + 1):
        row(date.fromordinal(ordinal).isoformat())
    activity_days = sorted(d for d, r in days.items() if r['submitted_minutes'] or r['judgement_count'])
    return dict(days=[days[d] for d in sorted(days)], segments=segments,
                learning_days=len(activity_days), activity_dates=activity_days,
                learning_day_basis='original_credit_day_or_judgement_local_date',
                through_date=through.isoformat(),
                history_debt_minutes=ledger.status(through).history_debt_minutes,
                overflow_minutes=ledger.status(through).overflow_minutes)


def build_report(store, plan_id, *, today) -> dict:
    """Build, atomically save via CourseStore.save_report, and return JSON data.

    Only a fully generated, completed plan with complete mastery is accepted.
    Unused estimates are not fabricated credits. Equal input produces equal output and no new
    outbox revision. CourseStore.save_report splits large reports into bounded
    sync records; CourseStore.load_report reconstructs the complete document.
    """
    today = _day(today)
    with store.transaction():
        plan = store.get_plan(plan_id)
        modules = store.records('modules', plan_id)
        points = store.records('points', plan_id)
        if (plan['state'] != 'completed' or not modules or len(modules) != plan['module_count']
                or any(m['point_count'] <= 0 or sum(p['module_id'] == m['id'] for p in points) != m['point_count'] for m in modules)):
            raise StoreError('PLAN_NOT_COMPLETE', '整份计划和检验完成后才能生成报告。')
        mastery, ledger = store.load_mastery(plan_id), store.load_ledger(plan_id)
        if not mastery.check_complete():
            raise StoreError('PLAN_NOT_COMPLETE', '整份计划和检验完成后才能生成报告。')
        judgements = store.records('judgements', plan_id)
        credits = store.records('credits', plan_id)
        commands = store.records('ledger_events', plan_id)
        operations = _operations(credits, commands)
        recorded_days = [_operation_day(op, raw) for _, _, op, raw in operations]
        recorded_days += [datetime.fromisoformat(j['event']['at']).date() for j in judgements]
        started = _day(plan['started_on']) if plan.get('started_on') else None
        finished = _day(plan['completed_on']) if plan.get('completed_on') else None
        if any(day is not None and day > today for day in recorded_days + [started, finished]):
            raise StoreError('REPORT_BEFORE_ACTIVITY', '报告日期不能早于已保存的学习活动或完成日期。')
        if started and finished and started > finished:
            raise StoreError('INVALID_REPORT_DATES')
        # Without a historical finish date, the supplied today is an observation
        # horizon only. It is never substituted for the actual completion date.
        through = max([finished or today] + [d for d in recorded_days if d is not None])
        timing = _time_series(ledger, judgements, through)
        timing.update(_peaks(plan, operations, through))
        start_basis = 'recorded' if started else 'unrecorded'
        if started is None and timing['activity_dates']:
            started = _day(timing['activity_dates'][0])
            start_basis = 'first_activity'
        live = ledger.to_dict()['credits']
        split_modules = [m for m in modules if 'final_check_minutes' in m]
        split_ids = {m['id'] for m in split_modules}
        split_points = {p['id'] for p in points if p['module_id'] in split_ids}
        final_estimate = sum(m['final_check_minutes'] for m in split_modules)
        final_actual = sum(c['minutes'] for c in live if c['unit_id'].startswith('check:'))
        module_estimate = sum(m['check_minutes'] for m in split_modules)
        module_actual = sum(c['minutes'] for c in live if c['unit_id'] in {f'test:{mid}' for mid in split_ids})
        timing['check_minutes'] = dict(
            accounting='split' if len(split_modules) == len(modules) else 'mixed' if split_modules else 'legacy_combined',
            final_estimated=final_estimate if split_modules else None,
            final_actual=final_actual if split_modules else None,
            final_difference=final_estimate-final_actual if split_modules else None,
            module_estimated=module_estimate if split_modules else None,
            module_actual=module_actual if split_modules else None,
            module_difference=module_estimate-module_actual if split_modules else None,
            legacy_combined_estimated=sum(m['check_minutes'] for m in modules if m['id'] not in split_ids),
            released_minutes=sum(r['minutes'] for r in commands if r['op'] == 'release'))
        confirmed = sum(c['minutes'] for c in live if c['state'] == 'confirmed')
        tentative = sum(c['minutes'] for c in live if c['state'] == 'tentative')
        replans = [dict(event_id=r['id'], operation_seq=r['operation_seq'], day=r['today'],
                        reason=r.get('reason'), cause=r.get('cause'), start_date=r['window']['start'],
                        end_date=r['window']['end'], planned_minutes=r['total_minutes'])
                   for _, _, op, r in operations if op == 'replan']
        stats = mastery.report_stats()
        module_rows = []
        for module in modules:
            mid = module['id']
            module_rows.append(dict(module_id=mid, stage_id=module['stage_id'], title=module['title'],
                                    position=module['position'], **stats['modules'][mid]))
        module_order = {m['id']: i for i, m in enumerate(modules)}
        point_rows = []
        for position, point in enumerate(sorted(points, key=lambda p: (module_order[p['module_id']], p['position'], p['id']))):
            pid = point['id']
            evidence = stats['points'][pid]
            point_rows.append(dict(point_id=pid, module_id=point['module_id'], summary=point['summary'],
                position=position, state=mastery.point(pid).state, level=mastery.point(pid).level, **evidence,
                weakness_score=evidence['casual_errors'] + evidence['module_errors'] + evidence['check_errors_before_pass']))
        profiles = store.records('teacher_profile', plan_id)
        profile = next((p['document'] for p in profiles if p['id'] == plan_id), None)
        teaching_points = (profile or {}).get('teaching_points', {})
        result = dict(schema=SCHEMA, schema_version=1, plan=dict(
            plan_id=plan_id, goal=plan['goal'], final_goal=plan['final_goal'], level=plan['level'], notes=plan['notes'],
            start_date=ledger.window.start.isoformat(), end_date=ledger.window.end.isoformat(),
            original_start_date=plan['initial_window']['start'], original_end_date=plan['initial_window']['end'],
            actual_start_date=started.isoformat() if started else None, start_date_basis=start_basis,
            actual_completion_date=finished.isoformat() if finished else None,
            completion_date_basis='recorded' if finished else 'unrecorded',
            early_days=max(0, (ledger.window.end - finished).days) if finished else None,
            overdue_days=max(0, (finished - ledger.window.end).days) if finished else None,
            replan_count=len(replans), replans=replans, planned_minutes=ledger.total_minutes,
            credited_minutes=confirmed + tentative, confirmed_minutes=confirmed, tentative_minutes=tentative),
            time=timing, modules=module_rows, points=point_rows,
            assessment='questions' if stats['questions'] else 'learning_only',
            questions=[dict(question_id=qid, module_id=mastery.question(qid).module_id, **evidence)
                       for qid,evidence in stats['questions'].items()],
            weakest_points=[dict(point_id=p['point_id'], score=p['weakness_score'])
                            for p in sorted(point_rows, key=lambda p: (-p['weakness_score'], p['position']))[:WEAKEST_LIMIT]],
            weakness_rule=dict(score='casual_errors + module_errors + check_errors_before_pass',
                               tie_break='plan_order', limit=WEAKEST_LIMIT),
            teacher_profile=profile,
            live_points=[dict(point_id=p['id'], title=p.get('title') or p['summary']) for p in points
                         if p['id'] in teaching_points])
        store.save_report(plan_id, result)
        return result
