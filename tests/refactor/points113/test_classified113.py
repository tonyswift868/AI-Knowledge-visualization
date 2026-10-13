import copy
import json
import pytest
from refactor.learning.course import planner_agents as a


def test_113_book_document_and_children_are_entirely_navigation():
    paragraphs={
        'c1':dict(source_id='one',section=['Java'],text='---\ntitle: Java 必读经典书籍\n---'),
        'c2':dict(source_id='one',section=['Java','JVM'],text='本书介绍GC。JVM管理内存。'),
        'c3':dict(source_id='two',section=['Java','JVM'],text='JVM管理堆与栈。')}
    sections=a.concept_sections(paragraphs)
    assert sections[0]['navigation']==['c1'] and sections[1]['navigation']==['c2']
    assert sections[2]['decision']=='pending'  # Do not leak a title across sources.


def test_113_mixed_section_is_partitioned_with_reason_and_exact_refs():
    section=dict(id='S1',refs=['c1','c2'],title='事务及延伸阅读')
    reply=[dict(id='S1',knowledge=['c1'],navigation=['c2'],reason='先解释事务，再推荐参考书。')]
    result=a.parse_section_purpose(json.dumps(reply),[section])
    assert result[0]['knowledge']==['c1'] and result[0]['navigation']==['c2']
    for mutation in ('unknown','duplicate','omitted'):
        row=copy.deepcopy(reply)
        row[0]['navigation']={'unknown':['c3'],'duplicate':['c1','c2'],'omitted':[]}[mutation]
        with pytest.raises(a.AgentFormatError):a.parse_section_purpose(json.dumps(row),[section])


def test_113_notes_reject_navigation_even_if_literal_and_allowed_by_subpoint():
    ps={'c1':dict(text='这本书很好。推荐阅读。',purpose='navigation')}
    points=[dict(id='K1',subpoints=[dict(id='K1-S1',refs=['c1'])])]
    with pytest.raises(a.AgentFormatError,match='导航'):a.parse_classified_notes('{}',points,ps)


def test_113_notes_reject_book_intro_misclassified_as_knowledge():
    ps={'c1':dict(text='这本书讲事务。事务保证一致性。',purpose='knowledge')}
    points=[dict(id='K1',subpoints=[dict(id='K1-S1',refs=['c1'])])]
    reply=dict(points=[dict(id='K1',subpoints=[dict(id='K1-S1',bullets=[
        dict(ref='c1',text='这本书讲事务。'),dict(ref='c1',text='事务保证一致性。')])])])
    with pytest.raises(a.AgentFormatError,match='书籍'):a.parse_classified_notes(json.dumps(reply),points,ps)


def test_113_minutes_are_existing_estimator_sum_not_target_padding():
    paragraphs={f'c{i}':dict(text='字'*100) for i in range(1,4)}
    old=[dict(id='old',difficulty='易')];required={'old':['c1','c2','c3']}
    structure=dict(points=[dict(id='K',minutes=30,subpoints=[dict(id=f'S{i}',refs=[f'c{i}']) for i in range(1,4)])])
    questions=[dict(id='q',point_ids=['old'])]
    a.concept_content_minutes(structure,paragraphs,old,required,questions)
    assert structure['points'][0]['minutes']==sum(s['minutes'] for s in structure['points'][0]['subpoints'])==10
    assert sum(s['minute_estimate']['questions'] for s in structure['points'][0]['subpoints'])==1
    assert structure['points'][0]['subpoints'][1]['minutes']==a.estimate_point_minutes(100,0,'易')


def test_113_fake_complete_chain_whole_navigation_mixed_notes_and_coverage():
    chunks=[dict(id='book',source_id='s',text='---\ntitle: 推荐书籍\n---\n\n本书介绍事务与索引。',section=['书单'],kind='paragraph'),
            dict(id='lesson',source_id='s',text='事务保证一致性。回滚撤销修改。\n\n索引加速查询。索引需要空间。\n\n主键唯一标识记录。外键建立关联。\n\n推荐拓展阅读。',section=['数据库教学'],
                 block_fragments=[dict(kind='paragraph',text=t) for t in ('事务保证一致性。回滚撤销修改。','索引加速查询。索引需要空间。','主键唯一标识记录。外键建立关联。','推荐拓展阅读。')])]
    old=[dict(id=c['id'],difficulty='中',chunk_ids=[c['id']],citations=[dict(chunk_id=c['id'],quote=c['text'].split('\n\n')[0])]) for c in chunks]
    stages=[]
    def model(stage,system,messages):
        stages.append(stage);packet=json.loads(messages[0]['content'])
        if stage=='classify':
            s=packet['sections'][0];refs=[p['id'] for p in s['paragraphs']]
            return json.dumps([dict(id=s['id'],reason='先讲知识后推荐',knowledge=refs[:3],navigation=refs[3:])])
        if stage=='extract':
            assert all('书' not in p['text'] and '推荐' not in p['text'] for p in packet['paragraphs'])
            return json.dumps([dict(title=p['text'][:2],kind='knowledge',refs=[p['id']]) for p in packet['paragraphs']])
        if stage=='merge':
            assert 'content_minutes' in packet['concepts'][0]
            return json.dumps(dict(navigation=[],points=[dict(title='数据库基础',goal='理解关系',minutes=999,
                subpoints=[dict(title=c['title'],concepts=[c['id']]) for c in packet['concepts']])]))
        return json.dumps(dict(points=[dict(id='K1',subpoints=[dict(id=s['id'],bullets=[
            dict(ref=s['refs'][0],text=t+'。') for t in packet['paragraphs'][s['refs'][0]].split('。') if t])
            for s in packet['points'][0]['subpoints']])]))
    result=a.rebuild_classified_concept_module(dict(id='m',title='数据库'),old,chunks,[],model)
    assert stages==['classify','extract','merge','notes']
    assert not result['uncovered'] and result['old_to_new']['book']['disposition']=='navigation'
    assert result['section_counts']==dict(navigation=1,knowledge=0,mixed=1)
    assert result['points'][0]['minutes']==9 and result['duration_issues']
    assert all(b['ref'] not in ['c1','c5'] for s in result['points'][0]['subpoints'] for b in s['bullets'])


def test_113_pure_navigation_never_extracts_teaches_or_estimates():
    text='---\ntitle: 必读经典书籍\n---\n本书讲数据库和操作系统。'
    chunks=[dict(id='c',text=text,section=['参考书'])]
    old=[dict(id='old',chunk_ids=['c'],citations=[dict(chunk_id='c',quote=text)])]
    def no_model(*args):pytest.fail('explicit book document must not need concept/notes calls')
    result=a.rebuild_classified_concept_module(dict(id='m',title='书单'),old,chunks,[],no_model)
    assert result['points']==[] and result['section_counts']['navigation']==1
    assert result['navigation'][0]['minutes']==0 and not result['navigation'][0]['assessed']
    assert result['old_to_new']['old']['sections'][0]['reason']


def test_113_page_exposes_section_counts_reasons_and_source_based_minutes():
    from .review113 import page
    html=page(dict(modules=[],scope_text='样本</script>',complete=False,status_text='未通过'))
    assert 'map.sections||[]' in html and 'counts.navigation' in html and 'counts.knowledge' in html
    assert 'est.characters' in html and 'est.questions' in html and '不补到固定分钟' in html
    assert html.count('</script>')==2
