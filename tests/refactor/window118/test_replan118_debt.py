"""BUG-83/86: public App rescheduling must keep unpaid targets live."""
from dataclasses import asdict, replace
from datetime import date, timedelta
import json
from types import SimpleNamespace

import pytest

from refactor.learning.app.plan_flow import PlanFlow
from refactor.learning.course.store import CourseStore
from refactor.learning.course.ledger import Ledger, LedgerError, PlanWindow
from refactor.learning.course.restructure import apply_structure
from refactor.learning.course.sync_course import CourseSync
from scripts import plan_restructure as cli
from tests.refactor.restructure114.test_restructure114_contract import seed, DAY, START, proposal, dump
from tests.refactor.sync_course.test_sync_course import connection  # noqa: F401


def app_change_window(store, **raw):
    flow = PlanFlow(SimpleNamespace(store=store), lambda *args: None, lambda: DAY)
    # Only omit the unrelated detail-page background preparations/model jobs.
    flow.detail = store.get_plan
    return flow.change_window('p', raw)


@pytest.fixture
def store(tmp_path):
    with CourseStore('account', tmp_path) as instance:
        seed(instance)
        instance.add_credit('p', 'repay-confirmed', 'old0', 4, DAY, 'confirmed')
        instance.add_credit('p', 'repay-tentative', 'old1', 2, DAY, 'tentative')
        yield instance


def test_replan118_app_extension_keeps_debt_repayment_and_prefix(store):
    before = store.load_ledger('p')
    status = before.status(DAY)
    assert (status.debt_minutes, status.today_repaid_confirmed, status.today_repaid_tentative) == (51, 4, 2)
    app_change_window(store, end='2026-10-22')
    after = store.load_ledger('p')
    updated = after.status(DAY)
    assert updated.debt_minutes == status.debt_minutes
    assert (updated.today_repaid_confirmed, updated.today_repaid_tentative) == (4, 2)
    assert {d:n for d,n in after.targets.items() if d < DAY} == {d:n for d,n in before.targets.items() if d < DAY}
    # Debt must remain repayable, not merely parked in a report-only history.
    store.add_credit('p', 'next-repayment', 'old0', 10, DAY, 'confirmed')
    assert store.load_ledger('p').status(DAY).debt_minutes == 41


@pytest.mark.parametrize('raw', [dict(daily_cap=90), dict(weekday_weights=[1,0,1,0.5,1,1,1]),
                                   dict(end='2026-10-18'), dict(end='2026-10-22', daily_cap=120)])
def test_replan118_app_other_future_window_changes_keep_fifo(store, raw):
    before = store.load_ledger('p')
    app_change_window(store, **raw)
    after = store.load_ledger('p')
    assert after.status(DAY).debt_minutes == before.status(DAY).debt_minutes
    assert (after.status(DAY).today_repaid_confirmed, after.status(DAY).today_repaid_tentative) == (4,2)
    assert {d:n for d,n in after.targets.items() if d < DAY} == {d:n for d,n in before.targets.items() if d < DAY}
    assert Ledger.from_dict(json.loads(json.dumps(after.to_dict()))).to_dict() == after.to_dict()


def test_replan118_public_replan_default_and_legacy_window_event_replay_keep_live_debt(store, tmp_path):
    before = store.load_ledger('p')
    immutable = store.records('ledger_events','p')
    store.replan_plan('p', DAY, new_window=replace(before.window, end=date(2026,10,22)))
    event = max(store.records('ledger_events','p'), key=lambda e:e['operation_seq'])
    assert event['cause'] == 'explicit_replan'
    assert store.load_ledger('p').status(DAY).debt_minutes == 51
    # Old events did not have a preservation flag; the same existing cause is
    # sufficient for both devices, without editing any uploaded fact.
    with store.transaction():
        event['cause'] = 'window_changed'
        store._put('ledger_events','p',dict(event,id='legacy-window-event',operation_seq=event['operation_seq']+1))
    with CourseStore('account', tmp_path/'second') as other:
        other.apply_remote([(r,i+1) for i,r in enumerate(store.pending_records(10000))])
        assert other.load_ledger('p').to_dict() == store.load_ledger('p').to_dict()
        assert other.load_ledger('p').status(DAY).debt_minutes == 51
        assert all(row in store.records('ledger_events','p') for row in immutable)


def test_replan118_restructure_deadline_one_public_replan_preview_apply_reopen(store, tmp_path, monkeypatch):
    before = store.load_ledger('p')
    progress = {e:store.records(e,'p') for e in ('credits','question_answers','judgements')}
    preview = apply_structure(store,'p',proposal(),dry_run=True,end_date='2026-10-22')
    assert preview['can_apply'] and preview['before']['overdue_to_repay'] == preview['after']['overdue_to_repay'] == 51
    calls=[]
    method=store.replan_plan
    def track(*args, **kwargs):
        calls.append(kwargs)
        return method(*args, **kwargs)
    monkeypatch.setattr(store,'replan_plan',track)
    old_count=len(store.records('ledger_events','p'))
    applied,backup=cli.execute(store,'p',proposal(),preview['preview_token'],end_date='2026-10-22')
    assert applied == preview and len(calls)==1 and calls[0]['cause']=='restructure'
    assert len(store.records('ledger_events','p'))==old_count+1 and backup.exists()
    ledger=store.load_ledger('p')
    assert ledger.window.end==date(2026,10,22)
    assert ledger.status(DAY).debt_minutes==51
    assert ledger.status(DAY).today_repaid_confirmed==4
    assert {d.isoformat():n for d,n in ledger.targets.items()}==preview['after']['targets']
    assert {d:n for d,n in ledger.targets.items() if d<DAY}=={d:n for d,n in before.targets.items() if d<DAY}
    future=[n for d,n in ledger.targets.items() if d>=DAY]
    assert len(future)==9 and max(future)-min(future)<=1
    assert all(store.records(e,'p')==records for e,records in progress.items())
    with CourseStore('account',tmp_path) as reopened:
        assert reopened.load_ledger('p').to_dict()==ledger.to_dict()


def test_replan118_local_rollback_restores_original_deadline_and_whole_database(store, tmp_path):
    original=dump(store)
    preview=apply_structure(store,'p',proposal(),dry_run=True,end_date='2026-10-22')
    _,backup=cli.execute(store,'p',proposal(),preview['preview_token'],end_date='2026-10-22')
    store.close()
    cli.rollback(tmp_path,'account','p',backup)
    with CourseStore('account',tmp_path) as restored:
        assert dump(restored)==original
        assert restored.get_plan('p')['window']['end']=='2026-10-20'


@pytest.mark.parametrize('incremental',[False,True])
def test_replan118_two_devices_replay_extension_and_synced_rollback(connection,tmp_path,incremental):
    server,transport,login=connection
    server.incremental=incremental
    account=login.identity.account_id
    a=CourseStore(account,tmp_path/'a')
    b=CourseStore(account,tmp_path/'b')
    try:
        seed(a)
        a.add_credit('p','today-repayment','old0',4,DAY,'confirmed')
        CourseSync(a,transport).sync();CourseSync(b,transport).sync()
        original=a.load_ledger('p')
        preview=apply_structure(a,'p',proposal(),dry_run=True,end_date='2026-10-22')
        _,backup=cli.execute(a,'p',proposal(),preview['preview_token'],end_date='2026-10-22')
        CourseSync(a,transport).sync();CourseSync(b,transport).sync()
        assert a.load_ledger('p').to_dict()==b.load_ledger('p').to_dict()
        assert b.get_plan('p')['window']['end']=='2026-10-22'
        assert b.load_ledger('p').status(DAY).debt_minutes==original.status(DAY).debt_minutes
        a.close()
        assert cli.rollback(tmp_path/'a',account,'p',backup,resync=True)['queued_for_sync']
        a=CourseStore(account,tmp_path/'a')
        CourseSync(a,transport).sync();CourseSync(b,transport).sync()
        assert a.load_ledger('p').to_dict()==b.load_ledger('p').to_dict()
        assert b.get_plan('p')['window']['end']=='2026-10-20'
        assert b.load_ledger('p').targets==original.targets
        assert b.load_ledger('p').status(DAY)==original.status(DAY)
        assert len([e for e in b.records('ledger_events','p') if e.get('cause')=='restructure'])==2
    finally:
        a.close();b.close()


def test_replan118_future_start_and_expired_extension_roundtrip_without_fake_debt():
    start=date(2026,10,16)
    ledger=Ledger(PlanWindow(start,date(2026,10,20),600),300)
    shifted=replace(ledger.window,start=date(2026,10,17),end=date(2026,10,22))
    ledger.replan(DAY,shifted,300,preserve_past=True)
    assert min(ledger.targets)==shifted.start and ledger.status(DAY).debt_minutes==0
    assert Ledger.from_dict(ledger.to_dict()).to_dict()==ledger.to_dict()
    expired=Ledger(PlanWindow(START,date(2026,10,10),600),400)
    expired.replan(DAY,replace(expired.window,end=date(2026,10,22)),400,preserve_past=True)
    assert expired.status(DAY).debt_minutes==400
    assert all(expired.targets[d]==0 for d in (date(2026,10,11),date(2026,10,12),date(2026,10,13)))
    assert Ledger.from_dict(expired.to_dict()).to_dict()==expired.to_dict()


def test_replan118_changed_past_weights_do_not_recalculate_frozen_targets(store):
    ledger=store.load_ledger('p')
    before=ledger.targets
    ledger.replan(DAY,replace(ledger.window,end=date(2026,10,22),date_weights={START:0,DAY:0}),
                  ledger.total_minutes,preserve_past=True)
    assert {d:n for d,n in ledger.targets.items() if d<DAY}=={d:n for d,n in before.items() if d<DAY}
    assert ledger.targets[DAY]==0 and ledger.status(DAY).debt_minutes==51
    assert Ledger.from_dict(ledger.to_dict()).to_dict()==ledger.to_dict()


def test_replan118_cli_apply_end_date_and_rollback_restore_original_window(store,tmp_path,monkeypatch):
    monkeypatch.setattr(cli,'_running_software',lambda:[])
    data=tmp_path/'structure.json'
    data.write_text(json.dumps(proposal()),encoding='utf-8')
    output=tmp_path/'preview.json'
    args=['--data-root',str(tmp_path),'--plan-id','p','--structure',str(data),'--end-date','2026-10-22']
    assert cli.main(['--dry-run',*args,'--report',str(output)])==0
    token=json.loads(output.read_text(encoding='utf-8'))['preview_token']
    assert cli.main(['--apply',*args,'--confirm-preview',token])==0
    assert store.get_plan('p')['window']['end']=='2026-10-22'
    backup=next(store.path.parent.glob('*.bak'))
    store.close()
    assert cli.main(['--rollback','--data-root',str(tmp_path),'--plan-id','p','--backup',str(backup)])==0
    with CourseStore('account',tmp_path) as restored:
        assert restored.get_plan('p')['window']['end']=='2026-10-20'
        assert restored.load_ledger('p').status(DAY).debt_minutes==51


@pytest.mark.parametrize('change', ['past_start','old_end','backdated','all_rest'])
def test_replan118_invalid_window_changes_are_atomic(store,change):
    ledger=store.load_ledger('p')
    ledger.replan(DAY,replace(ledger.window,end=date(2026,10,22)),ledger.total_minutes,preserve_past=True)
    before=ledger.to_dict()
    window=ledger.window
    today=DAY
    if change=='past_start':window=replace(window,start=START+timedelta(days=1))
    if change=='old_end':window=replace(window,end=DAY-timedelta(days=1))
    if change=='backdated':today=DAY-timedelta(days=1)
    if change=='all_rest':window=replace(window,weekday_weights=(0,)*7)
    with pytest.raises(LedgerError):ledger.replan(today,window,ledger.total_minutes,preserve_past=True)
    assert ledger.to_dict()==before
