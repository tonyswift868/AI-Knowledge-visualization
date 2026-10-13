"""BUG-89: report peaks must use the live ledger's replan policy."""
from datetime import timedelta

import pytest

from refactor.learning.course import report
from refactor.learning.course.ledger import PlanWindow
from tests.refactor.report import test_report as fixtures


@pytest.fixture
def case(tmp_path, monkeypatch):
    monkeypatch.setattr(fixtures, 'SCRATCH', tmp_path)
    instance = fixtures.ReportTests()
    instance.setUp()
    try:
        yield instance
    finally:
        instance.tearDown()


@pytest.mark.parametrize('cause', ['explicit_replan', 'window_changed', 'point_minutes', 'restructure'])
def test_report120_preserved_debt_peak_matches_live_ledger_14_instead_of_10(case, cause):
    start = fixtures.DAY
    store = case.prepare()
    store.add_credit('p', 'past', 'u', 10, start + timedelta(days=1), 'confirmed')
    store.replan_plan('p', start + timedelta(days=2), PlanWindow(start, start + timedelta(days=4), 200),
                      cause=cause)
    finish_day = start + timedelta(days=3)
    live = store.load_ledger('p').status(finish_day)
    # Frozen first two targets total 20, paid 10; the third day adds 4 unpaid.
    assert live.debt_minutes == 14
    result = case.finish(finish_day)
    assert result['time']['debt_peak_minutes'] == 14
    assert result['time']['ahead_peak_minutes'] == 3
    assert result['time']['history_debt_minutes'] == 0
    assert [r['target_minutes'] for r in result['time']['days']] == [10, 10, 4, 3, 3]


@pytest.mark.parametrize('cause', [None, 'module_generated', 'material_added', 'modules_reordered',
                                   'module_deleted', 'question_estimate', 'legacy_unknown'])
def test_report120_old_ordinary_events_keep_closed_segment_rules(case, cause):
    start = fixtures.DAY
    store = case.prepare()
    store.add_credit('p', 'past', 'u', 10, start + timedelta(days=1), 'confirmed')
    store.replan_plan('p', start + timedelta(days=2), PlanWindow(start, start + timedelta(days=4), 200),
                      cause=cause or 'module_generated')
    assert store.load_ledger('p').status(start + timedelta(days=2)).history_debt_minutes == 10
    result = case.finish(start + timedelta(days=3))
    commands = store.records('ledger_events', 'p')
    if cause is None:
        # Omit optional metadata only in the detached legacy report input.
        for event in commands:
            event.pop('cause', None)
    peaks = report._peaks(store.get_plan('p'), report._operations(store.records('credits', 'p'), commands),
                          start + timedelta(days=3))
    assert peaks['debt_peak_minutes'] == result['time']['debt_peak_minutes'] == 10
    assert peaks['ahead_peak_minutes'] == result['time']['ahead_peak_minutes'] == 6
    assert result['time']['history_debt_minutes'] == 10
