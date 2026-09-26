"""Thread-safe supervisor for one private resident GPT-SoVITS child.

Blocking warm_up()/synthesize() are worker APIs; TtsService supplies the worker.
Only owned processes are stopped. No global port lookup, process-name kill, or
reuse of an unrelated service (including other projects' GPT-SoVITS servers).
"""
from __future__ import annotations

import atexit
import base64
import binascii
from dataclasses import asdict, dataclass
import http.client
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import threading
import time
from uuid import uuid4
import wave

from .config import TtsCancelled, TtsConfig, TtsError


@dataclass(frozen=True)
class SpeechAudio:
    pcm: bytes
    sample_rate: int
    synthesis_seconds: float
    final: bool = True

    @property
    def duration_seconds(self):
        return len(self.pcm) / (self.sample_rate * 2)

    def wav_bytes(self):
        output = io.BytesIO()
        with wave.open(output, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(self.sample_rate)
            wav.writeframes(self.pcm)
        return output.getvalue()


def _windows_job(process):
    """Kill the entire owned child tree when this non-inherited handle closes."""
    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes as w
    class Basic(ctypes.Structure):
        _fields_ = [("process_time", ctypes.c_int64), ("job_time", ctypes.c_int64), ("flags", w.DWORD),
                    ("minimum", ctypes.c_size_t), ("maximum", ctypes.c_size_t), ("active", w.DWORD),
                    ("affinity", ctypes.c_size_t), ("priority", w.DWORD), ("scheduling", w.DWORD)]
    class IO(ctypes.Structure):
        _fields_ = [(name, ctypes.c_uint64) for name in ("read_ops", "write_ops", "other_ops", "read", "write", "other")]
    class Extended(ctypes.Structure):
        _fields_ = [("basic", Basic), ("io", IO), ("process_memory", ctypes.c_size_t),
                    ("job_memory", ctypes.c_size_t), ("peak_process", ctypes.c_size_t), ("peak_job", ctypes.c_size_t)]
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, w.LPCWSTR]
    kernel.CreateJobObjectW.restype = w.HANDLE
    kernel.SetInformationJobObject.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD]
    kernel.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
    kernel.CloseHandle.argtypes = [w.HANDLE]
    job = kernel.CreateJobObjectW(None, None)
    limits = Extended()
    limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not job or not kernel.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
        if job:
            kernel.CloseHandle(job)
        raise OSError("Unable to create private TTS process job")
    if not kernel.AssignProcessToJobObject(job, int(process._handle)):
        kernel.CloseHandle(job)
        raise OSError("Unable to bind private TTS process job")
    return lambda: kernel.CloseHandle(job)


class TtsRuntime:
    def __init__(self, config: TtsConfig | None = None, *, data_root=None,
                 process_factory=None, transport=None, job_factory=None):
        self.config = config or TtsConfig.from_env(data_root)
        self._factory = process_factory or subprocess.Popen
        self._transport = transport
        self._job_factory = job_factory or _windows_job
        self._process = self._job = self._log = None
        self._endpoint = None
        self._voice = None
        self._operation = threading.Lock()
        self._lifecycle = threading.RLock()
        self._active_lock = threading.Lock()
        self._active = None
        self._closed = threading.Event()
        self.run_dir: Path | None = None
        self.last_health = {}
        atexit.register(self.close)

    @property
    def pid(self):
        return self._process.pid if self._process and self._process.poll() is None else None

    def _check(self, cancel=None):
        if self._closed.is_set() or cancel is not None and cancel.is_set():
            raise TtsCancelled()

    def _acquire(self, cancel):
        while not self._operation.acquire(timeout=.05):
            self._check(cancel)
        try:
            self._check(cancel)
        except TtsCancelled:
            self._operation.release()
            raise

    def _launch(self):
        with self._lifecycle:
            self._check()
            self._voice = self.config.validate()
            self.run_dir = self.config.data_root.resolve() / "tts" / uuid4().hex
            self.run_dir.mkdir(parents=True)
            self._endpoint = {"instance": uuid4().hex, "token": uuid4().hex}
            voice = {key: str(value) if isinstance(value, Path) else value for key, value in asdict(self._voice).items()}
            settings = {**self._endpoint, "engine_root": str(self.config.engine_root.resolve()), "voice": voice,
                        "device": self.config.device, "cpu_threads": self.config.cpu_threads,
                        "base_assets": {key: str(path.resolve()) for key, path in self.config.base_assets().items()}}
            config_path = self.run_dir / "request-config.json"
            config_path.write_text(json.dumps(settings, ensure_ascii=False), encoding="utf-8")
            self._log = (self.run_dir / "engine.log").open("ab", buffering=0)
            try:
                self._process = self._factory([str(self.config.engine_python), "-B", "-u", "-X", "utf8",
                    str(Path(__file__).with_name("engine_server.py")), "--config", str(config_path)],
                    cwd=str(self.run_dir), env=self.config.child_environment(self.run_dir),
                    stdin=subprocess.PIPE, stdout=self._log, stderr=subprocess.STDOUT,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), close_fds=True)
                self._job = self._job_factory(self._process)
            except Exception:
                self._stop_process()
                raise TtsError("ENGINE_START_FAILED", "推理子进程启动失败，请检查引擎路径和运行日志。") from None

    def _http(self, method, route, payload=None, timeout=2, endpoint=None, track=False):
        endpoint = dict(endpoint or self._endpoint or {})
        if self._transport is not None:
            return self._transport(method, route, payload, timeout, endpoint)
        connection = http.client.HTTPConnection("127.0.0.1", endpoint["port"], timeout=timeout)
        if track:
            with self._active_lock:
                self._active = (endpoint, payload["request_id"], connection)
        try:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
            connection.request(method, route, body, {"Content-Type": "application/json", "Authorization": "Bearer " + endpoint["token"]})
            response = connection.getresponse()
            data = response.read(16 * 1024 * 1024 + 1)
            if len(data) > 16 * 1024 * 1024:
                raise TtsError("AUDIO_INVALID", "朗读音频响应过大。")
            return response.status, data, dict(response.getheaders())
        finally:
            connection.close()
            if track:
                with self._active_lock:
                    if self._active and self._active[2] is connection:
                        self._active = None

    def health(self):
        endpoint = self._endpoint
        if not endpoint or "port" not in endpoint or not self.pid:
            return {"ready": False}
        try:
            status, body, _ = self._http("GET", "/health", timeout=.5)
            value = json.loads(body)
            if status == 200 and value.get("instance") == endpoint["instance"] and value.get("pid") == self.pid:
                self.last_health = value
                return value
        except (OSError, ValueError, http.client.HTTPException):
            pass
        return {"ready": False}

    def _ensure_ready(self, cancel=None):
        self._check(cancel)
        if self.pid is None:
            self._stop_process()
            self._launch()
        started = time.monotonic()
        while time.monotonic() - started < self.config.startup_timeout:
            self._check(cancel)
            if not self.pid:
                raise TtsError("ENGINE_CRASHED", "朗读引擎启动时退出，请查看 engine.log。")
            endpoint_file = self.run_dir / "endpoint.json"
            if "port" not in self._endpoint and endpoint_file.is_file():
                try:
                    value = json.loads(endpoint_file.read_text(encoding="utf-8"))
                    if value.get("instance") == self._endpoint["instance"] and value.get("pid") == self.pid and 0 < int(value["port"]) < 65536:
                        self._endpoint["port"] = int(value["port"])
                except (ValueError, KeyError, OSError):
                    pass
            health = self.health()
            if health.get("ready"):
                return health
            if health.get("error"):
                self._stop_process()
                raise TtsError("ENGINE_START_FAILED", "引擎加载或预热失败，请查看 engine.log。")
            self._closed.wait(.05)
        self._stop_process()
        raise TtsError("ENGINE_START_TIMEOUT", "朗读引擎预热超时。")

    def warm_up(self, cancel=None):
        self._acquire(cancel)
        try:
            return self._ensure_ready(cancel)
        finally:
            self._operation.release()

    def synthesize(self, text, *, cancel=None):
        if not isinstance(text, str) or not text.strip() or len(text) > 120:
            raise TtsError("INVALID_TEXT", "请传入分句后的朗读文本。")
        self._acquire(cancel)
        try:
            for attempt in range(2):
                self._ensure_ready(cancel)
                request_id = uuid4().hex
                deadline = time.monotonic() + self.config.synthesis_timeout
                try:
                    while True:
                        self._check(cancel)
                        status, data, headers = self._http("POST", "/synthesize", {"text": text, "request_id": request_id},
                                                         timeout=max(.1, deadline - time.monotonic()), track=True)
                        self._check(cancel)
                        if status == 409 and json.loads(data).get("code") == "BUSY" and time.monotonic() < deadline:
                            self._closed.wait(.025)
                            continue
                        if status != 200:
                            raise TtsError("SYNTHESIS_FAILED", "本句语音合成失败，请查看引擎日志后重试。")
                        try:
                            with wave.open(io.BytesIO(data), "rb") as wav:
                                if wav.getnchannels() != 1 or wav.getsampwidth() != 2 or wav.getcomptype() != "NONE":
                                    raise ValueError("format")
                                rate, pcm = wav.getframerate(), wav.readframes(wav.getnframes())
                            if not 16000 <= rate <= 96000 or not pcm or len(pcm) % 2:
                                raise ValueError("pcm")
                            return SpeechAudio(pcm, rate, float(headers.get("X-Synthesis-Seconds", 0)))
                        except (ValueError, wave.Error, EOFError):
                            raise TtsError("AUDIO_INVALID", "朗读引擎返回的音频无效。") from None
                except (OSError, http.client.HTTPException):
                    self._check(cancel)
                    # One retry only for a child that really died, never replay
                    # a failed request against a healthy or unrelated server.
                    if attempt == 0 and self.pid is None:
                        self._stop_process()
                        continue
                    raise TtsError("ENGINE_CONNECTION", "朗读引擎连接中断或超时。") from None
            raise TtsError("ENGINE_CRASHED", "朗读引擎重启后再次退出。")
        finally:
            self._operation.release()

    def cancel(self):
        """Nonblocking best-effort compute cancel; playback is stopped by Qt."""
        with self._active_lock:
            active = self._active
        if not active:
            return
        endpoint, request_id, connection = active
        def send():
            try:
                self._http("POST", "/cancel", {"request_id": request_id}, timeout=.5, endpoint=endpoint)
            except (OSError, http.client.HTTPException):
                pass
            try:
                if connection.sock:
                    connection.sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        threading.Thread(target=send, name="mf-tts-cancel", daemon=True).start()

    def stream_synthesize(self, text, on_audio, *, cancel=None):
        """Deliver PCM fragments on the calling worker; final marks the sentence.

        Unlike synthesize(), playback may start before the sentence is complete.
        Never replay a partly delivered sentence following an engine crash.
        """
        if not isinstance(text, str) or not text.strip() or len(text) > 120:
            raise TtsError("INVALID_TEXT", "请传入分句后的朗读文本。")
        self._acquire(cancel)
        delivered = False
        try:
            for attempt in range(2):
                self._ensure_ready(cancel)
                deadline = time.monotonic() + self.config.synthesis_timeout
                try:
                    while time.monotonic() < deadline:
                        self._check(cancel)
                        endpoint = dict(self._endpoint)
                        request_id = uuid4().hex
                        connection = http.client.HTTPConnection("127.0.0.1", endpoint["port"],
                                                               timeout=max(.1, deadline - time.monotonic()))
                        with self._active_lock:
                            self._active = (endpoint, request_id, connection)
                        try:
                            body = json.dumps({"text": text, "request_id": request_id}, ensure_ascii=False).encode("utf-8")
                            connection.request("POST", "/stream", body, {"Content-Type": "application/json",
                                "Authorization": "Bearer " + endpoint["token"]})
                            response = connection.getresponse()
                            if response.status == 409:
                                self._closed.wait(.025)
                                continue
                            if response.status != 200:
                                raise TtsError("SYNTHESIS_FAILED", "本句流式语音合成失败。")
                            total = 0
                            while True:
                                self._check(cancel)
                                line = response.readline(2 * 1024 * 1024 + 1)
                                if not line:
                                    raise ConnectionResetError("TTS stream ended before its final frame")
                                total += len(line)
                                if len(line) > 2 * 1024 * 1024 or total > 16 * 1024 * 1024:
                                    raise TtsError("AUDIO_INVALID", "朗读音频响应过大。")
                                try:
                                    frame = json.loads(line)
                                    if frame.get("error"):
                                        raise TtsError("SYNTHESIS_FAILED", "本句语音合成失败，请查看引擎日志。")
                                    pcm = base64.b64decode(frame["pcm"], validate=True)
                                    rate, elapsed, final = int(frame["sample_rate"]), float(frame["synthesis_seconds"]), frame["final"]
                                    if not 16000 <= rate <= 96000 or len(pcm) % 2 or not isinstance(final, bool) or elapsed < 0:
                                        raise ValueError("invalid frame")
                                    if not pcm and not (final and delivered):
                                        raise ValueError("empty frame")
                                except (ValueError, KeyError, TypeError, binascii.Error):
                                    raise TtsError("AUDIO_INVALID", "朗读引擎返回的流式音频无效。") from None
                                self._check(cancel)
                                on_audio(SpeechAudio(pcm, rate, elapsed, final))
                                delivered = delivered or bool(pcm)
                                if final:
                                    return
                        finally:
                            connection.close()
                            with self._active_lock:
                                if self._active and self._active[2] is connection:
                                    self._active = None
                    raise TimeoutError("TTS stream timed out")
                except (OSError, http.client.HTTPException):
                    self._check(cancel)
                    if attempt == 0 and not delivered and self.pid is None:
                        self._stop_process()
                        continue
                    raise TtsError("ENGINE_CONNECTION", "朗读引擎连接中断或超时。") from None
        finally:
            self._operation.release()

    def _stop_process(self):
        with self._lifecycle:
            process, job, log = self._process, self._job, self._log
            self._process = self._job = self._log = None
            self._endpoint = None
            try:
                if process:
                    try:
                        if process.stdin:
                            process.stdin.close()
                    except OSError:  # A crashed child may have broken its pipe.
                        pass
                    if job:
                        job()
                    try:
                        if process.poll() is None:
                            process.terminate()
                    except OSError:
                        if process.poll() is None:
                            raise
                    try:
                        process.wait(timeout=self.config.shutdown_timeout)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=self.config.shutdown_timeout)
            finally:
                if log:
                    log.close()

    def close(self):
        self._closed.set()
        self.cancel()
        self._stop_process()
        atexit.unregister(self.close)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
