"""Website-owned TOC discovery and serial reading; no model link selection.

Existing lxml + urllib.robotparser are sufficient: sitemap libraries
flatten URLs, while this reader must retain the site's sidebar hierarchy.
HTTP bytes, limits/timeouts/redirect checks and body extraction stay with the
existing ResearchService/ResearchPipeline. No recursive body-link crawling.
"""
from contextlib import contextmanager
from dataclasses import replace
import json
import posixpath
import re
import time
from urllib.parse import urljoin, urlsplit, urlunsplit, unquote
from urllib.robotparser import RobotFileParser
import xml.etree.ElementTree as ET

from lxml import html as html_parser

from .research import ResearchService, ResearchFetchError, ResearchCancelled, _check_cancel
from .source_filter import normalize_url, RawHit
from .research_pipeline import ResearchPipeline, retry_selected, fetch_selected_bytes
from .extract import _document

DEFAULT_INTERVAL = 1.0
ROBOTS_CACHE_SECONDS = 1800
AGENT = 'MentorFlow'
TOCS = ('.toctree-wrapper', '.sidebar', '.VPSidebar', '.theme-doc-sidebar-container',
        '.md-nav--primary', '.sphinxsidebar', '.wy-menu', '.bd-docs-nav',
        '.sidebar-nav', '.book-summary', 'nav[aria-label="Main navigation"]')
SKIP = re.compile(r'(^|/)(login|signin|logout|search|tags?|categories|page|comments?|downloads?)(/|$)', re.I)
FILES = re.compile(r'\.(pdf|zip|7z|gz|exe|dmg|png|jpe?g|gif|svg|mp[34]|webm|docx?|xlsx?|epub)$', re.I)


class BookReadError(ValueError):
    def __init__(self,failed):
        self.failed_pages=failed
        super().__init__('所有所选页都读取失败，请查看失败列表，或下载后上传。')


class RobotsUnavailable(ResearchFetchError):
    code = 'robots_unavailable'


def canonical(url):
    url = normalize_url(url)
    if not url: return None
    p = urlsplit(url)
    path = posixpath.normpath(unquote(p.path or '/'))
    if p.path.endswith('/') and path != '/': path += '/'
    path = re.sub(r'index\.html?$', '', path).rstrip('/') or '/'
    return urlunsplit((p.scheme, p.netloc.lower(), path, p.query, ''))


def scope_for(url):
    p = urlsplit(canonical(url))
    path = p.path
    if re.search(r'index\.html?$',urlsplit(url).path): path=path.rstrip('/')+'/'
    if not path.endswith('/'):
        path = posixpath.dirname(path)+'/' if '.' in path.rsplit('/',1)[-1] else path+'/'
    return p.netloc, path


def allowed(url, root):
    value = canonical(url)
    if not value: return False
    p = urlsplit(value); host, prefix = scope_for(root)
    return (p.netloc == host and (p.path.startswith(prefix) or p.path == prefix.rstrip('/'))
            and not p.query and not SKIP.search(p.path) and not FILES.search(p.path))


class Reader:
    def __init__(self, root, *, fetcher=None, cancel=None, timeout=20.0, robot_snapshot=None):
        self.root, self.fetcher, self.cancel, self.timeout = root, fetcher, cancel, timeout
        self.last = None
        self.errors = {}
        self.rules = RobotFileParser()
        origin = urlunsplit((*urlsplit(root)[:2], '/robots.txt', '', ''))
        if (robot_snapshot and robot_snapshot.get('url')==origin and
                0<=time.time()-robot_snapshot.get('retrieved_at',0)<ROBOTS_CACHE_SECONDS):
            self.robot_snapshot=robot_snapshot
        else:
            try:
                fetched = self.raw(origin, metadata=True)
                text=fetched.body.decode('utf-8', errors='replace')
            except ResearchFetchError as exc:
                if exc.status_code in (404, 410): text=''
                elif exc.status_code in (401, 403): text='User-agent: *\nDisallow: /'
                else: raise RobotsUnavailable('网站的 robots.txt 暂时读不到，整本读取请稍后重试。'+str(exc)) from exc
            self.robot_snapshot=dict(url=origin,text=text,retrieved_at=time.time())
        self.rules.parse(self.robot_snapshot['text'].splitlines())
        self.interval = max(DEFAULT_INTERVAL, self.rules.crawl_delay(AGENT) or 0)
        rate = self.rules.request_rate(AGENT)
        if rate and rate.requests: self.interval = max(self.interval, rate.seconds/rate.requests)

    def pace(self, cancel=None):
        token = cancel if cancel is not None else self.cancel
        interval = getattr(self, 'interval', DEFAULT_INTERVAL)
        remaining = 0 if self.last is None else self.last+interval-time.monotonic()
        while remaining > 0:
            _check_cancel(token)
            delay = min(.1, remaining)
            if token is not None and hasattr(token,'wait'): token.wait(delay)
            else: time.sleep(delay)
            remaining = self.last+interval-time.monotonic()
        _check_cancel(token)
        self.last = time.monotonic()

    @contextmanager
    def guard(self, url, *, cancel=None, metadata=False):
        token = cancel if cancel is not None else self.cancel
        _check_cancel(token)
        if urlsplit(url).netloc != urlsplit(self.root).netloc:
            raise ResearchFetchError('目录页跳到了站外，未读取。')
        if not metadata and canonical(url)!=canonical(self.root) and not allowed(url,self.root):
            raise ResearchFetchError('目录页跳出了所选章节路径，未读取。')
        if (not metadata or hasattr(self,'interval')) and not self.rules.can_fetch(AGENT,url):
            raise ResearchFetchError('robots.txt 不允许读取此页。')
        self.pace(token)
        yield

    def raw(self, url, *, metadata=False, cancel=None):
        token = cancel if cancel is not None else self.cancel
        return retry_selected(lambda:self._raw_once(url,metadata=metadata,cancel=token),token)

    def _raw_once(self, url, *, metadata=False, cancel=None):
        token = cancel if cancel is not None else self.cancel
        if self.fetcher:
            with self.guard(url,metadata=metadata,cancel=token):
                value = self.fetcher(url,cancel=token,timeout=self.timeout)
            if value.final_url != url:
                with self.guard(value.final_url,metadata=metadata,cancel=token): pass
            _check_cancel(token)
            return value
        return fetch_selected_bytes(url,cancel=token,timeout=self.timeout,max_bytes=2_000_000,
            service=ResearchService(timeout=self.timeout,user_agent=AGENT),
            request_guard=lambda u,**kw:self.guard(u,metadata=metadata,cancel=token),
            allowed_types={'text/html','application/xhtml+xml','text/plain','text/markdown',
                           'application/xml','text/xml','application/pdf'})

    def fetch(self, url, *, cancel=None, timeout=None):
        try: return self.raw(url,cancel=cancel)
        except ResearchCancelled: raise
        except Exception as exc:
            self.errors[url]=str(exc)
            raise


def _label(node):
    # Only direct heading/link/label: never concatenate a whole nested menu.
    for child in node:
        classes = child.get('class') or ''
        if child.tag=='a' or any(v in classes for v in ('heading','label','caption','text','title','header','link')):
            text = ' '.join(child.text_content().split())
            if text: return text
    return ''


def toc_entries(html, root):
    soup = html_parser.document_fromstring(html,parser=html_parser.HTMLParser(encoding='utf-8',no_network=True))
    for selector in TOCS:
        entries, seen = [], set()
        xpath = ("//nav[@aria-label='Main navigation']" if selector.startswith('nav') else
                 "//*[contains(concat(' ',normalize-space(@class),' '),' "+selector[1:]+" ')]")
        for menu in soup.xpath(xpath):
            for a in menu.xpath('.//a[@href]'):
                href = a.get('href')
                if '#' in href: continue
                url = canonical(urljoin(root,href))
                title = ' '.join(a.text_content().split())
                if not title or not url or not allowed(url,root) or url in seen: continue
                ancestors = []
                for node in a.iterancestors():
                    if node is menu: break
                    classes = node.get('class','')
                    if node.tag=='li' or any(v in classes for v in ('sidebar-group','VPSidebarItem','section')):
                        text = _label(node)
                        if text and text != title and (not ancestors or ancestors[-1]!=text): ancestors.append(text)
                section = list(reversed(ancestors))+[title]
                seen.add(url);entries.append(dict(id=f'page{len(entries)+1}',url=url,title=title,section=section))
        if len(entries)>1: return entries,selector
    return [],''


def discover(url, *, fetcher=None, cancel=None, timeout=20.0):
    reader = Reader(url,fetcher=fetcher,cancel=cancel,timeout=timeout)
    fetched = reader.raw(url)
    if 'html' not in fetched.content_type: return None
    entries, origin = toc_entries(fetched.body,fetched.final_url)
    if not entries and b'docsify' in fetched.body.lower():
        # Docsify advertises a Markdown sidebar; read that one explicit menu,
        # not arbitrary scripts or hyperlinks in the lesson body.
        config=fetched.body.decode('utf-8',errors='replace')
        setting=re.search(r'loadSidebar\s*:\s*(true|[\x22\x27][^\x22\x27]+[\x22\x27])',config)
        if setting:
            name='_sidebar.md' if setting[1]=='true' else setting[1][1:-1]
            menu_url=urljoin(fetched.final_url,name)
            if allowed(menu_url,url):
                try:
                    menu=reader.raw(menu_url).body.decode('utf-8')
                    parents=[];seen=set()
                    for line in menu.splitlines():
                        match=re.match(r'(\s*)[-*+]\s+\[([^]]+)\]\(([^)]+)\)',line)
                        if not match: continue
                        indent,title,href=len(match[1]),match[2],match[3]
                        while parents and parents[-1][0]>=indent:parents.pop()
                        target=canonical(urljoin(menu_url,href))
                        if '#' not in href and target and allowed(target,url) and target not in seen:
                            entries.append(dict(id=f'page{len(entries)+1}',url=target,title=title,
                                section=[v[1] for v in parents]+[title]));seen.add(target)
                        parents.append((indent,title))
                    origin='_sidebar.md'
                except ResearchCancelled: raise
                except (ResearchFetchError,UnicodeError) as exc:
                    raise ResearchFetchError('网站声明的侧栏暂时读不到：'+str(exc)) from exc
    if not entries:
        sitemap_urls = reader.rules.site_maps() or [urljoin(url,'/sitemap.xml')]
        queue, seen, pages = list(sitemap_urls), set(), set()
        while queue:
            _check_cancel(cancel)
            target = queue.pop(0)
            if target in seen or urlsplit(target).netloc!=urlsplit(url).netloc: continue
            seen.add(target)
            try:
                if not reader.rules.can_fetch(AGENT,target): continue
                xml = ET.fromstring(reader.raw(target,metadata=True).body)
            except ResearchCancelled: raise
            except ResearchFetchError as exc:
                # An absent conventional sitemap is normal for a single page.
                # A broken declared/index sitemap must not yield a partial TOC.
                if exc.status_code in (404,410) and not reader.rules.site_maps() and target==urljoin(url,'/sitemap.xml'):
                    continue
                raise ResearchFetchError('网站目录暂时读不到：'+str(exc)) from exc
            except ET.ParseError as exc:
                raise ResearchFetchError('网站目录格式无法解析：'+str(exc)) from exc
            index = xml.tag.rsplit('}',1)[-1]=='sitemapindex'
            for node in xml:
                loc = next((x.text for x in node if x.tag.rsplit('}',1)[-1]=='loc'),None)
                if not loc: continue
                if index: queue.append(urljoin(target,loc));continue
                value=canonical(urljoin(target,loc))
                if value and allowed(value,url) and value not in pages:
                    pages.add(value)
                    parts=[unquote(v).removesuffix('.html').replace('-',' ') for v in urlsplit(value).path[len(scope_for(url)[1]):].split('/') if v]
                    section=parts or ['首页']
                    entries.append(dict(id=f'page{len(entries)+1}',url=value,title=section[-1],section=section))
            origin='sitemap.xml'
    if len(entries)<2: return None
    titles=html_parser.document_fromstring(fetched.body,parser=html_parser.HTMLParser(encoding='utf-8',no_network=True)).xpath('//title/text()')
    return dict(root=url,title=titles[0] if titles else url,
                entries=entries,origin=origin,interval=reader.interval,robot_snapshot=reader.robot_snapshot)


def read_book(book, selected, *, fetcher=None, cancel=None, lists=None, timeout=20.0, progress=None):
    if not isinstance(selected,list) or any(not isinstance(v,str) for v in selected):
        raise ValueError('请勾选需要读取的章节。')
    picked=set(selected); entries=[e for e in book['entries'] if e['id'] in picked]
    if not entries or len(picked)!=len(entries): raise ValueError('请选择目录中的章节，至少一页。')
    root=book['root'];reader=Reader(root,fetcher=fetcher,cancel=cancel,timeout=timeout,
                                 robot_snapshot=book.get('robot_snapshot'))
    blocks,failed,read=[],[],[]; first=None; total=len(entries)
    if progress: progress(0,total,[])
    documents={};failures={}
    with ResearchPipeline(fetcher=reader.fetch,timeout=timeout,max_sources=1,concurrency=1,per_site=1,retry_fetch=False) as pipeline:
      for round_number in (1,2):
        for index,entry in enumerate(entries,1):
            if entry['id'] in documents:continue
            _check_cancel(cancel)
            try:
                if not reader.rules.can_fetch(AGENT,entry['url']):
                    raise ValueError('robots.txt 不允许读取此页。')
                outcome=pipeline.run([RawHit(entry['url'])],lists=lists,user_provided=True,cancel=cancel)
                _check_cancel(cancel)
                if not outcome.verified:
                    raise ValueError(reader.errors.get(entry['url'],outcome.rejected[0].message))
                source=outcome.verified[0]
                from .research_pipeline import _title_check
                body='\n\n'.join(b.text for b in source.document.blocks)
                if not _title_check('',[source.title])[0] or sum(c.isalnum() for c in body)<120:
                    raise ValueError('需要登录、页面不可用或正文太少。')
                documents[entry['id']]=source
                failures.pop(entry['id'],None)
            except ResearchCancelled: raise
            except Exception as exc:
                _check_cancel(cancel)
                failures[entry['id']]=dict(url=entry['url'],title=entry['title'],message=str(exc),round=round_number)
            if progress: progress(index if round_number==1 else total,total,list(failures.values()))
    _check_cancel(cancel)
    failed=list(failures.values())
    for entry in entries:
        source=documents.get(entry['id'])
        if source is None:continue
        first=first or source
        for block in source.document.blocks:
            inner=block.section
            if inner and inner[0] in (source.title,entry['title']):inner=inner[1:]
            blocks.append(replace(block,section=tuple(entry['section'])+inner))
        read.append(dict(url=entry['url'],title=entry['title'],chars=source.document.char_count,section=entry['section']))
    if not first: raise BookReadError(failed)
    raw=json.dumps([dict(text=b.text,section=b.section,kind=b.kind) for b in blocks],ensure_ascii=False).encode()
    document=_document(raw,book['title'],'text/html',blocks,
        [v['url']+'：'+v['message'] for v in failed],cancel)
    verified=replace(first,url=root,final_url=root,title=book['title'],document=document,
                     content_sha256=document.sha256)
    return verified,dict(read_pages=read,failed_pages=failed,total_pages=total,done_pages=total,
                         successful_pages=len(read),interval=reader.interval)
