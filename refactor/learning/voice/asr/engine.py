"""VAD endpointing and repeated offline recognition, all on one worker thread."""
from __future__ import annotations

from collections import deque
from concurrent.futures import Future
from dataclasses import dataclass
from pathlib import Path
import threading
import time

import numpy as np

from .errors import AsrError
from .model_manager import verify_model_path

SAMPLE_RATE = 16000
PARTIAL_INTERVAL = 0.4
END_SILENCE_SECONDS = 0.8
MAX_BUFFERED_BYTES = SAMPLE_RATE * 2 * 30


@dataclass(frozen=True)
class Transcript:
    sentences: tuple[str, ...]
    partial: str

    @property
    def text(self) -> str:
        return " ".join(part for part in (*self.sentences, self.partial) if part)


class _SenseVoiceBackend:
    def __init__(self, path: Path, cancel: threading.Event):
        # Only local files; neither sherpa nor this module downloads models.
        verify_model_path(path, cancel)
        import sherpa_onnx

        started = time.perf_counter()
        # Retained from the old worker: CPU SenseVoice int8, auto language, ITN.
        self.recognizer = sherpa_onnx.OfflineRecognizer.from_sense_voice(
            model=str(path / "model.int8.onnx"), tokens=str(path / "tokens.txt"),
            num_threads=4, provider="cpu", language="auto", use_itn=True,
        )
        config = sherpa_onnx.VadModelConfig()
        config.sample_rate = SAMPLE_RATE
        config.num_threads = 1
        config.silero_vad.model = str(path / "silero_vad.onnx")
        config.silero_vad.threshold = 0.5
        config.silero_vad.min_speech_duration = 0.25
        config.silero_vad.min_silence_duration = END_SILENCE_SECONDS
        config.silero_vad.max_speech_duration = 20
        self.window_size = config.silero_vad.window_size
        self.vad = sherpa_onnx.VoiceActivityDetector(config, buffer_size_in_seconds=30)
        self.load_seconds = time.perf_counter() - started
        self.first_speech_sample = None

    def _completed(self) -> list[np.ndarray]:
        result = []
        while not self.vad.empty():
            result.append(np.asarray(self.vad.front.samples, dtype=np.float32).copy())
            self.vad.pop()
        return result

    def accept(self, samples: np.ndarray) -> list[np.ndarray]:
        self.vad.accept_waveform(samples)
        if self.first_speech_sample is None and self.vad.is_speech_detected():
            self.first_speech_sample = self.vad.current_segment.start
        return self._completed()

    def partial_audio(self) -> np.ndarray:
        if not self.vad.is_speech_detected():
            return np.empty(0, dtype=np.float32)
        return np.asarray(self.vad.current_segment.samples, dtype=np.float32)

    def flush(self) -> list[np.ndarray]:
        self.vad.flush()
        return self._completed()

    def decode(self, samples: np.ndarray) -> str:
        stream = self.recognizer.create_stream()
        stream.accept_waveform(SAMPLE_RATE, samples)
        self.recognizer.decode_stream(stream)
        return str(stream.result.text or "").strip()


class AsrEngine:
    """One recording session. feed/finish/snapshot never run inference.

    finish() returns a Future for the whole transcript; take_finalized() drains
    individual VAD-finalized sentences. cancel() discards all late results.
    Create another engine for the next recording. The backend seam is for local
    deterministic tests, not a remote recognizer.
    """
    def __init__(self, model_path: Path, *, backend_factory=None, partial_interval: float = PARTIAL_INTERVAL):
        self._path = Path(model_path)
        self._factory = backend_factory or _SenseVoiceBackend
        self._partial_interval = max(0.05, float(partial_interval))
        self._condition = threading.Condition()
        self._queue: deque[bytes | None] = deque()
        self._buffered = 0
        self._cancel = threading.Event()
        self._closed = threading.Event()
        self.ready = threading.Event()
        self._accepting = True
        self._sentences: list[str] = []
        self._finalized: deque[str] = deque()
        self._partial = ""
        self._failure: AsrError | None = None
        self._result: Future[str] = Future()
        self.load_seconds = 0.0
        self.decode_seconds: list[float] = []
        self.worker_thread_id: int | None = None
        self.first_speech_sample: int | None = None
        self._thread = threading.Thread(target=self._run, name="mf-sensevoice", daemon=True)
        self._thread.start()

    @property
    def failure(self) -> AsrError | None:
        with self._condition:
            return self._failure

    def feed(self, pcm_int16_16k: bytes) -> None:
        if len(pcm_int16_16k) % 2:
            raise AsrError("ASR_ENGINE_FAILED", "录音 PCM 数据不完整。")
        if not pcm_int16_16k:
            return
        with self._condition:
            if not self._accepting or self._cancel.is_set():
                return
            if self._failure:
                raise self._failure
            if self._buffered + len(pcm_int16_16k) > MAX_BUFFERED_BYTES:
                raise AsrError("ASR_ENGINE_FAILED", "语音识别处理落后过多，请结束录音后重试。")
            self._queue.append(bytes(pcm_int16_16k))
            self._buffered += len(pcm_int16_16k)
            self._condition.notify()

    def snapshot(self) -> Transcript:
        with self._condition:
            return Transcript(tuple(self._sentences), self._partial)

    def pending_partial(self) -> str:
        return self.snapshot().partial

    def take_finalized(self) -> list[str]:
        with self._condition:
            result = list(self._finalized)
            self._finalized.clear()
            return result

    def finish(self) -> Future[str]:
        with self._condition:
            if self._accepting:
                self._accepting = False
                self._queue.append(None)
                self._condition.notify()
            return self._result

    def cancel(self) -> None:
        with self._condition:
            self._cancel.set()
            self._accepting = False
            self._queue.clear()
            self._buffered = 0
            self._partial = ""
            self._sentences.clear()
            self._finalized.clear()
            self._result.cancel()
            self._condition.notify()

    def wait_closed(self, timeout: float = 5) -> bool:
        """Optional bounded join for tests/shutdown; never needed by the GUI."""
        return self._closed.wait(timeout)

    def _decode(self, backend, audio: np.ndarray, *, final: bool) -> None:
        if self._cancel.is_set() or not len(audio):
            return
        started = time.perf_counter()
        text = backend.decode(audio)
        elapsed = time.perf_counter() - started
        with self._condition:
            if self._cancel.is_set():
                return
            self.decode_seconds.append(elapsed)
            if final:
                if text:
                    self._sentences.append(text)
                    self._finalized.append(text)
                self._partial = ""
            else:
                self._partial = text

    def _run(self) -> None:
        self.worker_thread_id = threading.get_ident()
        try:
            backend = self._factory(self._path, self._cancel)
            self.load_seconds = getattr(backend, "load_seconds", 0.0)
            self.ready.set()
            tail = np.empty(0, dtype=np.float32)
            last_partial_at = 0.0
            last_partial_size = 0
            while not self._cancel.is_set():
                with self._condition:
                    self._condition.wait_for(
                        lambda: self._queue or self._cancel.is_set(), timeout=self._partial_interval
                    )
                    if self._cancel.is_set():
                        break
                    pcm = self._queue.popleft() if self._queue else b""
                    if pcm is not None:
                        self._buffered -= len(pcm)
                if pcm is None:
                    if len(tail):
                        padded = np.pad(tail, (0, backend.window_size - len(tail)))
                        for segment in backend.accept(padded):
                            self._decode(backend, segment, final=True)
                    for segment in backend.flush():
                        self._decode(backend, segment, final=True)
                    with self._condition:
                        if not self._cancel.is_set():
                            self._partial = ""
                            self._result.set_result(" ".join(self._sentences))
                    break
                audio = np.concatenate((tail, np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0))
                end = len(audio) - len(audio) % backend.window_size
                for offset in range(0, end, backend.window_size):
                    if self._cancel.is_set():
                        break
                    for segment in backend.accept(audio[offset:offset + backend.window_size]):
                        self._decode(backend, segment, final=True)
                        last_partial_size = 0
                    self.first_speech_sample = getattr(backend, "first_speech_sample", None)
                tail = audio[end:].copy()
                with self._condition:
                    backlog = bool(self._queue)
                # Coalesce stale partial requests; final sentences are never skipped.
                if not backlog and time.monotonic() - last_partial_at >= self._partial_interval:
                    partial = backend.partial_audio()
                    if len(partial) >= 1600 and len(partial) != last_partial_size:
                        last_partial_at = time.monotonic()
                        self._decode(backend, partial, final=False)
                        last_partial_size = len(partial)
        except Exception as exc:
            with self._condition:
                if not self._cancel.is_set():
                    self._failure = exc if isinstance(exc, AsrError) else AsrError(
                        "ASR_ENGINE_FAILED", f"本机语音识别失败，请重试（{type(exc).__name__}）。"
                    )
                    self._accepting = False
                    self._result.set_exception(self._failure)
        finally:
            with self._condition:
                self._queue.clear()
                self._buffered = 0
            self._closed.set()
