"""Slow valid results survive; only cancellation or connection failure falls back."""
from types import SimpleNamespace
from threading import Event, Thread
import time

from refactor.learning.voice.asr.correction import finish
from tests.refactor.asr.test_whole122_engine import Backend122, make, two_parts


def test_wait123_five_second_model_result_is_used():
    class Slow:
        def complete(self, *args, **kw):
            assert not kw['cancel'].wait(5)
            return SimpleNamespace(text='GraphRAG那边。')
    started=time.monotonic()
    result=finish('GphRAG那边。','GphRAG那边。',['GraphRAG'],'哪边？',Slow())
    assert result.text=='GraphRAG那边。' and result.outcome=='accepted'
    assert time.monotonic()-started>=5


def test_wait123_three_second_whole_result_is_used(tmp_path):
    class Slow(Backend122):
        def final(self, audio):
            Event().wait(3)
            return '向量那个。'
    engine,_=make(tmp_path,Slow())
    try:
        two_parts(engine);started=time.monotonic()
        assert engine.finish().result(6)=='向量那个。'
        assert engine.whole_outcome=='whole' and time.monotonic()-started>=3
    finally:engine.cancel();assert engine.wait_closed()


def test_wait123_connection_error_preserves_uncorrected_text():
    class Broken:
        def complete(self,*args,**kwargs):raise ConnectionError('offline')
    result=finish('GphRAG那边。','GphRAG那边。',['GraphRAG'],'哪边？',Broken())
    assert result.text=='GphRAG那边。' and result.outcome=='fallback'


def test_wait123_cancel_does_not_wait_or_publish_late_correction():
    entered,release,cancel=Event(),Event(),Event();box=[]
    class Late:
        def complete(self,*args,**kw):
            entered.set();release.wait(5)
            return SimpleNamespace(text='GraphRAG那边。')
    thread=Thread(target=lambda:box.append(finish('GphRAG那边。','GphRAG那边。',
        ['GraphRAG'],'哪边？',Late(),cancel=cancel)))
    thread.start()
    try:
        assert entered.wait(2);started=time.monotonic();cancel.set();thread.join(.3)
        assert not thread.is_alive() and time.monotonic()-started<.3
        assert box[0].outcome=='cancelled' and box[0].text=='GphRAG那边。'
        release.set();thread.join()
        assert len(box)==1
    finally:release.set();cancel.set();thread.join(2)
