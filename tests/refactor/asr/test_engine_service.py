from pathlib import Path
from threading import Event, get_ident
import time

import numpy as np
from PySide6.QtCore import QObject, Signal
from PySide6.QtTest import QTest
import pytest

from refactor.learning.voice.asr.engine import AsrEngine
from refactor.learning.voice.asr.errors import AsrError
from refactor.learning.voice.asr.service import AsrService


def until(qt_app, predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        qt_app.processEvents()
        QTest.qWait(10)
    assert predicate(), "ASR operation did not finish"


class FakeBackend:
    window_size = 512
    load_seconds = 0.01
    def __init__(self, path=None, cancel=None):
        self.samples = []
        self.threads = []
    def accept(self, samples):
        if np.max(np.abs(samples)) > 0.01:
            self.samples.extend(samples)
            return []
        return self.flush()
    def partial_audio(self):
        return np.asarray(self.samples, dtype=np.float32)
    def flush(self):
        if not self.samples:
            return []
        result = [self.partial_audio()]
        self.samples = []
        return result
    def decode(self, samples):
        self.threads.append(get_ident())
        return f"句子{len(samples)}"


SPEECH = (np.ones(3200, dtype=np.int16) * 8000).tobytes()
SILENCE = np.zeros(1024, dtype=np.int16).tobytes()


def test_loading_and_decoding_are_background_and_finish_drains_tail(tmp_path):
    entered, release = Event(), Event()
    backend = FakeBackend()
    factory_threads = []
    def factory(path, cancel):
        factory_threads.append(get_ident())
        entered.set()
        assert release.wait(5)
        return backend
    started = time.monotonic()
    engine = AsrEngine(tmp_path, backend_factory=factory)
    assert time.monotonic() - started < 0.2
    try:
        assert entered.wait(2)
        started = time.monotonic()
        engine.feed(SPEECH)
        result = engine.finish()
        assert time.monotonic() - started < 0.1
        assert not result.done()
        release.set()
        assert result.result(timeout=5).startswith("句子")
        assert factory_threads[0] != get_ident()
        assert backend.threads and all(thread != get_ident() for thread in backend.threads)
        assert engine.wait_closed()
    finally:
        release.set()
        engine.cancel()


def test_vad_finals_and_current_partial_do_not_duplicate(tmp_path, qt_app):
    engine = AsrEngine(tmp_path, backend_factory=FakeBackend, partial_interval=.05)
    try:
        engine.feed(SPEECH)
        until(qt_app, lambda: bool(engine.pending_partial()))
        engine.feed(SILENCE)
        until(qt_app, lambda: len(engine.snapshot().sentences) == 1)
        first = engine.snapshot().sentences[0]
        assert engine.pending_partial() == ""
        assert engine.take_finalized() == [first]
        assert engine.take_finalized() == []
        engine.feed(SPEECH * 2)
        until(qt_app, lambda: bool(engine.pending_partial()))
        snapshot = engine.snapshot()
        assert snapshot.text == first + " " + snapshot.partial
        result = engine.finish().result(timeout=5)
        assert result.count("句子") == 2
        assert engine.wait_closed()
    finally:
        engine.cancel()


class Installed:
    def __init__(self, path):
        self.path = path
    def status(self):
        return {"installed": True, "path": str(self.path)}


class Recorder(QObject):
    audio_ready = Signal(bytes)
    error = Signal(str, str)
    def __init__(self, parent=None):
        super().__init__(parent)
        self.started = self.stopped = False
        self.tail = b""
    def start(self):
        self.started = True
    def stop(self):
        self.stopped = True
        if self.tail:
            self.audio_ready.emit(self.tail)
            self.tail = b""
    def cancel(self):
        self.stopped = True


def service(tmp_path, *, backend=FakeBackend, recorder=Recorder):
    return AsrService(tmp_path, model_manager=Installed(tmp_path),
                      engine_factory=lambda path: AsrEngine(path, backend_factory=backend, partial_interval=.05),
                      recorder_factory=recorder)


def test_missing_model_start_never_opens_mic_or_loads_engine(qt_app, tmp_path):
    def forbidden(*args):
        pytest.fail("missing model must not open microphone/load engine")
    asr = AsrService(tmp_path, engine_factory=forbidden, recorder_factory=forbidden)
    errors, states = [], []
    asr.error.connect(lambda code, message: errors.append((code, message)))
    asr.state_changed.connect(states.append)
    asr.start()
    assert errors[0][0] == "ASR_MODEL_REQUIRED"
    assert states == ["error"]
    assert not list(tmp_path.iterdir())
    asr.dispose()


def test_service_partials_are_whole_transcript_stop_gives_final(qt_app, tmp_path):
    asr = service(tmp_path)
    partials, finals, states, signal_threads = [], [], [], []
    asr.partial_text.connect(lambda text: (partials.append(text), signal_threads.append(get_ident())))
    asr.final_text.connect(finals.append)
    asr.state_changed.connect(states.append)
    try:
        asr.start()
        recorder = asr._recorder
        engine = asr._engine
        recorder.audio_ready.emit(SPEECH)
        until(qt_app, lambda: any(partials))
        recorder.audio_ready.emit(SILENCE)
        until(qt_app, lambda: bool(engine.snapshot().sentences))
        recorder.audio_ready.emit(SPEECH * 2)
        until(qt_app, lambda: bool(partials and " " in partials[-1]))
        recorder.tail = SPEECH
        asr.stop()
        assert recorder.stopped
        assert asr.state == "finishing"
        until(qt_app, lambda: bool(finals))
        assert len(finals) == 1 and finals[0].count("句子") == 2
        assert states == ["recording", "finishing", "idle"]
        assert all(thread == get_ident() for thread in signal_threads)
        assert engine.worker_thread_id != get_ident()
    finally:
        asr.dispose()


def test_cancel_discards_late_decode_and_no_final(qt_app, tmp_path):
    entered, release = Event(), Event()
    class SlowBackend(FakeBackend):
        def decode(self, samples):
            entered.set()
            assert release.wait(5)
            return "不应出现的迟到文字"
    asr = service(tmp_path, backend=SlowBackend)
    finals, partials = [], []
    asr.final_text.connect(finals.append)
    asr.partial_text.connect(partials.append)
    try:
        asr.start()
        engine = asr._engine
        asr._recorder.audio_ready.emit(SPEECH)
        assert entered.wait(3)
        asr.cancel()
        release.set()
        assert engine.wait_closed()
        QTest.qWait(450)
        assert finals == []
        assert not any(partials)
        assert asr.state == "idle"
    finally:
        release.set()
        asr.dispose()


@pytest.mark.parametrize("code", ["ASR_MIC_UNAVAILABLE", "ASR_MIC_PERMISSION"])
def test_microphone_error_propagates_and_cancels_engine(qt_app, tmp_path, code):
    class BadRecorder(Recorder):
        def start(self):
            raise AsrError(code, "设备测试错误")
    asr = service(tmp_path, recorder=BadRecorder)
    errors = []
    asr.error.connect(lambda *value: errors.append(value))
    asr.start()
    assert errors == [(code, "设备测试错误")]
    assert asr.state == "error" and asr._engine is None
    asr.dispose()


def test_load_failure_is_reported(qt_app, tmp_path):
    def failed(path, cancel):
        raise RuntimeError("invalid runtime")
    asr = service(tmp_path, backend=failed)
    errors = []
    asr.error.connect(lambda *value: errors.append(value))
    asr.start()
    until(qt_app, lambda: bool(errors))
    assert errors[0][0] == "ASR_ENGINE_FAILED"
    assert asr.state == "error" and asr._recorder is None
    asr.dispose()


def test_silence_and_repeated_stop_are_safe(qt_app, tmp_path):
    asr = service(tmp_path)
    finals = []
    asr.final_text.connect(finals.append)
    try:
        asr.start()
        asr._recorder.audio_ready.emit(SILENCE)
        asr.stop()
        asr.stop()
        until(qt_app, lambda: finals == [""])
        asr.start()
        asr._recorder.audio_ready.emit(SPEECH)
        asr.stop()
        until(qt_app, lambda: len(finals) == 2)
        assert finals[1]
    finally:
        asr.dispose()
