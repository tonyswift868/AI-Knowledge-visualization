from dataclasses import replace
import base64
import io
import json
from pathlib import Path
import subprocess
import threading

import pytest

from refactor.learning.voice.tts.config import TtsCancelled, TtsError, VoicePack
from refactor.learning.voice.tts.runtime import SpeechAudio, TtsRuntime


class FakeProcess:
    def __init__(self, pid):
        self.pid, self.code = pid, None
        self.stdin = io.BytesIO()
        self.terminated = self.killed = False
        self.stubborn = False

    def poll(self):
        return self.code

    def terminate(self):
        self.terminated = True
        if not self.stubborn:
            self.code = -15

    def kill(self):
        self.killed, self.code = True, -9

    def wait(self, timeout):
        if self.code is None:
            raise subprocess.TimeoutExpired("explicit fake process", timeout)
        return self.code


class FakeEngine:
    def __init__(self):
        self.processes, self.launches, self.requests = [], [], []
        self.ready, self.crash_count = True, 0
        self.bad_audio = False

    def launch(self, args, **kwargs):
        config = json.loads(Path(args[-1]).read_text(encoding="utf-8"))
        process = FakeProcess(1000 + len(self.processes))
        self.processes.append(process)
        self.launches.append((args, kwargs))
        Path(kwargs["cwd"], "endpoint.json").write_text(json.dumps({"port": 50123, "pid": process.pid, "instance": config["instance"]}))
        return process

    def request(self, method, path, body, timeout, endpoint):
        self.requests.append(path)
        if path == "/health":
            return 200, json.dumps({"pid": self.processes[-1].pid, "instance": endpoint["instance"], "ready": self.ready}).encode(), {}
        if self.crash_count:
            self.crash_count -= 1
            self.processes[-1].code = 1
            raise ConnectionResetError("explicit fake crash")
        audio = SpeechAudio(b"\x01\x00" * 3200, 32000, .02)
        return 200, b"not wav" if self.bad_audio else audio.wav_bytes(), {"X-Synthesis-Seconds": ".02"}


@pytest.fixture
def rig(config):
    fake = FakeEngine()
    runtime = TtsRuntime(config, process_factory=fake.launch, transport=fake.request, job_factory=lambda _: None)
    yield runtime, fake
    runtime.close()


def test_resident_start_once_environment_scoped_and_reliable_close(rig, config):
    runtime, fake = rig
    assert runtime.warm_up()["ready"]
    runtime.warm_up()
    assert runtime.synthesize("我们先看一个例子。").duration_seconds == .1
    assert len(fake.processes) == 1
    args, options = fake.launches[0]
    assert args[0] == str(config.engine_python) and "-B" in args
    assert Path(options["cwd"]).is_relative_to(config.data_root)
    for key in ("TEMP", "TMP", "HF_HOME", "TORCH_HOME", "CUDA_CACHE_PATH", "NUMBA_CACHE_DIR"):
        assert Path(options["env"][key]).is_relative_to(config.data_root)
    assert options["env"]["HF_HUB_OFFLINE"] == "1"
    process = fake.processes[0]
    runtime.close()
    runtime.close()
    assert process.stdin.closed and process.terminated and runtime.pid is None


def test_crash_before_next_call_restarts_only_owned_process(rig):
    runtime, fake = rig
    runtime.warm_up()
    fake.processes[0].code = 1
    runtime.synthesize("重启后继续。")
    assert len(fake.processes) == 2


def test_mid_request_crash_retries_once_and_second_crash_surfaces(rig):
    runtime, fake = rig
    fake.crash_count = 1
    runtime.synthesize("中途崩溃后重试。")
    assert len(fake.processes) == 2
    fake.crash_count = 2
    with pytest.raises(TtsError):
        runtime.synthesize("再次崩溃。")
    assert len(fake.processes) == 3


def test_timeout_and_stubborn_child_get_killed(rig):
    runtime, fake = rig
    runtime.warm_up()
    fake.processes[0].stubborn = True
    runtime.close()
    assert fake.processes[0].killed


def test_broken_stdin_does_not_skip_owned_process_cleanup(rig):
    runtime, fake = rig
    runtime.warm_up()
    class BrokenPipe:
        def close(self):
            raise BrokenPipeError("explicit fake broken child pipe")
    fake.processes[0].stdin = BrokenPipe()
    log = runtime._log
    runtime.close()
    assert fake.processes[0].terminated and log.closed


def test_failed_job_binding_cleans_up_launched_child(config):
    fake = FakeEngine()
    def failed_job(process):
        raise OSError("explicit fake job binding failure")
    with TtsRuntime(config, process_factory=fake.launch, transport=fake.request, job_factory=failed_job) as runtime:
        with pytest.raises(TtsError) as error:
            runtime.warm_up()
        assert error.value.code == "ENGINE_START_FAILED"
        assert runtime.pid is None and fake.processes[0].terminated


def test_startup_timeout_releases_process(rig):
    runtime, fake = rig
    fake.ready = False
    with pytest.raises(TtsError, match="超时"):
        runtime.warm_up()
    assert fake.processes[0].terminated


def test_cancel_does_not_leak_lock_and_warmup_can_resume(rig):
    runtime, fake = rig
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(TtsCancelled):
        runtime.warm_up(cancel)
    assert runtime.warm_up()["ready"]
    assert len(fake.processes) == 1


def test_bad_audio_is_not_playable_and_does_not_restart(rig):
    runtime, fake = rig
    fake.bad_audio = True
    with pytest.raises(TtsError, match="音频无效"):
        runtime.synthesize("坏音频。")
    assert len(fake.processes) == 1


def test_catalog_default_and_no_writes_to_source(config):
    before = {path: path.read_bytes() for path in config.pack_roots[0].rglob("*") if path.is_file()}
    voice = config.validate()
    assert voice.version == "1.0.4" and voice.reference_text == "这是参考声音。"
    assert before == {path: path.read_bytes() for path in before}
    with pytest.raises(TtsError):
        replace(config, data_root=config.engine_root / "cache").validate()
    with pytest.raises(TtsError):
        replace(config, voice_version="../escape").validate()
    with pytest.raises(TtsError):
        replace(config, voice_id="kristina-kurisu-gpt-sovits").validate()


def test_manifest_rejects_reference_escape(config):
    manifest = config.voice().manifest
    data = json.loads(manifest.read_text())
    data["styles"]["daily"]["reference_audio"] = "../../other.wav"
    manifest.write_text(json.dumps(data))
    with pytest.raises(TtsError):
        VoicePack.load(manifest)


def test_missing_common_assets_are_explicit_and_fail_before_launch(config):
    fake = FakeEngine()
    missing = replace(config, sv_weights=config.engine_root / "missing-sv.ckpt")
    with TtsRuntime(missing, process_factory=fake.launch, transport=fake.request, job_factory=lambda _: None) as runtime:
        with pytest.raises(TtsError) as error:
            runtime.warm_up()
        assert error.value.code == "BASE_ASSET_MISSING"
    assert not fake.launches


def _stream_connection(monkeypatch, lines):
    class Response:
        status = 200
        def readline(self, size):
            item = lines.pop(0) if lines else b""
            return item() if callable(item) else item
    class Connection:
        sock = None
        def __init__(self, *args, **kwargs): pass
        def request(self, *args, **kwargs): pass
        def getresponse(self): return Response()
        def close(self): pass
    monkeypatch.setattr("refactor.learning.voice.tts.runtime.http.client.HTTPConnection", Connection)


def _frame(pcm=b"\x01\x00" * 10, final=False):
    return json.dumps({"pcm": base64.b64encode(pcm).decode(), "sample_rate": 32000,
                       "synthesis_seconds": .01, "final": final}).encode() + b"\n"


def test_stream_delivers_first_pcm_before_final_and_clears_connection(rig, monkeypatch):
    runtime, _ = rig
    chunks = []
    def final_line():
        assert len(chunks) == 1 and not chunks[0].final
        return _frame(b"", True)
    _stream_connection(monkeypatch, [_frame(), final_line])
    runtime.stream_synthesize("逐块生成。", chunks.append)
    assert len(chunks) == 2 and chunks[-1].final and runtime._active is None


def test_stream_cancellation_discards_future_frames(rig, monkeypatch):
    runtime, _ = rig
    cancel = threading.Event()
    _stream_connection(monkeypatch, [_frame(), _frame(b"", True)])
    with pytest.raises(TtsCancelled):
        runtime.stream_synthesize("取消剩余声音。", lambda _: cancel.set(), cancel=cancel)
    assert runtime._active is None


def test_partial_stream_crash_does_not_replay_already_spoken_words(rig, monkeypatch):
    runtime, fake = rig
    chunks = []
    def crash():
        fake.processes[0].code = 1
        return b""
    _stream_connection(monkeypatch, [_frame(), crash])
    with pytest.raises(TtsError) as error:
        runtime.stream_synthesize("已经说出的不能重复。", chunks.append)
    assert error.value.code == "ENGINE_CONNECTION" and len(fake.processes) == len(chunks) == 1
    _stream_connection(monkeypatch, [_frame(), _frame(b"", True)])
    runtime.stream_synthesize("下次调用重启。", lambda _: None)
    assert len(fake.processes) == 2


def test_stream_crash_before_audio_restarts_once(rig, monkeypatch):
    runtime, fake = rig
    def crash():
        fake.processes[0].code = 1
        return b""
    _stream_connection(monkeypatch, [crash, _frame(), _frame(b"", True)])
    chunks = []
    runtime.stream_synthesize("首音前崩溃可以重试。", chunks.append)
    assert len(fake.processes) == 2 and len(chunks) == 2


def test_invalid_stream_audio_never_reaches_playback(rig, monkeypatch):
    runtime, _ = rig
    _stream_connection(monkeypatch, [_frame(b"odd")])
    chunks = []
    with pytest.raises(TtsError) as error:
        runtime.stream_synthesize("损坏的音频。", chunks.append)
    assert error.value.code == "AUDIO_INVALID" and not chunks


def test_readonly_child_guard_denies_source_writes(config, tmp_path):
    import os
    import sys
    writable = tmp_path / "guard-run"
    writable.mkdir()
    source = config.engine_root / "python.exe"
    before = source.read_bytes()
    script = """from pathlib import Path
from refactor.learning.voice.tts.engine_server import _readonly_guard
import sys
_readonly_guard(sys.argv[1])
Path(sys.argv[1], 'allowed.txt').write_text('ok')
try:
    Path(sys.argv[2]).write_text('bad')
except PermissionError:
    print('blocked')
else:
    raise AssertionError('source write was allowed')
"""
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    result = subprocess.run([sys.executable, "-B", "-c", script, str(writable), str(source)],
                            env=env, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "blocked" and source.read_bytes() == before


def test_official_language_detector_uses_packaged_model_or_fails_offline(tmp_path, monkeypatch):
    import sys
    from types import ModuleType, SimpleNamespace
    from refactor.learning.voice.tts.engine_server import _use_bundled_language_model

    package = ModuleType("fast_langdetect")
    detector = ModuleType("fast_langdetect.infer")
    detector.__file__ = str(tmp_path / "infer.py")
    detector.LangDetectConfig = SimpleNamespace
    detector.LangDetector = lambda config: SimpleNamespace(config=config)
    detector._default_detector = original = object()
    package.infer = detector
    monkeypatch.setitem(sys.modules, "fast_langdetect", package)
    monkeypatch.setitem(sys.modules, "fast_langdetect.infer", detector)
    with pytest.raises(RuntimeError, match="Bundled language model is missing"):
        _use_bundled_language_model()
    assert detector._default_detector is original
    model = tmp_path / "resources/lid.176.ftz"
    model.parent.mkdir()
    model.write_bytes(b"pinned fixture")
    _use_bundled_language_model()
    bound = detector._default_detector.config
    assert Path(bound.custom_model_path) == model
    assert bound.allow_fallback is False
    assert model.read_bytes() == b"pinned fixture"
