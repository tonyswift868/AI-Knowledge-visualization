"""Self-verdict, immutable review facts and sync on the real classroom/store."""
import json
from types import SimpleNamespace

import pytest

from tests.refactor.mastery70.test_question70_assessment_flow import env, act, position, answer, PLAN, DAY, NOW
from refactor.learning.app.classroom import Classroom, register
from refactor.learning.course.store import CourseStore, SCHEMA_VERSION


def begin(e):
    position(e,'m0')
    v=act(e,'start_test','module_test')
    qid=v['question']['id']
    act(e,'recall_reveal',qid,'学生自己写的七条理解；不是参考答案。')
    return qid


def test_essay99_seven_of_ten_can_self_pass_and_draft_survives_restart(env):
    e=env;qid=begin(e)
    e.room.run(PLAN,lambda co,st,cx:st['pending']['question'].update(key_points=[f'要点{i}' for i in range(10)]))
    v=act(e,'recall_draft',qid,list(range(7)))
    assert v['question']['ticked']==list(range(7))
    assert not e.store.records('judgements',PLAN) and not e.store.records('question_answers',PLAN)
    e.room=Classroom(e.services,lambda:DAY,now=lambda:NOW);e.room._prepare_next_module=lambda *a,**kw:None
    v=e.room.run(PLAN,lambda *a:None)
    assert v['question']['ticked']==list(range(7)) and v['question']['revealed']
    v=act(e,'recall_submit',qid,list(range(7)),True)
    assert v['recall_result']==dict(question_id=qid,correct=True,missed=[7,8,9])
    assert e.store.load_mastery(PLAN).question(qid).module_correct is True
    fact=e.store.records('question_answers',PLAN)[0]
    assert fact['self_correct'] is True and fact['missed']==[7,8,9]
    assert len(fact['question']['key_points'])==10
    assert fact['answer']=='学生自己写的七条理解；不是参考答案。'


def test_essay99_full_ticks_can_self_fail_and_notebook_all_attempts(env):
    e=env;qid=begin(e)
    v=act(e,'recall_submit',qid,[0,1,2],False)
    assert e.store.load_mastery(PLAN).question(qid).module_correct is False
    assert v['recall_result']==dict(question_id=qid,correct=False,missed=[])
    while v['question']:v=answer(e,v)
    v=act(e,'start_test','retest')
    assert v['question']['id']==qid
    act(e,'recall_reveal',qid,'第二次回答')
    act(e,'recall_submit',qid,[0],True)
    rows=e.store.wrong_answers(PLAN)
    assert len(rows)==1 and rows[0]['wrong_count']==1 and len(rows[0]['attempts'])==2
    assert {a['context'] for a in rows[0]['attempts']}=={'module_test','retest'}
    assert {a['self_correct'] for a in rows[0]['attempts']}=={False,True}
    assert all(a['question']['reference'] and a['dialogue'] for a in rows[0]['attempts'])
    assert any('第二次回答' in m['content'] for a in rows[0]['attempts'] for m in a['dialogue'])
    assert len({a['attempt_id'] for a in rows[0]['attempts']})==2


def test_essay99_objective_raw_choice_snapshot_and_read_only_bridge(env,tmp_path):
    e=env;begin(e);v=act(e,'recall_submit','q0-0',[],True)
    qid=v['question']['id'];act(e,'answer',[1])
    fact=next(a for a in e.store.records('question_answers',PLAN) if a['question_id']==qid)
    assert fact['answer']==[1] and fact['correct'] is False and 'self_correct' not in fact
    assert fact['question']['correct_options']==[0] and fact['question']['options']==['2','3']
    registry=SimpleNamespace(methods={},push=lambda *a:None)
    registry.register=lambda name,fn:registry.methods.__setitem__(name,fn)
    register(registry,lambda:e.services,lambda:DAY,now=lambda:NOW)
    before=(e.room._file(PLAN).path.read_bytes(),e.store.load_mastery(PLAN).to_dict(),e.store.pending_records(10000))
    for _ in range(2):
        notebook=registry.methods['classroom.wrong_answers'](dict(plan_id=PLAN),None)
        assert notebook[0]['question_id']==qid
    after=(e.room._file(PLAN).path.read_bytes(),e.store.load_mastery(PLAN).to_dict(),e.store.pending_records(10000))
    assert before==after
    remote=CourseStore('question70',tmp_path/'other-device')
    try:
        records=e.store.pending_records(10000)
        remote.apply_remote([(r,i+1) for i,r in enumerate(records)])
        remote.apply_remote([(r,i+1) for i,r in enumerate(records)])
        assert remote.wrong_answers(PLAN)==notebook
        assert remote.records('question_answers',PLAN)==e.store.records('question_answers',PLAN)
    finally:remote.close()


def test_essay99_v8_upgrade_keeps_old_payload_and_verdict(env,tmp_path):
    e=env;qid=begin(e);act(e,'recall_submit',qid,[],False)
    root=tmp_path/'legacy';legacy=CourseStore('question70',root)
    legacy.apply_remote([(r,i+1) for i,r in enumerate(e.store.pending_records(10000))])
    row=legacy.records('question_answers',PLAN)[0]
    for key in ('self_correct','missed','question','question_message_id','dialogue_channel'):row.pop(key,None)
    # This is an old on-disk payload, not a product mutation of progress.
    raw=json.dumps(row,ensure_ascii=False)
    legacy._db.execute('UPDATE question_answers SET payload=?',(raw,))
    legacy._db.execute('UPDATE schema_version SET version=8');legacy.close()
    reopened=CourseStore('question70',root)
    try:
        assert SCHEMA_VERSION==9 and reopened._db.execute('SELECT version FROM schema_version').fetchone()[0]==9
        assert reopened._db.execute('SELECT payload FROM question_answers').fetchone()[0]==raw
        saved=reopened.wrong_answers(PLAN)[0]['attempts'][0]
        assert saved['correct'] is False and saved['self_correct'] is None
        assert saved['missed']==[0,1,2] and saved['question']['reference']
    finally:reopened.close()


def test_essay99_no_verdict_does_not_autograde_and_atomic_failure(env,monkeypatch):
    e=env;qid=begin(e)
    bad=e.room.perform(PLAN,'recall_submit',None,(qid,[0,1,2],None))
    assert bad['failure'] and not e.store.records('judgements',PLAN)
    original=e.store.append_question_answer
    monkeypatch.setattr(e.store,'append_question_answer',lambda *a,**kw:(_ for _ in ()).throw(RuntimeError('disk simulation')))
    bad=e.room.perform(PLAN,'recall_submit',None,(qid,[0,1,2],False))
    assert bad['failure'] and not e.store.records('judgements',PLAN)
    monkeypatch.setattr(e.store,'append_question_answer',original)
    act(e,'recall_submit',qid,[0,1,2],False)
    assert len(e.store.records('judgements',PLAN))==len(e.store.records('question_answers',PLAN))==1


def test_essay99_final_levels_follow_self_choice_and_not_ticks(env):
    e=env;qid=begin(e);v=act(e,'recall_submit',qid,[],True)
    while v['question']:v=answer(e,v)
    position(e,'m1','closing');act(e,'next')
    position(e,'m2');v=act(e,'start_test','module_test')
    while v['question']:v=answer(e,v)
    act(e,'next_module');v=act(e,'start_check')
    # Drive only to the recall card (no model), leaving final check in progress.
    while not v['question'] or v['question']['id']!=qid:
        v=answer(e,v) if v['question'] else act(e,'start_check')
    act(e,'recall_reveal',qid,'本次写了所有要点')
    v=act(e,'recall_submit',qid,[0,1,2],False)
    assert not e.store.load_mastery(PLAN).question(qid).passed
    for n in range(2):
        while not v['question'] or v['question']['id']!=qid:
            v=answer(e,v) if v['question'] else act(e,'start_check')
        act(e,'recall_reveal',qid,'本次自己判对')
        v=act(e,'recall_submit',qid,[],True)
        assert e.store.load_mastery(PLAN).question(qid).passed is (n==1)
    assert any(g['question_id']==qid for g in e.store.wrong_answers(PLAN))


def test_essay99_review_covers_legacy_judgement_only_and_frozen_snapshot(env):
    e=env;qid=begin(e);act(e,'recall_submit',qid,[0],False)
    fact=e.store.records('question_answers',PLAN)[0]
    bank=next(q for q in e.store.records('question_bank',PLAN) if q['id']==qid)
    bank['stem']='资料更新后的另一题面'
    e.store._db.execute('UPDATE question_bank SET payload=? WHERE id=?',(json.dumps(bank),qid))
    assert e.store.wrong_answers(PLAN)[0]['question']['stem']==fact['question']['stem']
    # A pre-v5 database may have judgements but no question-answer table facts.
    e.store._db.execute('DELETE FROM question_answers')
    old=e.store.wrong_answers(PLAN)[0]['attempts'][0]
    assert old['correct'] is False and old['self_correct'] is None
    assert old['answer']=='学生自己写的七条理解；不是参考答案。'
