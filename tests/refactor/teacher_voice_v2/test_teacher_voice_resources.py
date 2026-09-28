"""No network, model downloads or writes to the real voice/engine directories."""
import base64
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from refactor.learning.app.speech import SpeechResources
from refactor.learning.voice.tts import config as config_module
from refactor.learning.voice.tts.config import TtsConfig, TtsError
from refactor.learning.voice.tts.runtime import SpeechAudio, TtsRuntime


def installed_fixture(tmp_path):
    engine = tmp_path / "engine"
    for name in ("runtime/python.exe", "GPT_SoVITS/TTS_infer_pack/TTS.py"):
        file = engine / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(b"explicit fixture")
    voice = tmp_path / "retained-voice"
    voice.mkdir()
    for name in ("gpt.ckpt", "sovits.pth", "reference.wav"):
        (voice / name).write_bytes(b"fixture only")
    manifest = voice / "voice-pack.json"
    manifest.write_text(json.dumps({"schema": "mentorflow.voice-pack.v1", "pack_id": "phrolova-b-gpt-sovits", "version": "1.0.4",
        "runtime": {"adapter": "mentorflow.gpt-sovits.v1"},
        "files": [{"role": r, "path": p} for r, p in (("gpt_weight", "gpt.ckpt"), ("sovits_weight", "sovits.pth"), ("reference_audio", "reference.wav"))],
        "styles": {"daily": {"language": "zh", "transcript": "明确替身。", "reference_audio": "reference.wav"}}}), encoding="utf-8")
    base = tmp_path / "base"
    names = ("chinese-roberta-wwm-ext-large/config.json", "chinese-hubert-base/config.json", "G2PWModel/config.py", "sv/pretrained_eres2netv2w24s4ep4.ckpt")
    for name in names:
        path = base / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")
    config = TtsConfig(engine_root=engine, engine_python=engine / "runtime/python.exe", voice_manifest=manifest,
                       base_model_root=base, data_root=tmp_path / "data")
    manager = SimpleNamespace(required={name: (1, "0" * 64) for name in names}, size_bytes=4, path=config.data_root / "models", assets=[])
    return config, SpeechResources(config.data_root, config_factory=lambda _: config, manager=manager)


def test_teacher_voice_v2_reports_local_components_and_missing_file_sizes(tmp_path):
    config, resources = installed_fixture(tmp_path)
    status, _ = resources.inspect()
    assert status["ready"] and not status["download"]["required"]
    assert status["voice"] == {"id": "phrolova-b-gpt-sovits", "version": "1.0.4"}
    target = config.base_model_root / "G2PWModel/config.py"
    target.unlink()
    status, _ = resources.inspect()
    assert not status["ready"] and status["message"] == "朗读未启用"
    assert status["components"][1]["missing_files"] == [{"name": "G2PWModel/config.py", "size_bytes": 1}]
    assert status["download"]["size_bytes"] == 4
    config.engine_python.unlink()
    config.voice_manifest.unlink()
    status, _ = resources.inspect()
    assert all(not part["installed"] for part in status["components"])
    assert status["components"][0]["size_estimated"]


def test_teacher_voice_v2_prefers_readonly_d_copy_and_preserves_explicit_override(tmp_path, monkeypatch):
    config, _ = installed_fixture(tmp_path)
    for key in ("MF_TTS_PACK_ROOTS", "MF_TTS_VOICE_ID", "MF_TTS_VOICE_VERSION", "MF_TTS_VOICE_MANIFEST"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(config_module, "VOICE_BACKUP", config.voice_manifest)
    assert TtsConfig.from_env(tmp_path / "data").voice().manifest == config.voice_manifest
    monkeypatch.setenv("MF_TTS_VOICE_MANIFEST", str(tmp_path / "missing.json"))
    with pytest.raises(TtsError, match="声音包未安装"):
        TtsConfig.from_env(tmp_path / "data").voice()
    monkeypatch.delenv("MF_TTS_VOICE_MANIFEST")
    monkeypatch.setenv("MF_TTS_PACK_ROOTS", str(tmp_path / "explicit"))
    assert TtsConfig.from_env(tmp_path / "data").voice_manifest is None


def test_teacher_voice_v2_speed_goes_in_both_inference_requests_without_restart(tmp_path, monkeypatch):
    audio = SpeechAudio(b"\x01\x00" * 320, 32000, .01)
    requests = []
    runtime = TtsRuntime(TtsConfig(data_root=tmp_path), transport=lambda method, route, payload, *_: (
        requests.append((route, payload)) or (200, audio.wav_bytes(), {})))
    monkeypatch.setattr(runtime, "_ensure_ready", lambda _: {"ready": True})
    class Response:
        status = 200
        def __init__(self):
            self.raw = io.BytesIO((json.dumps({"pcm": base64.b64encode(audio.pcm).decode(), "sample_rate": 32000, "synthesis_seconds": .01, "final": True}) + "\n").encode())
        def readline(self, _): return self.raw.readline()
    class Connection:
        def __init__(self, *args, **kwargs): pass
        def request(self, method, route, body, headers): requests.append((route, json.loads(body)))
        def getresponse(self): return Response()
        def close(self): pass
    monkeypatch.setattr("refactor.learning.voice.tts.runtime.http.client.HTTPConnection", Connection)
    runtime._endpoint = {"port": 1, "token": "explicit-test-token"}
    try:
        runtime.set_speed(1.4)
        runtime.synthesize("普通请求。")
        chunks = []
        runtime.stream_synthesize("流式请求。", chunks.append)
        assert [(route, payload["speed"]) for route, payload in requests] == [("/synthesize", 1.4), ("/stream", 1.4)]
        assert chunks[0].pcm == audio.pcm and runtime.pid is None
        with pytest.raises(TtsError): runtime.set_speed(float("inf"))
    finally:
        runtime.close()
