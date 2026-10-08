"""Window-owned teacher voice bridge. Qt/audio stay on the GUI thread.

Only asset inspection and explicit model installation use the small worker pool;
TtsService owns synthesis, its two-sentence pipeline and the device clock. No
lesson decisions, implicit downloads, remote speech or shared-service shutdown.
"""
from __future__ import annotations

from collections import deque
from concurrent.futures import ThreadPoolExecutor
import math
from pathlib import Path
import re
import threading
import time
from uuid import uuid4

from PySide6.QtCore import QObject, QTimer, Qt, Signal, Slot

from .bridge import BridgeError
from ..voice.download import DownloadError
from ..voice.tts.config import TtsConfig, TtsError
from ..voice.tts.model_manager import ModelManager
from ..voice.tts.component_manager import CATALOG, ComponentManager
from ..voice.storage import choose_installation, storage_root
from ..voice.tts.service import TtsService
from ..voice.tts.text_prep import StreamingTextPrep

_ID = re.compile(r"[A-Za-z0-9_.:-]{1,64}")
_ENGINE_BYTES = round(12.2 * 1024 ** 3)  # Existing distribution survey, approximate.
_VOICE_BYTES = 155284856 + 134946803 + 334242


class SpeechResources:
    """Cheap local readiness, including separately located development assets."""
    def __init__(self, data_root, *, config_factory=TtsConfig.from_env, manager=None):
        self.data_root = Path(data_root)
        self.config_factory = config_factory
        self.base_manager = manager or ModelManager(self.data_root)
        self.manager = manager or (ComponentManager(self.data_root) if CATALOG.is_file() else self.base_manager)

    def inspect(self):
        config = self.config_factory(self.data_root)
        engine = config.engine_python.is_file() and (config.engine_root / "GPT_SoVITS/TTS_infer_pack/TTS.py").is_file()
        voice, voice_error = None, ""
        try:
            voice = config.voice()
        except (TtsError, OSError, ValueError, KeyError, TypeError) as exc:
            voice_error = getattr(exc, "message", "声音包描述不完整。")
        roots = config.base_assets()
        missing = []
        mapping = {"chinese-roberta-wwm-ext-large": "bert_root", "chinese-hubert-base": "hubert_root", "G2PWModel": "g2pw_root"}
        required = ({name.removeprefix("base/"): value for name, value in self.manager.required.items()
                     if name.startswith("base/")} if isinstance(self.manager, ComponentManager) else self.base_manager.required)
        for name, (size, _) in required.items():
            prefix, rest = name.split("/", 1)
            path = roots[mapping[prefix]] / rest if prefix in mapping else roots["sv_weights"]
            try:
                present = path.is_file() and path.stat().st_size == size
            except OSError:
                present = False
            if not present:
                missing.append({"name": name, "size_bytes": size})
        base = not missing
        ready = bool(engine and voice and base)
        if isinstance(self.manager, ComponentManager) and not self.manager.status()["installed"]:
            ready = False
        if ready:
            try:
                config.validate()  # Also checks the read-only resource/output boundary.
            except (TtsError, ValueError, OSError) as exc:
                return {"ready": False, "message": "朗读未启用", "reason": getattr(exc, "message", "朗读配置无效。"),
                        "components": [], "download": None}, config
        components = [
            {"id": "engine", "name": "GPT-SoVITS 引擎与 Python/CUDA 运行时", "installed": engine,
             "size_bytes": _ENGINE_BYTES, "size_estimated": True, "downloadable": False,
             "instruction": "随安装包离线提供，或通过 MF_TTS_ENGINE_ROOT / MF_TTS_ENGINE_PYTHON 指定已安装位置。"},
            {"id": "base", "name": "朗读通用底模", "installed": base, "size_bytes": self.manager.size_bytes,
             "size_estimated": False, "downloadable": True, "missing_files": missing,
             "instruction": "同意后从固定官方地址或备用镜像下载，并校验后安装。"},
            {"id": "voice", "name": "弗洛洛 1.0.4 声音包", "installed": voice is not None,
             "size_bytes": _VOICE_BYTES, "size_estimated": False, "downloadable": False,
             "instruction": voice_error or "引用已安装的声音包；开发机优先使用 D 盘保留副本。"},
        ]
        if isinstance(self.manager, ComponentManager):
            for item in components:
                item.update(downloadable=True, size_estimated=False,
                            instruction="同意后下载完整朗读组件，校验通过后即可使用。")
            for item in components:
                item["size_bytes"] = self.manager.component_sizes[item["id"]]
        simulation = isinstance(self.manager, ComponentManager) and self.manager.simulation
        return {"ready": ready, "message": "模拟朗读测试音" if ready and simulation else "朗读可用" if ready else "朗读未启用", "components": components,
                "voice": {"id": voice.pack_id, "version": voice.version} if voice else None,
                "download": {"required": not ready, "size_bytes": self.manager.size_bytes,
                    "installed_size_bytes": sum(size for size, _ in self.manager.required.values()),
                    "target": str(self.manager.path), "assets": [
                        {"name": a.name, "size_bytes": a.size, "sha256": a.sha256, "urls": list(a.urls)}
                        for a in self.manager.assets],
                    "instruction": "引擎、通用底模和弗洛洛声音包。" if isinstance(self.manager, ComponentManager)
                                   else "只下载通用底模；引擎和声音包须另行安装。"}}, config


def _params(params, keys):
    if set(params) - set(keys):
        raise BridgeError("INVALID_PARAMS", "朗读参数无效。")


def _id(params, key, *, optional=False):
    value = params.get(key)
    if optional and value is None:
        return None
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise BridgeError("INVALID_PARAMS", "朗读请求标识无效。")
    return value


def _number(value, low, high, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
        raise BridgeError("INVALID_PARAMS", f"{label}必须在 {low} 到 {high} 之间。")
    return float(value)


class TeacherSpeech(QObject):
    """Register once per LearningWindow, then reset on account scope changes."""
    _result = Signal(object)

    def __init__(self, bridge, data_root, parent=None, *, resources=None, service_factory=None,
                 idle_seconds=600):
        super().__init__(parent)
        self.bridge = bridge
        self.resources = resources or SpeechResources(data_root)
        self._factory = service_factory or (lambda config, parent: TtsService(config=config, parent=parent))
        self._pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="mf-speech-assets")
        self._result.connect(self._receive, Qt.ConnectionType.QueuedConnection)
        self._closed = False
        self._generation = 0
        self._prepare_cancel = threading.Event()
        self.service = None
        self._config = None
        self._owner = self._utterance = None
        self._stream = None
        self._stream_lock = threading.RLock()
        self._state = "idle"
        self._volume, self._speed = 1.0, 1.0
        self._download = None
        self._download_state = None
        # Keep a voice resident across ordinary gaps between replies, not across
        # a long break. This timer owns no thread and never expires mid-utterance.
        self._idle_timer = QTimer(self)
        self._idle_timer.setSingleShot(True)
        self._idle_timer.setInterval(round(idle_seconds * 1000))
        self._idle_timer.timeout.connect(self._idle_release)
        for name, fn, ui in (
            ("status", self.status, False), ("download", self.download, True),
            ("choose_download", self.choose_download, True),
            ("cancel_download", self.cancel_download, True), ("warm_up", self.warm_up, True),
            ("speak", self.speak, True), ("stream_start", self.stream_start, True),
            ("stream_append", self.stream_append, True), ("stream_end", self.stream_end, True),
            ("stop", self.stop, True), ("settings", self.settings, True),
        ):
            bridge.register("speech." + name, fn, ui=ui)

    def _emit_result(self, value):
        if not self._closed:
            try:
                self._result.emit(value)
            except RuntimeError:  # Parent window already destroyed.
                pass

    def _push(self, name, data):
        if not self._closed:
            self.bridge.push("speech." + name, data)

    def _scope(self):
        return {"owner": self._owner, "utterance_id": self._utterance}

    def _state_changed(self, state):
        self._state = state
        if self.service and state in {"idle", "ready", "stopped", "error"}:
            self._idle_timer.start()
        else:
            self._idle_timer.stop()
        self._push("state", {**self._scope(), "state": state})

    def _idle_release(self):
        if self._state in {"idle", "ready", "stopped", "error"}:
            self._halt()
            self._release_service()
            self._state_changed("idle")

    def status(self, params, ctx):
        _params(params, ())
        if self._closed:
            raise BridgeError("CLOSED", "朗读组件已关闭。")
        try:
            value, _ = self.resources.inspect()
        except (TtsError, OSError, ValueError, TypeError, KeyError):
            value = {"ready": False, "message": "朗读未启用", "reason": "请检查本机朗读资源配置。", "components": [], "download": None}
        if ctx.cancel.is_set():
            raise BridgeError("CANCELLED", "已取消。")
        return {**value, "state": self._state, "volume": self._volume, "speed": self._speed,
                "download_state": self._download_state}

    def _halt(self):
        with self._stream_lock:
            self._generation += 1
            self._prepare_cancel.set()
            self._stream = None
            if self.service:
                self.service.stop()  # Device reset and old sentence interruption happen before changing IDs.
            self._push("mouth", {**self._scope(), "level": 0.0})

    def _begin(self, owner, utterance, text, *, mode="speak"):
        if self._closed:
            raise BridgeError("CLOSED", "朗读组件已关闭。")
        self._halt()
        self._idle_timer.stop()
        self._owner, self._utterance = owner, utterance
        generation = self._generation
        cancel = self._prepare_cancel = threading.Event()
        self._state_changed("checking")

        def work():
            try:
                status, config = self.resources.inspect()
                result = {"kind": "prepared", "generation": generation, "mode": mode,
                          "text": text, "status": status, "config": config}
            except Exception:
                result = {"kind": "prepared", "generation": generation, "mode": mode, "text": text,
                          "status": {"ready": False, "message": "朗读未启用", "reason": "请检查本机朗读资源配置。", "components": [], "download": None}}
            if not cancel.is_set():
                self._emit_result(result)
        self._pool.submit(work)
        return {"accepted": True, **self._scope(), "state": "checking"}

    def warm_up(self, params, ctx):
        _params(params, ("owner",))
        owner = _id(params, "owner")
        if self._state in {"checking", "warming", "synthesizing", "playing", "ready"}:
            return {"accepted": False, "state": self._state}
        return self._begin(owner, None, None, mode="warm")

    def speak(self, params, ctx):
        _params(params, ("owner", "utterance_id", "text"))
        owner, utterance = _id(params, "owner"), _id(params, "utterance_id")
        text = params.get("text")
        if not isinstance(text, str) or not text.strip() or len(text) > 20000:
            raise BridgeError("INVALID_TEXT", "请输入 1 到 20000 字的朗读内容。")
        return self._begin(owner, utterance, text, mode="speak")

    def stream_start(self, params, ctx):
        """Start a producer-owned utterance; text arrives through append calls."""
        _params(params, ("owner", "utterance_id"))
        owner, utterance = _id(params, "owner"), _id(params, "utterance_id")
        with self._stream_lock:
            accepted = self._begin(owner, utterance, None, mode="stream")
            self._stream = {"owner": owner, "utterance": utterance,
                            "prep": StreamingTextPrep(), "queue": deque(),
                            "prepared": False, "ended": False, "input_closed": False,
                            "generation": self._generation}
        return {**accepted, "streaming": True}

    def _stream_matches(self, owner, utterance):
        stream = self._stream
        return (stream is not None and stream["generation"] == self._generation
                and stream["owner"] == owner and stream["utterance"] == utterance)

    def _queue_stream_sentences(self, sentences):
        stream = self._stream
        if stream is None:
            return 0
        stream["queue"].extend(sentences)
        return len(sentences)

    def stream_append(self, params, ctx):
        _params(params, ("owner", "utterance_id", "text"))
        owner, utterance = _id(params, "owner"), _id(params, "utterance_id")
        with self._stream_lock:
            if not self._stream_matches(owner, utterance):
                return {"accepted": False, "queued": 0, "buffered": 0}
            stream = self._stream
            if stream["ended"]:
                raise BridgeError("STREAM_CLOSED", "这段朗读已经结束。")
            text = params.get("text")
            if not isinstance(text, str) or len(text) > 20000:
                raise BridgeError("INVALID_TEXT", "朗读片段无效或过长。")
            try:
                queued = self._queue_stream_sentences(stream["prep"].append(text))
            except (TypeError, ValueError):
                raise BridgeError("INVALID_TEXT", "朗读片段无效或超过长度限制。")
            self._drain_stream()
            return {"accepted": True, "queued": queued,
                    "buffered": len(stream["prep"].buffered_text)}

    def stream_end(self, params, ctx):
        _params(params, ("owner", "utterance_id"))
        owner, utterance = _id(params, "owner"), _id(params, "utterance_id")
        with self._stream_lock:
            if not self._stream_matches(owner, utterance):
                return {"accepted": False, "queued": 0, "buffered": 0}
            stream = self._stream
            if stream["ended"]:
                return {"accepted": True, "queued": 0, "buffered": 0}
            stream["ended"] = True
            try:
                queued = self._queue_stream_sentences(stream["prep"].finish())
            except (TypeError, ValueError):
                raise BridgeError("INVALID_TEXT", "朗读片段无效或超过长度限制。")
            self._drain_stream()
            return {"accepted": True, "queued": queued,
                    "buffered": len(stream["prep"].buffered_text)}

    def _drain_stream(self):
        with self._stream_lock:
            stream = self._stream
            if (stream is None or stream["generation"] != self._generation
                    or not stream["prepared"] or stream["input_closed"] or self.service is None):
                return
            if stream["queue"]:
                self.service.append_sentences(tuple(stream["queue"]))
                stream["queue"].clear()
            if stream["ended"]:
                stream["input_closed"] = True
                self.service.end_stream()

    def stop(self, params, ctx):
        _params(params, ("owner", "utterance_id", "release"))
        if "release" in params and type(params["release"]) is not bool:
            raise BridgeError("INVALID_PARAMS", "朗读释放参数无效。")
        owner, utterance = _id(params, "owner"), _id(params, "utterance_id", optional=True)
        if owner != self._owner or utterance is not None and utterance != self._utterance:
            return {"stopped": False}
        self._halt()
        if params.get("release"):
            self._release_service()
        self._state_changed("stopped")
        return {"stopped": True}

    def settings(self, params, ctx):
        _params(params, ("volume", "speed"))
        volume = _number(params.get("volume", self._volume), 0, 1, "音量")
        speed = _number(params.get("speed", self._speed), .5, 2, "语速")
        self._volume, self._speed = volume, speed
        if self.service:
            self.service.set_volume(volume)
            self.service.set_speed(speed)
        result = {"volume": volume, "speed": speed, "speed_applies": "next_utterance"}
        self._push("settings", result)
        return result

    def choose_download(self, params, ctx):
        _params(params, ("owner",))
        _id(params, "owner")
        if self._download:
            raise BridgeError("DOWNLOAD_BUSY", "朗读资源正在下载。")
        if not isinstance(self.resources.manager, ComponentManager):
            raise BridgeError("CATALOG_UNAVAILABLE", "此开发版本未配置完整朗读组件下载清单。")
        manager = self.resources.manager
        selected = choose_installation(self.parent(), "下载朗读组件", size_bytes=manager.size_bytes,
                                       installed_bytes=sum(size for size, _ in manager.required.values()),
                                       default=storage_root(self.resources.data_root, "tts"))
        if selected is None:
            return {"cancelled": True}
        chosen = ComponentManager(self.resources.data_root, storage=selected)
        return self.download({**params, "consent": True}, ctx, manager=chosen)

    def download(self, params, ctx, *, manager=None):
        _params(params, ("owner", "consent"))
        owner = _id(params, "owner")
        if params.get("consent") is not True:
            raise BridgeError("CONSENT_REQUIRED", "请先查看下载内容与大小，确认同意后再下载。")
        if self._closed:
            raise BridgeError("CLOSED", "朗读组件已关闭。")
        if self._download:
            raise BridgeError("DOWNLOAD_BUSY", "朗读资源正在下载。")
        manager = manager or self.resources.manager
        job = {"id": uuid4().hex, "owner": owner, "cancel": threading.Event(), "manager": manager}
        self._download = job
        self._halt()
        self._release_service()
        self._download_event(job, "downloading", 0, manager.size_bytes)

        def work():
            last = 0.0
            def progress(done, total):
                nonlocal last
                now = time.monotonic()
                if now - last >= .1 or done == total:
                    last = now
                    self._emit_result({"kind": "progress", "id": job["id"], "done": done, "total": total})
            try:
                manager.download(progress=progress, cancel=job["cancel"])
                if isinstance(manager, ComponentManager):
                    self.resources.manager = manager
                status, _ = self.resources.inspect()
                result = {"state": "completed", "status": status}
            except DownloadError as exc:
                result = {"state": "cancelled" if exc.code == "已取消" else "error", "code": exc.code, "message": exc.message}
            except Exception:
                result = {"state": "error", "code": "DOWNLOAD_FAILED", "message": "朗读资源下载失败，请重试。"}
            self._emit_result({"kind": "downloaded", "id": job["id"], **result})
        self._pool.submit(work)
        return {"download_id": job["id"], "size_bytes": manager.size_bytes}

    def _download_event(self, job, state, done, total, **extra):
        self._download_state = {"download_id": job["id"], "owner": job["owner"], "state": state,
                                "received_bytes": done, "total_bytes": total, **extra}
        self._push("download", self._download_state)

    def cancel_download(self, params, ctx):
        _params(params, ("owner", "download_id"))
        owner, download_id = _id(params, "owner"), _id(params, "download_id", optional=True)
        job = self._download
        if job and job["owner"] == owner and (download_id is None or download_id == job["id"]):
            job["cancel"].set()
            self._download_event(job, "cancelling", self._download_state["received_bytes"], self.resources.manager.size_bytes)
            return {"cancelled": True}
        return {"cancelled": False}

    @Slot(object)
    def _receive(self, result):
        if self._closed:
            return
        kind = result["kind"]
        if kind == "prepared":
            if result["generation"] != self._generation:
                return
            self._push("availability", result["status"])
            if not result["status"]["ready"]:
                self._state_changed("disabled")
                return
            try:
                self._start_service(result["config"], result["text"],
                                    warm_existing=result.get("mode") != "stream")
                if result.get("mode") == "stream":
                    self.service.start_stream()
                    self._stream["prepared"] = True
                    self._drain_stream()
            except Exception as exc:
                self._release_service()
                self._state_changed("error")
                self._error(getattr(exc, "code", "TTS_FAILED"), getattr(exc, "message", "无法启动本机朗读。"))
        elif self._download and result["id"] == self._download["id"]:
            job = self._download
            if kind == "progress":
                if not job["cancel"].is_set():
                    self._download_event(job, "downloading", result["done"], result["total"])
            elif kind == "downloaded":
                total = self.resources.manager.size_bytes
                self._download_event(job, result["state"], total if result["state"] == "completed" else self._download_state["received_bytes"], total,
                                     code=result.get("code"), message=result.get("message"))
                self._download = None
                if "status" in result:
                    self._push("availability", result["status"])

    def _start_service(self, config, text, *, warm_existing=True):
        if self.service is None or config != self._config:
            self._release_service()
            self.service = self._factory(config, self)
            self._config = config
            self.service.state_changed.connect(self._state_changed)
            self.service.mouth_level.connect(self._mouth)
            self.service.sentence_timing.connect(self._sentence)
            self.service.error.connect(self._error)
        self.service.set_volume(self._volume)
        self.service.set_speed(self._speed)
        if text is None:
            if warm_existing:
                self.service.warm_up()
        else:
            self.service.speak(text)

    def _mouth(self, level):
        self._push("mouth", {**self._scope(), "level": max(0.0, min(1.0, float(level))), "at": time.monotonic()})

    def _sentence(self, timing):
        self._push("sentence", {**timing, **self._scope()})

    def _error(self, code, message):
        self._push("error", {**self._scope(), "code": code, "message": message})

    def _release_service(self):
        self._idle_timer.stop()
        service, self.service = self.service, None
        if service:
            for signal, slot in ((service.state_changed, self._state_changed), (service.mouth_level, self._mouth),
                                 (service.sentence_timing, self._sentence), (service.error, self._error)):
                signal.disconnect(slot)
            service.close()
            service.deleteLater()

    def reset(self):
        """Account exit/rebind: cancel only this window's voice and downloads."""
        self._halt()
        if self._download:
            self._download["cancel"].set()
        self._download = self._download_state = None
        self._release_service()
        self._state_changed("stopped")
        self._owner = self._utterance = None

    def dispose(self):
        if self._closed:
            return
        self.reset()
        self._closed = True
        self._pool.shutdown(wait=False, cancel_futures=True)
