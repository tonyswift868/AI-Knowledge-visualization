"""Real AppBridge + TtsService, with explicit fake inference and audio device."""
from array import array
import json
from pathlib import Path
import threading
import time
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QApplication
from PySide6.QtMultimedia import QAudio

from refactor.learning.app.bridge import AppBridge
from refactor.learning.app.speech import TeacherSpeech
from refactor.learning.voice.download import DownloadError
from refactor.learning.voice.tts.runtime import SpeechAudio
from refactor.learning.voice.tts.service import TtsService
from refactor.learning.voice.tts import service as tts_module


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


def spin(app, condition, timeout=3):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        app.processEvents()
        if condition():
            return
        time.sleep(.003)
    assert condition()


def tick(app, seconds):
    until = time.monotonic() + seconds
    spin(app, lambda: time.monotonic() >= until, seconds + 1)


class Engine:
    def __init__(self):
        self.calls, self.speed = [], 1.0
        self.closed = False

    def set_speed(self, speed):
        self.speed = speed

    def warm_up(self, cancel=None):
        self.calls.append(("warm", self.speed, threading.get_ident()))
        return {"ready": True}

    def stream_synthesize(self, text, receive, cancel=None):
        self.calls.append((text, self.speed, threading.get_ident()))
        receive(SpeechAudio(array("h", [9000] * 3200).tobytes(), 16000, .01, False))
        receive(SpeechAudio(array("h", [9000] * 12800).tobytes(), 16000, .01, True))

    def cancel(self): pass
    def close(self): self.closed = True


class Sink:
    def __init__(self, format, parent):
        self.rate, self.written, self.played, self.resets, self.volume = format.sampleRate(), 0, 0, 0, 1
        self.threads = [threading.get_ident()]
    def setBufferSize(self, value): pass
    def setVolume(self, value): self.volume = value
    def start(self): return self
    def error(self): return QAudio.Error.NoError
    def bytesFree(self): return 100000
    def write(self, data):
        self.threads.append(threading.get_ident())
        self.written += len(data)
        return len(data)
    def processedUSecs(self): return round(min(self.played, self.written) / (self.rate * 2) * 1e6)
    def reset(self): self.resets += 1
    def deleteLater(self): pass


class Manager:
    size_bytes = 100
    def __init__(self):
        self.calls, self.started, self.release = [], threading.Event(), threading.Event()
        self.failure = None
    def download(self, progress, cancel):
        self.calls.append(threading.get_ident())
        self.started.set()
        progress(25, 100)
        while not self.release.wait(.01):
            if cancel.is_set(): raise DownloadError("已取消", "资源下载已取消。")
        if self.failure: raise DownloadError(self.failure, "明确的模拟下载失败。")
        progress(100, 100)


class Resources:
    def __init__(self):
        self.manager, self.config = Manager(), object()
        self.ready, self.gate = True, None
        self.threads, self.entered = [], threading.Event()
    def inspect(self):
        self.threads.append(threading.get_ident())
        self.entered.set()
        if self.gate: self.gate.wait(2)
        return {"ready": self.ready, "message": "朗读可用" if self.ready else "朗读未启用", "components": [], "download": None}, self.config


class Harness:
    def __init__(self, app, tmp_path):
        self.app, self.bridge, self.resources = app, AppBridge(), Resources()
        self.engines, self.sinks, self.sent, self.event_threads = [], [], [], []
        self.counter = 0
        self.clock = time.monotonic
        def service(config, parent):
            assert threading.current_thread() is threading.main_thread()
            engine = Engine()
            self.engines.append(engine)
            def sink(format, parent):
                result = Sink(format, parent)
                self.sinks.append(result)
                return result
            return TtsService(parent, runtime=engine, sink_factory=sink)
        self.voice = TeacherSpeech(self.bridge, tmp_path, resources=self.resources, service_factory=service)
        self.bridge.event.connect(self.receive)
    def receive(self, raw):
        self.sent.append((self.clock(), json.loads(raw)))
        self.event_threads.append(threading.get_ident())
    def call(self, method, **params):
        self.counter += 1
        request_id = f"test-{self.counter}"
        self.bridge.call(json.dumps({"id": request_id, "method": "speech." + method, "params": params}))
        spin(self.app, lambda: any(m.get("id") == request_id for _, m in self.sent))
        return next(m for _, m in self.sent if m.get("id") == request_id)
    def events(self, name):
        return [(at, m["data"]) for at, m in self.sent if m.get("name") == "speech." + name]
    def close(self):
        self.voice.dispose(); self.bridge.close()
        if self.resources.gate: self.resources.gate.set()
        self.resources.manager.release.set()
        tick(self.app, .04)


@pytest.fixture
def h(app, tmp_path):
    result = Harness(app, tmp_path)
    try: yield result
    finally: result.close()


def test_teacher_voice_v2_pipeline_device_timing_and_thread_boundary(h):
    assert h.call("status")["result"]["ready"]
    assert not h.engines  # status cannot launch an engine or download.
    h.call("warm_up", owner="page")
    spin(h.app, lambda: h.voice._state == "ready")
    h.call("speak", owner="page", utterance_id="paragraph", text="第一句。第二句。第三句。")
    spin(h.app, lambda: h.voice.service and len(h.voice.service.last_metrics) == 2)
    assert not h.events("sentence")  # Synthesized PCM has not been consumed.
    sink = h.sinks[-1]
    assert [c[0] for c in h.engines[-1].calls] == ["warm", "第一句。", "第二句。"]
    sink.played = 32002
    spin(h.app, lambda: len(h.voice.service.last_metrics) == 3)
    events = [e for _, e in h.events("sentence")]
    assert [(e["index"], e["state"]) for e in events] == [(0, "started"), (0, "finished"), (1, "started")]
    sink.played = 96000
    spin(h.app, lambda: len([e for _, e in h.events("sentence") if e["state"] == "finished"]) == 3)
    assert [e["index"] for _, e in h.events("sentence") if e["state"] == "finished"] == [0, 1, 2]
    assert all(e["utterance_id"] == "paragraph" and e["owner"] == "page" for _, e in h.events("sentence"))
    main = threading.get_ident()
    assert all(t != main for t in h.resources.threads)
    assert all(c[2] != main for engine in h.engines for c in engine.calls)
    assert set(h.event_threads + sink.threads) == {main}


def test_teacher_voice_v2_mouth_20hz_stop_zero_and_owner_guard(h, monkeypatch):
    elapsed_us = 0
    h.clock = lambda: elapsed_us / 1_000_000
    monkeypatch.setattr(tts_module, 'time', SimpleNamespace(monotonic=h.clock))
    h.call("speak", owner="current", utterance_id="audio", text="一段持续朗读。后续不能在停止后播放。")
    # Finish fake synthesis at time zero so asynchronous preparation cannot
    # reset the mouth schedule halfway through the controlled playback.
    spin(h.app, lambda: bool(h.sinks) and h.sinks[-1].written >= 64000)
    sink = h.sinks[-1]
    service = h.voice.service
    assert service._timer.interval() == 5
    service._timer.stop()  # Drive the real pump; OS timer delivery is not the oracle.
    def pump(at_us):
        nonlocal elapsed_us
        elapsed_us = at_us
        sink.played = int(h.clock() * 32000)
        service._pump()
        h.app.processEvents()
    def positive_samples():
        return [(at, e) for at, e in h.events("mouth")
                if e["utterance_id"] == "audio" and e["level"] > 0]
    for i in range(1, 14):
        pump(i * 50000 - 1)
        assert len(positive_samples()) == i - 1  # Not before the 50 ms boundary.
        pump(i * 50000 + 1)  # One microsecond avoids binary float rounding.
        assert len(positive_samples()) == i
    samples = positive_samples()
    assert [at for at, _ in samples] == pytest.approx(
        [i * .05 + .000001 for i in range(1, 14)], rel=0, abs=1e-9)
    assert all(b[0] - a[0] == pytest.approx(.05, rel=0, abs=1e-9)
               for a, b in zip(samples, samples[1:]))
    assert h.call("stop", owner="old-page", utterance_id="audio")["result"]["stopped"] is False
    assert sink.resets == 0 and len(positive_samples()) == 13
    before = len(h.sent)
    response = h.call("stop", owner="current", utterance_id="audio")
    assert response["result"]["stopped"] and sink.resets == 1
    assert h.events("mouth")[-1][1]["level"] == 0
    assert h.events("sentence")[-1][1]["state"] == "interrupted"
    delivered = [message for _, message in h.sent[before:]]
    response_index = next(i for i, m in enumerate(delivered) if m.get("id") == response["id"])
    # Stop must reset the device and deliver zero/interruption before its reply,
    # rather than depending on the loaded machine meeting a wall-clock budget.
    assert any(m.get("name") == "speech.mouth" and m["data"]["level"] == 0
               for m in delivered[:response_index])
    assert any(m.get("name") == "speech.sentence" and m["data"]["state"] == "interrupted"
               for m in delivered[:response_index])
    assert elapsed_us == 650001
    count = len(h.events("sentence"))
    pump(elapsed_us + 200000)
    assert len(h.events("sentence")) == count
    assert len(positive_samples()) == 13 and sink.resets == 1


def test_teacher_voice_v2_late_preparation_and_missing_voice_are_quiet(h):
    h.resources.gate = threading.Event()
    h.call("speak", owner="page", utterance_id="old", text="这段已停止。")
    assert h.resources.entered.wait(.5)
    h.call("stop", owner="page", utterance_id="old")
    h.resources.gate.set()
    tick(h.app, .15)
    assert not h.engines
    h.resources.ready = False
    h.call("speak", owner="page", utterance_id="missing", text="缺模型也不影响其他页面。")
    spin(h.app, lambda: bool(h.events("availability")))
    assert not h.events("error") and not h.engines
    assert h.events("availability")[-1][1]["message"] == "朗读未启用"


def test_teacher_voice_v2_volume_immediate_speed_next_utterance(h):
    h.call("settings", volume=.4, speed=1.3)
    h.call("speak", owner="page", utterance_id="first", text="第一句。第二句。第三句。")
    spin(h.app, lambda: bool(h.sinks) and len(h.engines[-1].calls) == 2)
    assert h.sinks[-1].volume == .4
    h.call("settings", volume=0, speed=1.7)
    assert h.sinks[-1].volume == 0 and h.events("mouth")[-1][1]["level"] == 0
    h.sinks[-1].played = 32002
    spin(h.app, lambda: len(h.engines[-1].calls) == 3)
    assert all(c[1] == 1.3 for c in h.engines[-1].calls)
    h.call("speak", owner="page", utterance_id="next", text="新语速。")
    spin(h.app, lambda: h.engines[-1].calls[-1][0] == "新语速。")
    assert h.engines[-1].calls[-1][1] == 1.7


@pytest.mark.parametrize("params", [{"volume": -1}, {"volume": True}, {"speed": float("nan")}, {"speed": 3}, {"path": "forbidden"}])
def test_teacher_voice_v2_rejects_invalid_controls(h, params):
    result = h.call("settings", **params)
    assert not result["ok"] and result["error"]["code"] == "INVALID_PARAMS"
    assert h.voice._volume == h.voice._speed == 1


def test_teacher_voice_v2_download_consent_progress_cancel_and_retry(h):
    assert h.call("download", owner="page", consent="yes")["error"]["code"] == "CONSENT_REQUIRED"
    assert not h.resources.manager.calls
    result = h.call("download", owner="page", consent=True)["result"]
    spin(h.app, lambda: len(h.events("download")) >= 2)
    assert h.events("download")[-1][1]["received_bytes"] == 25
    assert h.call("download", owner="page", consent=True)["error"]["code"] == "DOWNLOAD_BUSY"
    h.call("cancel_download", owner="page", download_id=result["download_id"])
    spin(h.app, lambda: h.events("download")[-1][1]["state"] == "cancelled")
    h.resources.manager.release.set()
    h.call("download", owner="page", consent=True)
    spin(h.app, lambda: h.events("download")[-1][1]["state"] == "completed")
    assert h.events("download")[-1][1]["received_bytes"] == 100
    assert all(t != threading.get_ident() for t in h.resources.manager.calls)


@pytest.mark.parametrize("failure", ["网络失败", "校验失败", "磁盘不足"])
def test_teacher_voice_v2_download_failure_visible_and_can_retry(h, failure):
    h.resources.manager.failure = failure
    h.resources.manager.release.set()
    h.call("download", owner="page", consent=True)
    spin(h.app, lambda: h.events("download")[-1][1]["state"] == "error")
    assert h.events("download")[-1][1]["code"] == failure


def test_teacher_voice_v2_scope_reset_closes_only_owned_engine_and_cancels_download(h):
    h.call("speak", owner="page", utterance_id="leaving", text="退出账号会停止。")
    spin(h.app, lambda: bool(h.sinks))
    h.call("download", owner="page", consent=True)
    job = h.voice._download
    h.voice.reset()
    assert job["cancel"].is_set() and all(engine.closed for engine in h.engines)
    assert h.voice.service is None and h.sinks[-1].resets == 1
    h.call("speak", owner="new-page", utterance_id="new", text="新的会话。")
    spin(h.app, lambda: len(h.engines) == 2 and bool(h.voice.service.last_metrics))
    assert not h.engines[-1].closed


def test_teacher_voice_v2_stream_append_queues_complete_sentences_in_order(h):
    h.call("warm_up", owner="stream-page")
    spin(h.app, lambda: h.voice._state == "ready")
    result = h.call("stream_start", owner="stream-page", utterance_id="turn-1")
    assert result["result"]["streaming"] is True
    assert h.call("stream_append", owner="stream-page", utterance_id="turn-1", text="第一句")["result"]["queued"] == 0
    assert h.call("stream_append", owner="stream-page", utterance_id="turn-1", text="。第二句")["result"]["queued"] == 1
    assert h.call("stream_append", owner="stream-page", utterance_id="turn-1", text="。第三句。尾巴")["result"]["queued"] == 2
    spin(h.app, lambda: len(h.engines) == 1 and len(h.engines[0].calls) == 3)
    assert [call[0] for call in h.engines[0].calls] == ["warm", "第一句。", "第二句。"]
    assert h.call("stream_end", owner="stream-page", utterance_id="turn-1")["result"]["queued"] == 1
    # The fake device consumes PCM only when the test advances its clock. This
    # makes the producer wait in a deterministic way while preserving order.
    until = time.monotonic() + 2
    while time.monotonic() < until and (len(h.engines[0].calls) < 5 or len([e for _, e in h.events("sentence") if e["state"] == "started"]) < 4):
        for sink in h.sinks:
            sink.played = 1000000
        h.app.processEvents()
        time.sleep(.003)
    assert [call[0] for call in h.engines[0].calls] == ["warm", "第一句。", "第二句。", "第三句。", "尾巴"]
    assert [e["index"] for _, e in h.events("sentence") if e["state"] == "started"] == [0, 1, 2, 3]
    assert len(h.sinks) == 1  # All streamed sentences share the device timeline.


def test_teacher_voice_v2_stream_stop_drops_partial_and_queued_text(h):
    h.call("stream_start", owner="stream-page", utterance_id="turn-stop")
    h.call("stream_append", owner="stream-page", utterance_id="turn-stop", text="还没有句号")
    assert not [call for engine in h.engines for call in engine.calls if call[0] != "warm"]
    assert h.call("stop", owner="stream-page", utterance_id="turn-stop")["result"]["stopped"]
    assert h.call("stream_append", owner="stream-page", utterance_id="turn-stop", text="。不应朗读")["result"]["accepted"] is False
    tick(h.app, .15)
    assert not [call for engine in h.engines for call in engine.calls if call[0] != "warm"]
    assert not [e for _, e in h.events("sentence") if e["utterance_id"] == "turn-stop"]


def test_teacher_voice_v2_stream_calls_and_audio_events_stay_thread_safe(h):
    h.call("warm_up", owner="stream-page")
    spin(h.app, lambda: h.voice._state == "ready")
    h.call("stream_start", owner="stream-page", utterance_id="turn-thread")
    h.call("stream_append", owner="stream-page", utterance_id="turn-thread", text="线程边界。")
    h.call("stream_end", owner="stream-page", utterance_id="turn-thread")
    spin(h.app, lambda: bool(h.engines))
    assert all(thread == threading.main_thread().ident for thread in h.event_threads)
    assert all(thread != threading.main_thread().ident for thread in h.resources.threads)
    assert all(call[2] != threading.main_thread().ident for call in h.engines[0].calls)


def test_teacher_voice_v2_stream_waits_for_own_resource_check_and_flushes_early_end(h):
    h.call("warm_up", owner="page")
    spin(h.app, lambda: h.voice._state == "ready")
    h.resources.gate = threading.Event()
    h.call("stream_start", owner="page", utterance_id="pending")
    h.call("stream_append", owner="page", utterance_id="pending", text="第一句。第二句。末尾")
    h.call("stream_end", owner="page", utterance_id="pending")
    tick(h.app, .05)
    assert [c[0] for c in h.engines[0].calls] == ["warm"]
    h.resources.gate.set()
    spin(h.app, lambda: len(h.engines[0].calls) == 3)
    assert [c[0] for c in h.engines[0].calls] == ["warm", "第一句。", "第二句。"]
    spin(h.app, lambda: bool(h.sinks))
    h.sinks[-1].played = 32002
    spin(h.app, lambda: len(h.engines[0].calls) == 4)
    assert h.engines[0].calls[-1][0] == "末尾"


def test_teacher_voice_v2_reset_rejects_old_append_end_stop_and_keeps_new_timing(h):
    h.call("stream_start", owner="page", utterance_id="old")
    h.call("stream_append", owner="page", utterance_id="old", text="旧句。旧队列。未完")
    spin(h.app, lambda: bool(h.sinks))
    old_sink = h.sinks[-1]
    old_sink.played = 2
    spin(h.app, lambda: bool(h.events("sentence")))
    h.call("stop", owner="page", utterance_id="old")
    h.call("stream_start", owner="page", utterance_id="retry")
    assert not h.call("stream_append", owner="page", utterance_id="old", text="迟到。")['result']['accepted']
    assert not h.call("stream_end", owner="page", utterance_id="old")['result']['accepted']
    assert not h.call("stop", owner="page", utterance_id="old")['result']['stopped']
    h.call("stream_append", owner="page", utterance_id="retry", text="重试第一句。重试第二句。")
    h.call("stream_end", owner="page", utterance_id="retry")
    spin(h.app, lambda: len(h.sinks) == 2 and len(h.voice.service.last_metrics) == 2)
    assert old_sink.resets == 1
    h.sinks[-1].played = 64000
    spin(h.app, lambda: any(e['utterance_id'] == 'retry' and e['state'] == 'finished' and e['index'] == 1
                          for _, e in h.events('sentence')))
    assert [(e['text'], e['index']) for _, e in h.events('sentence') if e['state'] == 'started'] == [
        ('旧句。', 0), ('重试第一句。', 0), ('重试第二句。', 1)]
