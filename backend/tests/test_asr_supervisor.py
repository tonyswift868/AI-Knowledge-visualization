from __future__ import annotations

from pathlib import Path

from app import asr_supervisor, classroom_media


def test_default_asr_port_is_distinct_from_voice_port(monkeypatch):
    monkeypatch.delenv("MENTORFLOW_CLASSROOM_ASR_PORT", raising=False)
    monkeypatch.delenv("MENTORFLOW_CLASSROOM_ASR_URL", raising=False)
    assert asr_supervisor.worker_port() == 9882
    assert asr_supervisor.worker_url() == "http://127.0.0.1:9882"


def test_frozen_layout_discovers_signed_sibling_asr_project(tmp_path, monkeypatch):
    frozen_root = tmp_path / "product" / "release" / "MentorFlow" / "_internal"
    shared = tmp_path / "product" / "ai_agent"
    (shared / "agent_core").mkdir(parents=True)
    (shared / "tools").mkdir(parents=True)
    (shared / "agent_core" / "local_asr_sensevoice_client.py").write_text("# client", encoding="utf-8")
    (shared / "tools" / "kristina_sensevoice_worker.py").write_text("# worker", encoding="utf-8")
    monkeypatch.delenv("MENTORFLOW_AI_AGENT_ROOT", raising=False)
    monkeypatch.setattr(asr_supervisor, "project_root", lambda: frozen_root)

    assert asr_supervisor.shared_root() == shared.resolve()


def test_explicit_asr_root_remains_authoritative(tmp_path, monkeypatch):
    configured = tmp_path / "configured-asr"
    monkeypatch.setenv("MENTORFLOW_AI_AGENT_ROOT", str(configured))
    monkeypatch.setattr(asr_supervisor, "project_root", lambda: tmp_path / "other")

    assert asr_supervisor.shared_root() == configured.resolve()


def test_health_rejects_an_unexpected_engine(monkeypatch):
    monkeypatch.setattr(
        asr_supervisor,
        "_request",
        lambda *_args, **_kwargs: {"ok": True, "engine": "different-engine"},
    )
    monkeypatch.setattr(asr_supervisor, "assets_ready", lambda: True)
    result = asr_supervisor.status()
    assert result["ok"] is False
    assert result["error"] == "unexpected_asr_engine"


def test_transcribe_rejects_paths_outside_shared_allowlist(tmp_path, monkeypatch):
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"RIFF")
    monkeypatch.setattr(asr_supervisor, "upload_root", lambda: Path(tmp_path / "allowed"))
    try:
        asr_supervisor.transcribe(audio)
    except ValueError as exc:
        assert "允许目录" in str(exc)
    else:
        raise AssertionError("out-of-root audio must be rejected")


def test_classroom_audio_stages_under_supervisor_allowlist(
    tmp_path,
    monkeypatch,
):
    allowed = tmp_path / "shared" / "runtime" / "asr_uploads"
    divergent_classroom_root = tmp_path / "classroom" / "uploads"
    monkeypatch.setenv(
        "MENTORFLOW_CLASSROOM_ASR_UPLOAD_ROOT",
        str(divergent_classroom_root),
    )
    monkeypatch.setattr(asr_supervisor, "upload_root", lambda: allowed)

    observed: dict[str, object] = {}

    def fake_transcribe(audio_path, language="auto", *, timeout=45):
        resolved = Path(audio_path).resolve()
        observed["path"] = resolved
        observed["bytes"] = resolved.read_bytes()
        observed["language"] = language
        observed["timeout"] = timeout
        resolved.relative_to(allowed.resolve())
        return {
            "ok": True,
            "text": "测试语音",
            "engine": asr_supervisor.EXPECTED_ENGINE,
            "duration_ms": 640,
        }

    monkeypatch.setattr(asr_supervisor, "transcribe", fake_transcribe)

    result = classroom_media.transcribe_classroom_audio(
        b"RIFF accepted microphone wav",
        filename="recording.wav",
        language="zh",
    )

    assert result["ok"] is True
    assert result["text"] == "测试语音"
    assert observed["bytes"] == b"RIFF accepted microphone wav"
    assert observed["language"] == "zh"
    assert observed["timeout"] == 45
    staged_path = observed["path"]
    assert isinstance(staged_path, Path)
    staged_path.relative_to(allowed.resolve())
    assert not staged_path.exists()
    assert not divergent_classroom_root.exists()
