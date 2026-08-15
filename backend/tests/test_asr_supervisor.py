from __future__ import annotations

from pathlib import Path

from app import asr_supervisor


def test_default_asr_port_is_distinct_from_voice_port(monkeypatch):
    monkeypatch.delenv("MENTORFLOW_CLASSROOM_ASR_PORT", raising=False)
    monkeypatch.delenv("MENTORFLOW_CLASSROOM_ASR_URL", raising=False)
    assert asr_supervisor.worker_port() == 9882
    assert asr_supervisor.worker_url() == "http://127.0.0.1:9882"


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
