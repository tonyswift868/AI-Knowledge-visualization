"""Public metadata is optional; source bands and user-selected imports stay intact."""
import json
import threading
from datetime import date
from types import SimpleNamespace

import httpx
import pytest

from refactor.learning.app.bridge import CallContext
from refactor.learning.app.plan_flow import PlanFlow
from refactor.learning.course.planner_agents import RECOMMENDATION_SYSTEM
from refactor.learning.course.research import ResearchCancelled
from refactor.learning.course.research_pipeline import GithubStars, prepare_recommendations
from refactor.learning.course.source_filter import load_lists, recommendation_rejection, classify_source
from refactor.learning.course.source_quality import is_video_source, rank_key, VIDEO_NOTE
from refactor.learning.course.store import CourseStore


def card(url, title='完整学习资料', **kwargs):
    return dict(url=url, title=title, type='课程', why='配套文字资料', modules=['基础'], **kwargs)


@pytest.mark.parametrize('url,title,body,video', [
    ('https://youtube.com/playlist?list=python', '课程', '', True),
    ('https://youtu.be/abc', '课程', '', True),
    ('https://www.bilibili.com/video/BVabc', '课程', '', True),
    ('https://www.bilibili.com/read/cv123', '文字笔记', '', False),
    ('https://example.org/course/video-lectures', '课程', '', True),
    ('https://example.org/watch?id=1', '课程', '', True),
    ('https://example.org/course/index.html', '课程', '<video src="x.mp4">', True),
    ('https://example.org/course', '视频教程', '', True),
    ('https://ocw.mit.edu/courses/python/pages/lecture-notes/', '课程笔记', '', False),
    ('https://example.org/book.pdf', '视频制作教材', '', False),
    ('https://docs.python.org/3/tutorial/', 'Python 教程', '使用视频示例说明', False),
    ('https://coursera.org/learn/python', 'Python', '', True),
    ('https://example.org/course', '视频课程配套笔记', '', False),
    ('https://example.org/text.html', '文字教程', '<video></video><p>'+'正文讲解'*200+'</p>', False),
])
def test_recommend97_video_identity(url, title, body, video):
    assert is_video_source(url, title, body) is video


def test_recommend97_text_priority_bands_stars_and_fallback(tmp_path):
    stars = GithubStars('reco', tmp_path, transport=httpx.MockTransport(
        lambda r: httpx.Response(200, json={'stargazers_count': 12300 if r.url.path.endswith('popular') else 3})))
    rows = [card('https://youtube.com/playlist?list=1'),
            card('https://github.com/example/small'),
            card('https://github.com/example/popular'),
            card('https://docs.python.org/3/tutorial/index.html'),
            card('https://ocw.mit.edu/courses/python/pages/lecture-notes/')]
    ordered = prepare_recommendations(rows, stars=stars)
    assert len(ordered) == 4 and all(not c['is_video'] for c in ordered)
    bands = [classify_source(c['url'])['level'] or 4 for c in ordered]
    assert bands == sorted(bands)
    repos = [c for c in ordered if 'github.com/' in c['url']]
    assert [c['github_stars'] for c in repos] == [12300, 3]
    assert ordered.index(repos[0]) > ordered.index(next(c for c in ordered if 'docs.python.org' in c['url']))
    fallback = prepare_recommendations(rows[:1])
    assert fallback[0]['video_note'] == VIDEO_NOTE
    assert prepare_recommendations(rows[:1], previous=ordered) == []
    assert recommendation_rejection(rows[0]['url'])
    assert not recommendation_rejection(rows[0]['url'], allow_video=True)
    assert recommendation_rejection('https://example.org/signup/course', allow_video=True)
    # A text page is always ahead of an authoritative video in the old pipeline too.
    assert rank_key(dict(url='https://example.org/text', credibility=2)) < rank_key(
        dict(url='https://ocw.mit.edu/courses/python/video/1', credibility=5))
    assert '权威课程配套的文字笔记' in RECOMMENDATION_SYSTEM


def test_recommend97_stars_success_persistent_cache_expiry_and_no_key(tmp_path):
    calls, now = [], [100000.]
    def respond(req):
        calls.append(req)
        assert req.url.host == 'api.github.com' and req.url.path == '/repos/example/course'
        assert 'authorization' not in req.headers
        return httpx.Response(200, json={'stargazers_count': 12345})
    kwargs = dict(transport=httpx.MockTransport(respond), clock=lambda: now[0])
    lookup = GithubStars('cache', tmp_path, **kwargs)
    assert lookup.get('https://github.com/Example/Course') == 12345
    assert GithubStars('cache', tmp_path, **kwargs).get('https://github.com/example/course/tree/main') == 12345
    assert len(calls) == 1
    now[0] += 86400
    assert lookup.get('https://github.com/Example/Course') == 12345
    assert len(calls) == 2


@pytest.mark.parametrize('failure', ['network', 403, 429, 404, 'malformed', 'boolean'])
def test_recommend97_stars_failure_and_rate_limit_are_silent_cached(tmp_path, failure):
    calls = []
    def respond(req):
        calls.append(req)
        if failure == 'network': raise httpx.ConnectError('offline')
        if failure == 'malformed': return httpx.Response(200, text='not JSON')
        if failure == 'boolean': return httpx.Response(200, json={'stargazers_count': True})
        return httpx.Response(failure, headers={'Retry-After': '60'})
    lookup = GithubStars('fail', tmp_path, transport=httpx.MockTransport(respond))
    for _ in range(2):
        result = prepare_recommendations([card('https://github.com/a/b', github_stars=999)], stars=lookup)
        assert len(result) == 1 and 'github_stars' not in result[0]
    assert len(calls) == 1


def test_recommend97_stars_cancel_nonrepo_and_account_isolation(tmp_path):
    calls = []
    lookup = GithubStars('one', tmp_path, transport=httpx.MockTransport(
        lambda r: calls.append(r) or httpx.Response(200, json={'stargazers_count': 0})))
    cancel = threading.Event(); cancel.set()
    assert lookup.get('https://github.com/a/b', cancel=cancel) is None
    assert lookup.get('https://example.org/book') is None
    assert not calls
    with pytest.raises(ResearchCancelled):
        prepare_recommendations([card('https://example.org/book')], cancel=cancel)
    assert lookup.get('https://github.com/a/b') == 0
    other = GithubStars('two', tmp_path, transport=lookup.transport)
    assert other.get('https://github.com/a/b') == 0 and len(calls) == 2


def test_recommend97_actual_flow_uses_checked_redirect_and_does_not_import(tmp_path):
    store = CourseStore('flow97', tmp_path)
    def fetch(url, **kwargs):
        return SimpleNamespace(final_url='https://youtube.com/watch?v=1' if url.endswith('/redirect') else url,
                               body=b'<html>Complete course</html>')
    services = SimpleNamespace(store=store, account_id='flow97', data_root=tmp_path,
                               catalog=SimpleNamespace(load_lists=load_lists))
    stars = GithubStars('flow97', tmp_path, transport=httpx.MockTransport(
        lambda r: httpx.Response(200, json={'stargazers_count': 12300})))
    flow = PlanFlow(services, lambda *a: None, lambda: date(2026, 10, 10), fetcher=fetch, stars=stars)
    flow._ask = lambda *a, **k: ([card('https://example.org/redirect'), card('https://github.com/a/book'),
                               card('https://docs.python.org/3/tutorial/index.html')], None)
    ctx = CallContext('r97', threading.Event(), lambda *a: None)
    draft = flow.current()
    draft.update(goal='Python 数据分析', form={'goal': 'Python 数据分析'})
    try:
        flow._recommend(draft, ctx)
        saved = flow.load(draft['plan_id'])
        assert [c['url'] for c in saved['recommendations']] == [
            'https://docs.python.org/3/tutorial/index.html', 'https://github.com/a/book']
        assert saved['recommendations'][1]['github_stars'] == 12300
        assert all('_page_prefix' not in c for c in saved['recommendations'])
        assert not saved['sources'] and not store.records('source_chunks', draft['plan_id'])
    finally:
        store.close()


def test_recommend97_saved_recommendation_replay():
    # A saved offline recommendation includes a video course mixed with a book.
    rows = json.loads(__import__('pathlib').Path(__file__).with_name('recommend97_saved.json').read_text(encoding='utf-8'))
    cards = prepare_recommendations(rows)
    assert cards and all(not c['is_video'] for c in cards)
    assert any('openstax' in c['url'] for c in cards)


def test_recommend97_official_user_guides_keep_their_existing_band():
    for url in ('https://numpy.org/doc/stable/user/index.html',
                'https://matplotlib.org/stable/users/index.html'):
        assert classify_source(url)['level'] == 1
    assert classify_source('https://mit.edu/users/student/notes')['level'] == 0
    rows = [card('https://github.com/example/popular', github_stars=999999),
            card('https://numpy.org/doc/stable/user/index.html'),
            card('https://matplotlib.org/stable/users/index.html')]
    assert prepare_recommendations(rows)[-1]['url'].startswith('https://github.com/')
