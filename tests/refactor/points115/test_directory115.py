import copy
import json
import pytest
from refactor.learning.course import planner_agents as a
from .run115 import export_module


def materials(paths, length=80, repeated=False):
    chunks=[dict(id=f'chunk{i}',source_id='source',section=path,
                 text=('事务保证一致性。回滚撤销修改。' if repeated else f'原文{i}。第二句{i}。')+'字'*length)
            for i,path in enumerate(paths)]
    old=[dict(id=f'old{i}',position=i,title=str(i),minutes=8,difficulty='中',module_id='m',chunk_ids=[c['id']],
              citations=[dict(chunk_id=c['id'],quote=c['text'])]) for i,c in enumerate(chunks)]
    paragraphs,required=a.concept_materials(old,chunks)
    for p in paragraphs.values():p['purpose']='knowledge'
    return old,chunks,paragraphs,required


def test_115_original_heading_and_short_sibling_merge_only():
    old,chunks,paragraphs,required=materials([['根','甲'],['根','乙'],['另一个父','丙']])
    points=a.directory_structure(paragraphs,old,required,[])
    assert [p['title'] for p in points]==['甲、乙','丙']
    assert all(p['minutes']==sum(s['minutes'] for s in p['subpoints']) for p in points)
    assert not any('丙' in p['title'] and '甲' in p['title'] for p in points)


def test_115_global_question_assignment_rechecks_short_sibling_merge_threshold():
    paths=[['前置','主题']]+[['本章',str(i)] for i in range(8)]
    old,chunks,paragraphs,required=materials(paths)
    questions=[dict(id='q',point_ids=['old0','old1'])]
    points=a.directory_structure(paragraphs,old,required,questions)
    assert len(points)==2
    assert points[0]['minutes']==5 and points[1]['minutes']==24
    assert len(points[1]['subpoints'])==8
    assert sum(s['minute_estimate']['questions'] for p in points for s in p['subpoints'])==1


def test_115_long_parent_split_by_existing_children_never_invented():
    paths=[['根',t] for t in ('甲','乙','丙') for _ in range(4)]
    old,chunks,paragraphs,required=materials(paths,length=5000)
    points=a.directory_structure(paragraphs,old,required,[])
    assert [p['title'] for p in points]==['甲','乙','丙']
    assert all(len(p['subpoints'])==3 for p in points)


def test_115_parent_introduction_stays_with_first_child_not_an_isolated_subpoint():
    paths=[['根'],['根','第一节'],['根','第一节'],['根','第一节'],['根','第二节']]
    old,chunks,paragraphs,required=materials(paths)
    points=a.directory_structure(paragraphs,old,required,[])
    sub=next(s for p in points for s in p['subpoints'] if 'c1' in s['refs'])
    assert 'c2' in sub['refs']
    assert len([r for p in points for s in p['subpoints'] for r in s['refs']])==len(paragraphs)


def test_115_valid_model_excerpts_survive_one_oversized_bullet():
    paragraphs={'c1':dict(text='有效第一句。有效第二句。'+'长'*250,purpose='knowledge')}
    sub=dict(id='K1-S1',refs=['c1'])
    result=a.directory_note_subset('K1',sub,[dict(ref='c1',text='长'*250),dict(ref='c1',text='有效第一句。'),
                                           dict(ref='c1',text='有效第二句。')],paragraphs)
    assert [b['text'] for b in result]==['有效第一句。','有效第二句。']


@pytest.mark.parametrize('n',[3,7,12])
def test_115_subpoint_groups_three_to_eight_with_sufficient_chunks(n):
    old,chunks,paragraphs,required=materials([['根','节']]*n)
    points=a.directory_structure(paragraphs,old,required,[])
    assert 3<=len(points[0]['subpoints'])<=8
    assert set(r for p in points for s in p['subpoints'] for r in s['refs'])==set(paragraphs)


def test_115_same_chunk_owned_once_but_distinct_overlapping_source_remains():
    old,chunks,paragraphs,required=materials([['根','总览'],['根','类型']],repeated=True)
    paragraphs['c3']=dict(paragraphs['c1'],section=['根','后一个标题'])
    points=a.directory_structure(paragraphs,old,required,[])
    owners={}
    for p in points:
        for s in p['subpoints']:
            for r in s['refs']:owners.setdefault(paragraphs[r]['parent_chunk_id'],set()).add(s['id'])
    assert all(len(ids)==1 for ids in owners.values())
    assert len(owners)==2  # Identical words in different original chunks retained.


def fake_model(stage,system,messages):
    packet=json.loads(messages[0]['content'])
    assert stage in ('classify','notes')
    if stage=='classify':
        return json.dumps([dict(id=s['id'],reason='原文讲解',knowledge=[p['id'] for p in s['paragraphs']],navigation=[])
                           for s in packet['sections']])
    return json.dumps(dict(points=[dict(id=p['id'],subpoints=[dict(id=s['id'],bullets=[
        dict(ref=s['refs'][0],text=t+'。') for t in packet['paragraphs'][s['refs'][0]].split('。')[:2]])
        for s in p['subpoints']]) for p in packet['points']]))


def test_115_notes_failure_retries_once_then_original_fallback_nonblocking():
    old,chunks,_,_=materials([['根','节']]*3);calls=[]
    def model(stage,*args):
        calls.append(stage)
        return fake_model(stage,*args) if stage=='classify' else '{}'
    result=a.rebuild_directory_module(dict(id='m',title='模块'),old,chunks,[],model)
    assert calls==['classify','notes','notes']
    assert len(result['notes_failures'])==3 and result['hard_rules']['passed']
    result['old_points']=old
    output=export_module(result,{c['id'] for c in chunks})
    assert output['module_id']=='m' and all(s['notes']==[] for p in output['points'] for s in p['subpoints'])


def test_115_parallel_notes_keep_source_ownership_and_order():
    old,chunks,_,_=materials([['章',str(i)] for i in range(25)])
    expected=a.rebuild_directory_module(dict(id='m',title='模块'),old,chunks,[],fake_model)
    actual=a.rebuild_directory_module(dict(id='m',title='模块'),old,chunks,[],fake_model,note_workers=3)
    assert actual['points']==expected['points']
    assert actual['hard_rules']==expected['hard_rules']


def test_115_original_chunk_position_wins_over_input_order():
    old,chunks,_,_=materials([['甲章','甲节'],['乙章','乙节'],['丙章','丙节']])
    for i,c in enumerate(chunks):c['position']=i
    result=a.rebuild_directory_module(dict(id='m',title='模块'),old,list(reversed(chunks)),[],fake_model)
    assert [p['title'] for p in result['points']]==['甲节','乙节','丙节']


def test_115_reappearing_same_heading_keeps_original_nonadjacent_position():
    old,chunks,paragraphs,required=materials([['复习'],['知识章节'],['复习']])
    points=a.directory_structure(paragraphs,old,required,[])
    assert [p['title'] for p in points]==['复习','知识章节','复习']
    assert [s['refs'] for p in points for s in p['subpoints']]==[['c1'],['c2'],['c3']]


def test_115_notes_retry_changes_request_instead_of_replaying_cached_failure():
    old,chunks,_,_=materials([['根','节']]*3);systems=[]
    def model(stage,system,messages):
        if stage=='classify':return fake_model(stage,system,messages)
        systems.append(system)
        return '{}' if len(systems)==1 else fake_model(stage,system,messages)
    result=a.rebuild_directory_module(dict(id='m',title='模块'),old,chunks,[],model)
    assert len(systems)==2 and systems[0]!=systems[1]
    assert not result['notes_failures']


def test_115_reflow_reuses_literal_notes_without_model_semantic_decisions():
    from .reflow115 import reflow
    old,chunks,_,_=materials([['甲节'],['乙节'],['甲节']])
    result=a.rebuild_directory_module(dict(id='m',title='模块'),old,chunks,[],fake_model)
    moved=result['points'].pop()
    result['points'][0]['subpoints'][0]['refs']+=moved['subpoints'][0]['refs']
    result['points'][0]['subpoints'][0]['bullets']+=moved['subpoints'][0]['bullets']
    def no_model(*args):pytest.fail('Validated own-source excerpts should be reused')
    corrected=reflow(result,old,chunks,[],no_model)
    assert [p['title'] for p in corrected['points']]==['甲节','乙节','甲节']
    assert corrected['reflow']['reused_subpoints']==3
    assert not corrected['notes_failures'] and corrected['hard_rules']['passed']


def test_115_page_original_headings_fallback_and_collapsed_large_navigation():
    from .run115 import directory_page
    html=directory_page(dict(modules=[],scope_text='样本</script>',complete=False,status_text='未生成'))
    assert '原文标题：' in html and '本小要点照原文讲' in html
    assert 'nav.open=m.navigation.length<=5' in html
    assert html.count('</script>')==2


def test_115_books_whole_section_navigation_never_notes():
    text='title: 必读经典书籍\n本书讲数据库。'
    chunk=dict(id='book',text=text,section=['书籍推荐'])
    old=[dict(id='old',position=0,chunk_ids=['book'],citations=[dict(chunk_id='book',quote=text)])]
    def no_calls(*args):pytest.fail('Explicit navigation requires no model')
    result=a.rebuild_directory_module(dict(id='m',title='书单'),old,[chunk],[],no_calls)
    assert result['points']==[] and result['navigation'] and result['hard_rules']['passed']


def test_115_entire_interview_study_plan_is_navigation_even_with_technical_names():
    chunks=[dict(id='c0',source_id='s',section=['先选4周还是8周'],text='title: Java后端面试通关计划\n请选择复习窗口。'),
            dict(id='c1',source_id='s',section=['先选4周还是8周','第一阶段：项目深挖'],text='整理项目里的Redis缓存和线程池。')]
    old=[dict(id=c['id'],position=i,chunk_ids=[c['id']],citations=[dict(chunk_id=c['id'],quote=c['text'])]) for i,c in enumerate(chunks)]
    def no_model(*args):pytest.fail('A clearly named study plan is entirely navigation')
    result=a.rebuild_directory_module(dict(id='m',title='模块'),old,chunks,[],no_model)
    assert not result['points'] and len(result['navigation'])==2


def test_115_mixed_technical_document_not_all_navigation_due_to_route_in_title():
    paragraphs={'c1':dict(source_id='s',section=['系统是什么'],text='title: 系统详解：核心概念、架构演进与学习路线'),
                'c2':dict(source_id='s',section=['系统是什么','工作原理'],text='这里直接解释技术。'),
                'c3':dict(source_id='s',section=['系统是什么','学习路线'],text='先读文档，再做练习。')}
    result=a.directory_sections(paragraphs)
    assert result[0]['decision']==result[1]['decision']=='pending'
    assert result[2]['navigation']==['c3']


def test_115_hard_gate_missing_source_and_foreign_note_stop():
    old,chunks,paragraphs,required=materials([['根','节']]*3)
    structure=dict(points=a.directory_structure(paragraphs,old,required,[]),navigation=[])
    a.validate_directory_structure(structure,paragraphs)
    bad=copy.deepcopy(structure);bad['points'][0]['subpoints'][0]['refs']=[]
    with pytest.raises(a.AgentFormatError,match='未覆盖'):a.validate_directory_structure(bad,paragraphs)
    bad=copy.deepcopy(structure);bad['points'][0]['subpoints'][0]['bullets']=[dict(ref='c999',text='伪造')]
    with pytest.raises(a.AgentFormatError,match='讲义出处'):a.validate_directory_structure(bad,paragraphs)


def test_115_export_accepted_by_real_restructure_preview(tmp_path):
    from refactor.learning.course.store import CourseStore
    from refactor.learning.course.restructure import apply_structure
    from refactor.learning.course.ledger import PlanWindow
    from datetime import date
    old,chunks,_,_=materials([['根','节']]*3)
    result=a.rebuild_directory_module(dict(id='m',title='模块'),old,chunks,[],fake_model);result['old_points']=old
    with CourseStore('test',tmp_path) as store:
        store.create_plan('plan',goal='测试',total_minutes=24,
                          window=PlanWindow(date(2026,10,1),date(2026,10,20),60))
        store.add_sources('plan',[dict(id='source',kind='url',url='https://example.org',title='来源',site='example.org',
            tier='official',credibility=1,adopted=True,chunks=[dict(chunk_id=c['id'],text=c['text'],section=c['section'],
                                                               page_start=None,page_end=None) for c in chunks])])
        store.save_outline('plan',[dict(id='stage',title='阶段')],[dict(id='m',stage_id='stage',title='模块',prerequisites=[])])
        store.save_generated_module('plan','m',[dict(p,summary='理解') for p in old],check_minutes=5,today=date(2026,10,1))
        preview=apply_structure(store,'plan',dict(plan_id='plan',complete=True,today='2026-10-14',
                                  modules=[export_module(result,{c['id'] for c in chunks})]),dry_run=True)
        assert preview['can_apply'],preview['issues']
        assert preview['replaced_old_points']==3
