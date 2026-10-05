"""Injected public documents only; no draft, UI, model or network in pytest."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
import json
import socket
from threading import Barrier, Event
from urllib.parse import unquote

import pytest

from refactor.learning.course import addition_links as links, github_repo, materials, plan_additions
from refactor.learning.course.ledger import PlanWindow
from refactor.learning.course.store import CourseStore, StoreError
from refactor.learning.course.research import FetchedContent
from refactor.learning.course.research import ResearchTimeout


PAGE = 'https://docs.python.org/3/tutorial/controlflow.html'
REPO = 'https://github.com/acme/book'
TEXT = 'Python functions support keyword arguments, default values and reusable logic. ' * 25
FILES = {
    'README.md': '# 首页\n\n只有目录',
    'book/chapter1.md': '# English chapter\n\nOnly the original language.',
    'book/chapter2.md': '# English chapter two\n\nOnly the original language.',
    'book-zh/chapter1.md': '# 第一章 参数\n\n' + '参数与调用约定。' * 80,
    'book-zh/chapter2.md': '# 第二章 工具\n\n' + '工具注册机制。' * 80 + '正文末尾标记',
    'book-hu/chapter1.md': '# Other translation\n\nOther translation.',
    'book-zh/_sidebar.md': '# sidebar',
}


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError('offline test attempted network')
    monkeypatch.setattr(socket.socket, 'connect', blocked)
    monkeypatch.setattr(socket, 'getaddrinfo', blocked)


def active(store, pid='p'):
    start = date(2026, 9, 29)
    store.create_plan(pid, goal='Python', final_goal='理解函数与工具', level='beginner',
                      window=PlanWindow(start, start + timedelta(days=30), 60))
    store.add_sources(pid, [dict(id='old', url='https://example.test/old', adopted=True,
                                chunks=[dict(id='oc', text='旧资料')])])
    store.save_outline(pid, [dict(id='s', title='Python')],
                       [dict(id='m', title='基础', stage_id='s', prerequisites=[])])
    store.save_generated_module(pid, 'm', [dict(id='p0', title='基础', summary='解释函数',
                                minutes=10, chunk_ids=['oc'])], check_minutes=2, today=start)
    store.start_plan(pid, today=start)
    store.add_credit(pid, 'credit', 'p0', 10, start, 'confirmed')


@pytest.fixture
def store(tmp_path):
    with CourseStore('links-test', tmp_path / 'data') as store:
        active(store)
        yield store


def page(url, *, final=None, body=TEXT, title='Python Functions', media='text/html'):
    html = f'<html><title>{title}</title><main><h1>{title}</h1><p>{body}</p></main></html>'
    return FetchedContent(url, final or url, media, html.encode(), '2026-09-29T00:00:00Z')


def repo_fetch(calls, files=None):
    files = FILES if files is None else files
    def fetch(url, cancel):
        calls.append(url)
        if '/git/trees/' in url:
            return json.dumps(dict(tree=[dict(path=p, type='blob', size=len(b.encode())) for p, b in files.items()])).encode()
        if url.startswith('https://api.github.com/'):
            return b'{"full_name":"acme/book","default_branch":"main"}'
        path = unquote(url.split('/', 6)[6])
        return files[path].encode()
    return fetch


def fixed(store):
    return {e: store.records(e, 'p') for e in ('modules', 'points', 'credits', 'judgements', 'attempt_closed', 'ledger_events')}


def test_web_and_repo_full_text_into_analysis_without_changing_plan(store):
    before, old_plan = fixed(store), store.get_plan('p')
    calls = []
    result = links.import_links(store, 'p', [PAGE, REPO], fetcher=lambda u, **kw: page(u), repo_fetch=repo_fetch(calls))
    assert [r['status'] for r in result['results']] == ['imported', 'imported']
    assert len(result['source_ids']) == 2 and not result['cancelled']
    assert fixed(store) == before and store.get_plan('p') == old_plan
    state = plan_additions.snapshot(store, 'p', result['source_ids'])
    request = json.loads(plan_additions.analysis_request(state)[1][0]['content'])
    assert {c['id'] for c in request['material']} == {c['id'] for c in state['chunks']}
    assert {c['source_id'] for c in request['material']} == set(result['source_ids'])
    assert any('正文末尾标记' in c['text'] for c in state['chunks'])
    repo_source = next(s for s in state['sources'] if s['category'] == 'repository')
    chunks = [c for c in state['chunks'] if c['source_id'] == repo_source['id']]
    blocks = materials.stored_blocks(chunks)
    assert {b.section[0] for b in blocks} == {'第一章 参数', '第二章 工具'}
    assert '正文末尾标记' in blocks[-1].text
    assert not any('_sidebar' in u or 'book-hu' in u or '/book/chapter' in u for u in calls)
    assert all(s['adopted'] for s in state['sources'])


def test_duplicates_aliases_tracking_repository_case_and_retry(store):
    calls = []
    def fetch(u, **kw):
        calls.append(u)
        return page(u, final=PAGE)
    first = links.import_links(store, 'p', ['https://example.test/start', PAGE + '?utm_source=x#part',
                   'https://GITHUB.com/Acme/Book.git/?utm_source=x', REPO], fetcher=fetch, repo_fetch=repo_fetch(calls))
    assert [r['status'] for r in first['results']] == ['imported', 'existing', 'imported', 'existing']
    assert len(first['source_ids']) == 2
    prior = len(calls)
    again = links.import_links(store, 'p', ['https://example.test/start', PAGE, REPO], fetcher=fetch, repo_fetch=repo_fetch(calls))
    assert all(r['status'] == 'existing' for r in again['results'])
    assert set(again['source_ids']) == set(first['source_ids']) and len(calls) == prior


def test_redirect_to_existing_is_remembered_and_readopts_explicit_choice(store):
    first = links.import_links(store, 'p', [PAGE], fetcher=lambda u, **kw: page(u))
    sid = first['source_ids'][0]
    store.set_source_adopted('p', sid, False)
    alias = 'https://example.test/alias'
    second = links.import_links(store, 'p', [alias], fetcher=lambda u, **kw: page(u, final=PAGE))
    assert second['source_ids'] == [sid] and second['results'][0]['status'] == 'existing'
    assert next(s for s in store.records('sources', 'p') if s['id'] == sid)['adopted']
    assert links.import_links(store, 'p', [alias])['source_ids'] == [sid]  # no network on retry


def test_already_linked_source_not_sent_to_analysis(store):
    result = links.import_links(store, 'p', [PAGE], fetcher=lambda u, **kw: page(u))
    sid = result['source_ids'][0]
    cid = next(c['id'] for c in store.records('source_chunks', 'p') if c['source_id'] == sid)
    store.append_material_additions('p', [], [dict(point_id='p0', chunk_ids=[cid])], ['m'], today=date(2026, 9, 29))
    again = links.import_links(store, 'p', [PAGE])
    assert again['source_ids'] == [] and again['results'][0]['already_linked']
    assert again['results'][0]['code'] == 'already_linked'


@pytest.mark.parametrize('url,body,media,expected', [
    ('', TEXT, 'text/html', 'invalid_url'),
    ('file:///secret', TEXT, 'text/html', 'invalid_url'),
    ('https://example.test/bad', '', 'text/html', 'body_too_short'),
    ('https://example.test/bad', TEXT, 'image/png', 'ocr_required'),
])
def test_individual_failure_keeps_other_successes(store, url, body, media, expected):
    result = links.import_links(store, 'p', [url, PAGE],
        fetcher=lambda u, **kw: page(u, body=TEXT if u == PAGE else body, title='Python Functions' if body or u==PAGE else '', media='text/html' if u == PAGE else media))
    assert result['results'][0]['code'] == expected
    assert result['results'][1]['status'] == 'imported' and len(result['source_ids']) == 1


def test_repo_failure_timeout_bad_response_and_unsafe_body(store):
    for fetch, code in [(lambda u, c: b'{}', 'GITHUB_NOT_FOUND'),
                        (lambda u, c: b'not json', 'GITHUB_BAD_RESPONSE')]:
        outcome = links.import_links(store, 'p', [REPO], repo_fetch=fetch)
        assert outcome['results'][0]['code'] == code and not outcome['source_ids']
    def timeout(u, c):
        raise ResearchTimeout('sensitive exception details')
    outcome = links.import_links(store, 'p', [REPO, PAGE], repo_fetch=timeout, fetcher=lambda u, **kw: page(u))
    assert outcome['results'][0]['code'] == 'fetch_timeout' and len(outcome['source_ids']) == 1


def test_repository_selection_and_limits_are_not_silently_hidden(store, monkeypatch):
    monkeypatch.setattr(github_repo, 'MAX_FILES', 1)
    result = links.import_links(store, 'p', [REPO], repo_fetch=repo_fetch([]))
    assert result['results'][0]['status'] == 'imported'
    assert any('只读取了前 1 篇' in w for w in result['results'][0]['warnings'])


def test_repo_branches_and_subdirectories_remain_distinct(store):
    calls = []
    urls = [REPO, REPO + '/tree/dev', REPO + '/tree/main/book-zh']
    result = links.import_links(store, 'p', urls, repo_fetch=repo_fetch(calls))
    assert len(result['source_ids']) == 3
    assert [r['url'] for r in result['results']] == urls
    assert any('/trees/dev?' in u for u in calls)


def test_cancel_before_start_and_late_repo_result(store):
    event = Event(); event.set()
    outcome = links.import_links(store, 'p', [PAGE, REPO], cancel=event)
    assert outcome['cancelled'] and all(r['status'] == 'cancelled' for r in outcome['results'])
    event.clear()
    def late(u, cancel):
        event.set()
        return b'{"full_name":"acme/book"}'
    outcome = links.import_links(store, 'p', [PAGE, REPO, PAGE + '?x=2'], cancel=event,
                                 fetcher=lambda u, **kw: page(u), repo_fetch=late)
    assert len(outcome['source_ids']) == 1
    assert [r['status'] for r in outcome['results']] == ['imported', 'cancelled', 'cancelled']


@pytest.mark.parametrize('cancel_write', [False, True])
def test_write_failure_or_late_cancellation_rolls_back_sources_chunks_outbox(store, monkeypatch, cancel_write):
    original = materials.import_verified_source
    prior = store.pending_records(999)
    event = Event()
    def interrupted(*args, **kwargs):
        saved = original(*args, **kwargs)
        if cancel_write:
            event.set()
            return saved
        raise OSError('disk full')
    monkeypatch.setattr(materials, 'import_verified_source', interrupted)
    result = links.import_links(store, 'p', [PAGE], fetcher=lambda u, **kw: page(u), cancel=event)
    assert result['results'][0]['code'] == ('cancelled' if cancel_write else 'storage_failed')
    assert not result['source_ids'] and store.pending_records(999) == prior
    assert len(store.records('sources', 'p')) == 1


def test_plan_archived_during_fetch_no_late_write(store):
    def fetch(u, **kw):
        store.archive_plan('p')
        return page(u)
    result = links.import_links(store, 'p', [PAGE], fetcher=fetch)
    assert result['results'][0]['code'] == 'plan_not_active'
    assert len(store.records('sources', 'p')) == 1


def test_account_and_plan_isolation_and_invalid_state(store, tmp_path):
    links.import_links(store, 'p', [PAGE], fetcher=lambda u, **kw: page(u))
    active(store, 'second')
    assert links.import_links(store, 'second', [PAGE], fetcher=lambda u, **kw: page(u))['results'][0]['status'] == 'imported'
    with CourseStore('other-account', tmp_path / 'data') as other:
        with pytest.raises(StoreError): links.import_links(other, 'p', [PAGE])
        active(other)
        assert links.import_links(other, 'p', [PAGE], fetcher=lambda u, **kw: page(u))['results'][0]['status'] == 'imported'
    store.create_plan('not-started')
    with pytest.raises(links.LinkImportError, match='进行中'):
        links.import_links(store, 'not-started', [PAGE])


def test_concurrent_import_checks_again_inside_commit(store):
    barrier = Barrier(2)
    def fetch(u, **kw):
        barrier.wait(timeout=5)
        return page(u)
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _: links.import_links(store, 'p', [PAGE], fetcher=fetch), range(2)))
    assert sorted(r['results'][0]['status'] for r in results) == ['existing', 'imported']
    assert results[0]['source_ids'] == results[1]['source_ids']
    assert len(store.records('sources', 'p')) == 2


def test_incomplete_synced_source_does_not_claim_success(store):
    store.add_sources('p', [dict(id='partial', url=PAGE, adopted=True, chunk_count=2,
                                chunks=[dict(id='partial-c', text='only half')])])
    result = links.import_links(store, 'p', [PAGE])
    assert result['results'][0]['code'] == 'incomplete_source' and not result['source_ids']


def test_input_limit_and_empty_input(store):
    with pytest.raises(links.LinkImportError): links.import_links(store, 'p', PAGE)
    result = links.import_links(store, 'p', [PAGE, REPO], max_urls=1, fetcher=lambda u, **kw: page(u))
    assert result['results'][1]['code'] == 'limit_reached'
    assert links.import_links(store, 'p', [])['results'] == []


def test_explicit_low_source_is_validated_and_adopted(store):
    result = links.import_links(store, 'p', ['https://adg.csdn.net/tutorial'], fetcher=lambda u, **kw: page(u))
    assert result['results'][0]['status'] == 'imported'
    source = next(s for s in store.records('sources', 'p') if s['id'] in result['source_ids'])
    assert source['tier'] == 'neutral'  # explicit import is not recommendation-ranked
