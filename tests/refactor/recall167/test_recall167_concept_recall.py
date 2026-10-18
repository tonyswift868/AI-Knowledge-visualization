from datetime import date, timedelta
import pytest
from refactor.learning.course import point_materials as pm, question_bank as qb
from refactor.learning.course.store import CourseStore
from refactor.learning.course.ledger import PlanWindow


@pytest.fixture
def recall167_store(tmp_path):
    store=CourseStore('recall167',tmp_path)
    today=date(2026,10,18)
    store.create_plan('p',goal='指南',window=PlanWindow(today,today+timedelta(days=5),60))
    store.add_sources('p',[dict(id='src',title='原文',adopted=True,url='https://example.test/guide',chunks=[
        dict(chunk_id='c1',text='正文原文。',section=['模块'])])])
    store.save_outline('p',[dict(id='stage',title='阶段')],[dict(id='m',stage_id='stage',title='模块',prerequisites=[])])
    store.save_generated_module('p','m',[dict(id='point',title='大点',summary='目标',minutes=10,
        chunk_ids=['c1'],structure='concept_v1',goal='目标',subpoints=[
            dict(id='point-s1',title='小点',minutes=10,chunk_ids=['c1'],notes=[])])],check_minutes=5,today=today)
    yield store
    store.close()


def recall167_candidate(stem='小要点原题是什么？',reference='原文第一点。原文第二点。原文第三点。',suffix='one'):
    return dict(id=f'material-question-recall167-{suffix}',candidate_type='recall',stem=stem,
        reference=reference,chunk_ids=['c1'],link_chunk_ids=['c1'],
        block_ids=[1,2],source_id='src',source_title='原文',source_url='https://example.test/guide')


def test_recall167_concept_compiles_own_recall_question(recall167_store,monkeypatch):
    from refactor.learning.course import material_questions as mq
    monkeypatch.setattr(mq,'candidates',lambda chunks,sources: [recall167_candidate()])
    monkeypatch.setattr(mq,'for_module',lambda rows,points: [dict(rows[0],point_ids=['point'])])
    monkeypatch.setattr(pm,'_concept_cloze',lambda *a,**k: pytest.fail('recall should suppress cloze'))
    result=pm.prepare_point(recall167_store,'p',recall167_store.records('points','p')[0],{})
    rows=qb.assessment_questions(recall167_store.records('question_bank','p'),'m')
    assert result['questions']==1 and len(rows)==1
    row=rows[0]
    assert row['kind']=='recall' and row['origin']=='found'
    assert row['stem']=='小要点原题是什么？'
    assert row['reference']=='原文第一点。原文第二点。原文第三点。'
    assert row['key_points']==['原文第一点。','原文第二点。','原文第三点。']
    assert row['subpoint_id']=='point-s1' and row['source']['chunk_ids']==['c1']
    qb.validate_question(row)


def test_recall167_keeps_distinct_recall_questions_for_one_subpoint(recall167_store,monkeypatch):
    from refactor.learning.course import material_questions as mq
    questions=[recall167_candidate(suffix='one'),
        recall167_candidate('另一个原题是什么？','第二题原文第一点。第二题原文第二点。第二题原文第三点。','two'),
        recall167_candidate(suffix='duplicate')]
    monkeypatch.setattr(mq,'candidates',lambda chunks,sources: questions)
    monkeypatch.setattr(mq,'for_module',lambda rows,points: [dict(q,point_ids=['point']) for q in rows])
    monkeypatch.setattr(pm,'_concept_cloze',lambda *a,**k: pytest.fail('recall should suppress cloze'))
    result=pm.prepare_point(recall167_store,'p',recall167_store.records('points','p')[0],{})
    rows=qb.assessment_questions(recall167_store.records('question_bank','p'),'m')
    assert result['questions']==2 and result['skipped']==0
    assert {q['stem'] for q in rows}=={'小要点原题是什么？','另一个原题是什么？'}
    saved_point=recall167_store.records('points','p')[0]
    marker=saved_point['subpoint_preparation']['processed_subpoints']['point-s1']
    assert marker['status']=='valid'
    assert any(r.get('subpoint_id')=='point-s1' and '重复' in r.get('reason','')
        for r in saved_point['subpoint_preparation']['rejections'])
    used=[]
    for _ in range(2):
        q=qb.select_question(rows,'point','module',used)
        assert q and q['kind']=='recall'
        used.append(q['id'])
    assert qb.select_question(rows,'point','module',used) is None
    assert len(qb.assessment_questions(rows+[dict(rows[0],id='same-content-different-id')],'m'))==2


def test_recall167_mixed_subpoint_keeps_objective_and_all_recalls(recall167_store,monkeypatch):
    from refactor.learning.course import material_questions as mq
    recall=recall167_candidate()
    recall_two=recall167_candidate('另一个原题是什么？',
        '第二题原文第一点。第二题原文第二点。第二题原文第三点。','two')
    objective=dict(id='material-question-objective167',candidate_type='objective',stem='1. 20+22=____',
        options=[],answer='42',chunk_ids=['c1'],link_chunk_ids=['c1'],block_ids=[3],source_id='src',
        source_title='原文',source_url='https://example.test/guide')
    monkeypatch.setattr(mq,'candidates',lambda chunks,sources: [recall,recall_two,objective])
    monkeypatch.setattr(mq,'for_module',lambda rows,points: [dict(q,point_ids=['point']) for q in rows])
    monkeypatch.setattr(pm,'_concept_cloze',lambda *a,**k: pytest.fail('material question should suppress cloze'))
    pm.prepare_point(recall167_store,'p',recall167_store.records('points','p')[0],{})
    rows=qb.assessment_questions(recall167_store.records('question_bank','p'),'m')
    assert len(rows)==3
    assert sum(q['kind']=='objective' for q in rows)==1
    assert sum(q['kind']=='recall' for q in rows)==2
    used=[]
    taken=[]
    for _ in range(3):
        q=qb.select_question(rows,'point','module',used)
        taken.append(q)
        if q:used.append(q['id'])
    assert all(taken) and {q['kind'] for q in taken}=={'objective','recall'}
    assert qb.select_question(rows,'point','module',used) is None
    recall_row=mq.compile_question(dict(kind='recall',stem=recall['stem']),dict(recall,point_ids=['point']),'m')
    recall_row['subpoint_id']='point-s1'
    objective_row=next(q for q in rows if q['kind']=='objective')
    assert len(qb.assessment_questions([recall_row,objective_row],'m'))==2
    assert len(qb.assessment_questions([objective_row,recall_row],'m'))==2


def test_recall167_long_answer_reuses_original_key_point_selector(recall167_store,monkeypatch):
    from refactor.learning.course import material_questions as mq
    candidate=recall167_candidate(reference='原文第一点。原文第二点。原文第三点。原文第四点。原文第五点。原文第六点。')
    monkeypatch.setattr(mq,'candidates',lambda chunks,sources: [candidate])
    monkeypatch.setattr(mq,'for_module',lambda rows,points: [dict(rows[0],point_ids=['point'])])
    calls=[]
    def splitter(packet,**kwargs):
        calls.append(packet)
        return dict(questions=[dict(candidate_id=candidate['id'],key_point_ids=[2,4,6])])
    pm.prepare_point(recall167_store,'p',recall167_store.records('points','p')[0],{'question_splitter':splitter})
    row=qb.assessment_questions(recall167_store.records('question_bank','p'),'m')[0]
    assert len(calls)==1
    assert row['key_points']==['原文第二点。','原文第四点。','原文第六点。']


def test_recall167_bad_long_answer_selector_json_keeps_original(recall167_store,monkeypatch):
    from types import SimpleNamespace
    from refactor.learning.course import material_questions as mq
    candidate=recall167_candidate(reference='原文第一点。原文第二点。原文第三点。原文第四点。原文第五点。原文第六点。')
    monkeypatch.setattr(mq,'candidates',lambda chunks,sources: [candidate])
    monkeypatch.setattr(mq,'for_module',lambda rows,points: [dict(rows[0],point_ids=['point'])])
    class Gateway:
        calls=0
        def complete(self,*args,**kwargs):
            self.calls+=1
            return SimpleNamespace(text='malformed JSON')
    gateway=Gateway()
    monkeypatch.setattr(pm,'_concept_cloze',lambda *a,**k: pytest.fail('bad selector JSON must keep recall'))
    pm.prepare_point(recall167_store,'p',recall167_store.records('points','p')[0],{'gateway':gateway})
    row=qb.assessment_questions(recall167_store.records('question_bank','p'),'m')[0]
    assert gateway.calls==1
    assert row['key_points']==['原文第一点。','原文第二点。','原文第三点。','原文第四点。','原文第五点。']


def test_recall167_cloze_points_count_parent_points_and_planflow_saves(recall167_store,monkeypatch):
    from datetime import date, datetime, timezone
    from types import SimpleNamespace
    from refactor.learning.app.plan_flow import PlanFlow
    from refactor.learning.course import material_questions as mq
    point=recall167_store.records('points','p')[0]
    point['subpoints']=[dict(id='point-s1',title='小点一',minutes=5,chunk_ids=['c1'],notes=[]),
        dict(id='point-s2',title='小点二',minutes=5,chunk_ids=['c1'],notes=[])]
    with recall167_store.transaction():recall167_store._put('points','p',point)
    monkeypatch.setattr(mq,'candidates',lambda chunks,sources: [])
    monkeypatch.setattr(mq,'for_module',lambda rows,points: [])
    monkeypatch.setattr(pm,'_expand_output',lambda output,local,reasons: dict(cloze=output['cloze']))
    def finder(packet,passages,**kwargs):
        return dict(subpoints=[dict(id=s['id'],cloze=[dict(target=s['title'])]) for s in packet['subpoints']])
    def make_cloze(item,parent,by_id,passages,**kwargs):
        source=dict(title='原文',url='https://example.test/guide',locator='正文块 1 [c1]')
        return dict(id='cloze-'+parent['subpoint_id'],module_id='m',point_ids=['point'],
            subpoint_id=parent['subpoint_id'],kind='choice',stem=item['target']+'是什么？',
            options=['原文答案','错误选项一','错误选项二'],answer=[0],explanation='',origin='cloze',
            source=source,answer_source=source,importance='normal',role='bank',uses=[],
            created_at=datetime.now(timezone.utc).isoformat(),target=item['target'],
            cloze_subject='正文',contrast_evidence=[dict(chunk_id='c1',contrast_quote='正文原文。',
                contrast_subject='正文',contrast_value='原文'),dict(chunk_id='c1',contrast_quote='正文原文。',
                contrast_subject='正文',contrast_value='答案')])
    monkeypatch.setattr(pm,'_concept_cloze',make_cloze)
    def checked(rows,*args,**kwargs):
        at=datetime.now(timezone.utc).isoformat()
        return [dict(q,distractor_check=dict(state='passed',checks=[],reason='',at=at)) for q in rows]
    monkeypatch.setattr(pm,'check_distractors',checked)
    result=pm.prepare_module(recall167_store,'p','m',dict(finder=finder),lambda *args:None)
    assert result['points']==1 and result['cloze_points']==1
    rows=qb.assessment_questions(recall167_store.records('question_bank','p'),'m')
    assert len(rows)==2 and {q['subpoint_id'] for q in rows}=={'point-s1','point-s2'}
    flow=object.__new__(PlanFlow)
    flow.services=SimpleNamespace(store=recall167_store)
    flow.study_day=lambda:date(2026,10,18)
    flow._save_material_summary('p','m',result)
    module=next(m for m in recall167_store.records('modules','p') if m['id']=='m')
    assert module['material_preparation']['points']==1
    assert module['material_preparation']['cloze_points']==1
