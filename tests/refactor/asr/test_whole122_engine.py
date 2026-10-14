"""Two-pass finalization, lifecycle and correction integration; offline seams."""
from concurrent.futures import CancelledError, Future
import json
from threading import Event, current_thread
import time
from types import SimpleNamespace

import numpy as np
import pytest

from refactor.learning.voice.asr import engine as engine_module
from refactor.learning.voice.asr import service as service_module
from refactor.learning.voice.asr.engine import AsrEngine, _whole_chunks, SAMPLE_RATE
from refactor.learning.app.voice_input import VoiceInput
from refactor.learning.app.bridge import AppBridge
from tests.refactor.asr.test_engine_service import Recorder, Installed, until


def pcm(value, seconds=.2):
    return np.full(round(seconds*SAMPLE_RATE), value, dtype='<i2').tobytes()


class Backend122:
    window_size = 320
    first_speech_sample = None
    def __init__(self, *_):
        self.offset = 0
        self.pending = []
        self.speech_ranges = []
        self.whole_audio = None
    def accept(self, samples):
        if np.any(samples):
            if not self.pending:
                self.start = self.offset
            if self.first_speech_sample is None:
                self.first_speech_sample = self.offset
            self.pending.append(samples.copy())
            out = []
        else:
            out = self.flush()
        self.offset += len(samples)
        return out
    def partial_audio(self):
        return np.concatenate(self.pending) if self.pending else np.empty(0,dtype=np.float32)
    def flush(self):
        if not self.pending:return []
        audio = self.partial_audio()
        self.speech_ranges.append((self.start, self.start + len(audio)))
        self.pending = []
        return [audio]
    def decode(self, audio):
        if np.any(audio == 1000/32768) and np.any(audio == 2000/32768):
            self.whole_audio = audio.copy()
            return self.final(audio)
        return '下料。' if np.any(audio == 1000/32768) else '来个。'
    def final(self, audio):return '项梁那个。'


def make(tmp_path, backend=None):
    backend = backend or Backend122()
    engine = AsrEngine(tmp_path, backend_factory=lambda *_:backend, partial_interval=.05)
    assert engine.ready.wait(2)
    return engine,backend


def two_parts(engine, silence=1.):
    engine.feed(pcm(1000)+pcm(0,silence)+pcm(2000))


def test_whole122_final_uses_whole_audio_and_keeps_internal_pause(tmp_path):
    engine,backend = make(tmp_path)
    # Leading silence is cropped to .3 s before the first detected speech.
    engine.feed(pcm(0,1.)+pcm(1000)+pcm(0,1.)+pcm(2000))
    assert engine.finish().result(3)=='项梁那个。'
    assert engine.wait_closed()
    assert engine.whole_outcome=='whole'
    assert len(backend.whole_audio)==round(1.7*SAMPLE_RATE)
    assert engine.take_finalized()==['下料。','来个。']  # previews, not another appended final
    assert engine.snapshot().text=='项梁那个。'


@pytest.mark.parametrize('mode',['failed','empty','prepare'])
def test_whole122_failure_falls_back_without_error(tmp_path,monkeypatch,mode):
    release=Event()
    class Fallback(Backend122):
        def final(self,audio):
            if mode=='failed':raise RuntimeError('test whole decode failure')
            return ''
    engine,_=make(tmp_path,Fallback())
    if mode=='prepare':
        def failed_setup(*_):raise RuntimeError('optional final setup failed')
        monkeypatch.setattr(engine,'_whole_final',failed_setup)
    try:
        two_parts(engine)
        assert engine.finish().result(2)=='下料。 来个。'
        assert engine.failure is None
        assert engine.whole_outcome=='fallback_failed'
    finally:
        release.set();engine.cancel();assert engine.wait_closed()


def test_whole122_long_recording_only_splits_at_longest_safe_pause():
    audio=np.zeros(8*SAMPLE_RATE,dtype=np.float32)
    ranges=[(0,SAMPLE_RATE),(3*SAMPLE_RATE,4*SAMPLE_RATE),(7*SAMPLE_RATE,8*SAMPLE_RATE)]
    chunks=_whole_chunks(audio,ranges,max_seconds=4)
    assert [len(a)/SAMPLE_RATE for a in chunks]==[2.,3.5,2.5]
    assert all(len(a)<=4*SAMPLE_RATE for a in chunks)
    assert sum(map(len,chunks))==len(audio)
    # A .8-second endpoint must not be used as a long-recording cut.
    with pytest.raises(ValueError,match='no_long_pause'):
        _whole_chunks(audio,[(0,2*SAMPLE_RATE),(round(2.8*SAMPLE_RATE),8*SAMPLE_RATE)],max_seconds=4)


def test_whole122_no_safe_long_pause_returns_previews(tmp_path,monkeypatch):
    monkeypatch.setattr(engine_module,'WHOLE_CHUNK_SECONDS',.3)
    # Explicit helper seam: the product uses the measured 25-second threshold.
    original=engine_module._whole_chunks
    monkeypatch.setattr(engine_module,'_whole_chunks',lambda audio,ranges:original(audio,ranges,max_seconds=.3))
    engine,_=make(tmp_path)
    two_parts(engine,.8)
    assert engine.finish().result(3)=='下料。 来个。'
    assert engine.whole_outcome=='fallback_no_long_pause'


def test_whole122_cancel_during_second_pass_suppresses_late_result(tmp_path):
    entered,release=Event(),Event()
    class Slow(Backend122):
        def final(self,audio):
            entered.set();release.wait(2);return '迟到结果'
    engine,_=make(tmp_path,Slow())
    two_parts(engine);future=engine.finish()
    try:
        assert entered.wait(2)
        engine.cancel();release.set()
        with pytest.raises(CancelledError):future.result(2)
        assert engine.wait_closed() and engine.take_finalized()==[]
        assert engine.snapshot().text==''
    finally:release.set();engine.cancel()


def test_whole122_cancel_does_not_decode_remaining_long_blocks(tmp_path,monkeypatch):
    entered,release=Event(),Event()
    class Slow(Backend122):
        calls=0
        def final(self,audio):
            self.calls+=1;self.thread=current_thread();entered.set();release.wait(2)
            return '忽略迟到终稿'
    monkeypatch.setattr(engine_module,'_whole_chunks',lambda audio,ranges:[audio,audio])
    engine,backend=make(tmp_path,Slow())
    try:
        two_parts(engine);future=engine.finish()
        assert entered.wait(2)
        engine.cancel()
        with pytest.raises(CancelledError):future.result(2)
        assert engine.wait_closed()
        release.set();backend.thread.join(1)
        assert not backend.thread.is_alive() and backend.calls==1
    finally:release.set();engine.cancel();engine.wait_closed()


def test_whole122_both_corrections_use_whole_final_and_raw_undo(qt_app,tmp_path):
    bridge=AppBridge();messages=[]
    bridge.event.connect(lambda text:messages.append(json.loads(text)))
    calls=[]
    class Gateway:
        def complete(self,purpose,rows,**kw):
            payload=json.loads(rows[0]['content']);calls.append(payload)
            assert purpose=='asr_correction' and payload['recognized']=='向量来个。'
            return SimpleNamespace(text='向量那个。')
    class Whole(Backend122):
        def final(self,audio):return '下料来个。'
    engine_box=[]
    def factory(path):
        engine=AsrEngine(path,backend_factory=Whole)
        engine_box.append(engine);return engine
    voice=VoiceInput(bridge,tmp_path,model_manager=Installed(tmp_path),engine_factory=factory,
                     recorder_factory=Recorder,gateway_provider=lambda:Gateway())
    try:
        voice.start(dict(session_id='whole122',terms=['向量'],question='哪边擅长？'),None)
        two_parts(engine_box[0]);voice.service.stop()
        until(qt_app,lambda:any(m.get('name')=='voice.final' for m in messages))
        final=next(m['data'] for m in messages if m.get('name')=='voice.final')
        assert final['raw_text']=='下料来个。' and final['text']=='向量那个。'
        assert final['replacements'] and len(calls)==1
    finally:
        voice.dispose();bridge.close();engine_box[0].wait_closed()


@pytest.mark.parametrize('finish_during_snapshot',[False,True])
def test_whole122_final_poll_corrects_whole_result_once(qt_app,tmp_path,monkeypatch,finish_during_snapshot):
    calls=[];original=service_module.correct
    def tracked(raw,*args,**kwargs):
        calls.append(raw);return original(raw,*args,**kwargs)
    monkeypatch.setattr(service_module,'correct',tracked)
    asr=service_module.AsrService(tmp_path,model_manager=Installed(tmp_path),
        engine_factory=lambda path:AsrEngine(path,backend_factory=Backend122),recorder_factory=Recorder)
    finals=[];asr.final_text.connect(finals.append)
    try:
        asr.set_glossary(['向量']);asr.start();calls.clear()
        engine=asr._engine;two_parts(engine);asr.stop()
        assert asr._future.result(3)=='项梁那个。'
        if finish_during_snapshot:
            asr._future=Future();snapshot=engine.snapshot
            def final_snapshot():
                asr._future.set_result('项梁那个。')
                return snapshot()
            monkeypatch.setattr(engine,'snapshot',final_snapshot)
        asr._poll()
        assert calls==['项梁那个。'] and finals==['向量那个。']
    finally:asr.dispose();engine.wait_closed()
