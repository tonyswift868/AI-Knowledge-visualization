import pytest
from refactor.learning.course import planner_agents as a


def structure_for(paragraphs, nav_refs, point_refs):
    for p in paragraphs.values(): p['purpose'] = 'navigation' if p['id'] in nav_refs else 'knowledge'
    point = dict(id='K1', minutes=1, subpoints=[dict(id='K1-S1', refs=point_refs, minutes=1, bullets=[])])
    nav = [dict(id='N1', title='导航', refs=nav_refs)]
    return dict(points=[point], navigation=nav)


def test_167_same_paragraph_cannot_be_owned_by_navigation_and_subpoint():
    paragraphs = {'c1': dict(id='c1', text='资料和教学定义。', parent_chunk_id='chunk1', purpose='knowledge')}
    structure = structure_for(paragraphs, ['c1'], ['c1'])
    with pytest.raises(a.AgentFormatError) as error:
        a.validate_directory_structure(structure, paragraphs)
    assert str(error.value).count('重复归属') == 1


def test_167_directory_ownership_is_exactly_once_across_all_destinations():
    paragraphs = {
        'c1': dict(id='c1', text='定义一。', parent_chunk_id='chunk1', purpose='knowledge'),
        'c2': dict(id='c2', text='定义二。', parent_chunk_id='chunk2', purpose='knowledge'),
        'c3': dict(id='c3', text='学习建议。', parent_chunk_id='chunk3', purpose='navigation'),
    }
    point = dict(id='K1', minutes=1, subpoints=[dict(id='K1-S1', refs=['c1', 'c2'], minutes=1, bullets=[])])
    structure = dict(points=[point], navigation=[dict(id='N1', title='路线', refs=['c3'])])
    assert a.validate_directory_structure(structure, paragraphs)['passed']


def test_167_distinct_paragraphs_in_one_chunk_can_keep_their_own_destination():
    paragraphs = {
        'c1': dict(id='c1', text='事务保障一致性。', parent_chunk_id='chunk1', purpose='knowledge'),
        'c2': dict(id='c2', text='学习时先阅读补充资料。', parent_chunk_id='chunk1', purpose='navigation'),
    }
    point = dict(id='K1', minutes=1, subpoints=[dict(id='K1-S1', refs=['c1'], minutes=1, bullets=[])])
    structure = dict(points=[point], navigation=[dict(id='N1', title='学习建议', refs=['c2'])])

    checked = a.validate_directory_structure(structure, paragraphs)

    assert checked['passed']
    assert checked['owned_chunks'] == 1


def test_167_pure_tool_reading_guide_is_navigation_by_contract():
    import json
    guidance = [
        '先读 [00 主线](00-from-zero-to-one.md)，知道为什么要开这间资料室，再来认识里面的工具。别一进门就背二十个英文名：先跟着用户走一趟——打开窗口、登录、拿资料、学习、保存、更新。',
        '“常被问／偶尔问／基本不问”只表示本课按 Java + AI 副项目给出的复习优先级，不是概率统计。项目事实按 `e61fd00d` 核对；概念见各节出处表。先用一句话认工具，再用三句话讲流程，最后挑值得追问的机制深入。没有选型比较，就说没有，不替团队编理由。',
    ]
    system = a.SECTION_PURPOSE_SYSTEM
    assert '只列工具名、阅读顺序或复习优先级' in system
    assert '没有解释含义、职责或用法' in system
    assert '真实定义、职责或系统流程' in system

    paragraphs = {
        f'c{i}': dict(id=f'c{i}', source_id='source32', section=['技术栈：带你开一家学习资料室'],
                     text=text, parent_chunk_id='chunk32')
        for i, text in enumerate(guidance, 1)
    }
    def offline_guide_classifier(stage, prompt, messages):
        assert stage == 'classify'
        assert prompt == system
        assert all(text in messages[0]['content'] for text in guidance)
        return json.dumps([dict(id='S1', reason='章节导言仅提供阅读顺序与复习方法。',
                                knowledge=[], navigation=['c1', 'c2'])], ensure_ascii=False)

    classified = a.directory_purposes(paragraphs, offline_guide_classifier)

    assert classified[0]['knowledge'] == []
    assert classified[0]['navigation'] == ['c1', 'c2']
