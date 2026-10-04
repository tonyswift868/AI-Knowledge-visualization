import json
from types import SimpleNamespace
from threading import Event
import pytest
from refactor.learning.course import point_materials as pm
from refactor.learning.connections.errors import ModelError


def passages(n=12):
    return [dict(id=f'c{i}',text=f'第{i}段：压强是压力与受力面积的比值。',source_id='s',section=[],
                 authority=dict(level=1,type='textbook_publisher'),
                 source=dict(title='原文',url='https://pep.com.cn/book',locator=f'块{i}')) for i in range(n)]


def test_materials38_six_candidates_and_single_point():
    sent=[]
    def complete(purpose,messages,**kw):
        sent.append((json.loads(messages[0]['content']),kw))
        return SimpleNamespace(text='{"passages":[],"found":[],"cloze":[]}')
    pm._locate(dict(id='p',title='压强'),passages(),dict(gateway=SimpleNamespace(complete=complete),plan_id='plan'),'adopted')
    body,kw=sent[0]
    assert body['point']['id']=='p' and len(body['passages'])==6
    assert kw['max_tokens']==24576
    assert 'end_quote' in kw['system']


def test_materials38_nine_numbered_questions_are_not_sent():
    text='\n'.join(f'{i}. 第{i}题？\n答案：帕。' for i in range(1,10))
    chosen=pm._candidates([dict(id='c',text=text)])
    assert '8. 第8题？' in chosen[0]['text'] and '9. 第9题？' not in chosen[0]['text']


def test_materials38_truncation_halves_once_and_checks_visible_scope():
    sizes=[];statuses=[]
    def finder(point,items,**kwargs):
        sizes.append(len(items))
        if len(sizes)==1:raise ModelError('MODEL_OUTPUT_TRUNCATED','截断')
        return dict(passages=['c0','c5'],found=[],cloze=[])
    result=pm._locate(dict(id='p',title='压强'),passages(),dict(finder=finder,on_status=statuses.append),'adopted')
    assert sizes==[6,3] and result['passages']==['c0'] and len(statuses)==1


@pytest.mark.parametrize('code,count',[('MODEL_OUTPUT_TRUNCATED',2),('MODEL_CONNECTION_FAILED',1),('MODEL_CANCELLED',1)])
def test_materials38_retry_bound_only_for_truncation(code,count):
    calls=[]
    def finder(*args,**kwargs):calls.append(1);raise ModelError(code,'failure')
    with pytest.raises(ModelError):pm._locate(dict(id='p'),passages(),dict(finder=finder),'adopted')
    assert len(calls)==count


def test_materials38_cancel_between_truncation_and_retry():
    cancel=Event();calls=[]
    def finder(*args,**kw):calls.append(1);cancel.set();raise ModelError('MODEL_OUTPUT_TRUNCATED','截断')
    with pytest.raises(pm.ResearchCancelled):pm._locate(dict(id='p'),passages(),dict(finder=finder,cancel=cancel),'adopted')
    assert len(calls)==1


def test_materials38_single_chunk_question_candidates_are_halved_too():
    sent=[]
    def finder(point,items,**kw):
        sent.append(items[0]['text'])
        if len(sent)==1:raise ModelError('MODEL_OUTPUT_TRUNCATED','截断')
        return dict(passages=[],found=[],cloze=[])
    text='\n'.join(f'{i}. 原题？\n答案：帕。' for i in range(1,9))
    pm._locate(dict(id='p'),[dict(passages(1)[0],id='c',text=text)],dict(finder=finder),'answered_exercises')
    assert '8. 原题？' in sent[0] and '4. 原题？' in sent[1] and '5. 原题？' not in sent[1]


def test_materials38_compact_long_question_reconstructed_from_original():
    text='1. '+('题面原文，'*70)+'结尾____？\n答案：帕。'
    p=dict(passages(1)[0],text=text)
    item=dict(kind='fill',stem=dict(quote='1. 题面原文，',end_quote='结尾____？'),answer=[[dict(quote='帕')]],
              question_chunk='c0',answer_chunk='c0',answer_quote=dict(quote='帕。'),pairing='same_block')
    output=pm._expand_output(dict(passages=['c0'],found=[item],cloze=[]),[p])
    assert len(output['found'][0]['stem'])>300
    q=pm._found(output['found'][0],dict(id='point',module_id='module'),{'c0':p})
    assert '帕' in q['answer'][0] and q['stem'] in text and q['source']==q['answer_source']


@pytest.mark.parametrize('value',[
    dict(quote='不存在'),dict(quote='重复'),dict(quote='唯一终点',end_quote='唯一开头'),
    dict(quote='唯一开头',offset=999),dict(quote=''),dict(quote='字'*161)])
def test_materials38_invalid_or_ambiguous_anchor_rejected(value):
    with pytest.raises(ValueError):pm._quote(value,'唯一开头 重复 重复 唯一终点')


def test_materials38_found_and_cloze_output_capped():
    p=passages(1)[0]
    cloze=dict(target='本点原文定义是什么？',chunk_id='c0',sentence=dict(quote='压强是压力与受力面积的比值。'),word='压强',importance='key')
    out=pm._expand_output(dict(passages=['c0'],found=[],cloze=[cloze]*8),[p])
    assert len(out['cloze'])==3


def test_materials38_official_reference_answer_header_and_next_example_scope():
    p=dict(passages(1)[0],text='【例 1】压强单位是____？\n【参考答案】帕。\n【例 2】压力单位是什么？\n【参考答案】牛顿。')
    item=dict(kind='fill',stem='压强单位是____？',answer=[['帕']],answer_quote='帕。',question_chunk='c0',answer_chunk='c0',pairing='same_block')
    assert pm._found(item,dict(id='p',module_id='m'),{'c0':p})['answer'][0][0]=='帕'
    assert pm._found(dict(item,answer=[['牛顿']],answer_quote='牛顿。'),dict(id='p',module_id='m'),{'c0':p}) is None


def test_materials38_required_diagram_is_not_silently_lost():
    p=dict(passages(1)[0],text='1. 如图所示，求压强。\n答案：帕。')
    item=dict(kind='short',stem='如图所示，求压强。',answer='帕',answer_quote='帕。',question_chunk='c0',answer_chunk='c0',pairing='same_block')
    assert pm._found(item,dict(id='p',module_id='m'),{'c0':p}) is None
