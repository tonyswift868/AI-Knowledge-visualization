"""Offline bridge/lifecycle coverage; no microphone or network is used.

Optional real model: MENTORFLOW_ASR_TEST_DATA_ROOT. Optional built-UI smoke:
MENTORFLOW_VOICE_WEB_DIST. Both stay offline and never download missing assets.
"""
from concurrent.futures import Future
import json
import os
from pathlib import Path
import socket
import threading
import time
import wave

import pytest
from PySide6.QtCore import QObject, Qt, QUrl, Signal
from PySide6.QtWidgets import QApplication

from refactor.learning.app.bridge import AppBridge
from refactor.learning.app.voice_input import VoiceInput
from refactor.learning.voice.asr.engine import AsrEngine, Transcript
from refactor.learning.voice.asr.errors import AsrError, DownloadCancelled
from refactor.learning.voice.asr.model_manager import ModelManager


class FakeManager:
    def __init__(self, installed=True, size_bytes=1024):
        self.installed = installed
        self.size_bytes = size_bytes
        self.started = threading.Event()
        self.release = threading.Event()
        self.cancelled = threading.Event()
        self.finished = threading.Event()
        self.failure = None
        self.thread_id = None

    def status(self):
        return dict(installed=self.installed, model_name="test-offline", size_bytes=self.size_bytes,
                    download_url="https://invalid.example/unused", path="D:/unused-test-model")

    def download(self, progress, cancel):
        self.thread_id = threading.get_ident()
        self.started.set()
        try:
            progress(self.size_bytes // 4, self.size_bytes)
            while not self.release.wait(.01):
                if cancel.is_set():
                    self.cancelled.set()
                    raise DownloadCancelled()
            if self.failure:
                raise self.failure
            progress(self.size_bytes, self.size_bytes)
            self.installed = True
        finally:
            self.finished.set()


class FakeEngine:
    def __init__(self, _path):
        self.partial = ""
        self.sentences = []
        self.finalized = []
        self.result = Future()
        self.failure = None
        self.cancelled = False
        self.finishing = False

    def feed(self, _pcm):
        pass

    def snapshot(self):
        return Transcript(tuple(self.sentences), self.partial)

    def take_finalized(self):
        result, self.finalized = self.finalized, []
        return result

    def sentence(self, text):
        self.sentences.append(text)
        self.finalized.append(text)
        self.partial = ""

    def finish(self):
        self.finishing = True
        return self.result

    def complete(self):
        if self.partial:
            self.sentence(self.partial)
        if not self.result.done():
            self.result.set_result(self.snapshot().text)

    def cancel(self):
        self.cancelled = True
        self.result.cancel()


class FakeRecorder(QObject):
    audio_ready = Signal(bytes)
    error = Signal(str, str)
    failure = None

    def __init__(self, parent=None):
        super().__init__(parent)
        self.calls = [("init", threading.get_ident())]

    def start(self):
        self.calls.append(("start", threading.get_ident()))
        if self.failure:
            self.error.emit(self.failure, "test microphone failure")

    def stop(self):
        self.calls.append(("stop", threading.get_ident()))

    def cancel(self):
        self.calls.append(("cancel", threading.get_ident()))


@pytest.fixture(scope="module")
def voice_qapp():
    return QApplication.instance() or QApplication([])


def until(qapp, predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        qapp.processEvents()
        result = predicate()
        if result:
            return result
        time.sleep(.01)
    raise AssertionError("voice test timed out")


@pytest.fixture(autouse=True)
def no_voice_network(monkeypatch):
    def blocked(*_args, **_kwargs):
        raise AssertionError("voice tests must stay offline")
    monkeypatch.setattr(socket.socket, "connect", blocked)


class Harness:
    def __init__(self, qapp, root, *, manager=None, recorder_class=FakeRecorder, engine_class=FakeEngine):
        self.qapp = qapp
        self.bridge = AppBridge()
        self.messages, self.engines, self.recorders = [], [], []
        self.counter = 0
        self.manager = manager or FakeManager()
        self.bridge.event.connect(lambda s: self.messages.append(json.loads(s)))

        def engine(path):
            value = engine_class(path)
            self.engines.append(value)
            return value

        def recorder(parent):
            value = recorder_class(parent)
            self.recorders.append(value)
            return value

        self.voice = VoiceInput(self.bridge, root, model_manager=self.manager,
                                engine_factory=engine, recorder_factory=recorder)

    def send(self, method, **params):
        self.counter += 1
        request = f"voice-test-{self.counter}"
        self.bridge.call(json.dumps(dict(id=request, method="voice." + method, params=params)))
        return request

    def response(self, request):
        return until(self.qapp, lambda: next((m for m in self.messages if m.get("id") == request and m["type"] == "response"), None))

    def call(self, method, **params):
        return self.response(self.send(method, **params))

    def events(self, name):
        self.qapp.processEvents()
        return [m["data"] for m in self.messages if m.get("name") == "voice." + name]

    def close(self):
        self.voice.dispose()
        self.bridge.close()
        if self.manager.started.is_set():
            assert self.manager.finished.wait(2)


@pytest.fixture
def harness(voice_qapp, tmp_path):
    made = []

    def create(**kwargs):
        h = Harness(voice_qapp, tmp_path, **kwargs)
        made.append(h)
        return h

    yield create
    for h in made:
        h.close()


def test_voice_model_missing_never_opens_microphone_or_downloads(harness):
    h = harness(manager=FakeManager(installed=False))
    status = h.call("status")["result"]
    assert status["state"] == "not_downloaded" and status["size_bytes"] == 1024
    assert "path" not in status
    response = h.call("start", session_id="missing")
    assert response["error"]["code"] == "ASR_MODEL_REQUIRED"
    assert not h.engines and not h.recorders and not h.manager.started.is_set()


@pytest.mark.parametrize("consent", [None, False, "true", 1])
def test_voice_download_requires_explicit_consent(harness, consent):
    h = harness(manager=FakeManager(False))
    result = h.call("download", consent=consent, job_id="d1")
    assert result["error"]["code"] == "ASR_DOWNLOAD_CONSENT_REQUIRED"
    assert not h.manager.started.is_set()


def test_voice_download_progress_completion_and_thread(harness):
    h = harness(manager=FakeManager(False))
    assert h.call("download", consent=True, job_id="d1")["result"]["state"] == "downloading"
    until(h.qapp, lambda: any(p["downloaded_bytes"] == 256 for p in h.events("model")))
    assert h.call("status")["result"]["job_id"] == "d1"
    assert h.call("start", session_id="busy")["error"]["code"] == "ASR_BUSY"
    assert h.call("download", consent=True, job_id="d2")["error"]["code"] == "ASR_BUSY"
    assert h.manager.thread_id != threading.get_ident()
    h.manager.release.set()
    until(h.qapp, lambda: any(p["state"] == "ready" for p in h.events("model")))
    assert h.call("status")["result"]["installed"]
    assert not h.recorders  # Download completion does not start recording.


def test_voice_download_cancel_and_retry_failure(harness):
    h = harness(manager=FakeManager(False))
    h.call("download", consent=True, job_id="d1")
    h.call("download_cancel", job_id="old")
    assert not h.voice._job.cancel.is_set()
    h.call("download_cancel", job_id="d1")
    until(h.qapp, lambda: any(p.get("cancelled") for p in h.events("model")))
    assert h.manager.cancelled.is_set()
    assert h.call("status")["result"]["state"] == "not_downloaded"
    h.manager.failure = AsrError("ASR_MODEL_INVALID", "test checksum mismatch")
    h.manager.release.set()
    h.call("download", consent=True, job_id="d2")
    event = until(h.qapp, lambda: next((p for p in h.events("model") if p.get("error")), None))
    assert event["error"]["code"] == "ASR_MODEL_INVALID"
    assert not h.recorders


@pytest.mark.parametrize("terms, expected", [([], "扭断"), (["牛顿"], "牛顿")])
def test_voice_partial_segments_final_and_stop_on_gui_thread(harness, terms, expected):
    h = harness()
    assert h.call("start", session_id="s1", terms=terms)["ok"]
    engine = h.engines[-1]
    engine.partial = "扭断"
    until(h.qapp, lambda: any(p["text"] == expected for p in h.events("partial")))
    # Raw whole text is unchanged at VAD finalization. A separate segment poll
    # must still publish the sentence without waiting for another partial.
    engine.sentence("扭断")
    until(h.qapp, lambda: h.events("segment"))
    assert h.events("segment")[0]["text"] == expected
    request = h.send("stop", session_id="s1")
    until(h.qapp, lambda: engine.finishing)
    assert not any(m.get("id") == request for m in h.messages)
    assert h.call("status")["ok"]  # UI remains responsive while final is pending.
    engine.complete()
    result = h.response(request)
    assert result["result"]["text"] == expected
    assert result["result"] == h.events("final")[0]
    assert len(h.events("segment")) == 1
    assert h.call("stop", session_id="s1")["result"] == result["result"]
    assert len(h.events("final")) == 1
    if terms:
        assert result["result"]["replacements"] == [[0, 2, "扭断", "牛顿"]]
    assert all(thread == threading.get_ident() for _, thread in h.recorders[-1].calls)
    assert [p["state"] for p in h.events("state")] == ["recording", "finishing", "idle"]


@pytest.mark.parametrize("code", ["ASR_MIC_UNAVAILABLE", "ASR_MIC_PERMISSION"])
def test_voice_microphone_errors_are_visible_and_retryable(harness, code):
    class BrokenMic(FakeRecorder):
        failure = code
    h = harness(recorder_class=BrokenMic)
    assert h.call("start", session_id="badmic")["error"]["code"] == code
    assert h.events("error")[0]["code"] == code
    assert h.engines[-1].cancelled
    BrokenMic.failure = None
    assert h.call("start", session_id="retry")["ok"]


def test_voice_corrupt_model_requires_explicit_repair(harness):
    h = harness()
    h.call("start", session_id="corrupt")
    h.engines[-1].failure = AsrError("ASR_MODEL_REQUIRED", "integrity check failed")
    until(h.qapp, lambda: h.events("error"))
    assert h.events("error")[0]["code"] == "ASR_MODEL_INVALID"
    assert h.call("status")["result"]["invalid"]
    assert not h.manager.started.is_set()
    assert h.call("start", session_id="retry")["error"]["code"] == "ASR_MODEL_INVALID"
    h.manager.release.set()
    h.call("download", consent=True, job_id="repair")
    until(h.qapp, lambda: any(p["state"] == "ready" for p in h.events("model")))
    assert h.call("start", session_id="repaired")["ok"]


def test_voice_cancel_pending_stop_and_stale_session(harness):
    h = harness()
    h.call("start", session_id="old")
    request = h.send("stop", session_id="old")
    until(h.qapp, lambda: h.engines[-1].finishing)
    h.call("cancel", session_id="old")
    assert h.call("start", session_id="new")["ok"]
    h.call("cancel", session_id="old")
    assert h.response(request)["error"]["code"] == "CANCELLED"
    assert h.voice.service.state == "recording" and not h.engines[-1].cancelled
    assert not h.events("final")


def test_voice_bridge_cancel_releases_finishing_session(harness):
    h = harness()
    h.call("start", session_id="s1")
    request = h.send("stop", session_id="s1")
    until(h.qapp, lambda: h.engines[-1].finishing)
    h.bridge.call(json.dumps(dict(id="cancel-stop", method="cancel", params={"target": request})))
    assert h.response(request)["error"]["code"] == "CANCELLED"
    until(h.qapp, lambda: h.engines[-1].cancelled)


def test_voice_unexpected_finish_error_releases_recorder(harness, monkeypatch):
    h = harness()
    h.call("start", session_id="s1")

    def fail():
        raise RuntimeError("test native stop failure")

    monkeypatch.setattr(h.voice.service, "stop", fail)
    assert h.call("stop", session_id="s1")["error"]["code"] == "ASR_ENGINE_FAILED"
    until(h.qapp, lambda: h.engines[-1].cancelled)
    assert h.voice.service.state == "idle"


@pytest.mark.parametrize("params", [{}, {"session_id": "../x"}, {"session_id": "s", "terms": "牛顿"},
                                   {"session_id": "s", "terms": [3]}, {"session_id": "s", "terms": ["牛顿"] * 257}])
def test_voice_rejects_invalid_recording_params(harness, params):
    h = harness()
    assert h.call("start", **params)["error"]["code"] == "BAD_PARAMS"
    assert not h.recorders


def test_voice_reset_dispose_cancel_recording_and_download(harness):
    h = harness()
    h.call("start", session_id="s")
    request = h.send("stop", session_id="s")
    until(h.qapp, lambda: h.engines[-1].finishing)
    h.voice.reset()
    assert h.response(request)["error"]["code"] == "CANCELLED"
    assert h.engines[-1].cancelled
    h.call("download", consent=True, job_id="d")
    h.voice.dispose()
    h.voice.dispose()
    assert h.manager.finished.wait(2)
    assert h.manager.cancelled.is_set()
    assert h.call("start", session_id="closed")["error"]["code"] == "CANCELLED"


def test_voice_real_local_chinese_through_bridge(harness, record_property):
    root = Path(os.environ.get("MENTORFLOW_ASR_TEST_DATA_ROOT", "D:/MentorFlow-office/tools/temp/T05-data"))
    manager = ModelManager(root)
    if not manager.status()["installed"]:
        pytest.skip("local ASR model missing; no automatic download")
    # Real manager is read-only here; Harness's teardown expects fake job tokens.
    h = harness(engine_class=AsrEngine)
    h.voice.manager = h.voice.service.model_manager = manager
    h.call("start", session_id="real", terms=["牛顿"])
    engine = h.engines[-1]
    sample = Path(__file__).parents[1] / "asr/samples/chinese_5s.wav"
    with wave.open(str(sample), "rb") as wav:
        pcm = wav.readframes(wav.getnframes())
    h.recorders[-1].audio_ready.emit(pcm)
    request = h.send("stop", session_id="real")
    until(h.qapp, lambda: h.events("final"), timeout=30)
    text = h.response(request)["result"]["text"]
    assert "物理" in text and "牛顿" in text
    assert engine.worker_thread_id != threading.get_ident()
    assert engine.wait_closed()
    record_property("load_seconds", engine.load_seconds)
    record_property("decode_seconds", sum(engine.decode_seconds))
    print(f"\nVoice bridge real: load={engine.load_seconds:.3f}s decode={sum(engine.decode_seconds):.3f}s text={text}")


def test_voice_built_preview_download_record_undo_edit_and_manual_send(voice_qapp, tmp_path, monkeypatch):
    dist = os.environ.get("MENTORFLOW_VOICE_WEB_DIST")
    if not dist or not (Path(dist) / "index.html").is_file():
        pytest.skip("set MENTORFLOW_VOICE_WEB_DIST to an already built local web bundle")
    from refactor.learning.app import voice_input, window
    from refactor.learning.voice.asr import service
    manager = FakeManager(False, size_bytes=163646737)
    engines = []

    def engine_factory(path):
        engine = FakeEngine(path)
        engines.append(engine)
        return engine

    monkeypatch.setattr(window, "WEB_ROOT", Path(dist))
    monkeypatch.setattr(voice_input, "ModelManager", lambda _root: manager)
    monkeypatch.setattr(voice_input, "AsrEngine", engine_factory)
    monkeypatch.setattr(service, "AudioRecorder", FakeRecorder)
    win = window.LearningWindow("Voice Test", "LEARNER", "https://invalid.example", account_id="voice-test", data_root=tmp_path)
    win.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
    win.show()

    def js(script):
        results = []
        win.page.runJavaScript(script, lambda result: results.append(result))
        until(voice_qapp, lambda: len(results))
        return results[0]

    def wait_js(script):
        return until(voice_qapp, lambda: js(script), timeout=10)

    def click(selector):
        assert js(f"!!document.querySelector({json.dumps(selector)})")
        js(f"document.querySelector({json.dumps(selector)}).click()")

    def edit(value):
        js("(() => {const el=document.querySelector('textarea'); Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set.call(el,"
           + json.dumps(value) + "); el.dispatchEvent(new Event('input',{bubbles:true}));})()")

    def screenshot(name):
        if folder := os.environ.get("MENTORFLOW_VOICE_SCREENSHOTS"):
            # DOM acknowledgement precedes WebEngine's native compositor frame.
            deadline = time.monotonic() + .4
            while time.monotonic() < deadline:
                voice_qapp.processEvents()
                time.sleep(.01)
            win.grab().save(str(Path(folder) / name))

    try:
        url = QUrl.fromLocalFile(str(Path(dist) / "index.html"))
        url.setFragment("/dev/voice")
        win.view.setUrl(url)
        wait_js("!!document.querySelector('.mf-voice-mic') && document.body.innerText.includes('待下载')")
        click(".mf-voice-mic")
        wait_js("document.querySelector('dialog').open")
        assert not manager.started.is_set() and not engines
        assert js("document.querySelector('dialog').innerText.includes('录音不会上传')")
        assert js("document.querySelector('dialog').innerText.includes('156.1 MB')")
        screenshot("voice-download.png")
        click(".mf-voice-secondary")
        wait_js("!document.querySelector('dialog').open")
        assert not manager.started.is_set()
        click(".mf-voice-mic")
        wait_js("document.querySelector('dialog').open")
        # Cancel before the bridge download-start reply can render a progress UI.
        js("document.querySelector('.mf-voice-primary').click(); document.querySelector('.mf-voice-secondary').click()")
        until(voice_qapp, lambda: manager.cancelled.is_set())
        wait_js("!!document.querySelector('.mf-voice-primary')")
        click(".mf-voice-primary")
        wait_js("!!document.querySelector('progress')")
        manager.release.set()
        wait_js("!document.querySelector('dialog').open")
        assert not engines
        edit("我的回答：")
        click(".mf-voice-mic")
        until(voice_qapp, lambda: engines)
        engines[-1].partial = "扭断第一定律"
        wait_js("document.querySelector('textarea').value === '我的回答： 牛顿第一定律'")
        screenshot("voice-recording.png")
        assert not js("!!document.querySelector('[data-voice-sent]')")
        click(".mf-voice-correction")
        wait_js("document.querySelector('textarea').value.includes('扭断')")
        click(".mf-voice-mic")
        until(voice_qapp, lambda: engines[-1].finishing)
        engines[-1].complete()
        wait_js("document.querySelector('[data-voice-state]').dataset.voiceState === 'idle'")
        assert "扭断" in js("document.querySelector('textarea').value")
        assert not js("!!document.querySelector('[data-voice-sent]')")
        click(".mf-voice-send")
        wait_js("document.querySelector('[data-voice-sent]').innerText.includes('扭断')")
        # Editing during recognition must cancel and keep exactly the user's text.
        click(".mf-voice-mic")
        until(voice_qapp, lambda: len(engines) == 2)
        engines[-1].partial = "临时结果"
        wait_js("document.querySelector('textarea').value.includes('临时结果')")
        edit("我自己修改了回答")
        until(voice_qapp, lambda: engines[-1].cancelled)
        assert js("document.querySelector('textarea').value") == "我自己修改了回答"
        # Lifecycle registration releases capture when leaving the account.
        click(".mf-voice-mic")
        until(voice_qapp, lambda: len(engines) == 3)
        win.switch_requested.emit()
        until(voice_qapp, lambda: engines[-1].cancelled)
        wait_js("document.querySelector('[data-voice-state]').dataset.voiceState === 'idle'")
        screenshot("voice-sakura.png")
        js("document.documentElement.dataset.theme='aurora'; document.documentElement.classList.add('reduce-motion')")
        # Force a rendering turn before taking the second theme snapshot.
        wait_js("getComputedStyle(document.querySelector('.mf-voice-card')).color === 'rgb(238, 240, 255)'")
        screenshot("voice-aurora.png")
        click(".mf-voice-mic")
        until(voice_qapp, lambda: len(engines) == 4)
        js("window.location.hash='#/'")
        wait_js("!document.querySelector('.mf-voice-mic')")
        until(voice_qapp, lambda: engines[-1].cancelled)
    finally:
        win.dispose()
        win.close()
        voice_qapp.processEvents()
