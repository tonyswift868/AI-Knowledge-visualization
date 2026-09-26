"""Offline integration: local Windows Huihui TTS fixture, exactly five seconds.

The sample says: 今天我们学习物理，牛顿第一定律。
It was generated locally with System.Speech (Huihui Desktop, rate 0), then
linearly stretched from 4.26 to 5.00 seconds; no cloud TTS or downloaded audio.
Set MENTORFLOW_ASR_TEST_DATA_ROOT to an installed test data_root to run elsewhere.
"""
import os
from pathlib import Path
from threading import get_ident
import time
import wave

from PySide6.QtCore import QObject, QTimer, Signal
import pytest

from refactor.learning.voice.asr.engine import AsrEngine
from refactor.learning.voice.asr.model_manager import ModelManager
from refactor.learning.voice.asr.service import AsrService


SAMPLE = Path(__file__).parent / "samples" / "chinese_5s.wav"


@pytest.fixture
def real_model():
    default = Path(__file__).resolve().parents[4] / "temp" / "T05-data"
    manager = ModelManager(Path(os.environ.get("MENTORFLOW_ASR_TEST_DATA_ROOT", default)))
    if not manager.status()["installed"]:
        pytest.skip("本机测试模型未安装；指定 MENTORFLOW_ASR_TEST_DATA_ROOT，测试不会下载模型")
    return manager


def read_pcm():
    with wave.open(str(SAMPLE), "rb") as reader:
        assert (reader.getframerate(), reader.getnchannels(), reader.getsampwidth()) == (16000, 1, 2)
        assert reader.getnframes() == 80000
        return reader.readframes(reader.getnframes())


def test_real_five_second_chinese_and_worker_thread(real_model, record_property):
    engine = AsrEngine(real_model.path)
    try:
        engine.feed(read_pcm())
        text = engine.finish().result(timeout=30)
        assert "物理" in text and "牛顿" in text
        assert engine.worker_thread_id != get_ident()
        assert engine.wait_closed()
        record_property("model_load_seconds", engine.load_seconds)
        record_property("decode_seconds", sum(engine.decode_seconds))
        print(f"\nT05 real: load={engine.load_seconds:.3f}s decode={sum(engine.decode_seconds):.3f}s text={text}")
    finally:
        engine.cancel()


class FileRecorder(QObject):
    audio_ready = Signal(bytes)
    error = Signal(str, str)
    def __init__(self, parent=None):
        super().__init__(parent)
        self.raw = read_pcm()
        self.offset = 0
        self.timer = QTimer(self)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self.tick)
    def start(self):
        self.timer.start()
    def tick(self):
        chunk = self.raw[self.offset:self.offset + 3200]
        self.offset += len(chunk)
        if chunk:
            self.audio_ready.emit(chunk)
        if self.offset == len(self.raw):
            self.timer.stop()
    def stop(self):
        self.timer.stop()
    cancel = stop


def test_real_paced_audio_updates_partial_and_final(qt_app, real_model, record_property):
    service = AsrService(real_model.path.parents[2], model_manager=real_model, recorder_factory=FileRecorder)
    partials, finals, errors = [], [], []
    started = time.monotonic()
    service.partial_text.connect(lambda text: partials.append((time.monotonic() - started, text)) if text else None)
    service.final_text.connect(finals.append)
    service.error.connect(lambda *error: errors.append(error))
    try:
        service.start()
        recorder = service._recorder
        deadline = time.monotonic() + 15
        while recorder.offset < len(recorder.raw) and time.monotonic() < deadline and not errors:
            qt_app.processEvents()
            time.sleep(.01)  # Release the GIL, as Qt's normal app.exec() does.
        service.stop()
        while not finals and not errors and time.monotonic() < deadline:
            qt_app.processEvents()
            time.sleep(.01)
        assert not errors
        assert finals and "物理" in finals[0] and "牛顿" in finals[0]
        assert len({text for _, text in partials}) >= 2
        assert partials[0][0] < 5
        assert service.state == "idle"
        record_property("first_text_seconds_from_start", partials[0][0])
        print(f"\nT05 paced: first_text={partials[0][0]:.3f}s updates={len(partials)} final={finals[0]}")
    finally:
        service.dispose()
