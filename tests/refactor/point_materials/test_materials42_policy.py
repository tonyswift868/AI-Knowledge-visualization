"""Fixed original evidence, no model judging its own answers or network."""
from datetime import date, timedelta
from types import SimpleNamespace
import pytest

from refactor.learning.course import point_materials as pm, source_quality as quality
from refactor.learning.course.store import CourseStore, StoreError
from refactor.learning.course.ledger import PlanWindow
from refactor.learning.course.source_filter import load_lists
from refactor.learning.course.extract import extract_bytes
from refactor.learning.course.research_pipeline import ResearchPipeline
from refactor.learning.course.research import FetchedContent

TEXT = '压强是压力与受力面积的比值。国际单位制中压强的单位是帕。压力越大，在面积不变时压强越大。\n同章术语：压力、浮力、密度、面积。'


@pytest.fixture
def store42(tmp_path):
    with CourseStore('materials42', tmp_path) as store:
        today=date(2026,10,3)
        store.create_plan('p',goal='压强',window=PlanWindow(today,today+timedelta(days=5),100))
        store.add_sources('p',[dict(id='s',title='压强教材',url='https://pep.com.cn/physics/pressure',adopted=True,
            chunks=[dict(chunk_id='c',text=TEXT,section=['压强'])])])
        store.save_outline('p',[dict(id='stage',title='物理')],[dict(id='m',stage_id='stage',title='压强',prerequisites=[])])
        store.save_generated_module('p','m',[dict(id='point',title='压强',summary='压力面积',minutes=15,chunk_ids=['c'],importance='key')],check_minutes=5,today=today)
        yield store


def pointers42(cid='c'):
    return dict(passages=[cid],found=[],cloze=[
        dict(chunk_id=cid,sentence='压强是压力与受力面积的比值。',word='压强',importance='key',keywords=['浮力','密度']),
        dict(chunk_id=cid,sentence='国际单位制中压强的单位是帕。',word='帕',importance='key',keywords=[]),
        dict(chunk_id=cid,sentence='压力越大，在面积不变时压强越大。',word='压力',importance='key',keywords=[])])


def context42():
    return dict(finder=lambda *a,**k:pointers42(),search=lambda *a,**k:[],
                fetcher=lambda *a,**k:(_ for _ in ()).throw(OSError('offline')),stage='初二')


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


def test_materials42_three_search_rounds_quick_separation_reopen(store42):
    queries=[];context=context42()
    context['search']=lambda q,**k:(queries.append(q) or [])
    point=store42.records('points','p')[0]
    result=pm.prepare_point(store42,'p',point,context)
    assert result==dict(passages=1,questions=3,cloze=3)
    assert len(set(queries))==3 and 'practice questions with answers' in queries[-1]
    rows=store42.records('question_bank','p')
    assert sum(q['role']=='bank' for q in rows)==3
    assert sum(q['role']=='quick' for q in rows)==1
    quick=pm.pick_question(store42,'p','point','quick')
    assert quick['kind']=='choice' and quick['origin']=='cloze'
    assert all(option in TEXT for option in quick['options'])
    for purpose in ('module','retest','final','direct','free'):
        assert pm.pick_question(store42,'p','point',purpose)['role']=='bank'
        assert pm.pick_question(store42,'p','point',purpose,[q['id'] for q in rows if q['role']=='bank']) is None
    assert pm.pick_question(store42,'p','point','quick',[quick['id']]) is None
    pm.prepare_point(store42,'p',point,context)
    assert len(queries)==3
    with CourseStore(store42.account_id,store42.root.parents[2]) as reopened:
        assert pm.pick_question(reopened,'p','point','quick')['id']==quick['id']


def test_materials42_excerpt_only_selected_original_blocks(store42):
    text='压强是压力与受力面积的比值。\n\n这段只讲无关的古代建筑。\n\n版权所有：测试教育出版社。'
    doc=extract_bytes(text.encode(),'supplement.txt')
    source=SimpleNamespace(final_url='https://pep.com.cn/other',title='补充教材',document=doc)
    passages=pm._excerpt_candidates(source,dict(level=1,type='textbook_publisher',evidence=None))
    selected=passages[0]
    context=dict(context42(),store=store42,plan_id='p',lists=load_lists())
    pm._save_excerpts(store42.records('points','p')[0],passages,dict(passages=[selected['id']],found=[],cloze=[]),context)
    row=next(s for s in store42.records('sources','p') if s.get('excerpt'))
    chunks=[c for c in store42.records('source_chunks','p') if c['source_id']==row['id']]
    assert row['group']=='补充摘录' and row['original_title']=='补充教材'
    assert len(chunks)==1 and '古代建筑' not in chunks[0]['text']
    assert chunks[0]['original_locator']=='原文段落 1'
    pm._save_excerpts(store42.records('points','p')[0],passages,dict(passages=[selected['id']],found=[],cloze=[]),context)
    assert len(store42.records('sources','p'))==2


@pytest.mark.parametrize('count,fallback,heavy',[(5,1,False),(4,1,True),(5,2,True),(1,0,False)])
def test_materials42_twenty_percent_boundary_extra_round(store42,monkeypatch,count,fallback,heavy,caplog):
    points=[dict(id='point'+str(i),module_id='m',title='压强') for i in range(count)]
    real=store42.records
    monkeypatch.setattr(store42,'records',lambda kind,plan: points if kind=='points' else real(kind,plan))
    calls=[]
    rows=[]
    for i,p in enumerate(points):
        for j in range(2):rows.append(dict(point_ids=[p['id']],role='bank',origin='cloze' if i<fallback else 'found'))
        rows.append(dict(point_ids=[p['id']],role='quick',origin='cloze'))
    monkeypatch.setattr(store42,'records',lambda kind,plan: points if kind=='points' else rows if kind=='question_bank' else real(kind,plan))
    monkeypatch.setattr(pm,'prepare_point',lambda s,plan,p,ctx:(calls.append((p['id'],ctx.get('extra_search',False))) or dict(passages=1,questions=2,cloze=int(p['id'][5:])<fallback)))
    result=pm.prepare_module(store42,'p','m',context42(),lambda *a:None)
    assert result==dict(points=count,cloze_points=fallback,cloze_heavy=heavy)
    assert sum(extra for _,extra in calls)==(fallback if heavy else 0)
    assert ('module cloze-heavy' in caplog.text)==heavy


def test_materials42_incomplete_original_is_not_fake_success(store42):
    context=context42();context['finder']=lambda *a,**k:dict(passages=['c'],found=[],cloze=[])
    with pytest.raises(StoreError) as error:pm.prepare_point(store42,'p',store42.records('points','p')[0],context)
    assert error.value.code=='MATERIALS_INCOMPLETE'
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
    assert pm._found(item,point,{'c':p})['origin']=='found'
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


@pytest.mark.parametrize('arrival',[2,3])
def test_materials42_real_pipeline_late_answers_replace_only_unused_fallback(store42,arrival):
    queries=[]
    url='https://pep.com.cn/physics/extra'
    questions=[('压强单位是什么？','帕'),('受力面积不变压力增大时压强怎样变化？','增大'),('压强是哪些量的比值？','压力与受力面积')]
    html=('<h1>压强</h1>'+''.join(f'<p>{i+1}. {stem}\n答案：{answer}。\n原文解释：{TEXT}</p>' for i,(stem,answer) in enumerate(questions))+'<p>这段只讲无关的建筑。</p>').encode()
    context=context42()
    context['search']=lambda query,**kw:(queries.append(query) or ([dict(url=url,title='压强')] if len(queries)==arrival+1 else []))
    def fetch(candidate,**kwargs):
        if candidate!=url:raise OSError('offline: only the declared answer source exists')
        return FetchedContent(url,url,'text/html',html,'')
    context['fetcher']=fetch
    def finder(point,passages,mode,**kwargs):
        if mode=='adopted':return pointers42()
        found=[];selected=[]
        for passage in passages:
            for stem,answer in questions:
                if stem in passage['text']:
                    selected.append(passage['id']);found.append(dict(kind='short',stem=stem,answer=answer,
                        answer_quote=answer+'。',question_chunk=passage['id'],answer_chunk=passage['id'],pairing='same_block',explanation=''))
        return dict(passages=selected,found=found,cloze=[])
    context['finder']=finder
    if arrival==2:pm.prepare_point(store42,'p',store42.records('points','p')[0],context)
    else:
        result=pm.prepare_module(store42,'p','m',context,lambda *a:None)
        assert result==dict(points=1,cloze_points=0,cloze_heavy=False)
    rows=store42.records('question_bank','p')
    assert sum(q.get('role')=='bank' and q['origin']=='found' for q in rows)==3
    assert sum(q.get('role')=='quick' for q in rows)==1
    assert len(rows)==4 and len(queries)==arrival+1
    supplements=[s for s in store42.records('sources','p') if s.get('excerpt')]
    assert len(supplements)==1 and supplements[0]['group']=='补充摘录'
    assert not any('建筑' in c['text'] for c in store42.records('source_chunks','p') if c['source_id']==supplements[0]['id'])
