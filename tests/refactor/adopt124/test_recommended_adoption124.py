"""Real draft/store/import pipeline; only public HTTP and intent model are fake."""
import copy
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from refactor.learning.app.bridge import BridgeError
from refactor.learning.course import plan_intent
from tests.refactor.plan51.test_plan51_selected_material_flow import flow
from tests.refactor.plan_additions.test_addition_links import PAGE,REPO,page,repo_fetch


def recommend(f,pid,urls=(PAGE,)):
    d=f.load(pid)
    d['recommendations']=[dict(card_id=f'pick{i}',url=u,title=f'推荐资料{i}',type='文档',why='对应目标',modules=['基础'],
        status='new',kind='url',site=u,chars=0,credibility=3,relevance=None,note='推荐',summary='正文待读取')
        for i,u in enumerate(urls,1)]
    f.save(d)
    return d


def test_124_checkbox_reads_original_card_and_does_not_default_adopt(flow):
    f,c,pid,model=flow;events=[]
    f.push=lambda name,data:events.append((name,copy.deepcopy(data)))
    d=recommend(f,pid);f.fetcher=lambda u,**kw:page(u)
    assert not f.services.store.records('sources',pid)
    result=f.run(pid,lambda d,c:f.decide(d,'pick1',True,ctx=c),c)['draft']
    card=result['recommendations'][0]
    assert card['status']=='adopted' and card['source_id'] and card['chars']>120
    assert result['sources'][0]['card_id']=='pick1'
    assert any(data.get('recommendations',[{}])[0].get('read_status')=='reading' for name,data in events if name=='plan_draft')
    source=f.services.store.records('sources',pid)[0]
    assert source['adopted'] and source['char_count']==card['chars']
    assert f._adopted(f.load(pid))[0][0]['id']==source['id']
    before=len(f.services.store.records('source_chunks',pid))
    f.run(pid,lambda d,c:f.decide(d,'pick1',True,ctx=c),c)
    assert len(f.services.store.records('source_chunks',pid))==before


@pytest.mark.parametrize('title,body,url,reason',[
    ('Login','Please sign in to view this lesson. '*20,PAGE,'不可访问'),
    ('404 Not found','Page removed. '*20,PAGE,'已下架'),
    ('Unavailable','This book has been removed. '*20,PAGE,'已下架'),
    ('Python Functions','Too short.',PAGE,'太少'),
    ('Video','A video player. '*20,'https://youtube.com/watch?v=1','视频'),
])
def test_124_read_failure_explained_no_adoption(flow,title,body,url,reason):
    f,c,pid,_=flow;recommend(f,pid,[url])
    f.fetcher=lambda u,**kw:page(u,title=title,body=body)
    view=f.run(pid,lambda d,c:f.decide(d,'pick1',True,ctx=c),c)['draft']
    card=view['recommendations'][0]
    assert card['status']=='new' and card['read_status']=='failed'
    assert reason in card['read_error'] and '下载后上传' in card['read_error']
    assert not card.get('source_id') and not view['sources']
    assert not f.services.store.records('source_chunks',pid)


def test_124_does_not_replace_failed_original_with_another_page(flow):
    f,c,pid,_=flow;recommend(f,pid);calls=[]
    def fetch(url,**kw):
        calls.append(url)
        return page(url,body='<a href="https://docs.python.org/3/another-lesson.html">另一份课文</a>')
    f.fetcher=fetch
    view=f.run(pid,lambda d,c:f.decide(d,'pick1',True,ctx=c),c)['draft']
    # Directory discovery may read robots/sitemap and the requested page, but
    # must never substitute an arbitrary body hyperlink for the failed lesson.
    assert set(calls)<={PAGE,'https://docs.python.org/robots.txt','https://docs.python.org/sitemap.xml'}
    assert view['recommendations'][0]['url']==PAGE
    assert view['recommendations'][0]['read_status']=='failed'
    assert not f.services.store.records('sources',pid)


def test_124_known_video_card_is_explained_without_fetching(flow):
    f,c,pid,_=flow;d=recommend(f,pid)
    d['recommendations'][0]['is_video']=True;f.save(d)
    f.fetcher=lambda *a,**k:pytest.fail('known video fetched')
    card=f.run(pid,lambda d,c:f.decide(d,'pick1',True,ctx=c),c)['draft']['recommendations'][0]
    assert card['read_code']=='video_source' and card['status']=='new' and '下载后上传' in card['read_error']


def test_124_general_site_only_adopted_after_explicit_selection(flow):
    f,c,pid,_=flow;url='https://independent.example/lesson';recommend(f,pid,[url])
    f.fetcher=lambda u,**kw:page(u)
    assert not f._adopted(f.load(pid))[0]
    card=f.run(pid,lambda d,c:f.decide(d,'pick1',True,ctx=c),c)['draft']['recommendations'][0]
    assert card['credibility']==3 and card['status']=='adopted'


def test_124_cancel_fences_late_fetch_and_resets_card(flow):
    f,c,pid,_=flow;recommend(f,pid);entered=threading.Event();release=threading.Event()
    def slow(u,**kw):entered.set();assert release.wait(3);return page(u)
    f.fetcher=slow
    with ThreadPoolExecutor(max_workers=1) as pool:
        future=pool.submit(f.run,pid,lambda d,c:f.decide(d,'pick1',True,ctx=c),c)
        assert entered.wait(2)
        assert f.load(pid)['recommendations'][0]['read_status']=='reading'
        c.cancel.set();release.set()
        with pytest.raises(BridgeError) as err:future.result(4)
    assert err.value.code=='CANCELLED'
    card=f.load(pid)['recommendations'][0]
    assert card['read_status']=='cancelled' and card['status']=='new'
    assert not f.services.store.records('sources',pid)


def test_124_reopen_does_not_leave_an_uncancellable_reading_card(flow):
    from refactor.learning.app.plan_flow import PlanFlow
    f,c,pid,_=flow;d=recommend(f,pid)
    d['recommendations'][0]['read_status']='reading';f.save(d)
    fresh=PlanFlow(f.services,f.push,f.study_day,fetcher=f.fetcher)
    card=fresh.get_draft(pid)['recommendations'][0]
    assert card['read_status']=='cancelled' and '重新勾选' in card['read_error']


def test_124_github_uses_existing_repository_reader(flow):
    f,c,pid,_=flow;recommend(f,pid,[REPO]);calls=[]
    f.fetcher=lambda *a,**k:pytest.fail('repository went through web reader')
    f.repo_fetch=repo_fetch(calls)
    result=f.run(pid,lambda d,c:f.decide(d,'pick1',True,ctx=c),c)['draft']
    assert result['recommendations'][0]['status']=='adopted' and calls
    assert any('/git/trees/' in u for u in calls)
    assert any('book-zh/chapter2.md' in u for u in calls)


def test_124_chat_second_recommendation_same_reader_and_reports_failure_honestly(flow,monkeypatch):
    f,c,pid,model=flow;recommend(f,pid,[PAGE,PAGE.replace('controlflow','datastructures')])
    original=model.complete;requests=[]
    def complete(purpose,messages,**kw):
        if purpose=='plan_intent':
            requests.append(messages)
            return SimpleNamespace(text=json.dumps(dict(intents=[dict(kind='decide_sources',adopt=[2],reject=[])],reply='')))
        return original(purpose,messages,**kw)
    monkeypatch.setattr(model,'complete',complete)
    f.fetcher=lambda u,**kw:page(u)
    view=f.run(pid,lambda d,c:f.chat(d,c,'用第2个推荐'),c)['draft']
    assert view['recommendations'][1]['status']=='adopted' and view['recommendations'][0]['status']=='new'
    assert '不能直接采用推荐链接' not in plan_intent.INTENT_SYSTEM
    assert 'decide_sources' in plan_intent.INTENT_SYSTEM and requests
    # Failed selections must not produce the host's "已采用" acknowledgement.
    f.run(pid,lambda d,c:f.decide(d,'pick2',False,ctx=c),c)
    recommend(f,pid,['https://docs.python.org/3/new-failure.html'])
    f.fetcher=lambda u,**kw:page(u,body='short')
    monkeypatch.setattr(model,'complete',lambda *a,**k:SimpleNamespace(text=json.dumps(dict(
        intents=[dict(kind='decide_sources',adopt=[1],reject=[])],reply=''))))
    view=f.run(pid,lambda d,c:f.chat(d,c,'用第1个推荐'),c)['draft']
    assert view['messages'][-1]['text'].startswith('没有采用新的资料')


def test_124_adopted_recommendation_can_continue_material_structure(flow):
    f,c,pid,_=flow;recommend(f,pid)
    # Existing integrated fixture is a real extracted three-section document.
    f.run(pid,lambda d,c:f.decide(d,'pick1',True,ctx=c),c)
    view=f.run(pid,lambda d,c:f.make_outline(d,c),c)['draft']
    assert view['phase']=='material_preview' and view['material_structure']['modules']
    assert f._sections(f.load(pid))[1]
    view=f.run(pid,lambda d,c:f.resolve_missing(d,c,'continue'),c)['draft']
    points=[p for s in view['outline']['stages'] for m in s['modules'] for p in m['points']]
    assert view['phase']=='outline' and points
    assert all(p['structure']=='concept_v1' and p['subpoints'] for p in points)
