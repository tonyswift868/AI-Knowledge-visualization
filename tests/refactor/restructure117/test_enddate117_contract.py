"""Read-only deadline forecasts and the current ledger integration boundary.

These tests deliberately do not pretend that a changed deadline can be applied:
the permitted files cannot change CourseStore's replay or Ledger's window rule.
"""
from datetime import date
import json

import pytest

from refactor.learning.course.ledger import Ledger, LedgerError
from refactor.learning.course.restructure import apply_structure, RestructureError, format_report
from refactor.learning.course.store import CourseStore
from scripts import plan_restructure as cli
from tests.refactor.restructure114.test_restructure114_contract import DAY, seed, proposal, dump, concept


@pytest.fixture
def store(tmp_path):
    with CourseStore('account', tmp_path) as instance:
        seed(instance)
        yield instance


def test_enddate117_forecast_preserves_prefix_debt_and_spreads_same_suffix(store):
    original = dump(store)
    unchanged = apply_structure(store, 'p', proposal(), dry_run=True)
    extended = apply_structure(store, 'p', proposal(), dry_run=True, end_date='2026-10-22')
    assert dump(store) == original
    assert extended['end_date_before'] == '2026-10-20'
    assert extended['end_date_after'] == '2026-10-22'
    assert extended['remaining_days'] == 9
    assert extended['before']['overdue_to_repay'] == extended['after']['overdue_to_repay'] > 0
    assert {d:n for d,n in extended['before']['targets'].items() if d < DAY.isoformat()} == {
        d:n for d,n in extended['after']['targets'].items() if d < DAY.isoformat()}
    targets = [row['after'] for row in extended['daily']]
    assert len(targets) == 9 and max(targets)-min(targets) <= 1
    assert sum(targets) == sum(row['after'] for row in unchanged['daily'])
    assert [row['day'] for row in extended['daily']] == [f'2026-10-{day}' for day in range(14, 23)]
    assert extended['daily_cap']['fits'] and extended['daily_cap']['includes_overdue']
    assert extended['daily_cap']['capacity_minutes'] == 5400
    text = format_report(extended)
    assert '2026-10-20 → 2026-10-22' in text and '2026-10-22' in text and '不能执行' in text


def test_enddate117_existing_ledger_and_store_cannot_publish_restructure_with_changed_window(store):
    """Real interfaces, no replacement services or mocked successful replays."""
    from dataclasses import replace
    ledger = store.load_ledger('p')
    with pytest.raises(LedgerError, match='不能改变日期窗口'):
        ledger.replan(DAY, replace(ledger.window, end=date(2026,10,22)), 80, preserve_past=True)
    store.replan_plan('p', DAY, new_window=replace(ledger.window, end=date(2026,10,22)),
                      new_total_minutes=80, reason='restructure')
    event = max(store.records('ledger_events', 'p'), key=lambda e: e['operation_seq'])
    assert event['cause'] == 'explicit_replan'  # reason does not select cause.


def test_enddate117_changed_deadline_apply_refuses_without_backup_or_any_writes(store, monkeypatch):
    original = dump(store)
    preview = apply_structure(store, 'p', proposal(), dry_run=True, end_date='2026-10-22')
    assert not preview['can_apply'] and not preview['deadline_change_supported']
    assert any('store.py 与 ledger.py' in issue for issue in preview['issues'])
    def unexpected(*args, **kwargs):
        pytest.fail('Refused operation must not write a replan or backup')
    monkeypatch.setattr(store, '_replan', unexpected)
    monkeypatch.setattr(cli, 'make_backup', unexpected)
    with pytest.raises(RestructureError):
        apply_structure(store, 'p', proposal(), dry_run=False, end_date='2026-10-22')
    with pytest.raises(RestructureError):
        cli.execute(store, 'p', proposal(), preview['preview_token'], end_date='2026-10-22')
    assert dump(store) == original


@pytest.mark.parametrize('end', ['2026-10-13', '2026-10-06', '20261022', '2026-02-30', 'bad'])
def test_enddate117_invalid_deadline_refused_atomically(store, end):
    original = dump(store)
    preview = apply_structure(store, 'p', proposal(), dry_run=True, end_date=end)
    assert not preview['can_apply'] and any('不能早于今天或原开始日' in issue for issue in preview['issues'])
    with pytest.raises(RestructureError):
        apply_structure(store, 'p', proposal(), dry_run=False, end_date=end)
    assert dump(store) == original


def test_enddate117_end_before_original_start_refused_even_if_not_before_today(store):
    p = proposal()
    p['today'] = '2026-10-01'
    preview = apply_structure(store, 'p', p, dry_run=True, end_date='2026-10-06')
    assert not preview['can_apply'] and any('原开始日' in issue for issue in preview['issues'])


def test_enddate117_no_option_preserves114_apply_one_replan_and_exact_local_rollback(store, tmp_path, monkeypatch):
    before = dump(store)
    preview = apply_structure(store, 'p', proposal(), dry_run=True)
    explicit_same = apply_structure(store, 'p', proposal(), dry_run=True, end_date='2026-10-20')
    assert preview['can_apply'] and explicit_same['can_apply']
    assert explicit_same['after'] == preview['after']
    calls = []
    original = store._replan
    def count(*args, **kwargs):
        calls.append(kwargs['cause'])
        return original(*args, **kwargs)
    monkeypatch.setattr(store, '_replan', count)
    report, backup = cli.execute(store, 'p', proposal(), preview['preview_token'])
    assert report == preview and calls == ['restructure']
    store.close()
    result = cli.rollback(tmp_path, 'account', 'p', backup)
    assert result['local_restored']
    with CourseStore('account', tmp_path) as restored:
        assert dump(restored) == before
        assert restored._get('plans', 'p', 'p')['window']['end'] == '2026-10-20'


def test_enddate117_cap_reports_overflow_without_adjustment(store):
    p = proposal()
    p['modules'][0]['points'] = [concept(minutes=7000)]
    report = apply_structure(store, 'p', p, dry_run=True, end_date='2026-10-22')
    cap = report['daily_cap']
    assert not cap['fits'] and cap['excess_minutes'] > 0
    assert cap['total_capacity_excess_minutes'] == cap['outstanding_minutes'] - 5400
    assert cap['daily_target_excess_minutes'] == sum(row['excess_minutes'] for row in cap['over_cap_days'])
    assert report['end_date_after'] == '2026-10-22' and report['total_after'] == 7050


def test_enddate117_preview_token_covers_deadline(store):
    a = apply_structure(store, 'p', proposal(), dry_run=True, end_date='2026-10-22')
    b = apply_structure(store, 'p', proposal(), dry_run=True, end_date='2026-10-23')
    assert a['preview_token'] != b['preview_token']


def test_enddate117_shorter_valid_forecast_ends_at_requested_deadline(store):
    preview = apply_structure(store, 'p', proposal(), dry_run=True, end_date='2026-10-18')
    assert preview['daily'][-1]['day'] == '2026-10-18'
    assert preview['remaining_days'] == 5
    assert preview['after']['overdue_to_repay'] == preview['before']['overdue_to_repay']


def test_enddate117_capacity_can_fail_even_when_new_targets_fit(store):
    # Large unpaid prefix, then much smaller remaining course estimates.
    store.replan_plan('p', date(2026,10,7), new_total_minutes=14000)
    with store.transaction():
        plan = store._get('plans', 'p', 'p')
        store._replan(plan, DAY, store.load_ledger('p').window, 1000, cause='point_minutes')
        store._put('plans', 'p', plan)
    report = apply_structure(store, 'p', proposal(), dry_run=True, end_date='2026-10-22')
    assert report['daily_cap']['maximum_daily_minutes'] <= 600
    assert report['daily_cap']['outstanding_minutes'] > 5400
    assert report['daily_cap']['total_capacity_excess_minutes'] > 0
    assert not report['daily_cap']['fits']


def test_enddate117_cli_outputs_changed_deadline_forecast_and_refuses_apply(tmp_path, monkeypatch, capsys):
    with CourseStore('account', tmp_path) as s:
        seed(s)
        original = dump(s)
    monkeypatch.setattr(cli, '_running_software', lambda: [])
    source = tmp_path/'structure.json'
    source.write_text(json.dumps(proposal()), encoding='utf-8')
    out = tmp_path/'preview.json'
    args = ['--data-root', str(tmp_path), '--plan-id', 'p', '--structure', str(source),
            '--end-date', '2026-10-22']
    assert cli.main(['--dry-run', *args, '--report', str(out)]) == 2
    preview = json.loads(out.read_text(encoding='utf-8'))
    assert preview['end_date_after'] == '2026-10-22' and len(preview['daily']) == 9
    assert '截止日：2026-10-20 → 2026-10-22' in capsys.readouterr().out
    assert cli.main(['--apply', *args, '--confirm-preview', preview['preview_token']]) == 2
    assert not list((tmp_path/'learning/accounts/account').glob('*.bak'))
    with CourseStore('account', tmp_path) as s:
        assert dump(s) == original
