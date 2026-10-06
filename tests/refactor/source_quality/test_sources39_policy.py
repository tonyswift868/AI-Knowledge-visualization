"""Batch 39: source admission/ranking and unchanged original-answer checks."""
from datetime import date
from dataclasses import replace
from types import SimpleNamespace
import pytest

from refactor.learning.course import source_quality as quality, point_materials as pm
from refactor.learning.course.source_filter import classify_source, load_lists, RawHit, screen, DomainRule
from refactor.learning.course.research_pipeline import ResearchPipeline
from refactor.learning.course.research import FetchedContent
from tests.refactor.point_materials.test_point_materials37_contract import store


@pytest.mark.parametrize('url,body,credibility',[
    ('https://docs.python.org/3/tutorial/controlflow.html','Python functions parameters '*10,5),
    ('https://jyeoo.com/questions/123','Python functions parameters '*10,4),
    ('https://baike.baidu.com/item/python','Python functions parameters '*10,3),
    ('https://www.bbc.com/news/python','Python functions parameters '*10,3),
    ('https://company.example/lesson','作者：某公司教研组\nPython functions parameters '*10,3),
    ('https://school.example/lesson','发布机构：某学校教学研究室\nPython functions parameters '*10,3),
    ('https://teacher.example/~user/lesson','作者：王老师\nPython functions parameters '*10,3),
    ('https://blog.csdn.net/teacher/article/details/12','Python functions parameters '*10,3),
    ('https://www.zhihu.com/question/123','Python functions parameters '*10,3),
    ('https://tieba.baidu.com/p/123','Python functions parameters '*10,3),
    ('https://unknown.example/~user/lesson','Python functions parameters '*10,2),
    ('https://cnpython.com/lesson','Python functions parameters '*10,1),
    ('https://blog.csdn.net/teacher/article/details/12','转载自：某网站\nPython functions parameters '*10,1),
    ('https://unknown.example/lesson','',0),
])
def test_sources39_credibility_bands(url,body,credibility):
    source=SimpleNamespace(final_url=url,title='Python functions parameters',reasons=(),published_at=None,
        document=SimpleNamespace(blocks=[SimpleNamespace(text=body)],warnings=()))
    result=quality.assess(source,'Python functions parameters')
    assert result['credibility']==credibility
    assert result['eligible'] is (credibility>=3)


def test_sources39_challenge_page_is_not_a_textbook_body():
    source=SimpleNamespace(final_url='https://zh.khanacademy.org/science/physics/fluids',
        title='Client Challenge',reasons=(),published_at=None,
        document=SimpleNamespace(blocks=[SimpleNamespace(text='Please enable JavaScript to continue.')],warnings=()))
    result=quality.assess(source,'浮力与阿基米德原理')
    assert result['credibility']==1 and not result['eligible']
    assert 'lesson_body_unavailable' in result['reasons']


def test_sources39_general_evidence_cannot_claim_authority():
    text='本页发布者是某公司教学团队。'
    evidence=dict(type='company',publisher='某公司教学团队',quote=text)
    assert classify_source('https://unknown.example/chapter',page_text=text,publisher_evidence=evidence)['level']==3
    assert classify_source('https://unknown.example/chapter',page_text='没有这句',publisher_evidence=evidence)['level']==0
    assert classify_source('https://adg.csdn.net/chapter',page_text=text,publisher_evidence=evidence)['level']==0


def test_sources39_old_catalog_does_not_exclude_all_csdn_articles():
    lists=replace(load_lists(),low=(DomainRule('csdn.net','旧目录整站排除'),))
    article='https://blog.csdn.net/teacher/article/details/12'
    assert screen([RawHit(article)],lists=lists)[0].tier=='neutral'
    assert classify_source(article,lists=lists)['level']==3
    assert screen([RawHit('https://adg.csdn.net/article')],lists=lists)[0].tier=='low'


def test_sources39_rank_credibility_before_relevance_and_community_tie():
    rows=[dict(id='community',credibility=3,relevance=5,community=True),dict(id='general',credibility=3,relevance=5),
          dict(id='platform',credibility=4,relevance=4),dict(id='official',credibility=5,relevance=4),
          dict(id='general-low',credibility=3,relevance=4)]
    assert [r['id'] for r in sorted(rows,key=quality.rank_key)]==['official','platform','general','community','general-low']


def test_sources39_verified_search_is_sorted_after_actual_body_validation():
    urls=['https://blog.csdn.net/x/article/details/1','https://www.bbc.com/news/python',
          'https://jyeoo.com/questions/1','https://docs.python.org/3/tutorial/controlflow.html']
    def fetch(url,**kwargs):
        body=('<title>Python functions parameters</title><h1>Python functions parameters</h1><p>'
              +'Python functions accept parameters and return values. '*10+f'</p><p>Example identifier: {url}</p>').encode()
        return FetchedContent(url,url,'text/html',body,'')
    with ResearchPipeline(fetcher=fetch) as pipeline:
        result=pipeline.run([RawHit(url) for url in urls],goal='Python functions parameters')
    assert [s.url for s in result.verified]==[urls[3],urls[2],urls[1],urls[0]]


def test_sources39_fetch_limit_does_not_discard_late_authoritative_candidate():
    calls=[]
    def fetch(url,**kwargs):
        calls.append(url)
        return FetchedContent(url,url,'text/html',('<h1>压强</h1><p>'+'压强是压力与受力面积的比值。'*20+'</p>').encode(),'')
    urls=['https://unknown.example/1','https://unknown.example/2','https://pep.com.cn/pressure']
    with ResearchPipeline(fetcher=fetch,max_sources=1) as pipeline:
        result=pipeline.run([RawHit(u,'压强') for u in urls],goal='压强')
    assert calls==[urls[2]] and result.verified[0].url==urls[2]
    assert sum(r.code=='limit_reached' for r in result.rejected)==2


def change_source(store,url):
    source=store.records('sources','p')[0]
    store.add_sources('p',[dict(source,url=url,final_url=url)])


def material70(store):
    # Publisher classification does not exclude selected original material.
    from tests.refactor.material_questions70.test_material_questions70_contract import TEXT70,split70
    source=store.records('sources','p')[0]
    store.add_sources('p',[dict(source,chunks=[dict(chunk_id='c',text=TEXT70,section=['定义'])])])
    return dict(question_splitter=split70)


def test_sources39_general_materials_and_original_answers_are_usable(store):
    change_source(store,'https://blog.csdn.net/teacher/article/details/1')
    point=store.records('points','p')[0]
    assert pm.prepare_point(store,'p',point,material70(store))['questions']==2
    assert not pm.uncovered_points(store,'p',{})
    assert not quality.general_only(['c'],store.records('sources','p'),store.records('source_chunks','p'))
    bank=store.records('question_bank','p')
    assert all(q['origin']=='found' and q['kind']=='recall' and q['answer_source']['url'].startswith('https://blog.csdn.net/') for q in bank)


def test_sources39_selected_sources_share_one_batch_without_authority_bands(store):
    from tests.refactor.material_questions70.test_material_questions70_contract import TEXT70,split70
    context=material70(store)
    store.add_sources('p',[dict(id='g',title='社区教学',url='https://www.zhihu.com/question/12',adopted=True,
        chunks=[dict(chunk_id='gc',text=TEXT70,section=['定义'])])])
    point=store.records('points','p')[0];point['chunk_ids'].append('gc')
    with store.transaction():store._put('points','p',point)
    calls=[]
    def splitter(packet,**kwargs):
        calls.append({cid for q in packet['candidates'] for cid in q['chunk_ids']})
        return split70(packet)
    context['question_splitter']=splitter
    assert pm.prepare_point(store,'p',point,context)['questions']==2  # batch-66 stem dedup
    assert not calls  # Short original answers need no AI key-point selection.
    from refactor.learning.course.material_questions import candidates
    rows=candidates(store.records('source_chunks','p'),store.records('sources','p'))
    assert {'c','gc'} <= {cid for q in rows for cid in q['chunk_ids']}


def test_sources39_selected_unknown_publisher_is_usable(store):
    change_source(store,'https://unknown.example/chapter')
    assert pm.prepare_point(store,'p',store.records('points','p')[0],material70(store))['questions']==2
    assert not pm.uncovered_points(store,'p',{})
