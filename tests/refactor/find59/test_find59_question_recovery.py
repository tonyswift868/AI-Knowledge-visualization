"""Actual locator/store/search pipeline; no network or model in these tests."""
from datetime import date, timedelta
from types import SimpleNamespace
from threading import Event
import pytest
from refactor.learning.course import point_materials as pm, question_bank as qb
from refactor.learning.course.store import CourseStore
from refactor.learning.course.ledger import PlanWindow
from refactor.learning.course.research import FetchedContent
from refactor.learning.app.classroom import Classroom

BASE='压强是压力与受力面积的比值。速度是位移与时间的比值。密度是质量与体积的比值。'


@pytest.fixture
def data59(tmp_path):
    with CourseStore('find59',tmp_path) as s:
        day=date(2026,10,5)
        s.create_plan('p',goal='压强定义',window=PlanWindow(day,day+timedelta(days=6),60))
        s.add_sources('p',[dict(id='book',url='https://www.pep.com.cn/text',title='选定教材',adopted=True,
            chunks=[dict(chunk_id='c',text=BASE,section=['定义'])])])
        s.save_outline('p',[dict(id='st',title='物理')],[dict(id='m',stage_id='st',title='压强')])
        s.save_generated_module('p','m',[dict(id='pt',title='压强',summary='能判断压强与压力和受力面积的关系',minutes=15,chunk_ids=['c'],importance='key')],check_minutes=5,today=day)
        yield s,s.records('points','p')[0]


def exercise59(number):
    return f'{number}. 压强定义公式{number}是？\nA. 压力除以面积\nB. 压力乘面积\nC. 面积除以压力\n答案：A'


def finder59(point,passages,**kw):
    found=[]
    for p in passages:
        for n in range(1,7):
            stem=f'压强定义公式{n}是？'
            if stem in p['text'] and qb.original_text(stem) not in point.get('excluded_stems',[]):
                found.append(dict(kind='choice',stem=stem,options=['压力除以面积','压力乘面积','面积除以压力'],
                    answer=[0],answer_quote='A',question_chunk=p['id'],answer_chunk=p['id'],pairing='same_block'))
    cloze=[]
    if any(p['id']=='c' for p in passages):
        cloze=[dict(target='压强定义是什么？',subject='压强',chunk_id='c',sentence='压强是压力与受力面积的比值。',word='压力与受力面积的比值',importance='key',contrasts=[
            dict(chunk_id='c',contrast_quote='速度是位移与时间的比值。',contrast_subject='速度',contrast_value='位移与时间的比值'),
            dict(chunk_id='c',contrast_quote='密度是质量与体积的比值。',contrast_subject='密度',contrast_value='质量与体积的比值')])]
    return dict(passages=[p['id'] for p in passages],found=found,cloze=cloze)


def checker59(packet,**kw):
    return dict(questions=[dict(id=q['id'],checks=[dict(index=i,verdict='not_supported',quote='')
        for i in range(len(q['options'])) if i not in q['answer']]) for q in packet['questions']])


def context59(**kw):
    return dict(finder=finder59,distractor_checker=checker59,
        search=lambda *a,**k:[],fetcher=lambda *a,**k:(_ for _ in ()).throw(OSError('offline')),**kw)


def test_find59_bank_default_off_quick_unchanged_and_legacy_opt_in(data59,monkeypatch):
    s,p=data59
    monkeypatch.setattr(pm,'directory_candidates',lambda *a,**k:[])
    assert pm.prepare_point(s,'p',p,context59())['questions']==0
    rows=s.records('question_bank','p')
    assert len(rows)==1 and rows[0]['role']=='quick' and qb.checked_question(rows[0])
    with pytest.raises(pm.StoreError):  # Only one retained gap candidate exists.
        pm.prepare_point(s,'p',p,context59(),allow_bank_cloze=True)
    assert any(q['role']=='bank' and q['origin']=='cloze' for q in s.records('question_bank','p'))


def test_find59_material_questions_precede_web_and_unused_replenish(data59):
    s,p=data59
    s.add_sources('p',[dict(id='book',url='https://www.pep.com.cn/text',title='选定教材',adopted=True,
        chunks=[dict(chunk_id='c',text=BASE+'\n'+'\n'.join(exercise59(n) for n in range(1,5)),section=['定义'])])])
    ctx=context59();ctx['search']=lambda *a,**k:pytest.fail('material has enough original questions')
    result=pm.prepare_point(s,'p',p,ctx)
    assert result['questions']==2 and result['cloze']==0
    old=[q['id'] for q in s.records('question_bank','p') if q['role']=='bank']
    for qid in old:s.record_question_use('p',qid,'retest')
    pm.prepare_point(s,'p',p,dict(ctx,exclude_ids=old))
    rows=[q for q in s.records('question_bank','p') if q['role']=='bank']
    assert len(rows)==4 and all(q['question_origin']=='material' and q['source_level']==1 for q in rows)
    assert all('distractor_check' not in q for q in rows)
    assert pm.pick_question(s,'p','pt','retest',old)['id'] not in old


def test_find59_restored_search_pipeline_rank_excerpt_and_reopen(data59,monkeypatch):
    s,p=data59; calls=[]
    monkeypatch.setattr(pm,'directory_candidates',lambda *a,**k:[])
    urls=['https://other.example.org/questions','https://www.pep.com.cn/questions']
    def fetch(url,**kw):
        html='<title>压强习题</title><h1>压强习题</h1>'+''.join('<p>'+exercise59(n).replace('\n','<br>')+'</p>' for n in range(1,5))
        return FetchedContent(url,url,'text/html',html.encode(),'')
    ctx=context59();ctx.update(search=lambda query,**k:(calls.append(query) or [dict(url=u,title='压强习题') for u in urls]),fetcher=fetch)
    result=pm.prepare_point(s,'p',p,ctx)
    assert result['questions']==2 and len(calls)==1
    rows=[q for q in s.records('question_bank','p') if q['role']=='bank']
    assert all(q['question_origin']=='web' and q['source_level']==1 for q in rows)
    assert all(q['source']['url']==urls[1] for q in rows)
    assert len([v for v in s.records('sources','p') if v.get('question_discovery')])==1
    old=[q['id'] for q in rows]
    pm.prepare_point(s,'p',p,dict(ctx,exclude_ids=old))
    assert len([q for q in s.records('question_bank','p') if q['role']=='bank'])==4
    assert pm.pick_question(s,'p','pt','retest',old)
    with CourseStore(s.account_id,s.root.parents[2]) as reopened:
        assert pm.pick_question(reopened,'p','pt','module')['question_origin']=='web'


def test_find59_lower_band_only_after_authority_shortage(data59):
    s,p=data59;seen=[]
    passages=[]
    for level in (3,2,1):
        passages.append(dict(id=str(level),text=exercise59(1)+('\n'+exercise59(2) if level==2 else ''),
            authority=dict(level=level,type='general_site'),source=dict(url=f'https://site{level}.example.org',title='习题',locator='原题')))
    def finder(*args,**kw):
        seen.append(args[1][0]['authority']['level']);return finder59(*args,**kw)
    out=pm._locate_discovered(p,passages,dict(finder=finder,needed_questions=2),'supplementary_answered_exercises')
    assert seen==[1,2] and len(out['found'])==3


@pytest.mark.parametrize('same_question',[False,True])
def test_find59_classroom_two_wrong_searches_before_reuse(data59,same_question):
    s,p=data59
    s.add_sources('p',[dict(id='book',url='https://www.pep.com.cn/text',title='选定教材',adopted=True,
        chunks=[dict(chunk_id='c',text=BASE+'\n'+'\n'.join(exercise59(n) for n in range(1,5)),section=['定义'])])])
    pm.prepare_point(s,'p',p,context59())
    old=[q['id'] for q in s.records('question_bank','p') if q['role']=='bank']
    answered=old
    if same_question:
        with s.transaction():s._delete('question_bank','p',old[1])
        answered=[old[0],old[0]]
    for index,(qid,context) in enumerate(zip(answered,('module_test','retest'))):
        s.append_question_answer('p','wrong'+str(index),module_id='m',context=context,attempt_id='attempt'+str(index),
            question_id=qid,point_ids=['pt'],angle='本点掌握目标',correct=False,wrong_points=['pt'])
        s.record_question_use('p',qid,'module' if index==0 else 'retest')
    room=object.__new__(Classroom);seen=[]
    room._materials=lambda:pm
    def prepare(course,pid,ctx,**kw):
        seen.append(kw['exclude_ids'])
        return pm.prepare_point(s,'p',p,dict(context59(),exclude_ids=kw['exclude_ids']))
    room._prepare_one=prepare
    course=SimpleNamespace(store=s,plan_id='p',point=lambda _:p,points_of=lambda _:[p])
    assert room._pick(course,'pt','retest')['id'] not in answered
    assert seen==[tuple(dict.fromkeys(answered))]
    assert len([q for q in s.records('question_bank','p') if q['role']=='bank'])==(3 if same_question else 4)
