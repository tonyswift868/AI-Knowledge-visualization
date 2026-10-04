from datetime import date, timedelta
import json
from threading import Event
from concurrent.futures import ThreadPoolExecutor
import pytest

from refactor.learning.course.store import CourseStore, StoreError
from refactor.learning.course.ledger import PlanWindow
from refactor.learning.course import point_materials as pm
from refactor.learning.course.question_bank import validate_question
from refactor.learning.course.source_filter import classify_source, directory_matches, load_lists, parse_lists, SourceListsError


TEXT='压强是压力与受力面积的比值。国际单位制中压强的单位是帕。压力越大，在面积不变时压强越大。\n同章术语：压力、浮力、密度、面积。\n1. 压强单位是____？\n答案：帕。'


@pytest.fixture
def store(tmp_path):
    s=CourseStore('materials-test',tmp_path)
    today=date(2026,10,3)
    s.create_plan('p',goal='压强',window=PlanWindow(today,today+timedelta(days=3),100))
    s.add_sources('p',[dict(id='s',title='物理教材',url='https://www.pep.com.cn/physics/pressure',adopted=True,
        chunks=[dict(target='本点原文定义是什么？',chunk_id='c',text=TEXT,section=['第九章 压强'])])])
    s.save_outline('p',[dict(id='stage',title='初二物理')],[dict(id='m',stage_id='stage',title='压强',prerequisites=[])])
    s.save_generated_module('p','m',[dict(id='point',title='压强',summary='压力与受力面积',minutes=15,chunk_ids=['c'],importance='key')],check_minutes=5,today=today)
    yield s
    s.close()


def pointer(chunk='c'):
    return dict(passages=[chunk],found=[],cloze=[
        dict(target='本点原文定义是什么？',chunk_id=chunk,sentence='压强是压力与受力面积的比值。',word='压强',importance='key',keywords=['压力','浮力']),
        dict(target='本点原文定义是什么？',chunk_id=chunk,sentence='国际单位制中压强的单位是帕。',word='帕',importance='key',keywords=[]),
        dict(target='本点原文定义是什么？',chunk_id=chunk,sentence='压力越大，在面积不变时压强越大。',word='压力',importance='key',keywords=[])])


def ctx(output=None):
    def offline_fetch(*args,**kwargs): raise RuntimeError('offline: no page available')
    return dict(finder=lambda p,passages,**kw: output or pointer(),search=lambda *a,**k:[],fetcher=offline_fetch,stage='初二')


def incomplete(store, point, context):
    # Insufficient locator evidence is now an explicit non-ready result;
    # inspect partial rows only to retain the original provenance assertions.
    with pytest.raises(StoreError) as error:
        pm.prepare_point(store, 'p', point, context)
    assert error.value.code == 'CLOZE_FAILED'
    rows = store.records('question_bank', 'p')
    return pm._counts(rows, store.records('points', 'p')[0]['chunk_ids'])


def test_point_prepare_idempotent_and_provenance(store):
    calls=[]
    context=ctx(); context['finder']=lambda *a,**k:(calls.append(1) or pointer())
    point=store.records('points','p')[0]
    result=pm.prepare_point(store,'p',point,context)
    assert result==dict(passages=1,questions=3,cloze=3)
    assert pm.prepare_point(store,'p',point,context)==result
    assert len(calls)==1
    assert store._db.execute('SELECT chunk_id FROM point_sources').fetchall()[0][0]=='c'
    rows=store.records('question_bank','p')
    assert all(q['source']==q['answer_source'] and q['origin']=='cloze' for q in rows)
    assert not pm.uncovered_points(store,'p',context)


def test_normal_cloze_distractors_same_source_and_word_exact(store):
    with store.transaction():
        p=store._get('points','p','point');p['importance']='normal';store._put('points','p',p)
    out=pointer();out['cloze']=[dict(target='本点原文定义是什么？',chunk_id='c',sentence='压强是压力与受力面积的比值。',word='压强',importance='normal',keywords=['浮力','密度','不存在'])]
    assert incomplete(store,store.records('points','p')[0],ctx(out))['questions']==1
    q=store.records('question_bank','p')[0]
    assert q['kind']=='choice' and set(q['options'])=={'压强','浮力','密度'}
    out['cloze'][0]['word']='强压'
    assert not pm._cloze(out['cloze'][0],p,{'c':pm._passages(store,'p',dict(ctx(),store=store,plan_id='p',lists=load_lists()))[0]},[])


def test_found_same_block_has_original_answer_and_no_fabrication(store):
    out=pointer();out['cloze']=[]
    q=dict(kind='fill',stem='压强单位是____？',answer=[['帕']],answer_quote='帕。',explanation='',question_chunk='c',answer_chunk='c',pairing='same_block',question_number='1',answer_number='1')
    out['found']=[q]
    assert incomplete(store,store.records('points','p')[0],ctx(out))['questions']==1
    stored=store.records('question_bank','p')[0]
    assert stored['origin']=='found' and '帕' in stored['answer'][0]
    q['answer']=[['公斤']]; q['stem']='压力的单位是什么？'
    assert incomplete(store,store.records('points','p')[0],ctx(out))['questions']==1


def test_number_pairing_mismatch_is_rejected(store):
    out=pointer();out['cloze']=[]
    out['found']=[dict(kind='fill',stem='压强单位是____？',answer=[['帕']],answer_quote='1. 帕',question_chunk='c',answer_chunk='c',pairing='numbered',question_number='1',answer_number='2')]
    assert incomplete(store,store.records('points','p')[0],ctx(out))['questions']==0


def test_pick_unused_then_lru_and_never_calls_model(store):
    pm.prepare_point(store,'p',store.records('points','p')[0],ctx())
    picked=[]
    for i in range(3):
        q=pm.pick_question(store,'p','point','final');picked.append(q['id'])
        store.record_question_use('p',q['id'],'final',at=f'2026-10-03T00:00:0{i}+00:00')
    assert len(set(picked))==3
    assert pm.pick_question(store,'p','point','final')['id']==picked[0]
    assert pm.pick_question(store,'p','point','final',picked) is None
    assert pm.pick_question(store,'p','point','module') is not None
    pm.mark_used(store,'p',picked[0],'quick')
    assert next(q for q in store.records('question_bank','p') if q['id']==picked[0])['uses']
    with pytest.raises(ValueError): pm.pick_question(store,'p','point','bad')


def test_cancellation_no_late_write_and_background_dedup(store):
    cancel=Event();context=ctx()
    def locate(*a,**k): cancel.set();return pointer()
    context.update(cancel=cancel,finder=locate)
    with pytest.raises(pm.ResearchCancelled):pm.prepare_point(store,'p',store.records('points','p')[0],context)
    assert not store.records('question_bank','p')
    with ThreadPoolExecutor(2) as pool:
        results=list(pool.map(lambda _:pm.prepare_point(store,'p',store.records('points','p')[0],ctx()),range(2)))
    assert all(r['questions']==3 for r in results)
    assert len(store.records('question_bank','p'))==4


def test_old_plans_unusable_no_migration(store):
    with store.transaction():
        plan=store._get('plans','p','p');plan.pop('material_version');store._put('plans','p',plan)
    assert store.get_plan('p')['unusable_reason']=='旧版计划，已不可用'
    with pytest.raises(StoreError,match='旧版计划'):pm.prepare_point(store,'p',store.records('points','p')[0],ctx())


@pytest.mark.parametrize('url,level',[
    ('https://any.city.gov.cn/course',1),('https://basic.smartedu.cn/course',1),
    ('https://www.pep.com.cn/physics',1),('https://math.any.edu.cn/course',1),('https://math.any.edu/course',1),
    ('https://jyeoo.com/questions/123',2),('https://zxxk.com/answer/123',2),
    ('https://evilpep.com.cn/physics',0),('https://adg.csdn.net/physics',0),('https://any.webvpn.edu.cn/course',0),
    ('https://math.any.edu.cn/forum/post',3)])
def test_source_type_rules(url,level):assert classify_source(url)['level']==level


def test_unknown_identity_visible_evidence_only():
    text='本站由某省教育考试院发布，备案主体为某省教育考试院。'
    evidence=dict(type='government',publisher='某省教育考试院',quote=text)
    assert classify_source('https://exam.example.org/exam',publisher_evidence=evidence,page_text=text)['level']==1
    assert classify_source('https://exam.example.org/exam',publisher_evidence=evidence,page_text='不存在证据')['level']==0
    assert classify_source('https://csdn.net/exam',publisher_evidence=evidence,page_text=text)['level']==3  # never promoted to government
    assert classify_source('',publisher_evidence=evidence,page_text=text)['level']==1
    assert classify_source('javascript:fake',publisher_evidence=evidence,page_text=text)['level']==0
    assert classify_source('https://math.any.edu/course',page_text='转载自 CSDN：正文')['level']==0
    assert classify_source('https://pep.com.cn/chapter',page_text='未经许可不得转载。正文')['level']==1


def test_directory_match_and_strict_v2_format():
    lists=load_lists()
    assert lists.version==2
    matches=directory_matches('初二物理压强',lists=lists)
    assert any('压强' in e['keywords'] or '物理' in e['subjects'] for e in matches)
    assert any('Python' in e['subjects'] for e in directory_matches('Python 列表'))
    assert not any(e['id']=='c' for e in directory_matches('JavaScript 数组'))
    assert len([e for e in lists.directory if e['id'].startswith('openstax-')])==110
    from pathlib import Path
    import refactor.learning.course.source_filter as sf
    data=json.loads((Path(sf.__file__).with_name('data')/'source_lists.json').read_text(encoding='utf-8'))
    data['type_rules'][0]['level']=3
    with pytest.raises(SourceListsError,match='level'):parse_lists(data)


def test_schema_rejects_legacy_fields_and_unequal_fill_blanks(store):
    pm.prepare_point(store,'p',store.records('points','p')[0],ctx())
    q=store.records('question_bank','p')[0]
    q['angle']='recall'
    with pytest.raises(ValueError):validate_question(q)
    q.pop('angle');q['answer'].append(['编造'])
    with pytest.raises(ValueError):validate_question(q)


def test_module_progress_and_account_isolation(store,tmp_path):
    progress=[]
    result=pm.prepare_module(store,'p','m',ctx(),lambda *args:progress.append(args))
    assert result==dict(points=1,cloze_points=1,cloze_heavy=True) and progress[-1][0:2]==(1,1)
    with pytest.raises(StoreError):pm.prepare_module(store,'p','not-a-module',ctx(),lambda *a:None)
    with CourseStore('other-account',tmp_path) as other:
        with pytest.raises(StoreError):pm.pick_question(other,'p','point','final')


def test_image_textbook_uses_existing_ocr_and_original_digest():
    import io,hashlib
    from unittest.mock import patch
    from PIL import Image
    from refactor.learning.course.extract import extract_bytes
    from refactor.learning.course.research import FetchedContent
    from refactor.learning.course.research_pipeline import ResearchPipeline
    payload=io.BytesIO();Image.new('RGB',(20,20),'white').save(payload,format='PNG')
    data=payload.getvalue(); url='https://pep.com.cn/pressure.png'
    doc=extract_bytes(('Pressure definition. The force over area is pressure. '*5).encode(),'pressure.txt')
    fetch=lambda *a,**k:FetchedContent(url,url,'image/png',data,'2026-10-03T00:00:00+00:00')
    with ResearchPipeline(fetcher=fetch) as pipeline:
        result=pipeline.run([dict(url=url)])
        assert result.rejected[0].code=='ocr_required'
    engine=object()
    with patch('refactor.learning.course.research_pipeline.extract_bytes',return_value=doc) as extractor:
        with ResearchPipeline(fetcher=fetch,ocr=engine) as pipeline:
            result=pipeline.run([dict(url=url)])
        assert len(result.verified)==1
        assert extractor.call_args.kwargs['ocr'] is engine
        assert result.verified[0].content_sha256==hashlib.sha256(data).hexdigest()


def test_numbered_question_and_answer_separate_blocks_same_book(store):
    store.add_sources('p',[dict(id='answer',url='https://openstax.org/books/test/pages/chapter-9',title='Answers',adopted=True,
        chunks=[dict(target='本点原文定义是什么？',chunk_id='a',text='1. 帕。\n2. 牛顿。')])])
    with store.transaction():
        source=store._get('sources','p','s');source['url']='https://openstax.org/books/test/pages/exercises';store._put('sources','p',source)
    out=pointer();out['cloze']=[]
    out['found']=[dict(kind='fill',stem='压强单位是____？',answer=[['帕']],answer_quote='1. 帕。',question_chunk='c',answer_chunk='a',pairing='numbered',question_number='1',answer_number='1')]
    result=incomplete(store,store.records('points','p')[0],ctx(out))
    assert result['questions']==1
    assert store.records('question_bank','p')[0]['source']['url']!=store.records('question_bank','p')[0]['answer_source']['url']


def test_valid_cloze_survives_failed_external_locator(store):
    from refactor.learning.connections.errors import ModelError
    context=ctx()
    context['search']=lambda *a,**k:(_ for _ in ()).throw(ModelError('MODEL_OUTPUT_TRUNCATED','saved failure'))
    messages=[];context['on_status']=messages.append
    assert pm.prepare_point(store,'p',store.records('points','p')[0],context)['questions']==3
    assert messages and '原文挖空' in messages[0]


def test_program_can_pair_same_block_explicit_answer_letter(store):
    store.add_sources('p',[dict(id='choice',url='https://pep.com.cn/test',title='原卷',adopted=True,
        chunks=[dict(target='本点原文定义是什么？',chunk_id='choice-c',text='1. 压强的单位是？\nA. 帕\nB. 牛顿\n答案：A')])])
    out=pointer();out['cloze']=[]
    out['found']=[dict(kind='choice',stem='压强的单位是？',options=['帕','牛顿'],answer=[0],answer_quote='A',question_chunk='choice-c',answer_chunk='choice-c',pairing='same_block')]
    assert incomplete(store,store.records('points','p')[0],ctx(out))['questions']==1

    # The locator cannot silently omit a second correct option in a multi-answer.
    passages=pm._passages(store,'p',dict(ctx(),store=store,plan_id='p',lists=load_lists()))
    by_id={p['id']:p for p in passages}
    by_id['choice-c']['text']=by_id['choice-c']['text'].replace('答案：A','答案：AB')
    item=dict(out['found'][0],kind='multi',answer_quote='AB')
    assert pm._found(item,store.records('points','p')[0],by_id) is None


def test_record_uses_survives_reopen(store):
    pm.prepare_point(store,'p',store.records('points','p')[0],ctx())
    q=pm.pick_question(store,'p','point','final')
    pm.mark_used(store,'p',q['id'],'final')
    with CourseStore(store.account_id,store.root.parents[2]) as reopened:
        saved=next(row for row in reopened.records('question_bank','p') if row['id']==q['id'])
        assert saved['uses'][0]['purpose']=='final'
