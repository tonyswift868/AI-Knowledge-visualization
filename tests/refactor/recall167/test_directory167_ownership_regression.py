import pytest
from refactor.learning.course import planner_agents


def test_167_navigation_paragraph_cannot_be_repeated_in_two_navigation_items():
    paragraphs = {'c1': dict(id='c1', text='先读主线，再挑选需要的章节。',
        source_id='source', section=['导读'], parent_chunk_id='chunk', purpose='navigation')}
    structure = dict(points=[], navigation=[
        dict(id='N1', refs=['c1']), dict(id='N2', refs=['c1'])])
    with pytest.raises(planner_agents.AgentFormatError, match='正文段落重复归属'):
        planner_agents.validate_directory_structure(structure, paragraphs)


def test_167_subpoint_cannot_count_the_same_paragraph_twice():
    paragraphs = {'c1': dict(id='c1', text='事务保证一组数据库修改同时成功或失败。',
        source_id='source', section=['事务'], parent_chunk_id='chunk', purpose='knowledge')}
    structure = dict(navigation=[], points=[dict(id='K1', minutes=3, subpoints=[
        dict(id='K1-S1', refs=['c1', 'c1'], minutes=3, bullets=[])])])
    with pytest.raises(planner_agents.AgentFormatError, match='正文段落重复归属'):
        planner_agents.validate_directory_structure(structure, paragraphs)
