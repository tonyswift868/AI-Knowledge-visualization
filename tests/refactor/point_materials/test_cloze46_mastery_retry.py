"""Mastery-targeted spans and bounded retry against actual storage/classroom."""
import json
from datetime import date,timedelta
from threading import Event
from types import SimpleNamespace
import pytest
from refactor.learning.course import point_materials as pm
from refactor.learning.course.store import CourseStore,StoreError
from refactor.learning.course.ledger import PlanWindow
from refactor.learning.app.classroom import Classroom
from refactor.learning.connections.errors import ModelCancelled
from test_point_materials37_contract import approve37

TEXT='环球航行表明地球是一个球体。航行证实地球近似球形。首次航行开始于1519年，完成于1522年。航行经过大西洋、太平洋和印度洋。'
OTHER46='方盒的外形是方块。圆锥模型的外形是锥体。另一航行开始于1600年。第三航行开始于1700年。'

@pytest.fixture
def data46(tmp_path):
    with CourseStore('cloze46',tmp_path) as store:
        today=date(2026,10,4)
        store.create_plan('p',goal='航行意义',window=PlanWindow(today,today+timedelta(days=3),100))
        store.add_sources('p',[dict(id='s',url='https://history.edu/text',title='历史原文',adopted=True,
            chunks=[dict(chunk_id='c',text=TEXT,section=['历史']),
                dict(chunk_id='other46',text=OTHER46,section=['地理'])])])
        store.save_outline('p',[dict(id='st',title='历史')],[dict(id='m',stage_id='st',title='航行')])
        store.save_generated_module('p','m',[dict(id='pt',title='环球航行意义',summary='能说明航行表明地球是球体',
            minutes=15,chunk_ids=['c'],importance='key')],check_minutes=5,today=today)
        yield store,store.records('points','p')[0]

def items46():
    return [dict(target='环球航行表明地球是什么形状？',chunk_id='c',sentence='环球航行表明地球是一个球体。',
        word='球体',importance='key',subject='地球',contrasts=[
            dict(chunk_id='other46',contrast_quote='方盒的外形是方块。',contrast_subject='方盒',contrast_value='方块'),
            dict(chunk_id='other46',contrast_quote='圆锥模型的外形是锥体。',contrast_subject='圆锥模型',contrast_value='锥体')]),
        dict(target='航行证实地球是什么形状？',chunk_id='c',sentence='航行证实地球近似球形。',word='球形',importance='key',subject='地球',contrasts=[
            dict(chunk_id='other46',contrast_quote='方盒的外形是方块。',contrast_subject='方盒',contrast_value='方块'),
            dict(chunk_id='other46',contrast_quote='圆锥模型的外形是锥体。',contrast_subject='圆锥模型',contrast_value='锥体')])]

def context46(finder):
    return dict(finder=finder,search=lambda *a,**k:[],fetcher=lambda *a,**k:(_ for _ in ()).throw(OSError('offline')),distractor_checker=approve37)

def result46(items):return dict(passages=['c'],found=[],cloze=items)

def test_cloze46_system_uses_summary_question_and_preserves_phrase_years():
    assert 'summary（掌握目标）' in pm._SYSTEM and 'target' in pm._SYSTEM
    assert '年份、人数、人名或结论都不按类型排除' in pm._SYSTEM
    chunk=dict(id='c',source_id='s',text=TEXT,section='',source=dict(title='书',url='',locator='段落'))
    point=dict(id='pt',module_id='m',importance='key')
    other=dict(chunk,id='other46',text=OTHER46)
    year=dict(items46()[0],subject='首次航行',sentence='首次航行开始于1519年，完成于1522年。',word='1519年',contrasts=[
        dict(chunk_id='other46',contrast_quote='另一航行开始于1600年。',contrast_subject='另一航行',contrast_value='1600年'),
        dict(chunk_id='other46',contrast_quote='第三航行开始于1700年。',contrast_subject='第三航行',contrast_value='1700年')])
    q=pm._cloze(year,point,{'c':chunk},[chunk,other])
    assert q['kind']=='choice' and q['options'][q['answer'][0]]=='1519年'
    phrase='说明地球的形状并确认海洋相互连通'
    sentence='环球船队意义在于'+phrase+'。'
    chunk['text']=sentence
    other=dict(chunk,id='other',section='其他航行',text='测绘队的意义在于独立研究陆地边界。气象队的意义在于证明季节性风向变化。')
    item=dict(target='航行意义是什么？',chunk_id='c',sentence=sentence,word=phrase,importance='key',
        subject='环球船队',contrasts=[
        dict(chunk_id='other',contrast_quote='测绘队的意义在于独立研究陆地边界。',contrast_subject='测绘队',contrast_value='独立研究陆地边界'),
        dict(chunk_id='other',contrast_quote='气象队的意义在于证明季节性风向变化。',contrast_subject='气象队',contrast_value='证明季节性风向变化')])
    q=pm._quick(item,point,{'c':chunk},[chunk,other])
    assert q['kind']=='choice' and q['options'][q['answer'][0]]==phrase
    assert q['stem'].startswith(item['target']+'\n')
    assert q['target']==item['target']
    assert pm._stem_key(q)==pm._stem_key(dict(q,stem='换个问法？\n'+q['stem'].split('\n',1)[1]))
    assert pm._stem_key(dict(q,target='',stem='原文第一行\n原文第二行'))=='原文第一行原文第二行'

@pytest.mark.parametrize('sentence,word,reason',[
    ('球体与球体不是两个不同形状。','球体','出现多次'),
    ('CPU就是中央处理器。','CPU','别称'),
    ('一次航行越过大洋。','船员','不在原句')])
def test_cloze46_rejection_names_exact_ambiguity(sentence,word,reason):
    from refactor.learning.course.question_bank import blank_rejection
    assert reason in blank_rejection(sentence,word)

def test_cloze46_current_request_carries_mastery_and_literal_pointer_expansion():
    sent=[]
    def complete(purpose,messages,**kw):
        sent.append((json.loads(messages[0]['content']),kw['system']))
        item=dict(items46()[0],sentence={'quote':items46()[0]['sentence']},word={'quote':'球体'})
        return SimpleNamespace(text=json.dumps(result46([item]),ensure_ascii=False))
    chunk=dict(id='c',source_id='s',text=TEXT,section='',authority={'level':1},source={})
    point=dict(id='pt',summary='能说明航行表明地球是球体',chunk_ids=['c'])
    result=pm._locate_tier(point,[chunk],dict(gateway=SimpleNamespace(complete=complete),plan_id='p'),'adopted')
    assert sent[0][0]['point']['summary']==point['summary']
    assert result['cloze'][0]['word']=='球体' and result['cloze'][0]['target'].endswith('？')

def test_cloze46_all_rejected_retry_passes_reasons_and_changes_sentence(data46):
    store,point=data46;calls=[]
    bad=dict(items46()[0],word='不存在')
    def finder(p,ps,mode,**kw):
        calls.append((p,mode))
        return result46(items46() if mode=='cloze_retry' else [bad])
    result=pm.prepare_point(store,'p',point,context46(finder))
    assert result['questions']==2
    retries=[p for p,mode in calls if mode=='cloze_retry']
    assert len(retries)==1 and any('不在原句' in r['reason'] for r in retries[0]['cloze_rejections'])
    assert any(q['role']=='quick' for q in store.records('question_bank','p'))

def test_cloze46_retry_still_fails_raw_evidence_once(data46):
    store,point=data46;calls=[]
    bad=dict(items46()[0],contrasts=[],importance='normal')
    def finder(p,ps,mode,**kw):calls.append(mode);return result46([bad])
    with pytest.raises(StoreError) as error:pm.prepare_point(store,'p',point,context46(finder))
    assert error.value.code=='CLOZE_FAILED' and calls.count('cloze_retry')==1
    paths=list((store.root/'evidence'/'cloze').glob('*.json'))
    evidence=json.loads(paths[0].read_text(encoding='utf-8'))
    assert len(paths)==1 and evidence['calls'][-1]['request']['mode']=='cloze_retry'
    assert evidence['calls'][0]['reply']['cloze'][0]['word']=='球体'
    assert any('不足' in r['reason'] for r in evidence['rejected'])
    assert not store.records('question_answers','p') and not store.records('judgements','p')

def test_cloze46_bad_pointer_keeps_expansion_reason_in_retry(data46):
    store,point=data46;seen=[]
    def finder(p,ps,mode,**kw):
        seen.append(p)
        return result46([dict(items46()[0],sentence={'quote':'找不到'})]) if mode!='cloze_retry' else result46(items46())
    pm.prepare_point(store,'p',point,context46(finder))
    assert any('原文指针无效' in r['reason'] for r in seen[-1]['cloze_rejections'])

def test_cloze46_missing_original_is_program_invariant(data46):
    store,point=data46
    # Storage itself rejects missing provenance. Simulate a corrupted imported
    # point at the read boundary, without weakening the real store validator.
    real=store.records
    point=dict(point,chunk_ids=[])
    store.records=lambda entity,plan:[point] if entity=='points' else real(entity,plan)
    with pytest.raises(StoreError) as error:pm.prepare_point(store,'p',point,context46(lambda *a,**k:pytest.fail('no original')))
    assert error.value.code=='POINT_SOURCE_MISSING'

def test_cloze46_cancel_before_retry_has_no_late_write(data46):
    store,point=data46;cancel=Event()
    def finder(p,ps,mode,**kw):
        if mode=='cloze_retry':cancel.set()
        return result46([])
    with pytest.raises(pm.ResearchCancelled):pm.prepare_point(store,'p',point,dict(context46(finder),cancel=cancel))
    assert not store.records('question_bank','p')

def test_cloze46_classroom_program_vs_transient_and_no_login(data46):
    store,point=data46;room=object.__new__(Classroom);room.services=SimpleNamespace(gateway=None)
    course=SimpleNamespace(store=store,plan_id='p',point=lambda _:point)
    error=StoreError('CLOZE_FAILED','failed');error.evidence_saved=True
    room._materials=lambda:SimpleNamespace(prepare_point=lambda *a:(_ for _ in ()).throw(error))
    reason=room._prepare_one(course,point['id'],None)
    assert reason['kind']=='question_program_error'
    room._day=lambda _:date(2026,10,4);messages=[]
    room._say=lambda c,s,text,**kw:messages.append((text,kw))
    state={};room._skip_question(course,state,point['id'],'final',reason)
    assert state['check_missing']==[point['id']]
    assert '资料缺失' not in messages[0][0] and messages[0][1]['kind']=='question_program_error'
    error.code='MODEL_TIMEOUT'
    assert room._prepare_one(course,point['id'],None)['kind']=='material_gap'
    assert not store.records('question_answers','p') and not store.records('credits','p')

def test_cloze46_empty_test_skips_module_without_time_or_pass(data46):
    store,point=data46;room=object.__new__(Classroom)
    room._day=lambda _:date(2026,10,4);messages=[];room._say=lambda c,s,text,**kw:messages.append(text)
    course=SimpleNamespace(store=store,plan_id='p',points_of=lambda _:[point])
    state=dict(module_id='m',test=dict(queue=[],attempt='none'),pending=None)
    room._finish_test(course,state,None)
    assert state['step']=='test_done' and state['skipped_modules']==['m']
    assert not store.records('judgements','p') and not store.records('credits','p')
    assert '资料缺失' not in messages[0] and '时长或通过' in messages[0]
