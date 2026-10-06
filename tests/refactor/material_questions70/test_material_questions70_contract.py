"""Real extraction/admission/readiness with offline literal locator fixtures."""
from datetime import date,timedelta
from threading import Event
from types import SimpleNamespace
import copy
import json
import pytest
from refactor.learning.course import material_questions as mq,point_materials as pm
from refactor.learning.course.store import CourseStore
from refactor.learning.course.ledger import PlanWindow
from refactor.learning.course.question_bank import validate_question
from refactor.learning.app.plan_flow import PlanFlow

TEXT70='# 什么是队列？\n\n队列按先进先出处理元素。入队操作把元素放在队尾。出队操作从队首取元素。\n\n# 栈和队列的区别\n\n栈遵循后进先出。队列遵循先进先出。两者删除元素的位置不同。\n\n# 有没有 PDF 版？\n\n可以获取资料，但是这不是知识问题。'


def chunk70(text=TEXT70,cid='c',sid='s',position=0,**extra):
    return dict(id=cid,source_id=sid,text=text,position=position,section=[],**extra)


def raw70(candidate):
    if candidate['candidate_type']=='recall':
        # Offline fixture locates full literal sentences; it does not insert
        # prepared question records or bypass the product compiler/store.
        import re
        sentences=re.findall(r'[^。\n]+。',candidate['reference'])
        return dict(candidate_id=candidate['id'],kind='recall',stem=candidate['stem'],
                    reference=candidate['reference'],key_point_ids=list(range(1,min(5,len(mq.answer_units(candidate['reference'])))+1)))
    options=candidate['options'];stem=candidate['stem']
    subtype='multi' if options and '多选' in stem else 'choice' if options else 'fill' if '____' in stem else 'calc'
    return dict(candidate_id=candidate['id'],kind='objective',objective_kind=subtype,
                stem=stem,options=options,answer=candidate['answer'])


def split70(packet,**kwargs):
    return dict(questions=[raw70(c) for c in packet['candidates']])


@pytest.mark.parametrize('opening',['```json\n','```\n'])
def test_material70_existing_json_fence_format_is_supported(opening):
    assert pm._json(opening+'{"questions":[]}\n```')=={'questions':[]}


def test_material70_fingerprint_includes_answer_after_cited_chunk():
    chunks=[chunk70('# 什么是队列？',cid='head'),chunk70('原回答继续。',cid='body',position=1)]
    points=[dict(id='p',chunk_ids=['head'])]
    before=mq.source_fingerprint(points,chunks)
    chunks[1]['text']='原回答已更新。'
    assert mq.source_fingerprint(points,chunks)!=before


@pytest.mark.parametrize('prefix',['# ',''])
def test_material70_unnumbered_original_choice_and_heading_are_objective(prefix):
    rows=mq.candidates([chunk70(prefix+'哪种结构使用先进先出？\n\nA. 队列\n\nB. 栈\n\n答案：A')])
    assert len(rows)==1 and rows[0]['candidate_type']=='objective'
    q=mq.compile_question(raw70(rows[0]),dict(rows[0],point_ids=['p']),'m')
    assert q['stem']=='哪种结构使用先进先出？' and q['options']==['A. 队列','B. 栈'] and q['answer']=='A'


def test_material70_explicit_numbered_answer_section_pairs_same_source_only():
    qs=chunk70('# 习题\n\n1. 队列顺序？\nA. FIFO\nB. LIFO\n\n2. 栈顺序？\nA. FIFO\nB. LIFO',cid='exercise')
    answers=chunk70('# 答案\n\n1. A\n2. B',cid='answers',position=1)
    rows=mq.candidates([qs,answers])
    assert [q['answer'] for q in rows]==['A','B']
    assert mq.for_module(rows,[dict(id='p',chunk_ids=['exercise'])])[0]['point_ids']==['p']
    assert not mq.for_module(rows,[dict(id='other',chunk_ids=['answers'])])
    assert not mq.candidates([qs,dict(answers,source_id='other-book')])
    assert not mq.candidates([qs,dict(answers,text='# 答案\n\n3. A\n4. B')])


def test_material70_ambiguous_numbered_answers_are_not_paired():
    text='# 习题\n\n1. 队列顺序？\nA. FIFO\nB. LIFO\n\n# 答案\n\n1. A\n1. B'
    assert not mq.candidates([chunk70(text)])


def test_material70_reopen_keeps_zero_question_completion_and_account_isolation(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    with CourseStore('owned70',tmp_path) as s:
        day=date(2026,10,6)
        s.create_plan('p',goal='普通资料',window=PlanWindow(day,day+timedelta(days=3),60))
        s.add_sources('p',[dict(id='s',title='用户资料',url='https://example.org/course',adopted=True,chunks=[dict(chunk_id='c',text='这里只讲解，不含题目。')])])
        s.save_outline('p',[dict(id='st',title='学习')],[dict(id='m',stage_id='st',title='没有题')])
        s.save_generated_module('p','m',[dict(id='p1',title='说明',summary='读原文',chunk_ids=['c'],minutes=8)],check_minutes=2,today=day)
        with ThreadPoolExecutor(2) as pool:
            results=list(pool.map(lambda _:pm.prepare_module(s,'p','m',{},lambda *a:None),range(2)))
        assert results[0]==results[1] and results[0]['questions']==0
    with CourseStore('owned70',tmp_path) as reopened:
        assert pm.preparation_done(reopened,'p','m') and not reopened.records('question_bank','p')
    with CourseStore('other70',tmp_path) as other:
        assert not other.records('modules','p') and not pm.preparation_done(other,'p','m')


@pytest.fixture
def material_store70(tmp_path):
    with CourseStore('material70',tmp_path) as store:
        day=date(2026,10,6)
        store.create_plan('p',goal='队列',window=PlanWindow(day,day+timedelta(days=5),60))
        store.add_sources('p',[dict(id='s',title='用户资料',url='https://unknown.example/course',adopted=True,
            chunks=[dict(chunk_id='c',text=TEXT70,section=['正文']),dict(chunk_id='empty',text='普通介绍，没有资料题。',section=['无题'])])])
        store.save_outline('p',[dict(id='st',title='数据结构')],[dict(id='m',stage_id='st',title='有题'),dict(id='n',stage_id='st',title='无题')])
        for mid,cid in [('m','c'),('n','empty')]:
            store.save_generated_module('p',mid,[dict(id=mid+'p',title='队列',summary='说明队列顺序',chunk_ids=[cid],minutes=8)],check_minutes=2,today=day)
        yield store


@pytest.mark.parametrize('heading',['什么是 JVM？','A 和 B 的区别','为什么需要缓存','How does caching work?','数据库范式了解吗?','有哪些类型？'])
def test_material70_knowledge_heading_rules(heading):
    assert mq.knowledge_heading(heading)


@pytest.mark.parametrize('heading',['JavaGuide 有没有 PDF？','没项目经验怎么办？','项目介绍应该怎么讲？','知道如何获取招聘信息','如何学习Java？','是否收费？'])
def test_material70_nonknowledge_headings_excluded(heading):
    assert not mq.knowledge_heading(heading)


def test_material70_heading_answers_stop_before_next_question_and_document():
    rows=mq.candidates([chunk70()])
    assert len(rows)==2
    assert rows[0]['reference']=='队列按先进先出处理元素。入队操作把元素放在队尾。出队操作从队首取元素。'
    assert 'PDF' not in rows[1]['reference']
    assert mq.for_module(rows,chunk_ids=['elsewhere'])==[]


def test_material70_original_fragments_reassemble_and_preserve_ownership():
    heading=dict(index=0,kind='heading',text='什么是队列？',offset=0,section=['什么是队列？'])
    first=dict(index=1,kind='paragraph',text='队列按先进',offset=0,section=['什么是队列？'])
    second=dict(index=1,kind='paragraph',text='先进先出处理元素。入队操作把元素放在队尾。出队操作从队首取元素。',offset=3,section=['什么是队列？'])
    rows=mq.candidates([chunk70(cid='a',block_fragments=[heading,first]),chunk70(cid='b',position=1,block_fragments=[second])])
    assert len(rows)==1 and rows[0]['chunk_ids']==['a','b']
    assert rows[0]['reference'].startswith('队列按先进先出')
    linked=mq.for_module(rows,[dict(id='p1',chunk_ids=['a']),dict(id='p2',chunk_ids=['b'])])
    assert linked[0]['point_ids']==['p1','p2']


def test_material70_fragment_gap_does_not_invent_an_answer():
    parts=[dict(index=0,kind='heading',text='什么是队列？',offset=0,section=['什么是队列？']),
           dict(index=1,kind='paragraph',text='不完整原文。',offset=50,section=['什么是队列？'])]
    assert mq.candidates([chunk70(block_fragments=parts)])==[]


@pytest.mark.parametrize('stem,options,answer,subtype',[
('1. 队列顺序是？','A. 先进先出\nB. 后进先出','A','choice'),
('1. 多选：哪些是线性结构？','A. 栈\nB. 队列\nC. 树','A、B','multi'),
('1. 队列顺序是____。','','先进先出','fill'),
('1. 计算 2+3。','','5','calc')])
def test_material70_objective_fields_remain_original(stem,options,answer,subtype):
    text='背景知识。\n\n'+stem+'\n'+options+'\n答案：'+answer
    row=mq.candidates([chunk70(text)])[0];row['point_ids']=['p']
    compiled=mq.compile_question(raw70(row),row,'m')
    assert compiled['kind']=='objective' and compiled['objective_kind']==subtype
    assert compiled['answer']==answer and compiled['stem']==stem
    assert validate_question(compiled)==compiled


def test_material70_numbered_questions_keep_their_own_answers():
    text='1. 第一道？\nA. 甲\nB. 乙\n答案：A\n2. 第二道？\nA. 丙\nB. 丁\n答案：B'
    rows=mq.candidates([chunk70(text)])
    assert len(rows)==2 and [r['answer'] for r in rows]==['A','B']
    assert '第二道' not in rows[0]['reference']
    assert mq.candidates([chunk70('1. 没有答案？\nA. 甲\nB. 乙')])==[]


@pytest.mark.parametrize('mutation,reason',[('stem','逐字'),('kind','题型')])
def test_material70_compiler_rejects_original_question_rewriting(mutation,reason):
    c=mq.candidates([chunk70()])[0];c['point_ids']=['p'];raw=raw70(c)
    raw[mutation]='改写' if mutation=='stem' else 'objective'
    with pytest.raises(ValueError,match=reason):mq.compile_question(raw,c,'m')


def test_material70_long_reference_anchors_expand_complete_original():
    c=mq.candidates([chunk70()])[0];c['point_ids']=['p'];raw=raw70(c)
    raw['reference']={'quote':'队列按先进先出','end_quote':'从队首取元素。'}
    row=mq.compile_question(raw,c,'m')
    assert row['reference']==c['reference']


def test_material70_preparation_admits_all_original_questions_idempotently(material_store70):
    s=material_store70;calls=[]
    def splitter(packet,**kwargs):calls.append(packet);return split70(packet)
    result=pm.prepare_module(s,'p','m',dict(question_splitter=splitter),lambda *a:None)
    assert result['questions']==2 and result['recall']==2 and result['objective']==0
    assert pm.preparation_done(s,'p','m')
    assert pm.prepare_module(s,'p','m',dict(question_splitter=lambda *a,**k:pytest.fail('already done')),lambda *a:None)==result
    assert len(calls)==0
    assert all(q['origin']=='found' and q['role']=='bank' and q['point_ids']==['mp'] for q in s.records('question_bank','p'))
    flow=object.__new__(PlanFlow);flow.services=SimpleNamespace(store=s);flow.study_day=lambda:date(2026,10,6)
    assert flow._module_ready('p','m')
    flow._save_material_summary('p','m',result)
    assert flow._module_ready('p','m')  # legacy summary does not erase marker


def test_material70_zero_questions_completes_without_model_or_search(material_store70):
    s=material_store70
    result=pm.prepare_module(s,'p','n',dict(gateway=SimpleNamespace(complete=lambda *a,**k:pytest.fail('no model')),
        search=lambda *a,**k:pytest.fail('no search')),lambda *a:None)
    assert result['questions']==0 and result['question_extraction_done']
    assert pm.preparation_done(s,'p','n')
    flow=object.__new__(PlanFlow);flow.services=SimpleNamespace(store=s);flow.study_day=lambda:date(2026,10,6)
    assert flow._module_ready('p','n')


def test_material70_rejections_are_recorded_and_module_is_still_done(material_store70):
    s=material_store70
    def reject(packet,**kwargs):return {'questions':[dict(candidate_id=c['id'],reject_reason='原文不足三句') for c in packet['candidates']]}
    result=pm.prepare_module(s,'p','m',dict(question_splitter=reject),lambda *a:None)
    assert result['rejected']==0 and result['questions']==2 and pm.preparation_done(s,'p','m')
    assert not s.records('modules','p')[0]['question_extraction']['rejections']


def test_material70_cancel_fences_all_admission(material_store70):
    s=material_store70;cancel=Event()
    cancel.set()
    with pytest.raises(pm.ResearchCancelled):pm.prepare_module(s,'p','m',dict(cancel=cancel),lambda *a:None)
    assert not s.records('question_bank','p') and not pm.preparation_done(s,'p','m')


def test_material70_source_change_invalidates_completion(material_store70):
    s=material_store70
    pm.prepare_module(s,'p','m',dict(question_splitter=split70),lambda *a:None)
    c=s._get('source_chunks','p','c');c['text']+='\n原文发生变化。'
    with s.transaction():s._put('source_chunks','p',c)
    assert not pm.preparation_done(s,'p','m')


def test_material70_gateway_receives_only_cut_questions_and_disables_search(material_store70):
    s=material_store70;seen=[]
    class Gateway:
        def complete(self,purpose,messages,**kwargs):
            packet=json.loads(messages[0]['content']);seen.append(packet)
            assert purpose=='point_materials' and kwargs['enable_search'] is False
            return SimpleNamespace(text=json.dumps(split70(packet)))
    pm.prepare_module(s,'p','m',dict(gateway=Gateway()),lambda *a:None)
    assert len(seen)==0  # At most five literal units: no selection model.
    assert 'PDF' not in json.dumps(seen,ensure_ascii=False)


def test_material70_removed_generation_paths_have_no_switch_or_callable():
    import inspect
    assert all(not hasattr(pm,k) for k in ('_cloze','_discover_questions','_prepare_module_bank','question_queries','_recloze_after_check','_locate_tier'))
    assert 'allow_bank_cloze' not in inspect.signature(pm.prepare_point).parameters


def test_material70_structure_counts_are_program_only(material_store70):
    s=material_store70;flow=object.__new__(PlanFlow)
    sources=s.records('sources','p');chunks=s.records('source_chunks','p')
    flow._adopted=lambda d:(sources,{'s':chunks})
    flow._sections=lambda d:(sources,[SimpleNamespace(id='with',chunk_ids=['c']),SimpleNamespace(id='without',chunk_ids=['empty'])])
    flow._window=lambda d:PlanWindow(date(2026,10,6),date(2026,10,30),600)
    modules,ranges=flow.structure_ranges({'material_structure':{'modules':[dict(key='m',title='有题',refs=['with']),dict(key='n',title='无题',refs=['without'])]}})
    assert [m['estimated_question_count'] for m in modules]==[2,0]
    assert [m['has_material_questions'] for m in modules]==[True,False]
    assert ranges['estimated_question_count']==2 and ranges['modules_with_questions']==1
