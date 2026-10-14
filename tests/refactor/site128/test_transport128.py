"""Selected IO errors retain causes and the old content/redirect boundaries."""
import httpx
import pytest
from refactor.learning.course import research_pipeline as rp, site_book as book
from refactor.learning.course.research import ResearchService,ResearchFetchError,ResearchTimeout,ResearchUploadRequired
from tests.refactor.plan51.test_plan51_selected_material_flow import flow
from tests.refactor.site125.test_site_book125 import ROOT,TEXT,fetched,site


def test_128_idle_timeout_retried_and_final_reason_kept(monkeypatch):
    monkeypatch.setattr(rp,'SELECTED_BACKOFF',0);seen=[]
    def fetch(request):seen.append(1);raise httpx.ReadTimeout('no bytes',request=request)
    service=ResearchService(transport=httpx.MockTransport(fetch))
    with pytest.raises(ResearchTimeout,match='连续等待数据超时'):
        rp.retry_selected(lambda:rp.fetch_selected_bytes(ROOT,service=service))
    assert len(seen)==3


def test_128_permanent_failure_supplement_keeps_specific_http_reason(monkeypatch):
    monkeypatch.setattr(rp,'SELECTED_BACKOFF',0);monkeypatch.setattr(book,'DEFAULT_INTERVAL',0)
    fetch,_=site('<aside class="sidebar"><a href="a.html">第一节</a><a href="b.html">第二节</a></aside>');calls=[]
    def transport(url,**kw):
        if url.endswith('a.html'):calls.append(1);raise ResearchFetchError('网页返回 HTTP 503，未读取正文。',status_code=503)
        return fetch(url,**kw)
    toc=book.discover(ROOT,fetcher=transport)
    _,result=book.read_book(toc,['page1','page2'],fetcher=transport)
    assert len(calls)==6 and result['successful_pages']==1
    assert result['failed_pages'][0]['round']==2 and '503' in result['failed_pages'][0]['message']


def test_128_unavailable_sitemap_still_imports_original_single(flow,monkeypatch):
    monkeypatch.setattr(rp,'SELECTED_BACKOFF',0);monkeypatch.setattr(book,'DEFAULT_INTERVAL',0)
    f,c,pid,_=flow;calls=[]
    def transport(url,**kw):
        calls.append(url)
        if url.endswith('robots.txt'):return fetched(url,'','text/plain')
        if url.endswith('sitemap.xml'):raise ResearchFetchError('503 sitemap',status_code=503)
        return fetched(url,'<main><p>'+TEXT+'</p></main>')
    f.fetcher=transport;f.run(pid,lambda d,c:f._import_links(d,c,[ROOT]),c)
    assert f.load(pid)['sources'][0]['status']=='adopted'
    assert 'sitemap' in f.load(pid)['source_read_warnings'][ROOT]
    assert sum(u.endswith('sitemap.xml') for u in calls)==3


@pytest.mark.parametrize('status',[401,403])
def test_128_protected_smartedu_keeps_upload_guidance_no_retry(status,monkeypatch):
    monkeypatch.setattr(rp,'SELECTED_BACKOFF',0);calls=[]
    url='https://basic.smartedu.cn/tchMaterial/detail?contentId=aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'
    def transport(request):
        calls.append(str(request.url))
        if str(request.url).endswith('.json'):
            return httpx.Response(200,headers={'Content-Type':'application/json'},json={'title':'教材标题','ti_items':[{'ti_format':'pdf','ti_is_source_file':True,'ti_storages':['https://public.example/book.pdf']}]})
        return httpx.Response(status)
    service=ResearchService(transport=httpx.MockTransport(transport))
    with pytest.raises(ResearchUploadRequired,match='教材标题') as exc:
        rp.retry_selected(lambda:rp.fetch_selected_bytes(url,service=service))
    assert str(status) in str(exc.value) and len(calls)==2


def test_128_redirect_byte_and_type_checks_remain():
    def transport(request):
        if request.url.path=='/root':return httpx.Response(302,headers={'location':'/body'})
        return httpx.Response(200,headers={'Content-Type':'text/plain'},text='abcdef')
    service=ResearchService(transport=httpx.MockTransport(transport))
    assert rp.fetch_selected_bytes('https://example.edu/root',service=service).body==b'abcdef'
    with pytest.raises(ResearchFetchError,match='大小限制'):
        rp.fetch_selected_bytes('https://example.edu/root',service=service,max_bytes=3)


def test_128_expired_directory_robots_failure_at_confirmation_falls_back(flow,monkeypatch):
    monkeypatch.setattr(rp,'SELECTED_BACKOFF',0);monkeypatch.setattr(book,'DEFAULT_INTERVAL',0)
    f,c,pid,_=flow
    fetch,_=site('<aside class="sidebar"><a href="a.html">第一节</a><a href="b.html">第二节</a></aside><main><p>'+TEXT+'</p></main>')
    f.fetcher=fetch;f.run(pid,lambda d,c:f._import_links(d,c,[ROOT]),c)
    draft=f.load(pid);card=draft['sources'][0];card['site_book']['robot_snapshot']['retrieved_at']=0;f.save(draft)
    calls=[]
    def unavailable(url,**kw):
        calls.append(url)
        if url.endswith('/robots.txt'):raise ResearchFetchError('robots HTTP 503',status_code=503)
        assert url.rstrip('/')==ROOT.rstrip('/')
        return fetched(url,'<main><p>'+TEXT+'</p></main>')
    f.fetcher=unavailable
    f.run(pid,lambda d,c:f.decide(d,card['card_id'],True,ctx=c,selected_pages=['page1','page2']),c)
    final=f.load(pid);card=final['sources'][0]
    assert card['status']=='adopted' and card['chars']>120
    assert '整本读取请稍后重试' in final['source_read_warnings'][card['url']]
    assert sum(u.endswith('/robots.txt') for u in calls)==3 and len(calls)==4
