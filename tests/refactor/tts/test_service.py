import threading
import time
from enum import Enum
import pytest

from PySide6.QtMultimedia import QAudio

from refactor.learning.voice.tts.runtime import SpeechAudio
from refactor.learning.voice.tts.service import TtsService


def spin(app, condition, timeout=2):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        app.processEvents()
        if condition():
            return
        time.sleep(.003)
    assert condition()


class FakeRuntime:
    def __init__(self):
        self.calls = []
        self.closed = False
        self.block = None
        self.started = threading.Event()

    def warm_up(self, cancel=None):
        self.calls.append(("warm", threading.get_ident()))
        return {"ready": True}

    def synthesize(self, text, cancel=None):
        self.calls.append((text, threading.get_ident()))
        self.started.set()
        if self.block:
            self.block.wait(1)
        return SpeechAudio(b"\x01\x00" * 3200, 32000, .01)

    def cancel(self):
        pass

    def close(self):
        self.closed = True


class FakeSink:
    def __init__(self, format, parent):
        self.bytes = 0
        self.played = 0
        self.rate = format.sampleRate()
        self.reset_count = 0

    def setBufferSize(self, size):
        pass

    def start(self):
        return self

    def error(self):
        return QAudio.Error.NoError

    def bytesFree(self):
        return 100000

    def write(self, data):
        self.bytes += len(data)
        return len(data)

    def processedUSecs(self):
        return int(self.played * 1000000 / (2 * self.rate))

    def reset(self):
        self.reset_count += 1

    def deleteLater(self):
        pass


def test_warmup_background_pipelines_second_while_first_plays(qt_app):
    runtime = FakeRuntime()
    sinks = []
    def sink_factory(format, parent):
        sinks.append(FakeSink(format, parent))
        return sinks[-1]
    service = TtsService(runtime=runtime, sink_factory=sink_factory)
    started, errors = [], []
    service.sentence_started.connect(lambda index, text: started.append((index, text)))
    service.error.connect(lambda *args: errors.append(args))
    try:
        service.warm_up()
        spin(qt_app, lambda: service.state == "ready")
        service.speak("第一句。第二句。第三句。")
        spin(qt_app, lambda: len(service.last_metrics) == 2)
        assert [c[0] for c in runtime.calls] == ["warm", "第一句。", "第二句。"]
        assert len(sinks) == 1 and not started
        sinks[0].played = 6402
        spin(qt_app, lambda: len(service.last_metrics) == 3)
        assert [i for i, _ in started] == [0, 1]
        sinks[0].played = 19200
        spin(qt_app, lambda: service.state == "idle")
        assert [i for i, _ in started] == [0, 1, 2] and not errors
        assert all(thread != threading.get_ident() for _, thread in runtime.calls)
    finally:
        service.close()
    assert runtime.closed


def test_stop_and_new_speak_drop_late_old_audio(qt_app):
    runtime = FakeRuntime()
    runtime.block = threading.Event()
    service = TtsService(runtime=runtime, sink_factory=FakeSink)
    starts = []
    service.sentence_started.connect(lambda i, text: starts.append(text))
    try:
        service.speak("旧消息。后半句。")
        spin(qt_app, runtime.started.is_set)
        start = time.perf_counter()
        service.stop()
        assert time.perf_counter() - start < .05
        assert not service._chunks and not service._boundaries
        service.speak("新消息。")
        runtime.block.set()
        spin(qt_app, lambda: len(service.last_metrics) == 1)
        assert service.last_metrics[0]["text"] == "新消息。"
        service._sink.played = 6400
        spin(qt_app, lambda: service.state == "idle")
        assert starts == ["新消息。"]
    finally:
        runtime.block.set()
        service.close()


def test_stop_from_sentence_signal_resets_playback_without_late_callbacks(qt_app):
    service = TtsService(runtime=FakeRuntime(), sink_factory=FakeSink)
    service.sentence_started.connect(lambda *_: service.stop())
    try:
        service.speak("打断第一句。第二句不能出声。")
        spin(qt_app, lambda: service._sink is not None)
        sink = service._sink
        sink.played = 2
        spin(qt_app, lambda: service.state == "stopped")
        assert sink.reset_count == 1 and not service._boundaries
    finally:
        service.close()


def test_initial_empty_push_buffer_underrun_can_accept_audio(qt_app):
    class BackendError(Enum):  # Different Python enum, same Qt ABI value.
        NoError = QAudio.Error.NoError.value
        UnderrunError = QAudio.Error.UnderrunError.value
    class InitialUnderrunSink(FakeSink):
        def error(self):
            return BackendError.UnderrunError if not self.bytes else BackendError.NoError
    service = TtsService(runtime=FakeRuntime(), sink_factory=InitialUnderrunSink)
    errors = []
    service.error.connect(lambda *args: errors.append(args))
    try:
        service.speak("启动时尚未填充声音。")
        spin(qt_app, lambda: service._sink is not None and service._sink.bytes > 0)
        assert not errors
    finally:
        service.close()


def test_audio_open_nested_events_do_not_complete_utterance_early(qt_app):
    class ReentrantSink(FakeSink):
        def start(self):
            service._pump()
            return self
    service = TtsService(runtime=FakeRuntime(), sink_factory=ReentrantSink)
    try:
        service.speak("音频打开时可以分发嵌套事件。")
        spin(qt_app, lambda: service._sink is not None and service._sink.bytes > 0)
        assert len(service._boundaries) == 1
    finally:
        service.close()


def test_stream_starts_before_sentence_finishes_and_emits_once(qt_app):
    release = threading.Event()
    class StreamingRuntime(FakeRuntime):
        def stream_synthesize(self, text, receive, cancel=None):
            receive(SpeechAudio(b"\x01\x00" * 641, 32000, .01, False))
            release.wait(2)
            receive(SpeechAudio(b"\x01\x00" * 640, 32000, .01, False))
            receive(SpeechAudio(b"", 32000, .001, True))
    service = TtsService(runtime=StreamingRuntime(), sink_factory=FakeSink)
    starts = []
    service.sentence_started.connect(lambda *args: starts.append(args))
    try:
        service.speak("分块播放同一个句子。")
        spin(qt_app, lambda: service._sink is not None)
        sink = service._sink
        sink.played = 1282
        spin(qt_app, lambda: bool(starts))
        assert len(service._boundaries) == 1 and not service._boundaries[0]["finished"]
        release.set()
        spin(qt_app, lambda: sink.bytes == 2562)
        sink.played = 2562  # 1281 frames = 40031.25 microseconds, rounded by Qt.
        spin(qt_app, lambda: service.state == "idle")
        assert len(starts) == 1 and len(service.last_metrics) == 1
        assert service.last_metrics[0]["audio_seconds"] == pytest.approx(1281 / 32000)
    finally:
        release.set()
        service.close()
