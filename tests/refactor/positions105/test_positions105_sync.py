"""Two real stores/Classrooms, existing mock-HTTP sync protocol, no model/network."""
import copy
from dataclasses import replace
from datetime import datetime,timezone
import json
import os
from types import SimpleNamespace

import pytest

from refactor.learning.course.store import CourseStore,SCHEMA_VERSION,MAX_PAYLOAD,_json
from refactor.learning.course.mastery import Judgement,AttemptClosed
from refactor.learning.course.sync_contract import SyncRecord
from refactor.learning.course.sync_course import CourseSync
from tests.refactor.direct97 import support97 as h
from tests.refactor.sync_course.test_sync_course import connection

PLAN=h.PLAN
NOW=datetime(2026,10,10,10,tzinfo=timezone.utc)


@pytest.fixture
def pair(tmp_path,monkeypatch,connection):
    server,transport,login=connection
    server.incremental=True
    monkeypatch.setattr(h,'ACCOUNT',login.identity.account_id)
    a=h.make(tmp_path/'a',questions=0,points=5)
    # Use today's original-source bank schema. Old fill-format fixture rows
    # are intentionally retired on remote ingestion by the v8 migration.
    from tests.refactor.mastery70.test_question70_assessment_flow import rows
    questions=rows((5,))
    for i,q in enumerate(questions):
        q.update(point_ids=[f'p{i}'])
        for key in ('source','answer_source'):q[key].update(chunk_ids=['k1'])
    a.store.add_bank_questions(PLAN,'m0',questions)
    a.store.save_outline(PLAN,[],[dict(id='m2',stage_id='st',title='AI 大模型、Agent 与 RAG 核心')])
    a.store.save_generated_module(PLAN,'m2',[dict(id=f't{i}',title=f'Agent点{i}',summary='处理顺序',minutes=10,chunk_ids=['k1']) for i in range(5)],
        check_minutes=0,final_check_minutes=0,check_question_minutes=2,today=h.DAY)
    a.store.save_module_preparation(PLAN,'m2',dict(state='done'))
    bstore=CourseStore(login.identity.account_id,tmp_path/'b')
    b=SimpleNamespace(store=bstore,teacher=h.Teacher())
    b.services=SimpleNamespace(**{**vars(a.services),'store':bstore,'data_root':tmp_path/'b','gateway':b.teacher})
    b.room=h.fresh(b)
    sync=lambda e:CourseSync(e.store,transport).sync(limit=1000)
    try:yield a,b,server,sync
    finally:h.close(a);h.close(b)


def skips(a):
    for i,q in enumerate(a.store.records('question_bank',PLAN)):
        a.store.append_judgement(PLAN,Judgement('j'+str(i),i,NOW,'skip_test','m0','s0',tuple((p,True) for p in q['point_ids']),question_id=q['id']))
    a.store.append_attempt_closed(PLAN,AttemptClosed('done0',10,'m0','s0','skip_test'))
    a.store.add_credit(PLAN,'learn:other','other',10,h.DAY,'confirmed')
    a.store.append_attempt_closed(PLAN,AttemptClosed('done1',11,'m1','s1','skip_test'))
    for i in range(2):a.store.add_credit(PLAN,f'learn:t{i}',f't{i}',10,h.DAY,'tentative')
    co=a.room._course(PLAN);state=a.room._rebuild(co)
    assert state['module_id']=='m2' and state['index']==2
    state.update(step='teaching',day=h.DAY.isoformat(),in_session=True,conversation=dict(point_id='t2',options=[],interrupted=True))
    a.room._save(co,state)


def test_positions105_bidirectional_latest_and_old_file_never_rewinds(pair):
    a,b,server,sync=pair
    skips(a);sync(a);sync(b)
    # The destination has an older unsynced position from its old installation.
    old=dict(version=2,module_id='m0',index=3,step='opening',phase='learning',pending=None,test=None,check=None)
    legacy=b.room._file(PLAN);legacy.write(old);os.utime(legacy.path,ns=(1_600_000_000_000_000_000,)*2)
    before=legacy.path.read_bytes()
    v=h.act(b,'enter');assert v['module']['id']=='m2' and v['points'][2]['current']
    assert legacy.path.read_bytes()==before
    for _ in range(2):
        v=h.act(b,'next')
        # Meeting today's target pauses before the next point; continuing is
        # a separate ordinary classroom action, unrelated to position sync.
        if v['step']=='teach':v=h.act(b,'next')
        assert v['step']=='opening'
        h.act(b,'choose',0)
    sync(b);sync(a)
    v=h.act(a,'enter');assert v['module']['id']=='m2' and v['points'][4]['current']
    assert a.store.load_ledger(PLAN).to_dict()==b.store.load_ledger(PLAN).to_dict()


def test_positions105_pending_quiz_moves_with_same_answer_id_and_no_double_score(pair):
    a,b,server,sync=pair
    h.position(a,0)
    v=h.act(a,'start_test','skip_test')
    first=a.room._load(a.room._course(PLAN))['pending']['answer_id']
    h.act(a,'answer',[0])
    sync(a);sync(b)
    original=a.room._load(a.room._course(PLAN))
    v=h.act(b,'enter');restored=b.room._load(b.room._course(PLAN))
    assert restored['pending']['answer_id']==original['pending']['answer_id']
    assert restored['pending']['question']==original['pending']['question']
    assert [q['id'] for q in restored['test']['queue']]==[q['id'] for q in original['test']['queue']]
    assert len(b.store.records('question_answers',PLAN))==1
    assert b.store.records('question_answers',PLAN)[0]['id']==first
    h.act(b,'answer',[0],request_id='shared-answer')
    h.act(b,'answer',[0],request_id='shared-answer')
    sync(b);sync(a)
    assert len(a.store.records('question_answers',PLAN))==2
    assert a.store.load_mastery(PLAN).to_dict()==b.store.load_mastery(PLAN).to_dict()


def test_positions105_direct_review_and_reveal_ticks_survive_other_device(pair):
    a,b,server,sync=pair
    co=a.room._course(PLAN);state=a.room._rebuild(co)
    state.update(module_id='m2',index=0)
    a.room._save(co,state)
    h.act(a,'start_test','skip_test');h.act(a,'reply','这是我自己的答案');h.act(a,'direct_next')
    sync(a);sync(b)
    v=h.act(b,'enter')
    assert v['direct_review']['entries'][0]['answer']=='这是我自己的答案'
    assert v['direct_review']['number']==2 and not v['direct_review']['answered']
    assert not b.store.records('question_answers',PLAN)


def test_positions105_recall_answer_and_unsubmitted_ticks_move_without_scoring(pair):
    a,b,_,sync=pair
    co=a.room._course(PLAN)
    q=copy.deepcopy(a.store.records('question_bank',PLAN)[0])
    q.pop('_field_versions',None)
    q.update(id='recall105',kind='recall',objective_kind=None,answer='',options=[],
        stem='队列采用什么顺序？先加入的怎样取出？',
        reference='队列采用先进先出的顺序。先加入的先取出。',
        key_points=['队列采用先进先出的顺序。','先加入的先取出。'])
    a.store.add_bank_questions(PLAN,'m0',[q])
    st=a.room._rebuild(co)
    st.update(step='testing',test=dict(kind='module_test',attempt='recall-attempt105',queue=[q],index=0))
    a.room._show_question(co,st,q,'module_test',number=1,total=1)
    a.room._save(co,st)
    h.act(a,'recall_reveal',q['id'],'我写的回答：按加入的先后取出。')
    h.act(a,'recall_draft',q['id'],[0])
    original=a.room._load(co)['pending']
    sync(a);sync(b)
    view=h.act(b,'enter')
    assert view['question']['revealed'] and view['question']['ticked']==[0]
    assert view['question']['answer']=='我写的回答：按加入的先后取出。'
    assert b.room._load(b.room._course(PLAN))['pending']['answer_id']==original['answer_id']
    assert not b.store.records('question_answers',PLAN) and not b.store.records('judgements',PLAN)
    h.act(b,'recall_submit',q['id'],[0],True)
    sync(b);sync(a)
    assert len(a.store.records('question_answers',PLAN))==1
    assert a.store.records('question_answers',PLAN)[0]['answer']==original['learner_answer']


def test_positions105_passed_module_teaching_rebuilt_but_result_is_kept(pair):
    a,b,_,sync=pair
    skips(a)
    co=a.room._course(PLAN);st=a.room._load(co)
    st.update(module_id='m0',index=0,step='opening')
    a.room._save(co,st)
    before={k:copy.deepcopy(a.store.records(k,PLAN)) for k in ('credits','judgements','attempt_closed')}
    v=h.act(a,'enter');assert v['module']['id']=='m2' and v['points'][2]['current']
    assert all(a.store.records(k,PLAN)==v for k,v in before.items())
    st=a.room._load(co);st.update(module_id='m0',step='test_done',pending=None,test=None)
    a.room._save(co,st)
    assert h.act(a,'enter')['step']=='test_done'
    sync(a);sync(b);assert h.act(b,'enter')['step']=='test_done'


def test_positions105_taught_point_start_rebuilt_but_half_taught_kept(pair):
    a,_,_,_=pair
    h.position(a,2);co=a.room._course(PLAN)
    st=a.room._load(co);st.update(index=0,step='teach');a.room._save(co,st)
    assert a.room._load(co)['index']==2
    st.update(step='teaching',conversation=dict(point_id='p0',options=[]));a.room._save(co,st)
    assert a.room._load(co)['index']==0


def test_positions105_legacy_import_clock_later_file_wins_even_if_uploaded_late(pair):
    a,b,_,sync=pair
    sync(a);sync(b)
    co=a.room._course(PLAN);st=a.room._rebuild(co)
    newer=copy.deepcopy(st);newer.update(index=2,step='teaching')
    older=copy.deepcopy(st);older.update(index=1,step='teaching')
    for e,state,stamp in ((a,newer,1_700_000_000_000_000_000),(b,older,1_600_000_000_000_000_000)):
        legacy=e.room._file(PLAN);legacy.write(state);os.utime(legacy.path,ns=(stamp,stamp))
        assert e.room._load(e.room._course(PLAN))['index']==state['index']
        assert legacy.path.exists()
    sync(a);sync(b);sync(a)
    assert a.room._load(a.room._course(PLAN))['index']==2
    assert b.room._load(b.room._course(PLAN))['index']==2
    assert a.store.classroom_position(PLAN)['saved_at'].startswith('2023-11-14')


def test_positions105_newer_migration_not_overwritten_by_late_acknowledged_older_record(pair):
    a,b,_,_=pair
    co=a.room._course(PLAN);st=a.room._rebuild(co)
    a.store.save_classroom_position(PLAN,dict(st,index=2),modified_ns=200)
    r=next(r for r in a.store.pending_records(1000) if r.entity=='classroom_positions')
    a.store.mark_synced(r.entity,r.entity_id,r.local_revision,1)
    payload=copy.deepcopy(r.payload);payload['state']['index']=1
    payload['_field_versions']['state']=[100,'older-device']
    a.store.apply_remote([(replace(r,payload=payload),2)])
    assert a.store.classroom_position(PLAN)['state']['index']==2
    assert any(r.entity=='classroom_positions' for r in a.store.pending_records(1000))


def test_positions105_unknown_entity_skips_logs_and_incremental_cursor_advances(pair,caplog):
    a,b,server,sync=pair
    sync(a)
    unknown=SyncRecord('future_positions','32:'+PLAN+PLAN,PLAN,'mentorflow.course.future_positions.v1',{'id':PLAN},False,1)
    server.put_remote(unknown)
    a.store.save_teacher_profile(PLAN,dict(marker='unknown之后仍可同步'))
    sync(a);sync(b)
    assert b.store.records('teacher_profile',PLAN)[0]['document']['marker']=='unknown之后仍可同步'
    assert json.loads(b.store.sync_cursor())['since']==str(server.sequence)
    assert 'skipped unknown entity: future_positions' in caplog.text
    assert sync(b)['pulled']==0


def test_positions105_v9_database_upgrade_and_plan_delete_cascades(pair):
    a,_,_,_=pair
    path=a.store.path;a.store.close()
    import sqlite3
    with sqlite3.connect(path) as db:
        db.execute('DROP TABLE classroom_positions');db.execute('UPDATE schema_version SET version=9')
    a.store=CourseStore(a.services.account_id,a.services.data_root);a.services.store=a.store
    assert a.store.get_plan(PLAN)['state']=='active'
    assert a.store._db.execute('SELECT version FROM schema_version').fetchone()[0]==SCHEMA_VERSION==11
    h.act(a,'enter');assert a.store.classroom_position(PLAN)
    a.store.delete_plan(PLAN);assert a.store.classroom_position(PLAN) is None


def test_positions105_large_queue_is_bounded_and_server_accepts_entity(pair):
    a,_,_,_=pair
    co=a.room._course(PLAN);st=a.room._rebuild(co)
    q=a.store.records('question_bank',PLAN)[0]
    full=dict(q,source=dict(q['source'],text='原文'*1500))
    st.update(step='testing',test=dict(kind='module_test',attempt='large',queue=[full]*541,index=0),
        pending=dict(kind='module_test',question=full,answer_id='pending-unchanged'))
    assert len(_json(st,bounded=False).encode('utf8'))>MAX_PAYLOAD
    a.room._save(co,st)
    record=next(r for r in a.store.pending_records(1000) if r.entity=='classroom_positions')
    assert len(_json(record.payload).encode('utf8'))<32*1024
    loaded=a.room._load(co)
    assert len(loaded['test']['queue'])==541 and loaded['pending']['question']==full
    from refactor.server.enterprise_tenancy.structured_sync import _validate_course_record
    _validate_course_record({k:getattr(record,k) for k in ('schema','plan_key','entity','entity_id','payload')})


def test_positions105_foreign_process_flags_cleared_not_quiz_identity(pair):
    a,b,_,sync=pair
    co=a.room._course(PLAN);st=a.room._rebuild(co)
    st.update(step='teaching',day='2000-01-01',in_session=True,failure=dict(message='本机请求断了'),materials_paused=True)
    a.room._save(co,st);sync(a);sync(b)
    got=b.room._load(b.room._course(PLAN))
    assert got['day'] is None and not got['in_session'] and 'failure' not in got and 'materials_paused' not in got
    assert got['step']=='teaching'
