"""Local voice input for QWebChannel; recognition never sends an answer.

``voice.status/download/download_cancel/start/cancel`` run on the Qt thread.
Only explicit ``download(consent=True, job_id=...)`` can access the network.
``stop(session_id=...)`` waits on a bridge worker, dispatching recorder control
to Qt, and returns the same whole transcript as ``voice.final``. Each recording
has a caller-generated session_id; stale cancellation cannot stop a newer one.

Events: voice.model (status/progress), voice.state, voice.partial (whole text),
voice.segment (one VAD-finalized sentence), voice.final, voice.error. Transcript
payloads include raw_text and replacements with original Python-character
offsets (end exclusive, NOT JavaScript UTF-16 offsets). Audio stays in memory.
"""
from __future__ import annotations

from concurrent.futures import Future, TimeoutError
from dataclasses import dataclass, field
import json
from pathlib import Path
import re
import threading
import time

from PySide6.QtCore import QObject, QTimer, Qt, Signal, Slot

from ..voice.asr.engine import AsrEngine
from ..voice.asr.glossary import correct
from ..voice.asr.model_manager import ModelManager, REQUIRED_FILES
from ..voice.storage import choose_installation, storage_root
from ..voice.asr.service import AsrService
from .bridge import BridgeError


def _token(params, name):
    value = params.get(name)
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,64}", value):
        raise BridgeError("BAD_PARAMS", f"缺少有效的 {name}。")
    return value


@dataclass
class _Download:
    job_id: str
    cancel: threading.Event = field(default_factory=threading.Event)
    done: int = 0
    total: int = 0


class VoiceInput(QObject):
    _invoke = Signal(object)
    _progress = Signal(object, int, int)
    _download_done = Signal(object, object)

    def __init__(self, bridge, data_root: Path, parent=None, *, model_manager=None,
                 engine_factory=None, recorder_factory=None):
        super().__init__(parent)
        self.bridge = bridge
        self.manager = model_manager or ModelManager(data_root)
        self._closed = threading.Event()
        self._job = None
        self._session = None
        self._active = False
        self._result = None
        self._engine = None
        self._terms = ()
        self._segment_index = 0
        self._raw = ""
        self._replacements = []
        self._invalid_model = False
        # Tokens remain safe when the parent dies during a socket read.
        # Never join the downloader on the GUI thread.
        lifetime = self._lifetime = {"job": None}
        closed = self._closed
        self.destroyed.connect(lambda: (closed.set(), lifetime["job"].cancel.set()
                                        if lifetime["job"] else None))

        def make_engine(path):
            self._engine = (engine_factory or AsrEngine)(path)
            return self._engine

        self.service = AsrService(data_root, self, model_manager=self.manager,
                                  engine_factory=make_engine, recorder_factory=recorder_factory)
        self.service.state_changed.connect(self._state_changed)
        self.service.corrections_changed.connect(self._corrections)
        self.service.partial_text.connect(self._partial)
        self.service.final_text.connect(self._final)
        self.service.error.connect(self._error)
        self._segment_timer = QTimer(self)
        self._segment_timer.setInterval(400)
        self._segment_timer.timeout.connect(self._segments)
        self._invoke.connect(self._dispatch, Qt.ConnectionType.QueuedConnection)
        self._progress.connect(self._download_progress, Qt.ConnectionType.QueuedConnection)
        self._download_done.connect(self._download_finished, Qt.ConnectionType.QueuedConnection)
        for name, handler in (("status", self.status), ("download", self.download),
                              ("download_cancel", self.download_cancel), ("start", self.start),
                              ("cancel", self.cancel)):
            bridge.register("voice." + name, handler, ui=True)
        bridge.register("voice.stop", self.stop)

    def _check_open(self):
        if self._closed.is_set():
            raise BridgeError("CANCELLED", "语音输入已关闭。")

    def status(self, _params=None, _ctx=None):
        self._check_open()
        info = self.manager.status()
        job = self._job
        installed = bool(info["installed"]) and not self._invalid_model
        return {"state": "downloading" if job else "ready" if installed else "not_downloaded",
                "installed": installed, "model_name": info["model_name"], "size_bytes": info["size_bytes"],
                "download_url": info["download_url"], "job_id": job.job_id if job else None,
                "downloaded_bytes": job.done if job else 0,
                "total_bytes": job.total if job else info["size_bytes"],
                "cancelling": bool(job and job.cancel.is_set()), "invalid": self._invalid_model}

    def download(self, params, _ctx):
        self._check_open()
        if params.get("consent") is not True:
            raise BridgeError("ASR_DOWNLOAD_CONSENT_REQUIRED", "请先确认模型用途和下载大小。")
        job_id = _token(params, "job_id")
        if self._job is not None or self._active:
            raise BridgeError("ASR_BUSY", "请先结束当前录音或等待下载结束。")
        if params.get("choose_path") is True:
            selected = choose_installation(self.parent(), "下载语音识别模型",
                                           size_bytes=self.manager.status()["size_bytes"],
                                           installed_bytes=sum(size for size, _ in REQUIRED_FILES.values()),
                                           default=storage_root(self.manager.data_root, "asr"))
            if selected is None:
                return {**self.status(), "cancelled": True}
            self.manager.download_storage = selected
        job = self._job = _Download(job_id, total=self.manager.status()["size_bytes"])
        self._lifetime["job"] = job
        self.bridge.push("voice.model", self.status())
        threading.Thread(target=self._download_worker, args=(job,), name="mf-voice-download", daemon=True).start()
        return self.status()

    def _download_worker(self, job):
        error = None
        try:
            def progress(done, total):
                if not self._closed.is_set():
                    self._progress.emit(job, done, total)
            self.manager.download(progress, job.cancel)
        except Exception as exc:
            error = {"code": getattr(exc, "code", "ASR_DOWNLOAD_FAILED"),
                     "message": getattr(exc, "message", "模型下载失败，请检查网络后重试。")}
        if not self._closed.is_set():
            self._download_done.emit(job, error)

    @Slot(object, int, int)
    def _download_progress(self, job, done, total):
        if not self._closed.is_set() and job is self._job:
            job.done, job.total = done, total
            self.bridge.push("voice.model", self.status())

    @Slot(object, object)
    def _download_finished(self, job, error):
        if self._closed.is_set() or job is not self._job:
            return
        self._job = self._lifetime["job"] = None
        if not error:
            self._invalid_model = False
        payload = self.status()
        payload.update(job_id=job.job_id, cancelled=job.cancel.is_set())
        if error and not job.cancel.is_set():
            payload["error"] = error
        self.bridge.push("voice.model", payload)

    def download_cancel(self, params, _ctx):
        job_id = _token(params, "job_id")
        if self._job is not None and self._job.job_id == job_id:
            self._job.cancel.set()
            self.bridge.push("voice.model", self.status())
        return {"cancelled": True}

    def start(self, params, _ctx):
        self._check_open()
        session = _token(params, "session_id")
        terms = params.get("terms", [])
        if (not isinstance(terms, list) or len(terms) > 256
                or any(not isinstance(t, str) or not 1 <= len(t) <= 120 for t in terms)):
            raise BridgeError("BAD_PARAMS", "课程术语应为不超过 256 项的短文本列表。")
        if self._active or self._job is not None:
            raise BridgeError("ASR_BUSY", "已有语音输入或模型下载正在进行。")
        if self._invalid_model:
            raise BridgeError("ASR_MODEL_INVALID", "本机语音模型校验失败，请重新下载。")
        self.service.set_glossary(terms)
        self._session, self._terms = session, tuple(terms)
        self._active = True
        self._result = Future()
        self._segment_index = 0
        self._raw, self._replacements = "", []
        self.service.start()
        if self._result.done():
            self._result.result()  # Synchronous missing-model/microphone errors.
        self._segment_timer.start()
        return {"session_id": session, "state": self.service.state}

    @Slot(object)
    def _dispatch(self, command):
        fn, future = command
        if future.done():
            return
        try:
            self._check_open()
            future.set_result(fn())
        except Exception as exc:
            future.set_exception(exc)

    def _wait(self, future, ctx):
        deadline = time.monotonic() + 90
        while True:
            if self._closed.is_set() or ctx.cancel.is_set():
                raise BridgeError("CANCELLED", "语音输入已取消。")
            try:
                return future.result(timeout=.05)
            except TimeoutError:
                if time.monotonic() >= deadline:
                    raise BridgeError("ASR_ENGINE_FAILED", "最后一句识别超时，请重试。")

    def stop(self, params, ctx):
        session = _token(params, "session_id")
        dispatched = Future()

        def begin():
            if ctx.cancel.is_set():
                raise BridgeError("CANCELLED", "已取消。")
            if session != self._session or self._result is None:
                raise BridgeError("ASR_SESSION_EXPIRED", "这次语音输入已经结束。")
            self.service.stop()
            return self._result

        self._invoke.emit((begin, dispatched))
        try:
            return self._wait(self._wait(dispatched, ctx), ctx)
        except Exception as exc:
            # A newer session is protected from this delayed cleanup by its ID.
            if not self._closed.is_set():
                self._invoke.emit((lambda: self.cancel({"session_id": session}, None), Future()))
            if isinstance(exc, BridgeError):
                raise
            raise BridgeError("ASR_ENGINE_FAILED", "最后一句语音识别未完成，请重试。") from exc

    def _state_changed(self, state):
        # AsrService emits idle just before final_text. Publish idle after final.
        if self._active and state != "idle":
            self.bridge.push("voice.state", {"session_id": self._session, "state": state})

    def _corrections(self, raw, replacements):
        if self._active:
            self._raw, self._replacements = raw, list(replacements)

    def _payload(self, text):
        return {"session_id": self._session, "text": text, "raw_text": self._raw,
                "replacements": self._replacements}

    def _segments(self):
        if self._engine is not None:
            for raw in self._engine.take_finalized():
                text, replacements = correct(raw, self._terms)
                self.bridge.push("voice.segment", {"session_id": self._session, "index": self._segment_index,
                                 "text": text, "raw_text": raw, "replacements": replacements})
                self._segment_index += 1

    def _partial(self, text):
        if self._active:
            self._segments()
            self.bridge.push("voice.partial", self._payload(text))

    def _final(self, text):
        if not self._active:
            return
        self._segments()
        result = self._payload(text)
        self._active, self._engine = False, None
        self._segment_timer.stop()
        self.bridge.push("voice.final", result)
        self.bridge.push("voice.state", {"session_id": self._session, "state": "idle"})
        if not self._result.done():
            self._result.set_result(result)

    def _error(self, code, message):
        if not self._active:
            return
        if code in {"ASR_MODEL_INVALID", "ASR_MODEL_REQUIRED"}:
            self._invalid_model = self._engine is not None or code == "ASR_MODEL_INVALID"
            if self._invalid_model:
                code, message = "ASR_MODEL_INVALID", "本机语音模型校验失败，请重新下载。"
        self._active, self._engine = False, None
        self._segment_timer.stop()
        self.bridge.push("voice.error", {"session_id": self._session, "code": code, "message": message})
        if not self._result.done():
            self._result.set_exception(BridgeError(code, message))

    def cancel(self, params, _ctx):
        session = _token(params, "session_id")
        if session == self._session:
            self._active = False  # Suppress AsrService's empty reset transcript.
            self._segment_timer.stop()
            self.service.cancel()
            self._engine = None
            if self._result is not None and not self._result.done():
                self._result.set_exception(BridgeError("CANCELLED", "语音输入已取消。"))
            self.bridge.push("voice.state", {"session_id": session, "state": "idle", "cancelled": True})
            self._session = None
        return {"cancelled": True}

    @Slot()
    def reset(self):
        """GUI-thread lifecycle hook for page reload/account changes."""
        if self._session is not None:
            self.cancel({"session_id": self._session}, None)
        if self._job is not None:
            self.download_cancel({"job_id": self._job.job_id}, None)

    def dispose(self):
        if not self._closed.is_set():
            self.reset()
            self._closed.set()
            self.service.dispose()


def register(bridge, window):
    """Only registration and disposal are needed in LearningWindow."""
    voice = VoiceInput(bridge, window._data_root, window)
    window.switch_requested.connect(voice.reset)
    window.logout_requested.connect(voice.reset)
    window.page.loadStarted.connect(voice.reset)

    def lifecycle(text):
        message = json.loads(text)
        if message.get("type") == "event" and message.get("name") == "scope_changed":
            voice.reset()

    bridge.event.connect(lifecycle)
    return voice
