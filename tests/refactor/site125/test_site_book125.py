"""No external HTTP/models: fake sites, real extractor/store/draft code."""
import copy
from threading import Event
from urllib.parse import urlsplit

import pytest

from refactor.learning.course import site_book as book
from refactor.learning.course.research import FetchedContent, ResearchFetchError, ResearchCancelled
from tests.refactor.plan51.test_plan51_selected_material_flow import flow
from tests.refactor.adopt124.test_recommended_adoption124 import recommend

ROOT='https://book.example/network/'
TEXT='传输控制协议提供可靠的字节流，网络层负责寻址和路由。'*20


@pytest.fixture(autouse=True)
def no_delay(monkeypatch): monkeypatch.setattr(book,'DEFAULT_INTERVAL',0)


def fetched(url,body,media='text/html'):
    return FetchedContent(url,url,media,body.encode(),'2026-10-14T00:00:00Z')


def site(markup, *, robots='User-agent: *\nAllow: /', fail=(), sitemap=None):
    calls=[]
    def fetch(url,**kw):
        calls.append(url)
        path=urlsplit(url).path
        if path=='/robots.txt': return fetched(url,robots,'text/plain')
        if path=='/sitemap.xml':
            if sitemap:return fetched(url,sitemap,'application/xml')
            raise ResearchFetchError('404',status_code=404)
        if url in fail:raise ResearchFetchError('网页返回 HTTP 404',status_code=404)
        if url==ROOT:return fetched(url,'<title>网络书</title>'+markup)
        return fetched(url,'<title>传输协议</title><main><h1>传输协议</h1><h2>可靠传输</h2><p>'+TEXT+'</p></main>')
    return fetch,calls


@pytest.mark.parametrize('menu',[
 '<aside class="sidebar"><section class="sidebar-group"><p class="sidebar-heading">第一章</p><ul><li><a href="a.html">传输</a></li><li><a href="b.html">路由</a></li></ul></section></aside>',
 '<aside class="VPSidebar"><div class="VPSidebarItem"><div class="title">第一章</div><a href="a.html">传输</a><a href="b.html">路由</a></div></aside>',
 '<nav class="md-nav--primary"><ul><li><label class="md-nav__link">第一章</label><ul><li><a href="a.html">传输</a></li><li><a href="b.html">路由</a></li></ul></li></ul></nav>',
 '<div class="toctree-wrapper"><ul><li><a href="a.html">传输</a><ul><li><a href="b.html">路由</a></li></ul></li></ul></div>',
 '<nav class="theme-doc-sidebar-container"><ul><li><a href="a.html">传输</a></li><li><a href="b.html">路由</a></li></ul></nav>',
 '<nav class="book-summary"><ul><li><a href="a.html">传输</a></li><li><a href="b.html">路由</a></li></ul></nav>',
])
def test_125_site_owned_toc_order_and_hierarchy(menu):
    fetch,calls=site(menu);toc=book.discover(ROOT,fetcher=fetch)
    assert [e['url'] for e in toc['entries']]==[ROOT+'a.html',ROOT+'b.html']
    assert toc['entries'][1]['section'][-1]=='路由'
    assert any(len(e['section'])==2 for e in toc['entries']) or 'theme-doc' in menu or 'book-summary' in menu
    assert calls==['https://book.example/robots.txt',ROOT]


def test_125_scope_exclusions_and_canonical_duplicate():
    urls=['a.html','a.html','b.html','b.html#section','https://other.example/network/c',
          '/networking/c','/other/c','login/','search/?q=x','tags/python','page/2/',
          'comments/','download.zip','image.svg','#toc','../escape.html']
    menu='<aside class="sidebar">'+''.join(f'<a href="{v}">{i}</a>' for i,v in enumerate(urls))+'</aside>'
    fetch,_=site(menu);toc=book.discover(ROOT,fetcher=fetch)
    assert [e['url'] for e in toc['entries']]==[ROOT+'a.html',ROOT+'b.html']
    assert book.canonical(ROOT+'index.html')==book.canonical(ROOT)==book.canonical(ROOT.rstrip('/'))
    assert book.allowed(ROOT+'a.html',ROOT+'index.html')


def test_125_only_sitemap_and_single_page():
    xml='<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'+''.join(f'<url><loc>{v}</loc></url>' for v in [ROOT+'tcp/a.html',ROOT+'tcp/b.html','https://other.example/a','https://book.example/other/a'])+'</urlset>'
    fetch,_=site('<main><p>'+TEXT+'</p></main>',sitemap=xml)
    toc=book.discover(ROOT,fetcher=fetch)
    assert toc['origin']=='sitemap.xml' and len(toc['entries'])==2
    assert toc['entries'][0]['section']==['tcp','a']
    fetch,_=site('<main><p>'+TEXT+'</p></main>')
    assert book.discover(ROOT,fetcher=fetch) is None


def test_125_docsify_explicit_markdown_sidebar():
    fetch,_=site('<script>window.$docsify={loadSidebar:true}</script>')
    def transport(url,**kw):
        if url.endswith('_sidebar.md'):return fetched(url,'- [传输](a.md)\n  - [路由](b.md)','text/markdown')
        return fetch(url,**kw)
    toc=book.discover(ROOT,fetcher=transport)
    assert toc['origin']=='_sidebar.md' and toc['entries'][1]['section']==['传输','路由']


def test_125_sitemap_index_order_and_external_exclusion():
    fetch,_=site('<main>'+TEXT+'</main>',sitemap='<sitemapindex><sitemap><loc>https://book.example/map1.xml</loc></sitemap><sitemap><loc>https://other.example/map.xml</loc></sitemap></sitemapindex>')
    def transport(url,**kw):
        if url.endswith('/map1.xml'):return fetched(url,f'<urlset><url><loc>{ROOT}a.html</loc></url><url><loc>{ROOT}b.html</loc></url></urlset>','text/xml')
        return fetch(url,**kw)
    toc=book.discover(ROOT,fetcher=transport)
    assert [e['url'] for e in toc['entries']]==[ROOT+'a.html',ROOT+'b.html']


def test_125_selected_only_partial_failure_and_section_paths():
    fetch,calls=site('<aside class="sidebar"><li><a href="a.html">第一章</a><ul><li><a href="b.html">第二节</a></li><li><a href="c.html">不要</a></li></ul></li></aside>',fail=[ROOT+'b.html'])
    toc=book.discover(ROOT,fetcher=fetch);progress=[];calls.clear()
    verified,result=book.read_book(toc,['page1','page2'],fetcher=fetch,progress=lambda *v:progress.append(copy.deepcopy(v)))
    assert ROOT+'c.html' not in calls
    assert result['successful_pages']==1 and result['failed_pages'][0]['url']==ROOT+'b.html'
    assert progress[0][:2]==(0,2) and progress[-1][:2]==(2,2)
    assert verified.document.blocks[0].section[0]=='第一章'
    assert all('不要' not in b.section for b in verified.document.blocks)


def test_125_robots_disallow_delay_and_cancel(monkeypatch):
    fetch,calls=site('<aside class="sidebar"><a href="a.html">传输</a><a href="b.html">路由</a></aside>',
        robots='User-agent: *\nDisallow: /network/b.html\nCrawl-delay: 2')
    # No wall-clock sleeping: assert scheduling uses robots delay separately.
    monkeypatch.setattr(book.Reader,'pace',lambda self,cancel=None:None)
    toc=book.discover(ROOT,fetcher=fetch);assert toc['interval']==2
    verified,result=book.read_book(toc,['page1','page2'],fetcher=fetch)
    assert result['successful_pages']==1 and ROOT+'b.html' not in calls
    assert 'robots.txt' in result['failed_pages'][0]['message']
    token=Event();token.set()
    with pytest.raises(ResearchCancelled):book.read_book(toc,['page1'],fetcher=fetch,cancel=token)


def test_125_cancel_after_first_page_does_not_import_partial_book(flow):
    f,c,pid,_=flow;fetch,calls=site('<aside class="sidebar"><a href="a.html">传输</a><a href="b.html">路由</a></aside>')
    f.fetcher=fetch;recommend(f,pid,[ROOT])
    f.run(pid,lambda d,c:f.decide(d,'pick1',True,ctx=c),c)
    before=f.services.store.records('sources',pid)
    from refactor.learning.app.bridge import BridgeError
    def push(name,data):
        if name=='plan_draft' and data.get('recommendations',[{}])[0].get('read_done')==1:c.cancel.set()
    f.push=push
    with pytest.raises(BridgeError):f.run(pid,lambda d,c:f.decide(d,'pick1',True,ctx=c,selected_pages=['page1','page2']),c)
    assert f.services.store.records('sources',pid)==before
    assert ROOT+'b.html' not in calls


@pytest.mark.parametrize('route',['recommendation','paste'])
def test_125_card_selection_then_one_source_and_material_structure(flow,route):
    f,c,pid,_=flow;fetch,calls=site('<aside class="sidebar"><li><a href="a.html">第一章</a><ul><li><a href="b.html">第二节</a></li></ul></li></aside>');f.fetcher=fetch
    if route=='recommendation':
        recommend(f,pid,[ROOT]);f.run(pid,lambda d,c:f.decide(d,'pick1',True,ctx=c),c)
        card=f.load(pid)['recommendations'][0]
    else:
        f.run(pid,lambda d,c:f._import_links(d,c,[ROOT]),c);card=f.load(pid)['sources'][0]
        assert f.load(pid)['messages'][-1]['kind']=='sources'
        assert card['card_id'] in f.load(pid)['messages'][-1]['cards']
    assert card['read_status']=='selecting' and not card.get('source_id')
    assert not f.services.store.records('sources',pid)
    f.run(pid,lambda d,c:f.decide(d,card['card_id'],True,ctx=c,selected_pages=['page1','page2']),c)
    sources=f.services.store.records('sources',pid);assert len(sources)==1
    chunks=f.services.store.records('source_chunks',pid)
    assert any(v['section'][:2]==['第一章','第二节'] for v in chunks)
    view=f.load(pid);source_card=next(v for v in view['sources'] if v['source_id']==sources[0]['id'])
    assert source_card['read_done']==2 and source_card['status']=='adopted' and source_card['chars']>500
    assert f._sections(view)[1]


def test_125_unknown_selection_rejected(flow):
    f,c,pid,_=flow;fetch,_=site('<aside class="sidebar"><a href="a.html">传输</a><a href="b.html">路由</a></aside>');f.fetcher=fetch;recommend(f,pid,[ROOT])
    f.run(pid,lambda d,c:f.decide(d,'pick1',True,ctx=c),c)
    f.run(pid,lambda d,c:f.decide(d,'pick1',True,ctx=c,selected_pages=['not-in-directory']),c)
    assert not f.services.store.records('sources',pid)
    assert f.load(pid)['recommendations'][0]['read_status']=='failed'


def test_125_rejected_directory_can_be_selected_again(flow):
    f,c,pid,_=flow;fetch,_=site('<aside class="sidebar"><a href="a.html">传输</a><a href="b.html">路由</a></aside>');f.fetcher=fetch;recommend(f,pid,[ROOT])
    for adopt in [True,False,True]:f.run(pid,lambda d,c:f.decide(d,'pick1',adopt,ctx=c),c)
    card=f.load(pid)['recommendations'][0]
    assert card['status']=='new' and card['read_status']=='selecting' and card['site_book']


def test_125_rejected_pasted_directory_can_be_pasted_again(flow):
    f,c,pid,_=flow;fetch,_=site('<aside class="sidebar"><a href="a.html">传输</a><a href="b.html">路由</a></aside>');f.fetcher=fetch
    f.run(pid,lambda d,c:f._import_links(d,c,[ROOT]),c)
    card_id=f.load(pid)['sources'][0]['card_id']
    f.run(pid,lambda d,c:f.decide(d,card_id,False,ctx=c),c)
    f.run(pid,lambda d,c:f._import_links(d,c,[ROOT]),c)
    card=f.load(pid)['sources'][0]
    assert card['status']=='new' and card['read_status']=='selecting' and len(f.load(pid)['sources'])==1


def test_125_previous_single_page_snapshot_not_reused_as_whole_book(flow):
    f,c,pid,_=flow;fetch,_=site('<main><p>'+TEXT+'</p></main>');f.fetcher=fetch;recommend(f,pid,[ROOT])
    f.run(pid,lambda d,c:f.decide(d,'pick1',True,ctx=c),c)
    old=f.services.store.records('sources',pid)[0]['id']
    fetch,_=site('<aside class="sidebar"><a href="a.html">传输</a><a href="b.html">路由</a></aside>');f.fetcher=fetch;recommend(f,pid,[ROOT])
    f.run(pid,lambda d,c:f.decide(d,'pick1',True,ctx=c),c)
    f.run(pid,lambda d,c:f.decide(d,'pick1',True,ctx=c,selected_pages=['page1','page2']),c)
    card=f.load(pid)['recommendations'][0]
    assert card['source_id']!=old and card['read_success']==2


def test_125_all_failed_pages_are_listed(flow):
    f,c,pid,_=flow;fetch,_=site('<aside class="sidebar"><a href="a.html">传输</a><a href="b.html">路由</a></aside>',fail=[ROOT+'a.html',ROOT+'b.html']);f.fetcher=fetch;recommend(f,pid,[ROOT])
    f.run(pid,lambda d,c:f.decide(d,'pick1',True,ctx=c),c)
    f.run(pid,lambda d,c:f.decide(d,'pick1',True,ctx=c,selected_pages=['page1','page2']),c)
    card=f.load(pid)['recommendations'][0]
    assert card['read_status']=='failed' and len(card['failed_pages'])==2 and not card.get('source_id')


def test_125_robots_unavailable_not_treated_as_permission():
    def fail(url,**kw):raise ResearchFetchError('503',status_code=503)
    with pytest.raises(ResearchFetchError,match='robots.txt'):book.discover(ROOT,fetcher=fail)


def test_125_pacing_waits_robots_interval_and_checks_cancel(monkeypatch):
    now=[10.0];monkeypatch.setattr(book.time,'monotonic',lambda:now[0])
    sleeps=[]
    def sleep(n):sleeps.append(n);now[0]+=n
    monkeypatch.setattr(book.time,'sleep',sleep)
    reader=object.__new__(book.Reader);reader.cancel=None;reader.last=9.0;reader.interval=2.0
    reader.pace()
    assert sum(sleeps)==pytest.approx(1.0) and reader.last==pytest.approx(11)
    token=Event();token.set()
    with pytest.raises(ResearchCancelled):reader.pace(token)


def test_125_robots_snapshot_only_reused_while_fresh(monkeypatch):
    fetch,calls=site('<aside class="sidebar"><a href="a.html">传输</a><a href="b.html">路由</a></aside>')
    toc=book.discover(ROOT,fetcher=fetch);calls.clear()
    book.read_book(toc,['page1'],fetcher=fetch)
    assert 'https://book.example/robots.txt' not in calls
    toc['robot_snapshot']['retrieved_at']-=book.ROBOTS_CACHE_SECONDS+1;calls.clear()
    book.read_book(toc,['page1'],fetcher=fetch)
    assert calls[0]=='https://book.example/robots.txt'


def test_125_actual_local_http_site():
    from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
    import threading,httpx
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            text=('User-agent: *\nAllow: /' if self.path=='/robots.txt' else
                  '<aside class="sidebar"><a href="a.html">TCP</a><a href="b.html">UDP</a></aside>' if self.path=='/network/' else
                  '<main><h1>TCP</h1><p>'+TEXT+'</p></main>')
            self.send_response(200);self.send_header('Content-Type','text/plain' if self.path=='/robots.txt' else 'text/html');self.end_headers();self.wfile.write(text.encode())
        def log_message(self,*args):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    url=f'http://127.0.0.1:{server.server_port}/network/'
    try:
        with httpx.Client(trust_env=False) as client:
            def fetch(u,**kw):
                r=client.get(u);return FetchedContent(u,str(r.url),r.headers['content-type'],r.content,'2026-10-14T00:00:00Z')
            toc=book.discover(url,fetcher=fetch)
            verified,result=book.read_book(toc,['page2'],fetcher=fetch)
            assert result['successful_pages']==1 and verified.document.char_count>120
    finally:server.shutdown();server.server_close();thread.join(2)
