"""Exercise actual PlanFlow scoring/adoption gates with labelled stand-ins."""
from types import SimpleNamespace
from datetime import date
import threading

from refactor.learning.app.plan_flow import PlanFlow
from refactor.learning.course.research_pipeline import ResearchPipeline, ResearchPool
from refactor.learning.course.research import FetchedContent
from refactor.learning.course.source_filter import RawHit, load_lists


def test_only_ambiguous_authoritative_sources_go_to_model():
    goal = 'Python functions parameters'
    urls = ['https://docs.python.org/3/one', 'https://example.test/unknown', 'https://docs.python.org/3/two']
    titles = [goal, goal, 'Python tutorial']
    def fetch(url, **_):
        title = titles[urls.index(url)]
        text = ('Python supports reusable code.' if url.endswith('two') else 'Python functions parameters return values.') * 12
        html = f'<title>{title}</title><h1>{title}</h1><p>{text}</p>'.encode()
        return FetchedContent(url, url, 'text/html', html, '')
    with ResearchPipeline(fetcher=fetch) as p:
        sources = p.run([RawHit(u) for u in urls]).verified
    assert len(sources) == 3
    pool = ResearchPool(); pool.add(sources)
    flow = PlanFlow.__new__(PlanFlow)
    flow._pools = {'p': pool}
    flow._lists = load_lists
    flow.study_day = lambda: date(2026, 9, 29)
    flow.status = lambda *_: None
    flow.save = lambda *_: None
    calls = []
    def ask(purpose, request, parser, ctx, **kwargs):
        calls.append(request)
        assert 'example.test' not in request[1][0]['content']
        assert '[r1]' in request[1][0]['content'] and '[r0]' not in request[1][0]['content']
        return {'r1': {'relevance': 1, 'credibility': 5, 'note': 'The excerpt is only a generic introduction; parameters are absent.'}}, None
    flow._ask = ask
    draft = dict(plan_id='p', goal=goal, form=dict(level='beginner', goal=goal, final_goal='Write functions',
        start='2026-09-29', end='2026-10-29', daily_cap=60, notes='', level_note='', weekday_weights=[1]*7, special_days=[]),
        sources=[], research={}, messages=[])
    assert flow._show_batch(draft, SimpleNamespace(cancel=threading.Event())) == 1
    assert len(calls) == 1 and draft['sources'][0]['url'] == urls[0]
    assert not draft['research']['quality_audit'][urls[1]]['eligible']


def test_saved_source_can_be_readopted_without_memory_cache(tmp_path):
    from refactor.learning.course.store import CourseStore
    from refactor.learning.course.ledger import PlanWindow
    from refactor.learning.course.materials import import_verified_source
    from refactor.learning.app.bridge import BridgeError
    import pytest
    account='mfa1_'+'a'*64; pid='c'*32; today=date(2026,9,29)
    url='https://docs.python.org/3/tutorial/functions'
    def fetch(url, **_):
        return FetchedContent(url,url,'text/html',b'<h1>Python functions parameters</h1><p>'+ b'Python functions accept parameters and return values. '*8+b'</p>','')
    with CourseStore(account,tmp_path) as store, ResearchPipeline(fetcher=fetch) as p:
        store.create_plan(pid,goal='Python functions parameters',final_goal='Write functions',level='beginner',window=PlanWindow(today,date(2026,10,29),60))
        source=p.run([RawHit(url)]).verified[0]
        saved=import_verified_source(store,pid,source)
        flow=PlanFlow(SimpleNamespace(store=store,account_id=account,data_root=tmp_path),lambda *_:None,lambda:today)
        flow._lists=load_lists
        draft=flow.current()
        # Build an isolated saved draft around the already-imported source.
        draft.update(plan_id=pid,phase='sources',goal='Python functions parameters',form={'level':'beginner'},
                     sources=[dict(card_id='c',kind='url',url=url,source_id=saved['id'],status='rejected')],
                     research={'rejected':[url]})
        flow.save(draft)
        flow.decide(draft,'c',True)
        assert store.records('sources',pid)[0]['quality']['eligible']
        # Changing the goal cannot reuse a stale model or source score.
        draft=flow.load(pid); draft['goal']='Ancient Roman pottery'; flow.save(draft)
        with pytest.raises(BridgeError, match='门槛'):
            flow.decide(draft,'c',True)
