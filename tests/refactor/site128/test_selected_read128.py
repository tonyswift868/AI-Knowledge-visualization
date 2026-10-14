"""Real local slow HTTP, otherwise explicit offline transport/model fixtures."""
import threading,time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone,timedelta
from email.utils import format_datetime
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer

import httpx
import pytest

from refactor.learning.course import research_pipeline as rp, site_book as book
from refactor.learning.course.research import ResearchService,ResearchFetchError,ResearchTimeout,ResearchCancelled,FetchedContent
from refactor.learning.course.source_filter import RawHit
from tests.refactor.plan51.test_plan51_selected_material_flow import flow
from tests.refactor.adopt124.test_recommended_adoption124 import recommend
from tests.refactor.site125.test_site_book125 import ROOT,TEXT,site,fetched


@pytest.fixture(autouse=True)
def quick_backoff(monkeypatch):
    monkeypatch.setattr(rp,'SELECTED_BACKOFF',0)
    monkeypatch.setattr(book,'DEFAULT_INTERVAL',0)


def test_128_real_local_server_30_seconds_continuous_download_kept(monkeypatch):
    parts=[b'<html><title>Slow lesson</title><main><p>']+[('持续下载的原文。'*20).encode()]*30+['</p><p>末尾标记128</p></main></html>'.encode()]
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200);self.send_header('Content-Type','text/html');self.send_header('Content-Length',str(sum(map(len,parts))));self.end_headers()
            self.wfile.write(parts[0]);self.wfile.flush()
            for part in parts[1:-1]:time.sleep(1);self.wfile.write(part);self.wfile.flush()
            self.wfile.write(parts[-1]);self.wfile.flush()
        def log_message(self,*a):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    original=ResearchService
    monkeypatch.setattr(rp,'ResearchService',lambda **kw:original(transport=httpx.HTTPTransport(),**kw))
    started=time.monotonic()
    try:
        with rp.ResearchPipeline(timeout=2) as pipeline:
            result=pipeline.run([RawHit(f'http://127.0.0.1:{server.server_port}/lesson')],user_provided=True)
        import hashlib
        assert result.verified and result.verified[0].content_sha256==hashlib.sha256(b''.join(parts)).hexdigest()
        assert any('末尾标记128' in b.text for b in result.verified[0].document.blocks)
        assert time.monotonic()-started>=30
    finally:server.shutdown();server.server_close();thread.join(2)


def test_128_ssl_eof_twice_then_success():
    calls=[]
    def fetch(url,**kw):
        calls.append(url)
        if len(calls)<3:raise ResearchFetchError('SSL UNEXPECTED_EOF_WHILE_READING')
        return fetched(url,'<title>网络知识</title><main><p>'+TEXT+'</p></main>')
    with rp.ResearchPipeline(fetcher=fetch) as pipeline:result=pipeline.run([RawHit(ROOT+'a.html')],user_provided=True)
    assert result.verified and len(calls)==3


def test_128_429_retry_after_is_honoured():
    calls=[];times=[]
    def transport(request):
        calls.append(str(request.url));times.append(time.monotonic())
        if len(calls)==1:return httpx.Response(429,headers={'Retry-After':'1'})
        return httpx.Response(200,headers={'Content-Type':'text/html'},text='<main><p>'+TEXT+'</p></main>')
    service=ResearchService(transport=httpx.MockTransport(transport))
    result=rp.retry_selected(lambda:rp.fetch_selected_bytes(ROOT+'a.html',service=service))
    assert result.body and len(calls)==2 and times[1]-times[0]>=1
    error=ResearchFetchError('429',status_code=429);error.retry_after=format_datetime(datetime.now(timezone.utc)+timedelta(seconds=10))
    assert 8<=rp._retry_delay(error,0)<=10


def test_128_failed_first_pass_supplement_recovers_and_keeps_toc_order():
    fetch,_=site('<aside class="sidebar"><a href="a.html">第一节</a><a href="b.html">第二节</a></aside>');attempts=[]
    def transport(url,**kw):
        if url==ROOT+'a.html':
            attempts.append(url)
            if len(attempts)<=3:raise ResearchFetchError('SSL EOF')
        return fetch(url,**kw)
    toc=book.discover(ROOT,fetcher=transport)
    verified,result=book.read_book(toc,['page1','page2'],fetcher=transport)
    assert len(attempts)==4 and result['successful_pages']==2 and not result['failed_pages']
    assert [p['title'] for p in result['read_pages']]==['第一节','第二节']
    assert verified.document.blocks[0].section[0]=='第一节'


@pytest.mark.parametrize('error',[ResearchTimeout('robots连续无数据'),ResearchFetchError('503',status_code=503)])
@pytest.mark.parametrize('route',['paste','recommendation'])
def test_128_robots_failure_falls_back_to_selected_single_page(flow,error,route):
    f,c,pid,_=flow;calls=[]
    def fetch(url,**kw):
        calls.append(url)
        if url.endswith('/robots.txt'):raise error
        assert url.rstrip('/')==ROOT.rstrip('/')
        return fetched(url,'<aside class="sidebar"><a href="a.html">第一节</a><a href="b.html">第二节</a></aside><main><h1>当前页</h1><p>'+TEXT+'</p></main>')
    f.fetcher=fetch
    if route=='paste':f.run(pid,lambda d,c:f._import_links(d,c,[ROOT]),c)
    else:
        recommend(f,pid,[ROOT]);f.run(pid,lambda d,c:f.decide(d,'pick1',True,ctx=c),c)
    draft=f.load(pid);card=draft['sources'][0]
    assert card['status']=='adopted' and card['chars']>120 and not card.get('site_book')
    assert calls.count('https://book.example/robots.txt')==3 and sum(u.rstrip('/')==ROOT.rstrip('/') for u in calls)==1
    assert '整本读取请稍后重试' in draft['source_read_warnings'][ROOT]
    assert '整本读取请稍后重试' in draft['source_read_warnings'][card['url']]


def test_128_any_directory_parse_failure_falls_back_to_single_page(flow,monkeypatch):
    f,c,pid,_=flow
    monkeypatch.setattr(book,'toc_entries',lambda *a:(_ for _ in ()).throw(ValueError('坏目录')))
    fetch,_=site('<main><p>'+TEXT+'</p></main>');f.fetcher=fetch
    f.run(pid,lambda d,c:f._import_links(d,c,[ROOT]),c)
    d=f.load(pid);assert d['sources'][0]['status']=='adopted'
    assert '坏目录' in d['source_read_warnings'][ROOT]


def test_128_cancel_returns_before_blocked_fetch_finishes():
    entered=threading.Event();release=threading.Event();cancel=threading.Event()
    def fetch(url,**kw):entered.set();release.wait(5);return fetched(url,'<main>'+TEXT+'</main>')
    with ThreadPoolExecutor(max_workers=1) as pool, rp.ResearchPipeline(fetcher=fetch) as pipeline:
        future=pool.submit(pipeline.run,[RawHit(ROOT+'a.html')],user_provided=True,cancel=cancel)
        try:
            assert entered.wait(2);cancel.set();result=future.result(timeout=2)
            assert result.cancelled and not result.verified and not release.is_set()
        finally:release.set()


def test_128_cancel_during_retry_after_does_not_retry():
    entered=threading.Event();cancel=threading.Event();calls=[]
    def op():
        calls.append(1);entered.set();error=ResearchFetchError('429',status_code=429);error.retry_after='120';raise error
    with ThreadPoolExecutor(max_workers=1) as pool:
        future=pool.submit(rp.retry_selected,op,cancel)
        assert entered.wait(2);cancel.set()
        with pytest.raises(ResearchCancelled):future.result(timeout=2)
    assert len(calls)==1


def test_128_candidate_still_has_total_deadline():
    entered=threading.Event();release=threading.Event()
    def fetch(url,**kw):entered.set();release.wait(2);return fetched(url,'<main>'+TEXT+'</main>')
    with rp.ResearchPipeline(fetcher=fetch,timeout=.05) as pipeline:
        try:
            result=pipeline.run([RawHit(ROOT+'a.html')])
            assert entered.is_set() and not result.verified and result.rejected[0].code=='fetch_timeout'
        finally:release.set()
