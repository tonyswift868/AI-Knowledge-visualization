"""Shared real-store/source fixture; removed cloze locators have no callers."""
from datetime import date,timedelta
import json
import pytest
from refactor.learning.course.store import CourseStore,StoreError
from refactor.learning.course.ledger import PlanWindow

TEXT='压强是压力与受力面积的比值。国际单位制中压强的单位是帕。压力越大，在面积不变时压强越大。\n同章术语：压力、浮力、密度、面积。\n1. 压强单位是____？\n答案：帕。'
CONTRAST37='速度是位移与时间的比值。密度是质量与体积的比值。浮力随体积增大。重力随质量增大。'


@pytest.fixture
def store(tmp_path):
    s=CourseStore('materials-test',tmp_path)
    today=date(2026,10,3)
    s.create_plan('p',goal='压强',window=PlanWindow(today,today+timedelta(days=3),100))
    s.add_sources('p',[dict(id='s',title='物理教材',url='https://www.pep.com.cn/physics/pressure',adopted=True,
        chunks=[dict(target='本点原文定义是什么？',chunk_id='c',text=TEXT,section=['第九章 压强']),
            dict(chunk_id='other37',text=CONTRAST37,section=['运动学'])])])
    s.save_outline('p',[dict(id='stage',title='初二物理')],[dict(id='m',stage_id='stage',title='压强',prerequisites=[])])
    s.save_generated_module('p','m',[dict(id='point',title='压强',summary='压力与受力面积',minutes=15,chunk_ids=['c'],importance='key')],check_minutes=5,today=today)
    yield s
    s.close()

from refactor.learning.course import point_materials as pm
from refactor.learning.course.question_bank import validate_question
from refactor.learning.course.source_filter import classify_source,directory_matches,load_lists,parse_lists,SourceListsError


def legacy_bank(store):
    # Independently exercise the unchanged historical selector/use persistence.
    # These reviewed synthetic records are not the production extraction fixture.
    from tests.refactor.integration38.support import bank_question
    source=next(p['source'] for p in pm._selection_passages(store,'p') if p['id']=='c')
    store.add_bank_questions('p','m',[bank_question('point','m',index=i,kind='fill',answer='帕') | dict(source=source,answer_source=source) for i in range(2)])

def test_pick_unused_then_lru_and_never_calls_model(store):
    legacy_bank(store)
    picked=[]
    for i in range(2):
        q=pm.pick_question(store,'p','point','final');picked.append(q['id'])
        store.record_question_use('p',q['id'],'final',at=f'2026-10-03T00:00:0{i}+00:00')
    assert len(set(picked))==2
    assert pm.pick_question(store,'p','point','final')['id']==picked[0]
    assert pm.pick_question(store,'p','point','final',picked) is None
    assert pm.pick_question(store,'p','point','module') is not None
    pm.mark_used(store,'p',picked[0],'quick')
    assert next(q for q in store.records('question_bank','p') if q['id']==picked[0])['uses']
    with pytest.raises(ValueError): pm.pick_question(store,'p','point','bad')



def test_old_plans_upgrade_and_continue_preparation(store):
    with store.transaction():
        plan=store._get('plans','p','p');plan.pop('material_version');store._put('plans','p',plan)
    store.require_material_plan('p')
    assert store.get_plan('p')['material_version']==3
    assert not store.get_plan('p').get('unusable')
    # A legacy plan reaches the normal splitter path instead of being refused.
    with pytest.raises(StoreError,match='拆题模型尚未接入'):
        pm.prepare_point(store,'p',store.records('points','p')[0],{})



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
    legacy_bank(store)
    q=store.records('question_bank','p')[0]
    q['angle']='recall'
    with pytest.raises(ValueError):validate_question(q)
    q.pop('angle');q['answer'].append(['编造'])
    with pytest.raises(ValueError):validate_question(q)



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



def test_record_uses_survives_reopen(store):
    legacy_bank(store)
    q=pm.pick_question(store,'p','point','final')
    pm.mark_used(store,'p',q['id'],'final')
    with CourseStore(store.account_id,store.root.parents[2]) as reopened:
        saved=next(row for row in reopened.records('question_bank','p') if row['id']==q['id'])
        assert saved['uses'][0]['purpose']=='final'
