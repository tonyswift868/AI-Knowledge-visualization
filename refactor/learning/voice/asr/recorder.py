"""Default Qt microphone with chunk-continuous conversion to mono s16/16k."""
from __future__ import annotations

import sys

import numpy as np
from PySide6.QtCore import QCoreApplication, QMicrophonePermission, QObject, Qt, Signal, Slot
from PySide6.QtMultimedia import QtAudio, QAudioFormat, QAudioSource, QMediaDevices

from .errors import AsrError


class PcmConverter:
    """Downmix + anti-alias FIR + phase-continuous linear rate conversion.

    Fractional phase and partial frames survive readAll() boundaries. No scipy,
    external converter, subprocess, or per-chunk duration drift is required.
    """
    def __init__(self, rate: int, channels: int, sample_format: QAudioFormat.SampleFormat):
        formats = {QAudioFormat.SampleFormat.UInt8: ("u1", 1),
                   QAudioFormat.SampleFormat.Int16: ("<i2", 2),
                   QAudioFormat.SampleFormat.Int32: ("<i4", 4),
                   QAudioFormat.SampleFormat.Float: ("<f4", 4)}
        if rate <= 0 or channels <= 0 or sample_format not in formats:
            raise AsrError("ASR_MIC_UNAVAILABLE", "麦克风音频格式不受支持。")
        self.rate, self.channels, self.format = rate, channels, sample_format
        self.dtype, width = formats[sample_format]
        self.frame_bytes = width * channels
        self._pending = b""
        self._input_count = self._output_count = 0
        self._tail = np.empty(0, dtype=np.float32)
        self._history = np.zeros(62, dtype=np.float64)
        self._filter = None
        if rate > 16000:
            cutoff = 0.45 * 16000 / rate
            taps = np.arange(63) - 31
            kernel = 2 * cutoff * np.sinc(2 * cutoff * taps) * np.hamming(63)
            self._filter = kernel / kernel.sum()

    @staticmethod
    def _encode(samples) -> bytes:
        return np.clip(np.rint(samples * 32768), -32768, 32767).astype("<i2").tobytes()

    def convert(self, raw: bytes) -> bytes:
        raw = self._pending + raw
        end = len(raw) - len(raw) % self.frame_bytes
        self._pending = raw[end:]
        if not end:
            return b""
        samples = np.frombuffer(raw[:end], dtype=self.dtype).astype(np.float64)
        if self.format == QAudioFormat.SampleFormat.UInt8:
            samples = (samples - 128) / 128
        elif self.format == QAudioFormat.SampleFormat.Int16:
            samples /= 32768
        elif self.format == QAudioFormat.SampleFormat.Int32:
            samples /= 2147483648
        samples = np.nan_to_num(samples, nan=0.0, posinf=1.0, neginf=-1.0)
        samples = samples.reshape(-1, self.channels).mean(axis=1)
        if self._filter is not None:
            filtered_input = np.concatenate((self._history, samples))
            samples = np.convolve(filtered_input, self._filter, mode="valid")
            self._history = filtered_input[-62:].copy()
        total = self._input_count + len(samples)
        start = self._input_count - len(self._tail)
        values = np.concatenate((self._tail, samples))
        output_end = ((total - 1) * 16000) // self.rate + 1
        positions = np.arange(self._output_count, output_end, dtype=np.float64) * self.rate / 16000
        output = np.interp(positions, np.arange(start, total), values)
        self._tail = samples[-1:].copy()
        self._input_count, self._output_count = total, output_end
        return self._encode(output)

    def flush(self) -> bytes:
        count = max(0, round(self._input_count * 16000 / self.rate) - self._output_count)
        self._output_count += count
        self._pending = b""
        return self._encode(np.repeat(self._tail, count))


def _permission_denied() -> bool:
    app = QCoreApplication.instance()
    if app is not None and app.checkPermission(QMicrophonePermission()) != Qt.PermissionStatus.Granted:
        return True
    if sys.platform == "win32":
        import winreg
        key = r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\microphone"
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            for subkey in (key, key + r"\NonPackaged"):
                try:
                    with winreg.OpenKey(hive, subkey) as handle:
                        if winreg.QueryValueEx(handle, "Value")[0] == "Deny":
                            return True
                except OSError:
                    pass
    return False


class AudioRecorder(QObject):
    audio_ready = Signal(bytes)
    error = Signal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._source = None
        self._device = None
        self._converter = None
        self._stopping = False
        self._starting = False
        self._read_connected = False

    def start(self) -> None:
        if self._source is not None:
            return
        if _permission_denied():
            raise AsrError("ASR_MIC_PERMISSION", "请在系统隐私设置中允许此应用使用麦克风，然后重试。")
        device = QMediaDevices.defaultAudioInput()
        if device.isNull():
            raise AsrError("ASR_MIC_UNAVAILABLE", "没有可用的默认麦克风，请连接或选择输入设备。")
        desired = QAudioFormat()
        desired.setSampleRate(16000)
        desired.setChannelCount(1)
        desired.setSampleFormat(QAudioFormat.SampleFormat.Int16)
        actual = desired if device.isFormatSupported(desired) else device.preferredFormat()
        if not device.isFormatSupported(actual):
            raise AsrError("ASR_MIC_UNAVAILABLE", "默认麦克风没有可用的采集格式。")
        self._converter = PcmConverter(actual.sampleRate(), actual.channelCount(), actual.sampleFormat())
        self._stopping = False
        self._source = QAudioSource(device, actual, self)
        self._source.setBufferSize(actual.bytesForDuration(100000))
        self._source.stateChanged.connect(self._state_changed)
        self._starting = True
        try:
            self._device = self._source.start()
        finally:
            self._starting = False
        if self._device is None or self._source.error() != QtAudio.Error.NoError:
            self.stop()
            code = "ASR_MIC_PERMISSION" if _permission_denied() else "ASR_MIC_UNAVAILABLE"
            raise AsrError(code, "无法开启默认麦克风，请检查系统授权或设备是否被占用。")
        self._device.readyRead.connect(self._read)
        self._read_connected = True

    def _read(self) -> None:
        if self._device is None:
            return
        try:
            pcm = self._converter.convert(bytes(self._device.readAll()))
            if pcm:
                self.audio_ready.emit(pcm)
        except Exception:
            self.error.emit("ASR_MIC_UNAVAILABLE", "麦克风音频读取失败，请检查设备后重试。")

    @Slot()
    def _state_changed(self) -> None:
        if self._starting or self._stopping or self._source is None:
            return
        # PySide6 6.11 still names this signal argument QAudio::State, while
        # state() returns QtAudio.State. A zero-argument slot avoids its broken
        # enum conversion; use the current state directly.
        state = self._source.state()
        if state == QtAudio.State.StoppedState and self._source.error() != QtAudio.Error.NoError:
            code = "ASR_MIC_PERMISSION" if _permission_denied() else "ASR_MIC_UNAVAILABLE"
            self.error.emit(code, "麦克风已不可用，请检查权限或重新连接设备。")

    def stop(self) -> None:
        self._stop(drain=True)

    def _stop(self, *, drain: bool) -> None:
        self._stopping = True
        source, device, converter = self._source, self._device, self._converter
        self._source = self._device = self._converter = None
        if device is not None:
            if self._read_connected:
                device.readyRead.disconnect(self._read)
                self._read_connected = False
            if drain and converter is not None:
                try:
                    pcm = converter.convert(bytes(device.readAll())) + converter.flush()
                    if pcm:
                        self.audio_ready.emit(pcm)
                except Exception:
                    self.error.emit("ASR_MIC_UNAVAILABLE", "录音收尾读取失败，请检查麦克风设备。")
        if source is not None:
            source.stop()
            source.deleteLater()

    def cancel(self) -> None:
        self._stop(drain=False)
