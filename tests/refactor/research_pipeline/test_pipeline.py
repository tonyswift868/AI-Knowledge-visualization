"""Injected pages only. No model, real server, or network is used by pytest."""
from collections import Counter
from dataclasses import replace
from hashlib import sha256
from html import escape
import io
import json
from pathlib import Path
import socket
from threading import Event, Lock, Thread
import time

import httpx
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
import pytest

from refactor.learning.connections.providers.base import WebHit
from refactor.learning.course import research_pipeline as rp
from refactor.learning.course.research_pipeline import ResearchPipeline, ResearchPool
from refactor.learning.course.source_filter import RawHit, load_lists, screen, normalize_url
from refactor.learning.course.research import FetchedContent
from refactor.learning.course.research import ResearchService
from refactor.learning.course.research import ResearchFetchError
from refactor.learning.course.research import ResearchTimeout
from refactor.learning.course.research import ResearchUnsupportedContent
from refactor.learning.course.research import ResearchCancelled


TITLE = 'Python Functions Tutorial'
TEXT = 'Python functions allow reusable program logic. This chapter explains parameters, return values and local variables with examples. ' * 5


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError('pytest must not access the network')
    monkeypatch.setattr(socket.socket, 'connect', blocked)
    monkeypatch.setattr(socket.socket, 'connect_ex', blocked)
    monkeypatch.setattr(socket, 'getaddrinfo', blocked)


def page(url, *, title=TITLE, text=TEXT, media='text/html', final=None):
    # These fixtures exercise transport/scheduling, not identical lesson copies.
    body = f'<html><head><title>{escape(title)}</title></head><body><main><h1>{escape(title)}</h1><p>{escape(text)}</p><p>Example identifier: {escape(final or url)}</p></main></body></html>'.encode()
    return FetchedContent(url, final or url, media, body, '2026-09-27T10:00:00+00:00')


def pdf(title=TITLE):
    writer = PdfWriter()
    leaf = writer.add_blank_page(width=600, height=800)
    font = DictionaryObject({NameObject('/Type'): NameObject('/Font'), NameObject('/Subtype'): NameObject('/Type1'),
                             NameObject('/BaseFont'): NameObject('/Helvetica')})
    leaf[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): writer._add_object(font)})})
    content = DecodedStreamObject()
    content.set_data(f'BT /F1 12 Tf 10 780 Td ({TEXT}) Tj ET'.encode())
    leaf[NameObject('/Contents')] = writer._add_object(content)
    writer.add_metadata({'/Title': title})
    stream = io.BytesIO()
    writer.write(stream)
    return stream.getvalue()


def test_html_success_uses_body_title_chunks_hash_not_search_snippet():
    url = 'https://docs.python.org/3/tutorial/?utm_source=test'
    content = page(normalize_url(url))
    with ResearchPipeline(fetcher=lambda url, **kw: content) as pipeline:
        result = pipeline.run([WebHit(url, TITLE, 'fabricated search snippet')])
    assert result.attempted == 1 and not result.rejected
    item, = result.verified
    assert item.url == 'https://docs.python.org/3/tutorial'
    assert item.tier == 'whitelist' and item.category == 'official_docs'
    assert item.title == TITLE and item.site == 'docs.python.org'
    assert item.document.chunks and 'fabricated search snippet' not in item.summary
    assert item.content_sha256 == item.document.sha256 == sha256(content.body).hexdigest()
    assert item.retrieved_at == content.retrieved_at


@pytest.mark.parametrize('media,data,title', [
    ('application/pdf', pdf(), TITLE),
    ('text/plain; charset=utf-8', TEXT.encode(), ''),
    ('text/markdown', ('# ' + TITLE + '\n\n' + TEXT).encode(), TITLE),
])
def test_pdf_plain_and_markdown_paths(media, data, title):
    def fetch(url, **kw):
        return FetchedContent(url, url, media, data, '')
    with ResearchPipeline(fetcher=fetch) as pipeline:
        result = pipeline.run([RawHit('https://example.test/document', title)])
    assert not result.rejected and len(result.verified) == 1
    assert result.verified[0].content_sha256 == sha256(data).hexdigest()


@pytest.mark.parametrize('make,code', [
    (lambda url: page(url, text='tiny'), 'body_too_short'),
    (lambda url: page(url, title='Medieval Architecture History'), 'title_mismatch'),
    (lambda url: page(url, title='Just a moment'), 'title_mismatch'),
    (lambda url: page(url, text=TEXT + ' 色情直播 '), 'content_adult'),
    (lambda url: page(url, text=TEXT + ' online casino '), 'content_gambling'),
    (lambda url: page(url, text=TEXT + ' pirated software '), 'content_piracy'),
    (lambda url: page(url, media='image/png'), 'ocr_required'),
    (lambda url: FetchedContent(url, url, 'application/pdf', b'%PDF corrupt', ''), 'extract_failed'),
    (lambda url: page(url, final='https://porn.example.test/tutorial'), 'blocked_redirect'),
    (lambda url: page(url, final='https://adg.csdn.net/tutorial'), 'low_quality'),
    (lambda url: page(url, final='file:///secret'), 'invalid_redirect'),
    (lambda url: page(url, text=''), 'body_too_short'),
    (lambda url: 'incorrect fetch contract', 'fetch_failed'),
])
def test_each_content_rejection(make, code):
    with ResearchPipeline(fetcher=lambda url, **kw: make(url)) as pipeline:
        result = pipeline.run([RawHit('https://example.test/doc', TITLE)])
    assert not result.verified
    assert result.rejected[0].code == code and result.rejected[0].message


@pytest.mark.parametrize('error,code', [(OSError('offline'), 'fetch_failed'),
    (ResearchFetchError('HTTP 404'), 'fetch_failed'), (TimeoutError(), 'fetch_timeout'),
    (ResearchTimeout(), 'fetch_timeout'), (ResearchUnsupportedContent(), 'unsupported_type')])
def test_fetch_failures(error, code):
    def fetch(url, **kw):
        raise error
    with ResearchPipeline(fetcher=fetch) as pipeline:
        result = pipeline.run([RawHit('https://example.test/doc', TITLE)])
    assert result.rejected[0].code == code


def test_max_bytes_and_total_attempt_limit():
    with ResearchPipeline(fetcher=lambda url, **kw: page(url), max_bytes=30, max_sources=2) as pipeline:
        result = pipeline.run([RawHit(f'https://example.test/{i}', TITLE) for i in range(4)])
    assert result.attempted == 2
    assert Counter(x.code for x in result.rejected) == {'content_too_large': 2, 'limit_reached': 2}


def test_preflight_exclusions_never_fetched_and_low_opt_in():
    calls = []
    def fetch(url, **kw):
        calls.append(url)
        return page(url)
    hits = [RawHit(''), RawHit('https://example.test/safe', TITLE),
        RawHit('https://example.test/safe/?utm_source=x', TITLE), RawHit('https://adg.csdn.net/post', TITLE),
        RawHit('https://example.test/selected', TITLE), RawHit('https://example.test/rejected', TITLE),
        RawHit('https://porn.example.test', TITLE), RawHit('https://github.com/wshao12/hello-agents', TITLE),
        RawHit('https://github.com/datawhalechina/hello-agents', TITLE)]
    with ResearchPipeline(fetcher=fetch) as pipeline:
        result = pipeline.run(hits, accepted_urls=['https://example.test/selected/#part'],
                              rejected_urls=['https://example.test/rejected?share=1'])
        low = pipeline.run([hits[3]], include_low=True)
    assert result.attempted == 2
    assert {item.code for item in result.rejected} == {'invalid_url', 'duplicate', 'low_quality',
        'already_accepted', 'already_rejected', 'blocked', 'possible_fork'}
    assert low.verified[0].tier == 'low'


def test_redirect_dedup_exclusions_and_trust_downgrade():
    def fetch(url, **kw):
        return page(url, final='https://example.test/target')
    hits = [RawHit('https://docs.python.org/start', TITLE), RawHit('https://other.test/start', TITLE)]
    with ResearchPipeline(fetcher=fetch) as pipeline:
        result = pipeline.run(hits)
        excluded = pipeline.run(hits[:1], rejected_urls=['https://example.test/target'])
    assert len(result.verified) == 1 and result.verified[0].tier == 'neutral'
    assert result.rejected[0].code == 'duplicate'
    assert excluded.rejected[0].code == 'already_rejected'


def test_final_url_retains_actual_endpoint_while_pool_compares_normalized_aliases():
    actual = 'https://arxiv.org/pdf/2210.03629v2.pdf?utm_source=redirect'
    with ResearchPipeline(fetcher=lambda url, **kw: page(url, final=actual)) as pipeline:
        source, = pipeline.run([RawHit('https://example.test/start', TITLE)]).verified
    assert source.final_url == actual
    pool = ResearchPool()
    pool.add([source])
    pool.mark_rejected('https://arxiv.org/abs/2210.03629')
    assert not pool.take(1) and source.url in pool.rejected_urls


def test_matching_main_heading_can_override_generic_page_title():
    content = page('https://example.test/doc', title='Example Course Platform')
    content = replace(content, body=content.body.replace(b'<h1>Example Course Platform</h1>', f'<h1>{TITLE}</h1>'.encode()))
    with ResearchPipeline(fetcher=lambda url, **kw: content) as pipeline:
        result = pipeline.run([RawHit(content.url, TITLE)])
    assert len(result.verified) == 1


def test_real_subheading_can_verify_section_search_title():
    url = 'https://docs.python.org/3/reference/compound_stmts.html'
    title = 'The Python Language Reference — Function definitions'
    body = (b'<html><head><title>8. Compound statements</title></head><body><main>'
            b'<h1>8. Compound statements</h1><p>'
            + b'Other Python control flow chapters. ' * 6
            + b'</p><h2>Function definitions</h2><p>'
            + b'Python function definitions name parameters and bind them when called. ' * 8
            + b'</p></main></body></html>')
    content = FetchedContent(url, url, 'text/html', body, '')
    with ResearchPipeline(fetcher=lambda url, **kw: content) as pipeline:
        result = pipeline.run([RawHit(url, title)])
    assert len(result.verified) == 1
    assert any(b.kind == 'heading' and 'Function definitions' in b.text for b in result.verified[0].document.blocks)


def test_generic_publisher_word_cannot_verify_unrelated_section():
    url = 'https://docs.python.org/3/tutorial/introduction.html'
    body = (b'<html><head><title>An Informal Introduction to Python</title></head><body><main>'
            b'<h1>An Informal Introduction to Python</h1><h2>Numbers</h2><p>'
            + b'Basic arithmetic and lists are introduced with Python examples. ' * 8
            + b'</p></main></body></html>')
    content = FetchedContent(url, url, 'text/html', body, '')
    search_title = 'Python official tutorial 4.9.5 Defining Functions'
    with ResearchPipeline(fetcher=lambda url, **kw: content) as pipeline:
        result = pipeline.run([RawHit(url, search_title)])
    assert not result.verified
    assert result.rejected[0].code == 'title_mismatch'


def test_concurrency_four_total_two_per_hostname_including_ports():
    active, peaks, total = Counter(), Counter(), [0, 0]
    lock = Lock()
    def fetch(url, **kw):
        site = rp.urlsplit(url).hostname
        with lock:
            active[site] += 1
            peaks[site] = max(peaks[site], active[site])
            total[0] += 1
            total[1] = max(total)
        time.sleep(.06)
        with lock:
            active[site] -= 1
            total[0] -= 1
        return page(url)
    hits = [RawHit(f'https://same.test:{443+i}/{i}', TITLE) for i in range(6)]
    hits += [RawHit(f'https://another.test/{i}', TITLE) for i in range(6)]
    with ResearchPipeline(fetcher=fetch) as pipeline:
        result = pipeline.run(hits)
    assert len(result.verified) == 12 and total[1] == 4
    assert dict(peaks) == {'same.test': 2, 'another.test': 2}


def test_cancel_returns_promptly_and_discards_noncooperative_late_result():
    entered, release, cancel = Event(), Event(), Event()
    output = []
    def fetch(url, **kw):
        entered.set()
        release.wait(2)
        return page(url)
    pipeline = ResearchPipeline(fetcher=fetch, concurrency=1)
    thread = Thread(target=lambda: output.append(pipeline.run(
        [RawHit(f'https://example.test/{i}', TITLE) for i in range(3)], cancel=cancel)))
    try:
        thread.start()
        assert entered.wait(1)
        start = time.monotonic()
        cancel.set()
        thread.join(.5)
        assert not thread.is_alive() and time.monotonic() - start < .5
        assert output[0].cancelled and not output[0].verified
        assert Counter(item.code for item in output[0].rejected) == {'cancelled': 3}
    finally:
        release.set()
        thread.join(2)
        pipeline.close()


def test_cancel_before_fetch_and_deadline():
    cancel = Event(); cancel.set()
    calls = []
    def fetch(url, **kw):
        calls.append(url)
        while not kw['cancel'].is_set():
            time.sleep(.005)
        return page(url)
    with ResearchPipeline(fetcher=fetch, timeout=.08) as pipeline:
        cancelled = pipeline.run([RawHit('https://example.test/a', TITLE)], cancel=cancel)
        assert cancelled.attempted == 0 and not calls
        timed = pipeline.run([RawHit('https://example.test/a', TITLE)])
    assert not timed.verified and timed.rejected[0].code == 'fetch_timeout'
    assert timed.elapsed_seconds < .5


def test_pool_batches_decisions_redirect_aliases_and_previous_round():
    hits = [RawHit('https://example.test/1', TITLE), RawHit('https://adg.csdn.net/2', TITLE),
            RawHit('https://docs.python.org/3', TITLE), RawHit('https://example.test/4', TITLE)]
    with ResearchPipeline(fetcher=lambda url, **kw: page(url)) as pipeline:
        verified = pipeline.run(hits, include_low=True).verified
    pool = ResearchPool()
    pool.add(verified)
    first = pool.take(1)
    assert first[0].tier == 'whitelist'
    pool.mark_accepted(first[0].url + '?utm_source=1')
    pool.mark_rejected(verified[-1].url)
    pool.add(verified)  # A second search may return the same hits.
    second = pool.take(20)
    assert len(second) == 1 and second[0].tier == 'neutral'
    assert not pool.take(20)
    assert pool.take(20, include_low=True)[0].tier == 'low'
    assert first[0].url in pool.accepted_urls and verified[-1].url in pool.rejected_urls
    alias = replace(verified[0], url='https://example.test/alias', final_url='https://example.test/final')
    pool.add([alias])
    pool.mark_rejected(alias.final_url)
    assert alias.url in pool.rejected_urls and not pool.take(1)


def test_real_44_hit_replay_with_explicit_fake_pages():
    data = json.loads((Path(__file__).parents[1] / 'source_filter/fixtures/with_tool.json').read_text(encoding='utf-8'))
    hits = [RawHit(item.get('url') or '', item.get('title') or '')
        for block in data['response']['content'] if block.get('type') == 'web_search_tool_result'
        for item in block['content']]
    titles = {}
    for hit in screen(hits):
        titles.setdefault(hit.url, hit.title)
    def fake(url, **kw):
        return page(url, title=titles[url])
    with ResearchPipeline(fetcher=fake) as pipeline:
        result = pipeline.run(hits)
    assert len(hits) == 44 and result.attempted == 37
    assert len(result.verified) == 37
    assert Counter(item.code for item in result.rejected) == {
        'invalid_url': 3, 'duplicate': 1, 'possible_fork': 1, 'low_quality': 1, 'blocked': 1}


def test_default_adapter_pdf_and_original_fetch_api(monkeypatch):
    raw = pdf()
    service = ResearchService(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, content=raw, headers={'content-type': 'application/pdf'})))
    monkeypatch.setattr(rp, 'ResearchService', lambda **kw: service)
    with ResearchPipeline() as pipeline:
        result = pipeline.run([RawHit('https://example.test/doc', TITLE)])
    assert len(result.verified) == 1
    with pytest.raises(ResearchUnsupportedContent):
        service.fetch({'url': 'https://example.test/doc'})
    html_service = ResearchService(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, content=page(str(request.url)).body, headers={'content-type': 'text/html'})))
    original = html_service.fetch({'url': 'https://example.test/doc'})
    assert original.title == TITLE and original.chunks
    assert original.content_sha256 == sha256(original.text.encode()).hexdigest()


def test_redirect_guard_blocks_before_request(monkeypatch):
    calls = []
    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(302, headers={'location': 'https://casino.example.test/doc'})
    monkeypatch.setattr(rp, 'ResearchService', lambda **kw: ResearchService(transport=httpx.MockTransport(handler)))
    with ResearchPipeline() as pipeline:
        result = pipeline.run([RawHit('https://docs.python.org/start', TITLE)])
    assert len(calls) == 1 and result.rejected[0].code == 'blocked_redirect'


def test_per_site_guard_applies_to_converging_redirects(monkeypatch):
    active, peak, lock = 0, 0, Lock()
    def handler(request):
        nonlocal active, peak
        if request.url.host != 'target.test':
            return httpx.Response(302, headers={'location': 'https://target.test/' + request.url.host})
        with lock:
            active += 1; peak = max(peak, active)
        time.sleep(.04)
        with lock:
            active -= 1
        return httpx.Response(200, content=page(str(request.url)).body, headers={'content-type': 'text/html'})
    monkeypatch.setattr(rp, 'ResearchService', lambda **kw: ResearchService(transport=httpx.MockTransport(handler)))
    with ResearchPipeline() as pipeline:
        result = pipeline.run([RawHit(f'https://source{i}.test/doc', TITLE) for i in range(6)])
    assert len(result.verified) == 6 and peak == 2


@pytest.mark.parametrize('url', ['http://127.0.0.1/a', 'http://localhost/a', 'http://10.0.0.1/a', 'http://[::1]/a'])
def test_raw_fetch_retains_private_address_rejection(url):
    with pytest.raises(ResearchFetchError, match='私有|保留|回环'):
        ResearchService().fetch_bytes({'url': url})


def test_raw_fetch_redirect_size_and_cancel_limits():
    redirects = ResearchService(max_redirects=1, transport=httpx.MockTransport(
        lambda req: httpx.Response(302, headers={'location': '/loop'})))
    with pytest.raises(ResearchFetchError, match='重定向次数'):
        redirects.fetch_bytes({'url': 'https://example.test/doc'})
    sized = ResearchService(max_bytes=10, transport=httpx.MockTransport(
        lambda req: httpx.Response(200, content=b'x'*11, headers={'content-type': 'text/plain'})))
    with pytest.raises(ResearchFetchError, match='大小'):
        sized.fetch_bytes({'url': 'https://example.test/doc'})
    event = Event(); event.set()
    with pytest.raises(ResearchCancelled):
        sized.fetch_bytes({'url': 'https://example.test/doc'}, cancel=event)


def test_close_during_fetch_cancels_pending_and_late_completion():
    entered, release, output = Event(), Event(), []
    def fetch(url, **kw):
        entered.set(); release.wait(2)
        return page(url)
    pipeline = ResearchPipeline(fetcher=fetch, concurrency=1)
    worker = Thread(target=lambda: output.append(pipeline.run(
        [RawHit(f'https://example.test/{i}', TITLE) for i in range(4)])))
    try:
        worker.start()
        assert entered.wait(1)
        pipeline.close()
        worker.join(.5)
        assert not worker.is_alive()
        assert output[0].cancelled and not output[0].verified
        assert len(output[0].rejected) == 4
    finally:
        release.set()
        worker.join(2)
        pipeline.close()


def test_cancel_during_extraction_fences_result(monkeypatch):
    event = Event()
    original = rp.extract_html
    def cancelled_extract(*args, **kwargs):
        document = original(*args, **kwargs)
        event.set()
        return document
    monkeypatch.setattr(rp, 'extract_html', cancelled_extract)
    with ResearchPipeline(fetcher=lambda url, **kw: page(url)) as pipeline:
        result = pipeline.run([RawHit('https://example.test/doc', TITLE)], cancel=event)
    assert result.cancelled and not result.verified


def test_private_redirect_validated_even_when_first_hostname_is_public(monkeypatch):
    calls = []
    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(302, headers={'location': 'http://127.0.0.1/secret'})
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *args: [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', 80))])
    service = ResearchService()
    # Replace only the HTTP client, retaining production _transport=None DNS checks.
    monkeypatch.setattr(service, '_client', lambda: httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(ResearchFetchError, match='私有|保留'):
        service.fetch_bytes({'url': 'https://public.example.test/doc'})
    assert len(calls) == 1


def test_size_limit_applies_to_stream_without_length_header():
    class Body(httpx.SyncByteStream):
        def __iter__(self):
            yield b'x' * 65536
            yield b'x' * 65536
    service = ResearchService(max_bytes=65537, transport=httpx.MockTransport(lambda request:
        httpx.Response(200, stream=Body(), headers={'content-type': 'text/plain'})))
    with pytest.raises(ResearchFetchError, match='大小'):
        service.fetch_bytes({'url': 'https://example.test/doc'})


def test_unknown_or_translated_title_is_tagged_not_claimed_matched():
    with ResearchPipeline(fetcher=lambda url, **kw: page(url, title='函数教学')) as pipeline:
        result = pipeline.run([RawHit('https://example.test/doc', TITLE)])
    assert result.verified[0].reasons == ('title_unverified',)
