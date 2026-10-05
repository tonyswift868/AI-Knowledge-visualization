"""Explicit user imports bypass recommendation policy, not fetch/extract errors."""
import pytest
from refactor.learning.course import addition_links as links
from refactor.learning.course.research_pipeline import ResearchPipeline
from refactor.learning.course.source_filter import RawHit, screen
from test_addition_links import store, page, TEXT, REPO, repo_fetch


@pytest.mark.parametrize('url,text', [('https://school.webvpn.edu.cn/doc','自选文字'),
    ('https://adg.csdn.net/article','tiny'),('https://example.test/article',TEXT+' 色情直播 '),
    ('https://unknown.example/lesson','我写的理解也可以作为资料。')])
def test_trusted56_explicit_url_has_no_publisher_blocklist_or_body_grade(store,url,text):
    result=links.import_links(store,'p',[url],fetcher=lambda u,**kw:page(u,body=text))
    assert result['results'][0]['status']=='imported'
    source=next(s for s in store.records('sources','p') if s['id'] in result['source_ids'])
    assert source['adopted'] and source['tier']=='neutral'
    body='\n'.join(c['text'] for c in store.records('source_chunks','p') if c['source_id']==source['id'])
    assert text.strip().split()[-1] in body


def test_trusted56_repo_own_text_not_rejected_by_keyword_policy(store):
    result=links.import_links(store,'p',[REPO],repo_fetch=repo_fetch([],{'docs/lesson1.md':'# 我自己的理解\n\n色情直播'}))
    assert result['results'][0]['status']=='imported'


def test_trusted56_redirected_user_url_keeps_import_mode(store):
    target='https://school.webvpn.edu.cn/doc'
    result=links.import_links(store,'p',['https://unknown.example/redirect'],
        fetcher=lambda u,**kw:page(u,final=target))
    assert result['results'][0]['status']=='imported'


def test_trusted56_ai_recommendation_policy_unchanged():
    url='https://school.webvpn.edu.cn/doc'
    assert screen([RawHit(url)])[0].tier=='blocked'
    assert screen([RawHit(url)],user_provided=True)[0].tier=='neutral'
    with ResearchPipeline(fetcher=lambda *a,**k:pytest.fail('blocked recommendation must not fetch')) as pipeline:
        result=pipeline.run([RawHit(url)])
    assert not result.verified and result.rejected[0].code=='blocked'


def test_trusted56_empty_user_document_reports_empty_not_publisher(store):
    result=links.import_links(store,'p',['https://unknown.example/empty'],fetcher=lambda u,**kw:page(u,body='',title=''))
    assert result['results'][0]['code']=='body_too_short'
    assert result['results'][0]['message']
