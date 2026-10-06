"""A parent title must not hide material coverage; no paid model in pytest."""
import json

from refactor.learning.course import planner_agents as a
from refactor.learning.connections.pricing import _bounds


def sections(paths):
    return [a.Section(f's1.{i}', 'source', '完整资料', ' / '.join(path), (f'c{i}',), 20, i, i)
            for i, path in enumerate(paths, 1)]


def send(rows, removed=()):
    system, messages = a.material_structure_request('Java 后端与AI Agent面试', {}, None, rows, removed_content=removed)
    return system, json.loads(messages[0]['content'])


def test_plan62_breadth_first_names_expose_agent_and_java_topics():
    rows = sections([['大模型基础'], ['大模型基础','LLM'], ['大模型基础','LLM','Token'],
        ['大模型基础','Agent'], ['大模型基础','Agent','ReAct'], ['大模型基础','Agent','MCP'],
        ['大模型基础','RAG'], ['Java','并发'], ['Java','JVM'], ['Java','Spring'],
        ['Java','消息队列'], ['Java','缓存'], ['Java','MySQL']] + [['附录',f'补充{i}'] for i in range(160)])
    system, payload = send(rows)
    ai = next(r for r in payload['directory'] if r['title']=='大模型基础')
    assert ai['headings'] == ['LLM','Agent','RAG','Token','ReAct','MCP']
    text = json.dumps(payload['directory'], ensure_ascii=False)
    assert all(word in text for word in ('Agent','ReAct','MCP','RAG','并发','JVM','Spring','消息队列','缓存','MySQL'))
    assert '同时看title与headings' in system and '只有目标内容在整份目录' in system
    assert all(len(r.get('headings',[])) <= 30 for r in payload['directory'])
    assert len(payload['sources']) == 1


def test_plan62_overflow_names_marked_not_body_and_refs_still_expand():
    rows = sections([['教材',f'章节{i}'] for i in range(400)])
    _, payload = send(rows)
    entry = payload['directory'][0]
    assert entry['headings']==[f'章节{i}' for i in range(30)]
    assert '共400个' in entry['more'] and '370个未展开' in entry['more']
    assert '正文' not in json.dumps(payload['directory'],ensure_ascii=False)
    reply=json.dumps(dict(summary='结构',modules=[dict(key='m1',title='教材',summary='学章节',refs=[entry['id']])],missing=[]))
    parsed=a.parse_material_structure(reply,rows)
    assert parsed['modules'][0]['refs']==[r.id for r in rows]


def test_plan62_large_names_reduce_automatically_with_directory_budget():
    rows=sections([[f'根{i//40}', f'长标题{i}'+('数学公式解释'*20)] for i in range(6000)])
    _, payload=send(rows)
    directory=dict(sources=payload['sources'],directory=payload['directory'])
    assert _bounds(json.dumps(directory,ensure_ascii=False,separators=(',',':')))[1] <= 15000
    assert max(len(r.get('headings',[])) for r in payload['directory']) < 30
    assert any('more' in r for r in payload['directory'])
    assert len(payload['directory']) == 150


def test_plan62_deleted_children_not_visible_or_reintroduced():
    rows=sections([['教材'],['教材','保留'],['教材','删除'],['教材','删除','更深']]+
                  [['教材',f'另一个{i}'] for i in range(200)])
    removed=[dict(kind='chapter',title='教材 / 删除',source_id='source')]
    _, payload=send(rows,removed)
    text=json.dumps(payload['directory'],ensure_ascii=False)
    assert '删除' not in text and '更深' not in text
    reply=json.dumps(dict(summary='结构',modules=[dict(key='m1',title='教材',summary='保留',refs=[payload['directory'][0]['id']])],missing=[]))
    parsed=a.parse_material_structure(reply,rows,removed_content=removed)
    assert rows[1].id in parsed['modules'][0]['refs']
    assert rows[2].id not in parsed['modules'][0]['refs'] and rows[3].id not in parsed['modules'][0]['refs']
