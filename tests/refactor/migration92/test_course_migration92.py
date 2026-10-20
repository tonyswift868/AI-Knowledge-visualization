"""Versioned upgrade and old-device sync, without a model or user-data access."""
from contextlib import closing
from dataclasses import asdict, replace
from datetime import date, datetime, timedelta, timezone
import json
import sqlite3
import threading
from types import SimpleNamespace

import pytest

from refactor.learning.course.store import CourseStore, StoreError, SCHEMA_VERSION, PROGRESS, _wire_id
from refactor.learning.course.ledger import PlanWindow
from refactor.learning.course.mastery import Judgement, AttemptClosed, event_to_dict, MasteryBook
from refactor.learning.course.question_bank import assessment_questions, select_question
from refactor.learning.course.material_questions import compile_question
from refactor.learning.course import point_materials
from refactor.learning.course.sync_contract import SyncRecord
from refactor.learning.app.classroom import Classroom
from refactor.learning.app.plan_flow import PlanFlow
from refactor.learning.app.bridge import CallContext

DAY=date(2026,10,10)
NOW=datetime(2026,10,10,10,tzinfo=timezone.utc)
PLAN='a'*32
TEXT='队列按先进先出处理元素。元素从队尾加入，从队首移除。'


def modern():
    c=dict(id='candidate',candidate_type='recall',stem='队列怎样处理元素？',reference=TEXT,
        answer=None,options=[],source_title='资料',source_url='',source_id='s',chunk_ids=['c'],block_ids=['b'],point_ids=['point'])
    return compile_question(dict(kind='recall',stem=c['stem']),c,'m')


def legacy(n=0,kind=None):
    q=dict(id=f'old-{n}',module_id='m',point_ids=['point'],type='choice',stem=f'旧题{n}？',
           options=['甲','乙'],answer='甲',reference='历史参考答案',angle='recall',chunk_ids=['c'])
    if kind is not None:q.update(kind=kind,origin='cloze',role='bank',uses=[])
    return q


def insert_raw(store,entity,payload):
    # The SQL fixture represents persisted older schemas; no production writer
    # is loosened just to manufacture the old data.
    identifier=_wire_id(entity,PLAN,payload['id'])
    raw=json.dumps(payload,ensure_ascii=False,sort_keys=True,separators=(',',':'))
    store._db.execute('INSERT OR REPLACE INTO _records VALUES(?,?,?,?,0,1,0,1)',(entity,identifier,PLAN,raw))
    store._materialize(entity,PLAN,payload,1)


def seed(root,version=1,questions=(),*,history=False,missing_source=False):
    s=CourseStore('migration92',root)
    s.create_plan(PLAN,goal='理解队列',window=PlanWindow(DAY,DAY+timedelta(days=3),120))
    s.add_sources(PLAN,[dict(id='s',title='资料',url='https://example.invalid',adopted=True,
        chunks=[dict(chunk_id='c',text=TEXT,section=['队列'])])])
    s.save_outline(PLAN,[dict(id='stage',title='基础')],[dict(id='m',stage_id='stage',title='队列',prerequisites=[])])
    s.save_generated_module(PLAN,'m',[dict(id='point',title='队列',summary='理解队列',minutes=8,chunk_ids=['c'])],check_minutes=2,today=DAY)
    s.start_plan(PLAN)
    with s.transaction():
        p=s.get_plan(PLAN)
        if version is None:p.pop('material_version',None)
        else:p['material_version']=version
        if version!=3:p.update(unusable=True,unusable_reason='历史版本标记')
        s._put('plans',PLAN,p)
        for q in questions:insert_raw(s,'question_bank',q)
        if missing_source:
            point=s._get('points',PLAN,'point');point['chunk_ids']=[]
            insert_raw(s,'points',point)
        if history:
            for e in (Judgement('j',1,NOW,'module_test','m','attempt',(('point',True),),question_id='old-0'),
                      AttemptClosed('closed',2,'m','attempt','module_test'),
                      Judgement('check',3,NOW,'check','m',results=(('point',True),),question_id='old-0')):
                kind='judgements' if isinstance(e,Judgement) else 'attempt_closed'
                s._put(kind,PLAN,dict(id=e.event_id,event=event_to_dict(e)))
            s.add_credit(PLAN,'learn:point','point',8,DAY,'confirmed')
    s._db.execute('UPDATE schema_version SET version=7')
    s.close()
    return CourseStore('migration92',root)


def services(store,root):
    return SimpleNamespace(store=store,account_id='migration92',data_root=root,
        settings=SimpleNamespace(load=lambda:dict(refresh_time='04:00')))


@pytest.mark.parametrize('version,questions,retired',[
    (None,[legacy()],1),(1,[legacy()],1),(2,[legacy(kind='fill')],1),(2,[],0),(3,[modern()],0),
])
def test_migration92_versions_keep_courses_open(tmp_path,version,questions,retired):
    with seed(tmp_path,version,questions) as s:
        assert SCHEMA_VERSION==11 and s._db.execute('SELECT version FROM schema_version').fetchone()[0]==SCHEMA_VERSION
        assert s.get_plan(PLAN)['material_version']==3 and not s.get_plan(PLAN).get('unusable')
        bank=s.records('question_bank',PLAN)
        assert sum(q.get('role')=='retired' for q in bank)==retired
        for old in questions:
            q=next(q for q in bank if q['id']==old['id'])
            assert all(q[k]==v for k,v in old.items() if k!='role')
        co=Classroom(services(s,tmp_path),lambda:DAY,now=lambda:NOW)._course(PLAN)
        assert len(co.modules)==1 and co.point('point')['minutes']==8
        detail=PlanFlow(services(s,tmp_path),lambda *a:None,lambda:DAY).detail(PLAN)
        assert detail['stages'][0]['modules'][0]['points'][0]['id']=='point'
        assert not detail.get('unusable')
        if retired or version in (1,None):
            assert s.records('modules',PLAN)[0]['material_preparation']['state']=='pending'
            assert detail['migration_message']
            assert not point_materials.preparation_done(s,PLAN,'m')
        assert not assessment_questions(bank) if retired else len(assessment_questions(bank))==len(questions)


def test_migration92_history_mastery_and_passed_detection_are_retained(tmp_path):
    with seed(tmp_path,1,[legacy()],history=True) as s:
        book=s.load_mastery(PLAN)
        assert book.module('m').state=='passed' and book.module('m').module_test_count==1
        assert (book.point('point').state,book.point('point').level)==('green',3)
        assert book.stats()['check_questions']==0 and not book.stats()['questions']
        assert len(s.records('judgements',PLAN))==2 and len(s.records('attempt_closed',PLAN))==1
        assert len(s.records('credits',PLAN))==1
        assert MasteryBook.from_dict(book.to_dict()).to_dict()==book.to_dict()
        # Adding fresh material Qs cannot undo the historical passed test.
        s.add_bank_questions(PLAN,'m',[modern()])
        assert s.load_mastery(PLAN).module('m').state=='passed'
        assert s.load_mastery(PLAN).point('point').level==3
        assert not s.load_mastery(PLAN).question(modern()['id']).passed


def test_migration92_repeat_is_identical_and_no_model_is_used(tmp_path):
    with seed(tmp_path,1,[legacy()]) as s:
        snapshot='\n'.join(s._db.iterdump())
        with s.transaction():
            s._migrate();s._migrate()
            s._upgrade_material_plan(PLAN);s._upgrade_material_plan(PLAN)
        assert '\n'.join(s._db.iterdump())==snapshot


def test_migration92_failure_rolls_back_everything(tmp_path,monkeypatch):
    s=seed(tmp_path,3,[])
    with s.transaction():
        insert_raw(s,'question_bank',legacy(0));insert_raw(s,'question_bank',legacy(1))
        s._db.execute('UPDATE schema_version SET version=7')
    before='\n'.join(s._db.iterdump());path=s.path;s.close()
    original=CourseStore._retire_legacy_question
    calls=[]
    def failing(q):
        calls.append(q['id'])
        if len(calls)==2:raise RuntimeError('injected midway through upgrade')
        return original(q)
    monkeypatch.setattr(CourseStore,'_retire_legacy_question',staticmethod(failing))
    with pytest.raises(RuntimeError,match='injected'):CourseStore('migration92',tmp_path)
    with closing(sqlite3.connect(path)) as db:assert '\n'.join(db.iterdump())==before


@pytest.mark.parametrize('child_first',[False,True])
def test_migration92_old_sync_records_are_retired_and_reprepared(tmp_path,child_first):
    with seed(tmp_path/'source',3,[]) as source, CourseStore('migration92',tmp_path/'target') as target:
        records=source.pending_records(100)
        q=legacy()
        old=SyncRecord('question_bank',_wire_id('question_bank',PLAN,q['id']),PLAN,'mentorflow.course.question_bank.v1',q,False,1)
        if child_first:target.apply_remote([(old,1)])
        target.apply_remote([(r,2) for r in records])
        if not child_first:target.apply_remote([(old,3)])
        assert target.get_plan(PLAN)['material_version']==3
        assert target.records('question_bank',PLAN)[0]['role']=='retired'
        assert target.records('modules',PLAN)[0]['material_preparation']['state']=='pending'
        assert not assessment_questions(target.records('question_bank',PLAN))
        pending=next(r for r in target.pending_records(100) if r.entity=='question_bank')
        assert pending.payload['role']=='retired'  # durable repair also propagates
        target.apply_remote([(pending,5)])
        assert target.records('question_bank',PLAN)[0]['role']=='retired'


def test_migration92_retired_cards_cannot_be_selected_or_scored(tmp_path):
    with seed(tmp_path,1,[legacy()]) as s:
        bank=s.records('question_bank',PLAN)
        for purpose in ('quick','module','final','free'):
            assert select_question(bank,'point',purpose) is None
        with pytest.raises(StoreError) as error:s.record_question_use(PLAN,'old-0','module')
        assert error.value.code=='QUESTION_RETIRED'
        with pytest.raises(StoreError):s.append_question_answer(PLAN,'new-answer',module_id='m',context='module_test',attempt_id='a',question_id='old-0',point_ids=['point'],angle=None,correct=True)
        with pytest.raises(StoreError):s.append_practice_result(PLAN,'old-0','m',['point'],True)
        assert not s.records('question_answers',PLAN)
        assert s.add_bank_questions(PLAN,'m',[dict(modern(),id='old-0')])==[]
        assert s.records('question_bank',PLAN)==bank


def test_migration92_retired_practice_is_readable_but_not_current_review(tmp_path):
    with seed(tmp_path,1,[legacy()]) as s:
        with s.transaction():
            insert_raw(s,'practice_results',dict(id='past-practice',question_id='old-0',module_id='m',
                point_ids=['point'],correct=False,wrong_points=['point'],reason='',at=NOW.isoformat(),sequence=1))
        flow=PlanFlow(services(s,tmp_path),lambda *a:None,lambda:DAY)
        flow.ensure_point_links=lambda *a,**k:'pending'
        flow.ensure_point_minutes=lambda *a,**k:'pending'
        detail=flow.detail(PLAN)
        assert not detail['stages'][0]['modules'][0]['points'][0]['needs_review']
        assert s.records('practice_results',PLAN)[0]['correct'] is False


def test_migration92_missing_source_is_repaired_during_preparation_only(tmp_path):
    with seed(tmp_path,1,[],missing_source=True) as s:
        point=s.records('points',PLAN)[0]
        assert point['material_repair']=='missing_provenance' and point['chunk_ids']==[]
        assert not s.get_plan(PLAN).get('unusable')
        calls=[]
        def locate(packet,**kwargs):
            calls.append(packet)
            return dict(points=[dict(id='point',citations=[dict(chunk_id='c',quote=TEXT)])])
        result=point_materials.prepare_module(s,PLAN,'m',dict(source_locator=locate),lambda *a:None)
        assert result['questions']==0 and len(calls)==1
        point=s.records('points',PLAN)[0]
        assert point['chunk_ids']==['c'] and 'material_repair' not in point
        assert point_materials.preparation_done(s,PLAN,'m')
        assert s.records('credits',PLAN)==[]


def test_migration92_retired_cached_scene_is_rebuilt_without_losing_progress(tmp_path):
    with seed(tmp_path,1,[legacy()],history=True) as s:
        room=Classroom(services(s,tmp_path),lambda:DAY,now=lambda:NOW)
        course=room._course(PLAN)
        state=room._rebuild(course)
        state.update(step='question',pending=dict(kind='module_test',question=legacy()))
        room._file(PLAN).write(state)
        reopened=room._load(course)
        assert reopened['pending'] is None and room._questions(course)==[]
        assert course.book().module('m').state=='passed'
        # No automatic completion while replacement questions are pending.
        room.enter(course,reopened,None)
        assert s.get_plan(PLAN)['state']=='active'


def test_migration92_bad_json_still_fails_without_partial_upgrade(tmp_path):
    with seed(tmp_path,3,[]) as s:
        s._db.execute('PRAGMA ignore_check_constraints=ON')
        s._db.execute("UPDATE plans SET payload='not-json'")
        s._db.execute('UPDATE schema_version SET version=7')
        path=s.path
    with pytest.raises(json.JSONDecodeError):CourseStore('migration92',tmp_path)
    with closing(sqlite3.connect(path)) as db:
        assert db.execute('SELECT version FROM schema_version').fetchone()[0]==7
        assert db.execute('SELECT payload FROM plans').fetchone()[0]=='not-json'
