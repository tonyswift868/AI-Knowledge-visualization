import numpy as np
from PySide6.QtMultimedia import QAudioFormat
import pytest

from refactor.learning.voice.asr.recorder import AudioRecorder, PcmConverter
from refactor.learning.voice.asr.errors import AsrError


@pytest.mark.parametrize("rate,channels,format,dtype", [
    (48000, 2, QAudioFormat.SampleFormat.Float, "<f4"),
    (44100, 1, QAudioFormat.SampleFormat.Int16, "<i2"),
    (8000, 1, QAudioFormat.SampleFormat.Int32, "<i4"),
    (16000, 1, QAudioFormat.SampleFormat.UInt8, "u1"),
])
def test_chunk_boundaries_do_not_change_resampling(rate, channels, format, dtype):
    wave = 0.5 * np.sin(np.arange(rate) * (2 * np.pi * 440 / rate))
    scales = {"<f4": 1, "<i2": 32768, "<i4": 2147483648, "u1": 128}
    wave = wave * scales[dtype] + (128 if dtype == "u1" else 0)
    raw = np.repeat(wave[:, None], channels, axis=1).astype(dtype).tobytes()
    whole = PcmConverter(rate, channels, format)
    expected = whole.convert(raw) + whole.flush()
    fragmented = PcmConverter(rate, channels, format)
    actual = b"".join(fragmented.convert(raw[i:i + 713]) for i in range(0, len(raw), 713)) + fragmented.flush()
    assert actual == expected
    assert len(actual) == 32000
    samples = np.frombuffer(actual, dtype="<i2")
    assert 10000 < samples.max() < 17000


def test_antialias_downsampling_rejects_out_of_band_tone():
    rate = 48000
    def amplitude(freq):
        raw = (np.sin(np.arange(rate) * 2 * np.pi * freq / rate) * .5).astype("<f4").tobytes()
        converter = PcmConverter(rate, 1, QAudioFormat.SampleFormat.Float)
        samples = np.frombuffer(converter.convert(raw) + converter.flush(), dtype="<i2")[100:]
        return np.sqrt(np.mean(samples.astype(float) ** 2))
    assert amplitude(12000) < amplitude(1000) / 30


def test_permission_error_precedes_device_open(qt_app, monkeypatch):
    from refactor.learning.voice.asr import recorder as module
    monkeypatch.setattr(module, "_permission_denied", lambda: True)
    monkeypatch.setattr(module.QMediaDevices, "defaultAudioInput", lambda: pytest.fail("must not enumerate/open device"))
    recorder = AudioRecorder()
    with pytest.raises(AsrError) as error:
        recorder.start()
    assert error.value.code == "ASR_MIC_PERMISSION"


def test_missing_default_microphone(qt_app, monkeypatch):
    from refactor.learning.voice.asr import recorder as module
    from PySide6.QtMultimedia import QAudioDevice
    monkeypatch.setattr(module, "_permission_denied", lambda: False)
    monkeypatch.setattr(module.QMediaDevices, "defaultAudioInput", QAudioDevice)
    recorder = AudioRecorder()
    with pytest.raises(AsrError) as error:
        recorder.start()
    assert error.value.code == "ASR_MIC_UNAVAILABLE"


def test_qt611_enums_capture_fallback_and_device_loss(qt_app, monkeypatch):
    from PySide6.QtCore import QObject, Signal
    from PySide6.QtMultimedia import QtAudio
    from refactor.learning.voice.asr import recorder as module
    preferred = QAudioFormat()
    preferred.setSampleRate(48000)
    preferred.setChannelCount(2)
    preferred.setSampleFormat(QAudioFormat.SampleFormat.Float)
    class Device:
        def isNull(self): return False
        def isFormatSupported(self, fmt): return fmt.sampleRate() == 48000
        def preferredFormat(self): return preferred
    class Stream(QObject):
        readyRead = Signal()
        def readAll(self): return (np.ones((4800, 2), dtype="<f4") * .25).tobytes()
    class Source(QObject):
        stateChanged = Signal(object)
        def __init__(self, device, fmt, parent):
            super().__init__(parent)
            assert fmt.sampleRate() == 48000
            self.stream = Stream(self)
            self.problem = QtAudio.Error.NoError
        def setBufferSize(self, size): pass
        def start(self): return self.stream
        def error(self): return self.problem
        def state(self): return QtAudio.State.StoppedState
        def stop(self): self.stateChanged.emit(QtAudio.State.StoppedState)
    monkeypatch.setattr(module, "_permission_denied", lambda: False)
    monkeypatch.setattr(module.QMediaDevices, "defaultAudioInput", Device)
    monkeypatch.setattr(module, "QAudioSource", Source)
    recorder = AudioRecorder()
    audio, errors = [], []
    recorder.audio_ready.connect(audio.append)
    recorder.error.connect(lambda *error: errors.append(error))
    recorder.start()  # QAudio.Error.NoError incorrectly differs in PySide6 6.11.
    recorder._device.readyRead.emit()
    assert audio and len(audio[0]) == 3200
    recorder._source.problem = QtAudio.Error.IOError
    recorder._source.stateChanged.emit(QtAudio.State.StoppedState)
    assert errors[-1][0] == "ASR_MIC_UNAVAILABLE"
    recorder.cancel()
    assert len(audio) == 1  # Cancel must not emit an extra tail.
