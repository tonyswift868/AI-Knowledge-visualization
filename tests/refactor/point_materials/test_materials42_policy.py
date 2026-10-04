"""Fixed original evidence, no model judging its own answers or network."""
from datetime import date, timedelta
import pytest

from refactor.learning.course import point_materials as pm, source_quality as quality
from refactor.learning.course.store import CourseStore, StoreError
from refactor.learning.course.ledger import PlanWindow
from refactor.learning.course.research_pipeline import ResearchPipeline
from refactor.learning.course.research import FetchedContent
from test_point_materials37_contract import pointer,CONTRAST37,approve37

TEXT = '压强是压力与受力面积的比值。国际单位制中压强的单位是帕。压力越大，在面积不变时压强越大。\n同章术语：压力、浮力、密度、面积。'


@pytest.fixture
def store42(tmp_path):
    with CourseStore('materials42', tmp_path) as store:
        today=date(2026,10,3)
        store.create_plan('p',goal='压强',window=PlanWindow(today,today+timedelta(days=5),100))
        store.add_sources('p',[dict(id='s',title='压强教材',url='https://pep.com.cn/physics/pressure',adopted=True,
            chunks=[dict(target='本点原文定义是什么？',chunk_id='c',text=TEXT,section=['压强']),
                dict(chunk_id='other37',text=CONTRAST37,section=['运动学'])])])
        store.save_outline('p',[dict(id='stage',title='物理')],[dict(id='m',stage_id='stage',title='压强',prerequisites=[])])
        store.save_generated_module('p','m',[dict(id='point',title='压强',summary='压力面积',minutes=15,chunk_ids=['c'],importance='key')],check_minutes=5,today=today)
        yield store


def pointers42(cid='c'):
    return pointer(cid)


def context42():
    return dict(finder=lambda *a,**k:pointers42(),search=lambda *a,**k:[],
                fetcher=lambda *a,**k:(_ for _ in ()).throw(OSError('offline')),stage='初二',distractor_checker=approve37)


@pytest.mark.parametrize('level,title,fit',[('beginner','Advanced Python functions parameters','too_advanced'),('advanced','Absolute beginner Python functions parameters','review_only')])
def test_materials42_difficulty_never_lowers_scores_or_review(level,title,fit):
    source=dict(url='https://docs.python.org/3/tutorial/controlflow.html',title=title)
    chunks=[dict(text='Python functions parameters. Functions accept parameters. '*12,source_id='s')]
    plain=quality.assess_stored(source,chunks,'Python functions parameters',level='')
    difficult=quality.assess_stored(source,chunks,'Python functions parameters',level=level)
    assert difficult['eligible'] and difficult['relevance']==plain['relevance'] and difficult['credibility']==plain['credibility']
    assert difficult['level_fit']==fit
    if fit=='too_advanced':assert '偏难，老师会讲浅' in difficult['note']
    unresolved=quality.assess_stored(source,chunks,'Python functions parameters exceptions decorators generators modules',level=level)
    assert unresolved['needs_review']
    assert quality.apply_review(unresolved,dict(relevance=4,note='The original explicitly covers the relevant functions.'))['eligible']
    assert quality.rank_key(plain)<quality.rank_key(difficult)


@pytest.mark.parametrize('title,body,blocked',[
    ('压强教材','本站内容均由自动采集。'+TEXT,True),
    ('震惊！压强竟然是这样','加微信领取，购买课程。'+TEXT,True),
    ('广告推广：学习捷径','限时抢购，buy now。'+TEXT,True),
    ('压强教材','未经许可不得转载。版权所有物理老师。'+TEXT,False),
    ('压强教学','课程页面下方有广告，但内容由教师独立编写。'+TEXT,False)])
def test_materials42_junk_is_evidence_based_and_blocks_pipeline(title,body,blocked):
    assert bool(quality.junk_reasons(title,body))==blocked
    url='https://pep.com.cn/pressure'
    html=(f'<title>{title}</title><h1>{title}</h1><p>{body*3}</p>').encode()
    with ResearchPipeline(fetcher=lambda *a,**k:FetchedContent(url,url,'text/html',html,'')) as pipe:
        result=pipe.run([dict(url=url,title=title)])
    assert bool(result.rejected)==blocked
    assert bool(result.verified)==(not blocked)


def test_materials50_no_search_quick_separation_reopen(store42):
    queries=[];context=context42()
    context['search']=lambda q,**k:(queries.append(q) or [])
    point=store42.records('points','p')[0]
    result=pm.prepare_point(store42,'p',point,context)
    assert result==dict(passages=1,questions=2,cloze=2)
    assert queries==[]
    rows=store42.records('question_bank','p')
    assert sum(q['role']=='bank' for q in rows)==2
    assert sum(q['role']=='quick' for q in rows)==1
    quick=pm.pick_question(store42,'p','point','quick')
    assert quick['kind']=='choice' and quick['origin']=='cloze'
    assert all(option in TEXT+CONTRAST37 for option in quick['options'])
    for purpose in ('module','retest','final','direct','free'):
        assert pm.pick_question(store42,'p','point',purpose)['role']=='bank'
        assert pm.pick_question(store42,'p','point',purpose,[q['id'] for q in rows if q['role']=='bank']) is None
    assert pm.pick_question(store42,'p','point','quick',[quick['id']]) is None
    pm.prepare_point(store42,'p',point,context)
    assert queries==[]
    with CourseStore(store42.account_id,store42.root.parents[2]) as reopened:
        assert pm.pick_question(reopened,'p','point','quick')['id']==quick['id']


@pytest.mark.parametrize('count,fallback,heavy',[(5,1,False),(4,1,True),(5,2,True),(1,0,False)])
def test_materials50_ratio_is_only_statistics_no_extra_round(store42,monkeypatch,count,fallback,heavy,caplog):
    points=[dict(id='point'+str(i),module_id='m',title='压强',chunk_ids=['c']) for i in range(count)]
    real=store42.records
    monkeypatch.setattr(store42,'records',lambda kind,plan: points if kind=='points' else real(kind,plan))
    calls=[]
    rows=[]
    for i,p in enumerate(points):
        check=dict(state='passed',checks=[],reason='',at='2026-10-04T00:00:00Z')
        for j in range(2):rows.append(dict(module_id='m',point_ids=[p['id']],kind='choice',role='bank',origin='cloze' if i<fallback else 'found',
            cloze_subject='压强',contrast_evidence=pointer()['cloze'][0]['contrasts'],distractor_check=check,source=dict(url='https://pep.com.cn/physics/pressure',locator='压强；正文块 1 [c]'),answer_source=dict(url='https://pep.com.cn/physics/pressure',locator='压强；正文块 1 [c]')))
        rows.append(dict(module_id='m',point_ids=[p['id']],kind='choice',role='quick',origin='cloze',
            cloze_subject='压强',contrast_evidence=pointer()['cloze'][0]['contrasts'],distractor_check=check,source=dict(url='https://pep.com.cn/physics/pressure',locator='压强；正文块 1 [c]'),answer_source=dict(url='https://pep.com.cn/physics/pressure',locator='压强；正文块 1 [c]')))
    monkeypatch.setattr(store42,'records',lambda kind,plan: points if kind=='points' else rows if kind=='question_bank' else real(kind,plan))
    monkeypatch.setattr(pm,'prepare_point',lambda s,plan,p,ctx:(calls.append((p['id'],ctx.get('extra_search',False))) or dict(passages=1,questions=2,cloze=int(p['id'][5:])<fallback)))
    result=pm.prepare_module(store42,'p','m',context42(),lambda *a:None)
    assert result==dict(points=count,cloze_points=fallback,cloze_heavy=heavy)
    assert len(calls)==count and not any(extra for _,extra in calls)
    assert 'module cloze-heavy' not in caplog.text


def test_materials42_incomplete_original_is_not_fake_success(store42):
    context=context42();context['finder']=lambda *a,**k:dict(passages=['c'],found=[],cloze=[])
    with pytest.raises(StoreError) as error:pm.prepare_point(store42,'p',store42.records('points','p')[0],context)
    assert error.value.code=='CLOZE_FAILED'
    assert not store42.records('question_bank','p')


def test_materials42_authorized_is_not_a_publisher():
    from refactor.learning.course.source_filter import classify_source
    assert classify_source('https://unknown.example/lesson',page_text='Database prevents unauthorized access.')['level']==0
    assert classify_source('https://unknown.example/lesson',page_text='Author: Jane Doe\nLesson.')['level']==3


def test_materials42_written_question_heading_pairs_only_its_answer():
    p=dict(id='c',source_id='s',text='1. What is a CPU?\nA central processing unit executes instructions.\n2. What is RAM?\nRandom access memory.',
           qa_headings=['1. What is a CPU?','2. What is RAM?'],qa_complete=True,source=dict(title='原文问答',url='https://school.edu/questions',locator='提问标题1'))
    point=dict(id='point',module_id='m')
    item=dict(kind='short',stem='What is a CPU?',answer='A central processing unit executes instructions.',answer_quote='A central processing unit executes instructions.',explanation='',question_chunk='c',answer_chunk='c',pairing='heading_answer')
    assert pm._found(item,point,{'c':p}) is None  # historical subjective headings are no longer admitted
    assert pm._found(dict(item,answer='Random access memory.',answer_quote='Random access memory.'),point,{'c':p}) is None
    assert pm._found(item,point,{'c':dict(p,qa_headings=[])}) is None
    assert pm._found(item,point,{'c':dict(p,qa_complete=False)}) is None


def test_materials42_definition_cannot_answer_all_enumerated_forms():
    stem='Explain normal forms (1NF, 2NF, 3NF, and BCNF) with examples.'
    quote='Normalization reduces redundancy.'
    p=dict(id='c',source_id='s',text=stem+'\n'+quote,qa_headings=[stem],qa_complete=True,
        source=dict(title='原文问答',url='https://school.edu/db',locator='问题1'))
    item=dict(kind='short',stem=stem,answer=quote,answer_quote=quote,explanation='',question_chunk='c',answer_chunk='c',pairing='heading_answer')
    assert pm._found(item,dict(id='point',module_id='m'),{'c':p}) is None


def test_materials42_long_literal_anchor_needs_unique_original():
    sentence='This is the original sentence. '*6+' End.'
    assert 160<len(sentence)<512
    assert pm._quote(dict(quote=sentence),sentence)==sentence
    with pytest.raises(ValueError):pm._quote(dict(quote=sentence),sentence+sentence)
