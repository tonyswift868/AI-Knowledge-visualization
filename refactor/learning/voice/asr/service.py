"""Qt facade for voice input only; never sends text or downloads a model."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal

from .engine import AsrEngine
from .errors import AsrError
from .glossary import correct
from .model_manager import ModelManager
from .recorder import AudioRecorder


class AsrService(QObject):
    state_changed = Signal(str)
    partial_text = Signal(str)
    final_text = Signal(str)
    # Emitted immediately before its text signal: raw whole transcript and
    # [(start, end, original, replacement), ...], including [] to clear UI undo.
    # Positions refer to raw Python characters, not to the corrected output.
    corrections_changed = Signal(str, object)
    error = Signal(str, str)

    def __init__(self, data_root: Path, parent=None, *, model_manager=None, engine_factory=None, recorder_factory=None):
        super().__init__(parent)
        self.model_manager = model_manager or ModelManager(data_root)
        self._engine_factory = engine_factory or AsrEngine
        self._recorder_factory = recorder_factory or AudioRecorder
        self._engine = self._recorder = self._future = None
        self._state = "idle"
        self._last_text = ""
        self._last_raw_text = ""
        self._last_replacements = []
        self._glossary = ()
        self._protected_terms = ()
        self._disposed = False
        self._timer = QTimer(self)
        self._timer.setInterval(400)
        self._timer.timeout.connect(self._poll)
        lifetime = self._lifetime = {"engine": None}
        self.destroyed.connect(lambda: lifetime["engine"].cancel() if lifetime["engine"] is not None else None)

    @property
    def state(self) -> str:
        return self._state

    def _set_state(self, state: str) -> None:
        if self._state != state:
            self._state = state
            self.state_changed.emit(state)

    def set_glossary(self, terms, *, protected_terms=()) -> None:
        """Replace the caller's course glossary; an empty iterable disables it.

        Update an active transcript immediately. Finished/edited input is not
        rewritten when the caller switches courses. Call on the QObject thread.
        """
        if isinstance(terms, (str, bytes)):
            raise TypeError("terms must be an iterable of strings")
        terms = tuple(terms)
        correct("", terms)  # Validate/prepare before changing the active glossary.
        self._glossary = terms
        self._protected_terms = tuple(protected_terms)
        if not self._disposed and self._engine is not None:
            self._publish_partial(self._engine.snapshot().text)

    def _publish_partial(self, raw: str) -> None:
        text, replacements = correct(raw, self._glossary, protect_existing=False, protected_terms=self._protected_terms,lesson_match=True)
        if (raw, text, replacements) != (self._last_raw_text, self._last_text, self._last_replacements):
            self._last_raw_text, self._last_text = raw, text
            self._last_replacements = replacements
            self.corrections_changed.emit(raw, list(replacements))
            self.partial_text.emit(text)

    def _reset_text(self) -> None:
        self._last_raw_text = self._last_text = ""
        self._last_replacements = []
        self.corrections_changed.emit("", [])
        self.partial_text.emit("")

    def start(self) -> None:
        if self._disposed or self._state in {"recording", "finishing"}:
            return
        status = self.model_manager.status()
        if not status["installed"]:
            self._fail("ASR_MODEL_REQUIRED", "使用语音前需要下载本机识别模型，请确认模型大小后下载。")
            return
        self._reset_text()
        try:
            self._engine = self._engine_factory(Path(status["path"]))
            self._lifetime["engine"] = self._engine
            self._recorder = self._recorder_factory(self)
            self._recorder.audio_ready.connect(self._feed)
            self._recorder.error.connect(self._fail)
            self._set_state("recording")
            if self._state != "recording":
                return
            self._recorder.start()
            if self._engine is not None:
                self._timer.start()
        except AsrError as exc:
            self._fail(exc.code, exc.message)
        except Exception:
            self._fail("ASR_ENGINE_FAILED", "语音输入启动失败，请重试。")

    def _feed(self, pcm: bytes) -> None:
        if self._engine is None or self._state not in {"recording", "finishing"}:
            return
        try:
            self._engine.feed(pcm)
        except AsrError as exc:
            self._fail(exc.code, exc.message)

    def _release_recorder(self) -> None:
        recorder, self._recorder = self._recorder, None
        if recorder is not None:
            recorder.audio_ready.disconnect(self._feed)
            recorder.error.disconnect(self._fail)
            recorder.cancel()
            recorder.deleteLater()

    def stop(self) -> None:
        if self._disposed or self._state != "recording":
            return
        self._set_state("finishing")
        if self._recorder is not None:
            # Drain capture/converter before placing the engine's finish marker.
            self._recorder.stop()
            self._release_recorder()
        if self._engine is not None:
            self._future = self._engine.finish()
            self._timer.start()

    def _poll(self) -> None:
        if self._disposed or self._engine is None:
            return
        failure = self._engine.failure
        if failure is not None:
            self._fail(failure.code, failure.message)
            return
        # The whole-recording final replaces the preview. Do not correct and
        # publish it as a partial, then do the same expensive correction again.
        # Snapshot first: final may become ready while this snapshot is read.
        preview = self._engine.snapshot().text
        if self._future is None or not self._future.done():
            self._publish_partial(preview)
        if self._future is not None and self._future.done():
            try:
                raw = self._future.result()
            except Exception:
                self._fail("ASR_ENGINE_FAILED", "最后一句语音识别未完成，请重试。")
                return
            self._timer.stop()
            self._engine = self._future = None
            self._lifetime["engine"] = None
            self._set_state("idle")
            text, replacements = correct(raw, self._glossary, protect_existing=False, protected_terms=self._protected_terms,lesson_match=True)
            self._last_raw_text, self._last_text = raw, text
            self._last_replacements = replacements
            self.corrections_changed.emit(raw, list(replacements))
            self.final_text.emit(text)

    def _fail(self, code: str, message: str) -> None:
        if self._disposed:
            return
        self._clear()
        self._set_state("error")
        self.error.emit(code, message)

    def _clear(self) -> None:
        self._timer.stop()
        engine, self._engine = self._engine, None
        self._lifetime["engine"] = None
        self._future = None
        if engine is not None:
            engine.cancel()
        self._release_recorder()

    def cancel(self) -> None:
        self._clear()
        self._reset_text()
        self._set_state("idle")

    def dispose(self) -> None:
        if not self._disposed:
            self.cancel()
            self._disposed = True
