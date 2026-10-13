"""114: real store and source compiler; only locator/reviewer are fake."""
from datetime import date, timedelta
import pytest
from refactor.learning.course import point_materials as pm, question_bank as qb
from refactor.learning.course.store import CourseStore
from refactor.learning.course.ledger import PlanWindow

TEXT='队列采用先进先出。栈采用后进先出。优先队列采用优先级顺序。'
TEXT2='密度等于质量除以体积。速度等于位移除以时间。压强等于压力除以面积。'


def pointer(cid='c1', second=False):
    if second:
        return dict(target='密度怎样计算？',chunk_id=cid,sentence='密度等于质量除以体积。',word='质量除以体积',subject='密度',
            contrasts=[dict(chunk_id=cid,contrast_quote='速度等于位移除以时间。',contrast_subject='速度',contrast_value='位移除以时间'),
                       dict(chunk_id=cid,contrast_quote='压强等于压力除以面积。',contrast_subject='压强',contrast_value='压力除以面积')])
    return dict(target='队列采用哪种顺序？',chunk_id=cid,sentence='队列采用先进先出。',word='先进先出',subject='队列',
        contrasts=[dict(chunk_id=cid,contrast_quote='栈采用后进先出。',contrast_subject='栈',contrast_value='后进先出'),
                   dict(chunk_id=cid,contrast_quote='优先队列采用优先级顺序。',contrast_subject='优先队列',contrast_value='优先级顺序')])


@pytest.fixture
def store(tmp_path):
    s=CourseStore('questions114',tmp_path)
    today=date(2026,10,14)
    s.create_plan('p',goal='数据结构与物理',window=PlanWindow(today,today+timedelta(days=5),100))
    s.add_sources('p',[dict(id='src',title='合成教材',adopted=True,url='https://example.edu/text',chunks=[
        dict(chunk_id='c1',text=TEXT,section=['数据结构']),dict(chunk_id='c2',text=TEXT2,section=['物理']),
        dict(chunk_id='empty',text='这是资料导航，不含可用知识。',section=['导航']),
        dict(chunk_id='found',text='1. 计算 1+1=____。\n答案：2',section=['练习'])])])
    s.save_outline('p',[dict(id='stage',title='阶段')],[dict(id='m',stage_id='stage',title='模块',prerequisites=[])])
    subs=[dict(id='point-s1',title='队列顺序',minutes=5,chunk_ids=['c1'],notes=[]),
          dict(id='point-s2',title='密度',minutes=5,chunk_ids=['c2'],notes=[]),
          dict(id='point-s3',title='不可备题',minutes=5,chunk_ids=['empty'],notes=[])]
    s.save_generated_module('p','m',[dict(id='point',title='大知识点',summary='掌握原文概念',minutes=15,
        chunk_ids=['c1','c2','empty'],structure='concept_v1',goal='掌握原文概念',subpoints=subs)],check_minutes=5,today=today)
    yield s
    s.close()


def reviewer(packet,**kwargs):
    return dict(questions=[dict(id=q['id'],checks=[dict(index=i,verdict='not_supported',quote='')
        for i in range(len(q['options'])) if i not in q['answer']]) for q in packet['questions']])


def locator(packet,passages,**kwargs):
    return dict(subpoints=[dict(id=s['id'],cloze=[] if s['id'].endswith('s3') else
        [pointer(s['chunk_ids'][0],s['id'].endswith('s2'))]) for s in packet['subpoints']])


def ctx(**changes):return dict(finder=locator,distractor_checker=reviewer,**changes)


def put(store,entity,row):
    with store.transaction():store._put(entity,'p',row)


def test_subpoint114_generate_tag_skip_persist_and_reopen(store,tmp_path):
    point=store.records('points','p')[0]
    result=pm.prepare_point(store,'p',point,ctx())
    assert result['questions']==1 and result['skipped']==2  # strict historical contrast containment rejects queue/priority queue
    rows=store.records('question_bank','p')
    assert rows[0]['subpoint_id']=='point-s2'
    assert rows[0]['source']['locator'].endswith('[c2]')
    assert qb.checked_question(rows[0]) and qb.assessment_question(rows[0])
    reasons=store.records('points','p')[0]['subpoint_preparation']['rejections']
    assert {r['subpoint_id'] for r in reasons}=={'point-s1','point-s3'}
    with CourseStore(store.account_id,tmp_path) as reopened:
        assert pm.prepare_point(reopened,'p',point,{'finder':lambda *a,**k:pytest.fail('repeat locator')})==result
        assert qb.assessment_question(reopened.records('question_bank','p')[0])


def test_subpoint114_three_subpoints_get_three_not_two_questions(store):
    # Three distinct original exercises, one per subpoint; never call the model.
    for i in range(3):
        chunk=store._get('source_chunks','p',f'c{i+1}' if i<2 else 'empty')
        chunk['text']=f'{i+1}. 计算 {i}+1=____。\n答案：{i+1}'
        put(store,'source_chunks',chunk)
    result=pm.prepare_module(store,'p','m',{},lambda *a:None)
    assert result['questions']==3 and result['objective']==3
    rows=qb.assessment_questions(store.records('question_bank','p'),'m')
    assert {q['subpoint_id'] for q in rows}=={'point-s1','point-s2','point-s3'}
    for purpose in ('module','retest','final','free','direct'):
        used=[]
        for _ in range(3):
            q=pm.pick_question(store,'p','point',purpose,used);assert q;used.append(q['id'])
        assert pm.pick_question(store,'p','point',purpose,used) is None
    assert pm.preparation_done(store,'p','m')


def test_subpoint114_out_of_scope_pointer_is_rejected(store):
    def bad(packet,*a,**k):return dict(subpoints=[dict(id=s['id'],cloze=[pointer('c1')]) for s in packet['subpoints']])
    result=pm.prepare_point(store,'p',store.records('points','p')[0],dict(finder=bad,distractor_checker=reviewer))
    assert result['questions']==0
    assert len(store.records('points','p')[0]['subpoint_preparation']['rejections'])==3


def test_subpoint114_also_supported_or_missing_review_cannot_enter_bank(store):
    result=pm.prepare_point(store,'p',store.records('points','p')[0],dict(finder=locator,distractor_checker=lambda *a,**k:dict(questions=[])))
    assert result['questions']==0
    assert any(r['reason']=='干扰项核对未通过' for r in store.records('points','p')[0]['subpoint_preparation']['rejections'])


def test_subpoint114_legacy_point_still_only_extracts_material_questions(store):
    p=store._get('points','p','point')
    for key in ('structure','goal','subpoints'):p.pop(key)
    p['chunk_ids']=['empty'];put(store,'points',p)
    assert pm.prepare_point(store,'p',p,{})['questions']==0
    assert pm.pick_question(store,'p','point','module') is None
    assert not qb.assessment_question(dict(kind='choice',origin='cloze',role='bank'))


def test_subpoint114_schema_rejects_wrong_parent_and_assessment_deduplicates_only_subpoints(store):
    p=store._get('points','p','point');p['subpoints']=[dict(id='point-s1',title='原题',chunk_ids=['found'],minutes=15,notes=[])]
    p['chunk_ids']=['found'];put(store,'points',p)
    assert pm.prepare_point(store,'p',p,{})['questions']==1
    q=store.records('question_bank','p')[0];qb.validate_question(q)
    with pytest.raises(ValueError,match='小要点'):qb.validate_question(dict(q,subpoint_id='another-s1'))
    assert len(qb.assessment_questions([q,dict(q,id='duplicate')]))==1
    old=dict(q);old.pop('subpoint_id')
    assert len(qb.assessment_questions([old,dict(old,id='legacy2')]))==2


def test_subpoint114_mixed_module_preserves_legacy_questions_and_grading(store):
    p=store._get('points','p','point')
    p['subpoints']=p['subpoints'][1:2];p['chunk_ids']=['c2'];p['minutes']=5;put(store,'points',p)
    with store.transaction():
        store._put('points','p',dict(id='old',module_id='m',position=1,title='旧点',summary='原题',
            minutes=15,chunk_ids=['found']))
    def splitter(packet,**kwargs):
        return dict(questions=[dict(candidate_id=c['id'],kind='objective',stem=c['stem'],
            options=c.get('options',[]),answer=c['answer'],objective_kind='fill') for c in packet['candidates']])
    pm.prepare_module(store,'p','m',dict(finder=locator,distractor_checker=reviewer,question_splitter=splitter),lambda *a:None)
    rows=qb.assessment_questions(store.records('question_bank','p'),'m')
    assert len(rows)==2
    assert len([q for q in rows if q.get('subpoint_id')=='point-s2'])==1
    old=next(q for q in rows if 'old' in q['point_ids'])
    assert 'subpoint_id' not in old
    from refactor.learning.course.answer_compare import grade
    assert grade(qb.grading_question(old),'2')[0]
    new=next(q for q in rows if q.get('subpoint_id'))
    assert grade(new,new['answer'])[0]


def test_subpoint114_shared_original_exercise_counts_only_actual_admission(store):
    p=store._get('points','p','point')
    p['subpoints']=[dict(id=f'point-s{i+1}',title='共享原题',chunk_ids=['found'],minutes=5,notes=[]) for i in range(3)]
    p['chunk_ids']=['found'];put(store,'points',p)
    result=pm.prepare_point(store,'p',p,{})
    assert result['questions']==1 and result['skipped']==2
    assert len(store.records('question_bank','p'))==1
    saved=store.records('points','p')[0]['subpoint_preparation']
    assert len(saved['rejections'])==2
    assert all('重复' in r['reason'] for r in saved['rejections'])
