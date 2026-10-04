"""Exercise actual PlanFlow scoring/adoption gates with labelled stand-ins."""
from types import SimpleNamespace
from datetime import date
import threading

from refactor.learning.app.plan_flow import PlanFlow
from refactor.learning.course.research_pipeline import ResearchPipeline, ResearchPool
from refactor.learning.course.research import FetchedContent
from refactor.learning.course.source_filter import RawHit, load_lists




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
        assert store.records('sources',pid)[0]['adopted']
        # Changing the goal cannot reuse a stale model or source score.
        draft=flow.load(pid); draft['goal']='Ancient Roman pottery'; flow.save(draft)
        flow.decide(draft,'c',True)
        assert store.records('sources',pid)[0]['adopted']  # User-owned material has no creation scoring gate.
