"""Find a small number of same-site lesson files behind a course entry page."""
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit
import re

from .source_filter import normalize_url


def is_document_preview(url, title='', body=''):
    """Converted document previews are not the uploaded original document."""
    parsed = urlsplit(url or '')
    if parsed.path.lower().endswith(('.pdf', '.docx', '.pptx', '.epub')):
        return False
    host = parsed.hostname or ''
    repositories = ('51jiaoxi.com', 'jiaoxiwang.com', 'docin.com', 'wenku.baidu.com',
                    'book118.com', 'renrendoc.com', 'doc88.com')
    return any(host == h or host.endswith('.' + h) for h in repositories)


def is_spec_overview_index(url):
    parsed = urlsplit(url)
    return (parsed.hostname == 'modelcontextprotocol.io'
            and bool(re.fullmatch(r'/specification/20\d\d-\d\d-\d\d/?', parsed.path)))


class _Anchors(HTMLParser):
    def __init__(self):
        super().__init__()
        self.entries = []
        self.current = None

    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            data = dict(attrs)
            self.current = [data.get('href', ''), data.get('aria-label', ''), '']

    def handle_data(self, data):
        if self.current is not None:
            self.current[2] += data

    def handle_endtag(self, tag):
        if tag == 'a' and self.current is not None:
            self.entries.append(tuple(self.current))
            self.current = None


def lesson_links(html, page_url, *, limit=2):
    """Only direct lesson/transcript downloads on the page's own host.

    This is discovery, not approval. The caller still safely fetches, extracts,
    validates and scores the resulting full document. No search or model call.
    """
    if not isinstance(html, (str, bytes)):
        return []
    parser = _Anchors()
    parser.feed(html.decode('utf-8', 'replace') if isinstance(html, bytes) else html)
    parent = urlsplit(page_url)
    candidates = []
    for href, aria, text in parser.entries:
        if not href or href.startswith(('#', 'javascript:', 'mailto:')):
            continue
        url = normalize_url(urljoin(page_url, href))
        if not url or urlsplit(url).hostname != parent.hostname or urlsplit(url).scheme != parent.scheme:
            continue
        label = (aria + ' ' + text).casefold()
        path = urlsplit(url).path.casefold()
        is_document = path.endswith(('.pdf', '.txt', '.md'))
        is_transcript = any(term in label for term in ('transcript', '字幕', '文字稿'))
        is_spec_overview = (is_spec_overview_index(page_url) and path == parent.path.rstrip('/') + '/basic'
                            and 'overview' in label)
        if not (is_transcript or (is_document and any(term in label for term in
                ('download', 'lecture', 'notes', 'problem set', 'view lesson', '讲义', '下载', '课件')))
                or is_spec_overview):
            continue
        priority = 0 if is_transcript or is_spec_overview else 1
        if (url, priority) not in candidates:
            candidates.append((url, priority))
    return [url for url, _ in sorted(candidates, key=lambda entry: entry[1])[:limit]]
