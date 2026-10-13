from copy import deepcopy
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
import sqlite3

import pytest

from refactor.learning.course.store import CourseStore, StoreError, PARENTS, PROGRESS, SCHEMA_VERSION
from refactor.learning.course.mastery import Judgement, AttemptClosed
from refactor.learning.course.ledger import PlanWindow
from refactor.learning.course.restructure import apply_structure, RestructureError
from refactor.learning.course.sync_course import CourseSync
from scripts import plan_restructure as cli
from tests.refactor.mastery70.support import seed_assessment
from tests.refactor.sync_course.test_sync_course import connection

DAY = date(2026, 10, 14)
START = date(2026, 10, 7)


def seed(store):
    store.create_plan('p', goal='离线重切验证', window=PlanWindow(START, date(2026,10,20), 600))
    store.add_sources('p', [dict(id='src', kind='url',url='https://example.org/book',title='资料',site='example.org',
        tier='official',credibility=1,adopted=True,chunks=[dict(chunk_id='c',text='依据正文',page_start=1,page_end=1,section=['章'])])])
    store.save_outline('p', [dict(id='s',title='阶段')], [dict(id='m',stage_id='s',title='模块',prerequisites=[])])
    store.save_generated_module('p','m',[dict(id='old'+str(i),title='旧点'+str(i),summary='旧知识',minutes=20,
        chunk_ids=['c']) for i in range(6)],check_minutes=10,today=START)
    seed_assessment(store,'p')
    store.start_plan('p')
    store.add_credit('p','credit0','old0',10,START,'confirmed')
    store.save_classroom_position('p',dict(module_id='m',index=2,phase='learning',step='opening',in_session=False,
        completed_requests=['keep-request'],pending=None,test=None,check=None))


def concept(pid='new1', minutes=30):
    return dict(id=pid,module_id='m',position=2,title='新概念',summary='掌握目标',structure='concept_v1',goal='掌握目标',
        minutes=minutes,chunk_ids=['c'],subpoints=[dict(id=pid+'-s1',title='小要点',minutes=minutes,
            chunk_ids=['c'],notes=[dict(text='依据正文',chunk_id='c')])])


def proposal():
    return dict(plan_id='p',today=DAY.isoformat(),modules=[dict(module_id='m',replace_point_ids=['old2','old3','old4','old5'],
        points=[concept()],navigation=[dict(id='nav',title='书单',chunk_ids=['c'],reason='只供查看')])])


@pytest.fixture
def store(tmp_path):
    with CourseStore('account',tmp_path) as s:
        seed(s)
        yield s


def dump(store): return '\n'.join(store._db.iterdump())


def test_restructure114_preview_apply_preserve_facts_navigation_retire_position_and_debt(store):
    points = {p['id']:p for p in store.records('points','p')}
    progress = {e:store.records(e,'p') for e in PROGRESS}
    before = dump(store)
    report = apply_structure(store,'p',proposal(),dry_run=True)
    assert dump(store) == before and report['can_apply']
    assert (report['retained_old_points'], report['replaced_old_points'], report['new_points'],report['navigation_entries']) == (2,4,1,1)
    assert report['before']['overdue_to_repay'] == report['after']['overdue_to_repay'] > 0
    assert all(report['before']['targets'][d]==n for d,n in report['after']['targets'].items() if d<DAY.isoformat())
    assert apply_structure(store,'p',proposal(),dry_run=False) == report
    rows = {p['id']:p for p in store.records('points','p')}
    assert set(rows)=={'old0','old1','new1'}
    assert rows['old0']==points['old0'] and rows['old1']==points['old1']
    module = store.records('modules','p')[0]
    assert module['navigation']==proposal()['modules'][0]['navigation']
    assert module['point_count']==3 and module['learning_minutes']==70
    bank = {q['id']:q for q in store.records('question_bank','p')}
    assert all(bank['old'+str(i)]['role']=='retired' for i in range(2,6))
    assert bank['old0']['role']!='retired'
    assert all(store.records(e,'p')==rows for e,rows in progress.items() if e!='ledger_events')
    scene=store.classroom_position('p')['state']
    assert scene['index']==2 and scene['completed_requests']==['keep-request'] and scene['step']=='teach'
    event=next(e for e in store.records('ledger_events','p') if e.get('cause')=='restructure')
    assert event['total_minutes']==report['total_after']
    ledger=store.load_ledger('p')
    assert ledger.status(DAY).debt_minutes==report['after']['debt_minutes']
    assert {d.isoformat():n for d,n in ledger.targets.items()}==report['after']['targets']
    assert store._db.execute('SELECT version FROM schema_version').fetchone()[0]==SCHEMA_VERSION==10


@pytest.mark.parametrize('evidence',['credit','judgement','answer','practice','skip','taught','message'])
def test_restructure114_refuses_any_progress_in_covered_points_without_mutation(store,evidence):
    if evidence=='credit':store.add_credit('p','revoked','old3',5,DAY,'tentative');store.revoke_credits('p','old3',today=DAY)
    elif evidence=='judgement':store.append_judgement('p',Judgement('j',1,datetime(2026,10,14,tzinfo=timezone.utc),'casual','m',results=(('old3',False),)))
    elif evidence=='answer':
        with store.transaction():store._put('question_answers','p',dict(id='a',module_id='m',at='2026-10-14T00:00:00+00:00',
            context='quick_confirm',correct=True,point_ids=['old3'],wrong_points=[]))
    elif evidence=='practice':store.append_practice_result('p','old3','m',['old3'],False,at='2026-10-14T00:00:00+00:00')
    elif evidence=='skip':store.append_attempt_closed('p',AttemptClosed('skip',1,'m','attempt','skip_test'))
    elif evidence=='taught':
        with store.transaction():
            p=store._get('points','p','old3');p['taught']=True;store._put('points','p',p)
    elif evidence=='message':
        with store.transaction():store._put('messages','p',dict(id='msg',module_id='m',point_id='old3',role='assistant',kind='teach',
            content='已经讲过',at='2026-10-14T00:00:00+00:00',compressed=False,summary_of=[]))
    before=dump(store)
    preview=apply_structure(store,'p',proposal(),dry_run=True)
    assert not preview['can_apply'] and any('已有进度' in i for i in preview['issues'])
    with pytest.raises(RestructureError):apply_structure(store,'p',proposal(),dry_run=False)
    assert dump(store)==before


@pytest.mark.parametrize('mutation', ['missing_chunk','note_chunk','sub_id','minutes','goal','navigation','subpoint_question'])
def test_restructure114_optional_field_validation(store,mutation):
    p=concept()
    entity='points'
    if mutation=='missing_chunk':p['subpoints'][0]['chunk_ids']=['missing'];p['subpoints'][0]['notes'][0]['chunk_id']='missing';p['chunk_ids']=['missing']
    elif mutation=='note_chunk':p['subpoints'][0]['notes'][0]['chunk_id']='other'
    elif mutation=='sub_id':p['subpoints'][0]['id']='wrong-s1'
    elif mutation=='minutes':p['minutes']+=1
    elif mutation=='goal':p['goal']='另一个目标'
    elif mutation=='navigation':entity='modules';p=store.records('modules','p')[0];p['navigation']=[dict(id='n',title='导航',reason='资料',chunk_ids=['missing'])]
    elif mutation=='subpoint_question':entity='question_bank';p=store.records('question_bank','p')[0];p['subpoint_id']='new1-s1'
    before=dump(store)
    with pytest.raises(StoreError):
        with store.transaction():store._put(entity,'p',p)
    assert dump(store)==before


def test_restructure114_dangling_reference_report_and_incomplete_proposal(store):
    bad=proposal();p=bad['modules'][0]['points'][0]
    p['chunk_ids']=['missing'];p['subpoints'][0]['chunk_ids']=['missing'];p['subpoints'][0]['notes'][0]['chunk_id']='missing'
    report=apply_structure(store,'p',bad,dry_run=True)
    assert not report['can_apply'] and any('missing' in i for i in report['issues'])
    bad=proposal();bad['complete']=False
    assert not apply_structure(store,'p',bad,dry_run=True)['can_apply']


def test_restructure114_atomic_rollback_on_mid_apply_failure(store,monkeypatch):
    before=dump(store);original=store._put
    def fail(entity,*args,**kwargs):
        if entity=='modules':raise OSError('injected disk failure')
        return original(entity,*args,**kwargs)
    monkeypatch.setattr(store,'_put',fail)
    with pytest.raises(OSError):apply_structure(store,'p',proposal(),dry_run=False)
    assert dump(store)==before


def test_restructure114_backup_confirmation_exact_local_restore_and_new_activity_refusal(tmp_path):
    with CourseStore('account',tmp_path) as s:
        seed(s);before=dump(s)
        with pytest.raises(StoreError,match='必须先看预演'):cli.execute(s,'p',proposal(),'not confirmed')
        preview=apply_structure(s,'p',proposal(),dry_run=True)
        _,backup=cli.execute(s,'p',proposal(),preview['preview_token'])
    result=cli.rollback(tmp_path,'account','p',backup)
    assert result['local_restored'] and not result['queued_for_sync']
    with CourseStore('account',tmp_path) as s:
        assert dump(s)==before
        preview=apply_structure(s,'p',proposal(),dry_run=True)
        _,backup=cli.execute(s,'p',proposal(),preview['preview_token'])
        s.add_credit('p','new-study','new1',5,DAY,'confirmed')
    with pytest.raises(StoreError,match='覆盖新进度'):cli.rollback(tmp_path,'account','p',backup)


@pytest.mark.parametrize('incremental',[False,True])
def test_restructure114_two_devices_and_synced_rollback_compensates_immutable_replan(connection,tmp_path,incremental):
    server,transport,login=connection;server.incremental=incremental
    account=login.identity.account_id
    a=CourseStore(account,tmp_path/'a');b=CourseStore(account,tmp_path/'b')
    try:
        seed(a);CourseSync(a,transport).sync();CourseSync(b,transport).sync()
        old_points=a.records('points','p');old_modules=a.records('modules','p');old_ledger=a.load_ledger('p').to_dict()
        preview=apply_structure(a,'p',proposal(),dry_run=True)
        _,backup=cli.execute(a,'p',proposal(),preview['preview_token'])
        CourseSync(a,transport).sync();CourseSync(b,transport).sync()
        assert b.records('points','p')==a.records('points','p')
        assert b.records('modules','p')==a.records('modules','p')
        assert b.load_ledger('p').to_dict()==a.load_ledger('p').to_dict()
        a.close()
        with pytest.raises(StoreError,match='不能仅恢复本机'):cli.rollback(tmp_path/'a',account,'p',backup)
        cli.rollback(tmp_path/'a',account,'p',backup,resync=True)
        a=CourseStore(account,tmp_path/'a')
        assert a.records('points','p')==old_points and a.records('modules','p')==old_modules
        assert a.load_ledger('p').targets=={date.fromisoformat(k):v for k,v in old_ledger['targets'].items()}
        sent=CourseSync(a,transport).sync();CourseSync(b,transport).sync()
        assert not sent.get('failed') and not sent['pending']
        assert b.records('points','p')==old_points and b.records('modules','p')==old_modules
        assert b.load_ledger('p').to_dict()==a.load_ledger('p').to_dict()
        assert len([e for e in b.records('ledger_events','p') if e.get('cause')=='restructure'])==2
    finally:a.close();b.close()


def test_restructure114_legacy_optional_fields_and_shuffled_remote_dependencies(store,tmp_path):
    # Old records have no new fields and still work. New children can arrive first.
    apply_structure(store,'p',proposal(),dry_run=False)
    with CourseStore('account',tmp_path/'other') as target:
        records=store.pending_records(10000)
        for record in sorted(records,key=lambda r: r.entity in ('plans','sources','source_chunks')):
            target.apply_remote([(record,100+records.index(record))])
        assert target.records('points','p')==store.records('points','p')
        assert target.records('modules','p')==store.records('modules','p')


def test_restructure114_cli_rejects_running_software_before_opening_database(monkeypatch,tmp_path,capsys):
    monkeypatch.setattr(cli,'_running_software',lambda:[dict(ProcessId=123,Name='MentorFlow.exe')])
    assert cli.main(['--dry-run','--data-root',str(tmp_path),'--plan-id','p','--structure','unused.json'])==2
    assert 'APP_RUNNING' in capsys.readouterr().err and not list(tmp_path.rglob('course.sqlite3'))


def test_restructure114_sample113_shape_and_real_chunk_aliases(store):
    sample=json.loads(Path(__file__).with_name('sample114.json').read_text(encoding='utf-8'))
    with store.transaction():
        source=store.records('sources','p')[0]
        for cid in sample['chunk_ids']:
            store._put('source_chunks','p',dict(id=cid,source_id=source['id'],position=100,text='113 样例出处',page_start=None,page_end=None,section=[]))
        point=dict(sample,module_id='m',position=2)
        store._put('points','p',point)
    assert point['minutes']==sum(s['minutes'] for s in point['subpoints'])
    assert all(s['id'].startswith(point['id']+'-s') for s in point['subpoints'])


def test_restructure114_valid_question_subpoint_and_cross_plan_reference(store):
    with store.transaction():
        store._put('points','p',concept())
        q=store.records('question_bank','p')[0]
        q.update(id='newq',point_ids=['new1'],subpoint_id='new1-s1')
        store._put('question_bank','p',q)
    assert store._get('question_bank','p','newq')['subpoint_id']=='new1-s1'
    store.create_plan('other',window=PlanWindow(START,DAY,100))
    store.add_sources('other',[dict(id='other-src',kind='url',url='https://example.org/other',title='他计划',site='example.org',
        tier='official',credibility=1,adopted=True,chunks=[dict(chunk_id='other-c',text='其他计划',page_start=None,page_end=None,section=[])])])
    p=concept('cross');p['chunk_ids']=['other-c'];p['subpoints'][0]['chunk_ids']=['other-c'];p['subpoints'][0]['notes'][0]['chunk_id']='other-c'
    with pytest.raises(StoreError):
        with store.transaction():store._put('points','p',p)


def test_restructure114_confirmation_stale_and_manifest_write_failure_rolls_back(store,monkeypatch):
    preview=apply_structure(store,'p',proposal(),dry_run=True)
    store.add_credit('p','later','old0',1,DAY,'confirmed')
    with pytest.raises(StoreError,match='必须先看预演'):cli.execute(store,'p',proposal(),preview['preview_token'])
    preview=apply_structure(store,'p',proposal(),dry_run=True);before=dump(store)
    original=Path.write_text
    def fail(path,*args,**kwargs):
        if str(path).endswith('.bak.json'):raise OSError('manifest disk error')
        return original(path,*args,**kwargs)
    monkeypatch.setattr(Path,'write_text',fail)
    with pytest.raises(OSError):cli.execute(store,'p',proposal(),preview['preview_token'])
    assert dump(store)==before


def test_restructure114_instance_lock_and_cli_dry_apply_roundtrip(tmp_path,monkeypatch,capsys):
    from PySide6.QtCore import QLockFile
    with CourseStore('account',tmp_path) as s:seed(s)
    file=tmp_path/'apply-ready.json';file.write_text(json.dumps(proposal()),encoding='utf-8')
    monkeypatch.setattr(cli,'_running_software',lambda:[])
    lockdir=tmp_path/'runtime';lockdir.mkdir()
    lock=QLockFile(str(lockdir/'MentorFlow.instance.lock'));assert lock.tryLock(0)
    args=['--data-root',str(tmp_path),'--plan-id','p','--structure',str(file)]
    try:assert cli.main(['--dry-run',*args])==2
    finally:lock.unlock()
    out=tmp_path/'preview.json'
    assert cli.main(['--dry-run',*args,'--report',str(out)])==0
    token=json.loads(out.read_text(encoding='utf-8'))['preview_token']
    assert cli.main(['--apply',*args,'--confirm-preview',token])==0
    assert list((tmp_path/'learning/accounts/account').glob('*.bak'))


@pytest.mark.parametrize('evidence',['module_time','pending_question'])
def test_restructure114_indirect_duration_and_pending_question_are_progress(store,evidence):
    if evidence=='module_time':store.add_credit('p','test-time','test:m',2,DAY,'confirmed')
    else:
        scene=store.classroom_position('p')['state']
        scene['pending']=dict(point_ids=['old4'],question_id='old4')
        store.save_classroom_position('p',scene)
    preview=apply_structure(store,'p',proposal(),dry_run=True)
    assert not preview['can_apply'] and 'old4' in preview['protected']


def test_restructure114_replaced_points_remain_complete_live_sync_records(store):
    originals={p['id']:p for p in store.records('points','p')}
    apply_structure(store,'p',proposal(),dry_run=False)
    for record in store.pending_records(10000):
        if record.entity=='points' and record.payload['id'] in {'old2','old3','old4','old5'}:
            assert not record.deleted and record.payload['replaced_by']==['new1']
            assert all(record.payload[k]==v for k,v in originals[record.payload['id']].items())


def test_restructure114_cli_uses_account_refresh_day(tmp_path,monkeypatch):
    from refactor.learning.app.settings import AppSettings
    with CourseStore('account',tmp_path) as s:seed(s)
    AppSettings('account',tmp_path).save(dict(refresh_time='08:00'))
    zone=datetime.now().astimezone().tzinfo
    instant=datetime(2026,10,14,6,tzinfo=zone)
    class Clock:
        @staticmethod
        def now(tz=None):return instant.astimezone(tz) if tz is not None else instant
    monkeypatch.setattr(cli,'datetime',Clock)
    monkeypatch.setattr(cli,'_running_software',lambda:[])
    data=proposal();data.pop('today')
    file=tmp_path/'structure.json';file.write_text(json.dumps(data),encoding='utf-8')
    output=tmp_path/'preview.json'
    assert cli.main(['--dry-run','--data-root',str(tmp_path),'--plan-id','p','--structure',str(file),'--report',str(output)])==0
    assert json.loads(output.read_text(encoding='utf-8'))['today']=='2026-10-13'
