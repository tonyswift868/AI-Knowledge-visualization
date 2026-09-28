"""Qt-thread playback with one background producer and a two-sentence window.

A single QAudioSink stays open for the utterance. PCM for sentence n+1 is
written directly after n, without player/file reopen gaps. stop() resets the
device immediately, invalidates the generation, and discards all queued PCM.
"""
from __future__ import annotations

from collections import deque
from queue import Empty, Queue
import math
import threading
import time

from PySide6.QtCore import QCoreApplication, QObject, QTimer, Signal, Slot
from PySide6.QtMultimedia import QAudio, QAudioFormat, QAudioSink, QMediaDevices

from .config import TtsCancelled, TtsError
from .runtime import TtsRuntime
from .playback import PlaybackMeter
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
        self.input = None
        self.active_cancel = None
        self.closed = False
        self.thread = threading.Thread(target=self.run, name="mf-tts-producer", daemon=True)
        self.thread.start()

    def submit(self, token, sentences, speed=1.0, *, open_input=False):
        cancel, slots = threading.Event(), threading.Semaphore(2)
        with self.condition:
            if self.pending:
                self.pending["cancel"].set()
            if self.active_cancel:
                self.active_cancel.set()
            self.input = self.pending = {"token": token, "sentences": deque(sentences or ()),
                "warm": sentences is None, "cancel": cancel, "slots": slots, "speed": speed,
                "ended": not open_input}
            self.condition.notify_all()
        return cancel

    def append(self, token, sentences):
        with self.condition:
            job = self.input
            if not job or job["token"] != token or job["ended"] or job["cancel"].is_set():
                return False
            job["sentences"].extend(sentences)
            self.condition.notify_all()
            return True

    def finish(self, token):
        with self.condition:
            job = self.input
            if not job or job["token"] != token or job["cancel"].is_set():
                return False
            job["ended"] = True
            self.condition.notify_all()
            return True

    def cancel(self):
        with self.condition:
            if self.pending:
                self.pending["cancel"].set()
            self.pending = None
            self.input = None
            if self.active_cancel:
                self.active_cancel.set()
            self.condition.notify_all()
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
                job = self.pending
                token, cancel, slots = job["token"], job["cancel"], job["slots"]
                self.pending = None
                self.active_cancel = cancel
            try:
                if job["warm"]:
                    value = self.runtime.warm_up(cancel)
                    if not cancel.is_set():
                        self.events.put((token, "ready", value))
                    continue
                if hasattr(self.runtime, "set_speed"):
                    self.runtime.set_speed(job["speed"])
                index = 0
                while True:
                    with self.condition:
                        self.condition.wait_for(lambda: cancel.is_set() or job["sentences"] or job["ended"])
                        if cancel.is_set():
                            raise TtsCancelled()
                        if not job["sentences"]:
                            break
                        text = job["sentences"].popleft()
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
                    index += 1
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
    """Call on the Qt thread. Signals also run on that thread.

    sentence_timing emits dictionaries with utterance_id, index, text, state
    (started/finished/interrupted), start_seconds and end_seconds (None until
    finished). Times are on this utterance's consumed PCM timeline, starting at
    zero, excluding device stalls; no synthesis or wall-clock estimates. An
    interrupted event closes only a sentence that actually started playing.
    mouth_level is a smoothed 0..1 RMS level sampled about every 50 ms against
    QAudioSink.processedUSecs(), with synchronous zero on stop.
    """
    state_changed = Signal(str)
    sentence_started = Signal(int, str)
    sentence_timing = Signal(dict)
    mouth_level = Signal(float)
    error = Signal(str, str)

    def __init__(self, parent=None, *, data_root=None, config=None, runtime=None, sink_factory=None):
        super().__init__(parent)
        self.runtime = runtime if runtime is not None else TtsRuntime(config, data_root=data_root)
        self._producer = _Producer(self.runtime)
        self._sink_factory = sink_factory
        self._sink = self._output = None
        self._token = 0
        self._closed = False
        self._stream_token = None
        self._pumping = False
        self.state = "idle"
        self.last_metrics = []
        self._chunks, self._boundaries = deque(), deque()
        self._done = False
        self._scheduled_frames = 0
        self._rate = None
        self._meter = None
        self._mouth_value = 0.0
        self._mouth_due = 0.0
        self._volume, self._speed = 1.0, 1.0
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
        self._producer.submit(self._token, sentences, self._speed)
        self._state("synthesizing")

    @Slot()
    def start_stream(self):
        """Open one utterance on the Qt thread; append already prepared sentences.

        An empty input queue waits without ending the utterance or reopening the
        device. end_stream() closes input, then all accepted sentences drain.
        """
        if self._closed:
            return
        self.stop()
        self.last_metrics = []
        self._stream_token = self._token
        self._producer.submit(self._token, (), self._speed, open_input=True)
        self._state("synthesizing")

    def append_sentences(self, sentences):
        """Append Python-prepared sentences in order, without resetting playback."""
        if self._closed or self._stream_token != self._token:
            return False
        sentences = tuple(sentences)
        if any(not isinstance(s, str) or not s.strip() or len(s) > 60 for s in sentences):
            raise ValueError("Expected nonempty, prepared sentences of at most 60 characters")
        return self._producer.append(self._token, sentences)

    @Slot()
    def end_stream(self):
        if self._stream_token != self._token:
            return False
        self._stream_token = None
        return self._producer.finish(self._token)

    def set_volume(self, value):
        """Qt-thread control; affects the current sink immediately."""
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 1:
            raise TtsError("INVALID_VOLUME", "音量必须在 0 到 1 之间。")
        self._volume = float(value)
        if self._sink is not None:
            self._sink.setVolume(self._volume)
        if self._volume == 0:
            self._mouth_value = 0.0
            self.mouth_level.emit(0.0)

    def set_speed(self, value):
        """Relative to the selected voice's default; snapshots at next speak."""
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not .5 <= value <= 2:
            raise TtsError("INVALID_SPEED", "语速必须在 0.5 到 2 倍之间。")
        self._speed = float(value)

    @Slot()
    def stop(self):
        interrupted = []
        if self._sink is not None:
            played = round(self._sink.processedUSecs() * self._rate / 1000000)
            interrupted = [self._timing(segment, "interrupted", min(played, segment["end"]))
                           for segment in self._boundaries if segment["started"]]
        self._token += 1
        self._stream_token = None
        token = self._token
        self._producer.cancel()
        had_mouth = self._mouth_value != 0.0
        self._reset_audio()
        while True:
            try:
                self._producer.events.get_nowait()
            except Empty:
                break
        if had_mouth:
            self.mouth_level.emit(0.0)
            if token != self._token:
                return
        for timing in interrupted:
            self.sentence_timing.emit(timing)
            if token != self._token:
                return
        if not self._closed:
            self._state("stopped")

    def _timing(self, segment, state, end=None):
        return {"utterance_id": self._token, "index": segment["index"], "text": segment["text"],
                "state": state, "start_seconds": segment["start"] / self._rate,
                "end_seconds": end / self._rate if end is not None else None}

    def _update_mouth(self, played):
        now = time.monotonic()
        if now >= self._mouth_due:
            # Keep a 20 Hz schedule despite Windows timer quantization, without
            # emitting a burst of catch-up frames after a blocked event loop.
            self._mouth_due = self._mouth_due + .05 if now - self._mouth_due < .05 else now + .05
            self._mouth_value = self._meter.sample(min(played, self._scheduled_frames)) * self._volume
            self.mouth_level.emit(self._mouth_value)

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
        self._meter = None
        self._mouth_value = 0.0
        self._mouth_due = 0.0

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
        if self._volume != 1.0:  # QAudioSink already defaults to 1.0.
            self._sink.setVolume(self._volume)
        self._rate = rate
        self._meter = PlaybackMeter(rate)
        self._mouth_due = time.monotonic() + .05
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
                    if audio.pcm:
                        self._meter.append(self._scheduled_frames, audio.pcm)
                    self._scheduled_frames += frames
                    boundary["end"] = self._scheduled_frames
                    boundary["finished"] = audio.final
                    if audio.pcm:
                        self._chunks.append({"audio": audio, "offset": 0, "boundary": boundary})
                    self.last_metrics[-1]["synthesis_seconds"] += audio.synthesis_seconds
                    self.last_metrics[-1]["audio_seconds"] += audio.duration_seconds
            if self._sink is None:
                if self._done:
                    self._state("idle")
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
            while self._boundaries:
                segment = self._boundaries[0]
                if played > segment["start"] and segment["written"] and not segment["started"]:
                    self._state("playing")
                    if token != self._token:
                        return
                    segment["started"] = True
                    self.sentence_timing.emit(self._timing(segment, "started"))
                    if token != self._token:
                        return
                    self.sentence_started.emit(segment["index"], segment["text"])
                    if token != self._token:
                        return
                if not segment["finished"] or played < segment["end"]:
                    break
                self._boundaries.popleft()
                segment["slots"].release()
                if segment["started"]:
                    self.sentence_timing.emit(self._timing(segment, "finished", segment["end"]))
                    if token != self._token:
                        return
            self._update_mouth(played)
            if token != self._token:
                return
            if self._done and not self._boundaries and not self._chunks:
                had_mouth = self._mouth_value != 0.0
                self._reset_audio()
                if had_mouth:
                    self.mouth_level.emit(0.0)
                    if token != self._token:
                        return
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
