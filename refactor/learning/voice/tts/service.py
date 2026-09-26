"""Qt-thread playback with one background producer and a two-sentence window.

A single QAudioSink stays open for the utterance. PCM for sentence n+1 is
written directly after n, without player/file reopen gaps. stop() resets the
device immediately, invalidates the generation, and discards all queued PCM.
"""
from __future__ import annotations

from collections import deque
from queue import Empty, Queue
import threading

from PySide6.QtCore import QCoreApplication, QObject, QTimer, Signal, Slot
from PySide6.QtMultimedia import QAudio, QAudioFormat, QAudioSink, QMediaDevices

from .config import TtsCancelled, TtsError
from .runtime import TtsRuntime
from .text_prep import split_sentences

# Some PySide6 builds return QtAudio.Error while QAudio.Error is a distinct
# deprecated enum type. Compare the stable Qt values, not enum identities.
_PLAYABLE_ERRORS = {QAudio.Error.NoError.value, QAudio.Error.UnderrunError.value}


class _Producer:
    def __init__(self, runtime):
        self.runtime = runtime
        self.events = Queue()
        self.condition = threading.Condition()
        self.pending = None
        self.active_cancel = None
        self.closed = False
        self.thread = threading.Thread(target=self.run, name="mf-tts-producer", daemon=True)
        self.thread.start()

    def submit(self, token, sentences):
        cancel, slots = threading.Event(), threading.Semaphore(2)
        with self.condition:
            if self.pending:
                self.pending[2].set()
            if self.active_cancel:
                self.active_cancel.set()
            self.pending = token, sentences, cancel, slots
            self.condition.notify()
        return cancel

    def cancel(self):
        with self.condition:
            if self.pending:
                self.pending[2].set()
            self.pending = None
            if self.active_cancel:
                self.active_cancel.set()
        self.runtime.cancel()

    def close(self):
        self.cancel()
        with self.condition:
            self.closed = True
            self.condition.notify()

    def run(self):
        while True:
            with self.condition:
                self.condition.wait_for(lambda: self.closed or self.pending is not None)
                if self.closed:
                    return
                token, sentences, cancel, slots = self.pending
                self.pending = None
                self.active_cancel = cancel
            try:
                if sentences is None:
                    value = self.runtime.warm_up(cancel)
                    if not cancel.is_set():
                        self.events.put((token, "ready", value))
                    continue
                for index, text in enumerate(sentences):
                    while not slots.acquire(timeout=.025):
                        if cancel.is_set():
                            raise TtsCancelled()
                    if cancel.is_set():
                        raise TtsCancelled()
                    def receive(audio):
                        if cancel.is_set():
                            raise TtsCancelled()
                        self.events.put((token, "audio", (index, text, audio, slots)))
                    stream = getattr(self.runtime, "stream_synthesize", None)
                    if stream:
                        stream(text, receive, cancel=cancel)
                    else:
                        receive(self.runtime.synthesize(text, cancel=cancel))
                if not cancel.is_set():
                    self.events.put((token, "done", None))
            except TtsCancelled:
                pass
            except Exception as exc:
                if not cancel.is_set():
                    failure = (exc.code, exc.message) if isinstance(exc, TtsError) else ("TTS_FAILED", "本地朗读失败，请查看引擎日志。")
                    self.events.put((token, "error", failure))
            finally:
                with self.condition:
                    if self.active_cancel is cancel:
                        self.active_cancel = None


class TtsService(QObject):
    state_changed = Signal(str)
    sentence_started = Signal(int, str)
    error = Signal(str, str)

    def __init__(self, parent=None, *, data_root=None, config=None, runtime=None, sink_factory=None):
        super().__init__(parent)
        self.runtime = runtime if runtime is not None else TtsRuntime(config, data_root=data_root)
        self._producer = _Producer(self.runtime)
        self._sink_factory = sink_factory
        self._sink = self._output = None
        self._token = 0
        self._closed = False
        self._pumping = False
        self.state = "idle"
        self.last_metrics = []
        self._chunks, self._boundaries = deque(), deque()
        self._done = False
        self._scheduled_frames = 0
        self._rate = None
        self._timer = QTimer(self)
        self._timer.setInterval(5)
        self._timer.timeout.connect(self._pump)
        self._timer.start()
        app = QCoreApplication.instance()
        if app:
            app.aboutToQuit.connect(self.close)
        # These callbacks do not touch the already-destroyed QObject.
        producer, owned_runtime = self._producer, self.runtime
        def release_owned(_object=None):
            producer.close()
            owned_runtime.close()
        self.destroyed.connect(release_owned)

    def _state(self, value):
        if value != self.state:
            self.state = value
            self.state_changed.emit(value)

    @Slot()
    def warm_up(self):
        if self._closed or self.state in {"warming", "synthesizing", "playing"}:
            return
        self.stop()
        self._producer.submit(self._token, None)
        self._state("warming")

    @Slot(str)
    def speak(self, text):
        if self._closed:
            return
        self.stop()
        try:
            sentences = split_sentences(text)
        except (ValueError, TypeError):
            self._fail("INVALID_TEXT", "朗读文本无效或过长，请分段提交。")
            return
        if not sentences:
            self._state("idle")
            return
        self.last_metrics = []
        self._producer.submit(self._token, sentences)
        self._state("synthesizing")

    @Slot()
    def stop(self):
        self._token += 1
        self._producer.cancel()
        self._reset_audio()
        while True:
            try:
                self._producer.events.get_nowait()
            except Empty:
                break
        if not self._closed:
            self._state("stopped")

    def _reset_audio(self):
        if self._sink is not None:
            self._sink.reset()
            self._sink.deleteLater()
        self._sink = self._output = None
        self._chunks.clear()
        self._boundaries.clear()
        self._done = False
        self._rate = None
        self._scheduled_frames = 0

    def _ensure_output(self, rate):
        if self._sink is not None:
            if rate != self._rate:
                raise TtsError("AUDIO_FORMAT", "本次朗读的音频采样率发生变化。")
            return
        audio_format = QAudioFormat()
        audio_format.setSampleRate(rate)
        audio_format.setChannelCount(1)
        audio_format.setSampleFormat(QAudioFormat.SampleFormat.Int16)
        if self._sink_factory:
            self._sink = self._sink_factory(audio_format, self)
        else:
            device = QMediaDevices.defaultAudioOutput()
            if device.isNull() or not device.isFormatSupported(audio_format):
                raise TtsError("AUDIO_DEVICE", "没有可用的音频输出设备，或设备不支持朗读格式。")
            self._sink = QAudioSink(device, audio_format, self)
        self._sink.setBufferSize(int(rate * 2 * .08))
        self._rate = rate
        opening = self._sink
        output = opening.start()
        # Opening a Windows audio device may dispatch GUI events, including a
        # user's stop/new speak. Do not restore an output from that old turn.
        if self._sink is not opening:
            return
        self._output = output
        if self._output is None or self._sink.error().value not in _PLAYABLE_ERRORS:
            raise TtsError("AUDIO_DEVICE", "无法打开音频输出设备（" + self._sink.error().name + "）。")

    def _fail(self, code, message):
        self.stop()
        self._state("error")
        self.error.emit(code, message)

    def _pump(self):
        if self._closed or self._pumping:
            return
        self._pumping = True
        try:
            self._pump_audio()
        finally:
            self._pumping = False

    def _pump_audio(self):
        token = self._token
        try:
            while True:
                try:
                    event_token, event, value = self._producer.events.get_nowait()
                except Empty:
                    break
                if event_token != token:
                    continue
                if event == "error":
                    self._fail(*value)
                    return
                if event == "ready":
                    self._state("ready")
                    return
                if event == "done":
                    self._done = True
                if event == "audio":
                    index, text, audio, slots = value
                    self._ensure_output(audio.sample_rate)
                    if token != self._token:
                        return
                    frames = len(audio.pcm) // 2
                    if not self._boundaries or self._boundaries[-1]["index"] != index:
                        boundary = {"index": index, "text": text, "slots": slots, "written": False,
                            "start": self._scheduled_frames, "end": self._scheduled_frames, "started": False,
                            "finished": False}
                        self._boundaries.append(boundary)
                        self.last_metrics.append({"index": index, "text": text, "synthesis_seconds": 0,
                                                  "audio_seconds": 0})
                    boundary = self._boundaries[-1]
                    self._scheduled_frames += frames
                    boundary["end"] = self._scheduled_frames
                    boundary["finished"] = audio.final
                    if audio.pcm:
                        self._chunks.append({"audio": audio, "offset": 0, "boundary": boundary})
                    self.last_metrics[-1]["synthesis_seconds"] += audio.synthesis_seconds
                    self.last_metrics[-1]["audio_seconds"] += audio.duration_seconds
            if self._sink is None:
                return
            if self._sink.error().value not in _PLAYABLE_ERRORS:
                raise TtsError("AUDIO_PLAYBACK", "音频设备中断，朗读已停止。")
            while self._chunks and self._sink.bytesFree() >= 2:
                segment = self._chunks[0]
                pcm = segment["audio"].pcm
                size = min(len(pcm) - segment["offset"], self._sink.bytesFree()) // 2 * 2
                written = self._output.write(pcm[segment["offset"]:segment["offset"] + size])
                if written < 0:
                    raise TtsError("AUDIO_PLAYBACK", "无法向音频设备写入声音。")
                if written == 0:
                    break
                segment["offset"] += written
                segment["boundary"]["written"] = True
                if segment["offset"] == len(pcm):
                    self._chunks.popleft()
            # Qt truncates to integer microseconds. Rounding back to frames
            # avoids leaving the final fractional frame pending forever.
            played = round(self._sink.processedUSecs() * self._rate / 1000000)
            for segment in self._boundaries:
                if played > segment["start"] and segment["written"] and not segment["started"]:
                    segment["started"] = True
                    self._state("playing")
                    if token != self._token:
                        return
                    self.sentence_started.emit(segment["index"], segment["text"])
                    if token != self._token:
                        return
            while self._boundaries and self._boundaries[0]["finished"] and played >= self._boundaries[0]["end"]:
                self._boundaries.popleft()["slots"].release()
            if self._done and not self._boundaries and not self._chunks:
                self._reset_audio()
                self._state("idle")
        except TtsError as exc:
            self._fail(exc.code, exc.message)

    @Slot()
    def close(self):
        if self._closed:
            return
        self.stop()
        self._closed = True
        self._timer.stop()
        self._producer.close()
        self.runtime.close()
        self._state("closed")
