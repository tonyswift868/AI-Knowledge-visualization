"""Module shared search, original spans and shortage-only supplementation."""
from datetime import date,timedelta
from threading import Event
import pytest

from refactor.learning.course import point_materials as pm
from refactor.learning.course.research import FetchedContent
from refactor.learning.course.research_questions import question_blocks
from refactor.learning.course.store import CourseStore
from refactor.learning.course.ledger import PlanWindow


def passage(text,identifier='original',origin='material',url='https://course.edu.cn/exam'):
    return dict(id=identifier,source_id='s',text=text,source=dict(title='原卷',url=url,locator=identifier),
                question_origin=origin,authority=dict(level=1,type='university'),section='',page=None,position=0)


def exercises(i):
    return f'1. 对象{i}的标记是____？\n答案：数值{i}。\n2. 对象{i}的代号是____？\n答案：代号{i}。'


def definition(i):
    return f'对象{i}的标记是数值{i}。对照甲{i}的标记是甲值{i}。对照乙{i}的标记是乙值{i}。'


@pytest.fixture
def module67(tmp_path):
    with CourseStore('module67',tmp_path) as store:
        day=date(2026,10,6)
        store.create_plan('p',goal='对象标记',window=PlanWindow(day,day+timedelta(days=3),120))
        store.add_sources('p',[dict(id='s',title='用户资料',url='https://book.example.org/exam',adopted=True,
            chunks=[dict(chunk_id=f'c{i}',text=definition(i)+'\n'+exercises(i)) for i in range(2)])])
        store.save_outline('p',[dict(id='stage',title='阶段')],[dict(id='m',stage_id='stage',title='标记知识')])
        store.save_generated_module('p','m',[dict(id=f'p{i}',title=f'对象{i}',summary=f'理解对象{i}的标记和代号',
            chunk_ids=[f'c{i}'],minutes=10) for i in range(2)],check_minutes=5,today=day)
        yield store


def quick(point,passages,**kwargs):
    i=point['id'][-1];cid=f'c{i}'
    return dict(passages=[cid],found=[],cloze=[dict(target=f'对象{i}的标记是什么？',subject=f'对象{i}',chunk_id=cid,
        sentence=f'对象{i}的标记是数值{i}。',word=f'数值{i}',contrasts=[
            dict(chunk_id=cid,contrast_quote=f'对照甲{i}的标记是甲值{i}。',contrast_subject=f'对照甲{i}',contrast_value=f'甲值{i}'),
            dict(chunk_id=cid,contrast_quote=f'对照乙{i}的标记是乙值{i}。',contrast_subject=f'对照乙{i}',contrast_value=f'乙值{i}')])])


def pair(packet,**kwargs):
    found=[]
    for point in packet['points']:
        i=point['id'][-1]
        for word,value in (('标记','数值'),('代号','代号')):
            stem=f'对象{i}的{word}是____？';answer=f'{value}{i}'
            p=next((p for p in packet['passages'] if stem in p['text'] and '答案：'+answer in p['text']),None)
            if p:
                found.append(dict(point_id=point['id'],kind='fill',stem=stem,answer=[[answer]],answer_quote=answer+'。',
                    question_chunk=p['id'],answer_chunk=p['id'],pairing='same_block',explanation=''))
    return dict(found=found)


def approve(packet,**kwargs):
    return dict(questions=[dict(id=q['id'],checks=[dict(index=i,verdict='not_supported',quote='')
        for i in range(len(q['options'])) if i not in q['answer']]) for q in packet['questions']])


def context(**kwargs):return dict(finder=quick,module_finder=pair,distractor_checker=approve,**kwargs)


def test_module67_filter_keeps_literal_exercises_and_removes_prose():
    text='这是一段目录广告。'*1000+'\n'+exercises(0)
    blocks=question_blocks([passage(text)])
    assert len(blocks)==2
    assert all('目录广告' not in b['text'] for b in blocks)
    assert all(b['text']==text[slice(*b['question_span'])] for b in blocks)
    assert blocks==question_blocks([passage(text)])
    assert all(b['source']['locator']=='original' for b in blocks)


def test_module67_separate_numbered_answer_key_is_retained():
    blocks=question_blocks([passage('1. 值是多少？\nA. 一 B. 二\n2. 值是哪一个？\nA. 甲 B. 乙\n参考答案：\n1. A\n2. B')])
    assert any('1. A' in b['text'] for b in blocks)
    assert any('2. B' in b['text'] for b in blocks)


def test_module67_numbered_key_still_uses_original_pairing_compiler():
    blocks=question_blocks([passage('1. 下列哪个值为一？\nA. 一 B. 二',identifier='q'),
        passage('参考答案：\n1. A',identifier='a')])
    q=next(b for b in blocks if '下列哪个' in b['text']);a=next(b for b in blocks if '1. A' in b['text'])
    point=dict(id='p0',module_id='m')
    item=dict(kind='choice',stem='下列哪个值为一？',options=['一','二'],answer=[0],answer_quote='1. A',
        question_chunk=q['id'],answer_chunk=a['id'],pairing='numbered',question_number='1',answer_number='1')
    assert pm._found(item,point,{q['id']:q,a['id']:a})['answer']==[0]
    assert pm._found(dict(item,answer_number='2'),point,{q['id']:q,a['id']:a}) is None


def test_module67_authority_originals_precede_general_site(module67):
    rows=[]
    for level,label,url in ((3,'普通','https://author.example.org/exam'),(1,'权威','https://course.edu.cn/exam')):
        p=passage(f'1. {label}甲的标记是____？\n答案：{label}甲值。\n2. {label}乙的标记是____？\n答案：{label}乙值。',
                  identifier=label,origin='web',url=url)
        p.update(source_id='excerpt-'+label,authority=dict(level=level,type='university' if level==1 else 'general_site'))
        rows.append(p)
    def finder(packet,**kwargs):
        out=[]
        # Return weaker-band items first deliberately; saving still follows tier.
        for p in sorted(packet['passages'],key=lambda p:-p['authority']['level']):
            text=p['text'];stem=text.split('. ',1)[1].split(chr(10),1)[0];answer=text.split('答案：',1)[1].strip('。'+chr(10))
            out.append(dict(point_id='p0',kind='fill',stem=stem,answer=[[answer]],answer_quote=answer+'。',
                question_chunk=p['id'],answer_chunk=p['id'],pairing='same_block',explanation=''))
        return dict(found=out)
    ctx=dict(context(),store=module67,plan_id='p',lists=pm.load_lists());ctx['module_finder']=finder
    assert pm._match_module_questions(module67.records('points','p')[:1],rows,ctx,'module_web_exercises')==2
    assert all(q['source_level']==1 for q in module67.records('question_bank','p'))


def test_module67_material_batch_fills_points_without_search_and_reopen(module67):
    batches=[]
    def finder(packet,**kwargs):batches.append(packet);return pair(packet)
    def forbidden(*args,**kwargs):raise AssertionError('Materials already have two original exercises per point')
    ctx=context(module_finder_unused=None,search=forbidden)
    ctx['module_finder']=finder
    assert pm.prepare_module(module67,'p','m',ctx,lambda *args:None)['points']==2
    assert len(batches)==1 and len(batches[0]['points'])==2
    assert all('对照甲' not in p['text'] for p in batches[0]['passages'])
    bank=[q for q in module67.records('question_bank','p') if q['role']=='bank']
    assert len(bank)==4 and all(q['origin']=='found' and q['question_origin']=='material' for q in bank)
    assert all(pm.pick_question(module67,'p',f'p{i}','module') for i in range(2))
    pm.prepare_module(module67,'p','m',ctx,lambda *args:None)
    assert len(batches)==1


def web_fixture(module67):
    for i in range(2):
        row=module67._get('source_chunks','p',f'c{i}');row['text']=definition(i)
        with module67.transaction():module67._put('source_chunks','p',row)
    body=('<html><head><title>对象标记考试</title></head><body><h1>对象标记考试</h1>'
          +'<p>目录广告，不是一道题。</p>'*100
          +''.join('<p>'+exercises(i).replace('\n','</p><p>')+'</p>' for i in range(2))+'</body></html>').encode()
    url='https://course.edu.cn/exam'
    return url,lambda *args,**kwargs:FetchedContent(url,url,'text/html',body,'2026-10-06T00:00:00Z')


def test_module67_one_shared_web_search_fills_both_points(module67):
    url,fetch=web_fixture(module67);queries=[];batches=[]
    def search(query,**kwargs):queries.append(query);return [dict(url=url,title='对象标记考试')]
    def matcher(packet,**kwargs):batches.append(packet);return pair(packet)
    ctx=context(search=search,fetcher=fetch);ctx['module_finder']=matcher
    pm.prepare_module(module67,'p','m',ctx,lambda *args:None)
    assert len(queries)==1 and '对象0' in queries[0] and '对象1' in queries[0]
    assert len(batches)==1 and len(batches[0]['points'])==2
    assert all('目录广告' not in p['text'] for p in batches[0]['passages'])
    bank=[q for q in module67.records('question_bank','p') if q['role']=='bank']
    assert len(bank)==4 and all(q['question_origin']=='web' and q['source_level']==1 for q in bank)


def test_module67_only_shortage_point_gets_one_supplement(module67):
    url,fetch=web_fixture(module67);queries=[];modes=[]
    def search(query,**kwargs):queries.append(query);return [dict(url=url,title='对象标记考试')]
    def matcher(packet,**kwargs):
        modes.append((packet['mode'],[p['id'] for p in packet['points']]))
        out=pair(packet)
        if packet['mode']=='module_web_exercises':out['found']=[q for q in out['found'] if q['point_id']=='p0']
        return out
    ctx=context(search=search,fetcher=fetch);ctx['module_finder']=matcher
    pm.prepare_module(module67,'p','m',ctx,lambda *args:None)
    assert len(queries)==3
    assert modes==[('module_web_exercises',['p0','p1']),('module_web_exercises',['p1']),('point_web_exercises',['p1'])]
    assert '对象1' in queries[-1] and '对象0' not in queries[-1]


def test_module67_fake_answer_or_unknown_point_is_never_saved(module67):
    ctx=dict(context(),store=module67,plan_id='p',lists=pm.load_lists())
    def bad(packet,**kwargs):
        out=pair(packet);out['found'][0]['answer_quote']='不存在的原文答案'
        out['found'][1]['point_id']='other-account-point'
        return out
    ctx['module_finder']=bad
    n=pm._match_module_questions(module67.records('points','p')[:1],pm._passages(module67,'p',ctx),ctx,'module_adopted_exercises')
    assert n==0 and not module67.records('question_bank','p')


def test_module67_prompt_does_not_grow_with_unrelated_question_bank(module67):
    ctx=dict(context(),store=module67,plan_id='p',lists=pm.load_lists())
    points=module67.records('points','p');passages=pm._passages(module67,'p',ctx)
    assert pm._match_module_questions(points[1:],passages,ctx,'module_adopted_exercises')==2
    packets=[]
    def capture(packet,**kwargs):packets.append(packet);return pair(packet)
    ctx['module_finder']=capture
    pm._match_module_questions(points[:1],passages,ctx,'module_adopted_exercises')
    assert packets[0]['excluded_stems']==[]


def test_module67_cancellation_after_mapping_leaves_no_late_rows(module67):
    event=Event();ctx=context(cancel=event)
    def finder(packet,**kwargs):event.set();return pair(packet)
    ctx['module_finder']=finder
    with pytest.raises(pm.ResearchCancelled):pm.prepare_module(module67,'p','m',ctx,lambda *args:None)
    assert not module67.records('question_bank','p')


def test_module67_navigation_skips_without_model_or_network(module67):
    with module67.transaction():
        for p in module67.records('points','p'):
            p.update(title='了解资料入口',summary='定位学习路线并按阅读顺序取舍资料')
            module67._put('points','p',p)
    def forbidden(*args,**kwargs):raise AssertionError('Navigation must not search')
    result=pm.prepare_module(module67,'p','m',dict(search=forbidden,module_finder=forbidden),lambda *args:None)
    assert result['skipped_points']==['p0','p1']
    assert all(p['question_preparation']['state']=='skipped' for p in module67.records('points','p'))
    assert not pm._navigation_point(dict(title='Java 学习路线中的 JVM 内存区域',summary='解释堆和栈的区别'))


def test_module67_standalone_point_supplement_is_at_most_one_round(module67,monkeypatch):
    rounds=[]
    def discover(point,ctx,round_index=0):rounds.append(round_index);return dict(passages=[],found=[],cloze=[])
    monkeypatch.setattr(pm,'_discover_questions',discover)
    pm.prepare_point(module67,'p',module67.records('points','p')[0],context())
    assert rounds==[0]
