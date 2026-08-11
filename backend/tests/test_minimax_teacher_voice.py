import asyncio
import json
from pathlib import Path

import httpx
import pytest

from app import casual_chat, minimax_teacher_voice as minimax


def _configure(tmp_path: Path, monkeypatch, *, key: str = "test-secret-key") -> tuple[Path, Path]:
    config = tmp_path / "minimax.json"
    config.write_text(
        json.dumps(
            {
                "schema": "mentorflow.minimax-teacher-voice.local.v1",
                "enabled": True,
                "voice_id": "MentorFlowTestVoice123",
                "model": "speech-2.8-hd",
                "profile": "test-private-profile",
                "speeds": {"lively": 1.0, "relaxed": 1.0},
            }
        ),
        encoding="utf-8",
    )
    key_file = tmp_path / "key.txt"
    key_file.write_text(key, encoding="utf-8")
    monkeypatch.setenv("MENTORFLOW_MINIMAX_VOICE_CONFIG", str(config))
    monkeypatch.setenv("MENTORFLOW_MINIMAX_API_KEY_FILE", str(key_file))
    monkeypatch.delenv("MENTORFLOW_MINIMAX_API_KEY", raising=False)
    return config, key_file


def test_status_is_local_only_and_never_exposes_key(tmp_path, monkeypatch):
    _configure(tmp_path, monkeypatch, key="super-private-token")

    status = minimax.minimax_teacher_voice_status()

    assert status == {
        "ok": True,
        "warmed": False,
        "engine": "minimax",
        "profile": "test-private-profile",
        "release": "minimax:6ee2d5bef73705c1",
        "reason": "server_credentials_ready",
    }
    assert "super-private-token" not in json.dumps(status)


def test_synthesis_sends_server_key_and_returns_valid_mp3(tmp_path, monkeypatch):
    _configure(tmp_path, monkeypatch, key="server-only-token")
    captured = {}

    class FakeClient:
        def __init__(self, **kwargs):
            captured["client"] = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        def stream(self, _method, url, *, headers, json):
            captured.update(url=url, headers=headers, payload=json)
            response = httpx.Response(
                200,
                json={
                    "base_resp": {"status_code": 0, "status_msg": "success"},
                    "data": {"audio": (b"ID3" + b"x" * 2048).hex()},
                    "trace_id": "trace-safe",
                },
            )

            class Stream:
                async def __aenter__(self):
                    return response

                async def __aexit__(self, *_args):
                    return None

            return Stream()

    monkeypatch.setattr(minimax.httpx, "AsyncClient", FakeClient)
    result = asyncio.run(minimax.synthesize_minimax_teacher_voice("你好", "relaxed"))

    assert result.audio.startswith(b"ID3")
    assert result.media_type == "audio/mpeg"
    assert result.profile == "test-private-profile"
    assert captured["url"] == minimax.API_URL
    assert captured["headers"] == {"Authorization": "Bearer server-only-token"}
    assert captured["payload"]["voice_setting"] == {
        "voice_id": "MentorFlowTestVoice123",
        "speed": 1.0,
        "vol": 1.0,
        "pitch": 0,
    }
    assert captured["client"]["trust_env"] is False


def test_upstream_auth_failure_is_sanitized(tmp_path, monkeypatch):
    _configure(tmp_path, monkeypatch, key="must-not-leak")

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        def stream(self, *_args, **_kwargs):
            response = httpx.Response(
                401,
                json={"base_resp": {"status_code": 1004, "status_msg": "must-not-leak"}},
            )

            class Stream:
                async def __aenter__(self):
                    return response

                async def __aexit__(self, *_args):
                    return None

            return Stream()

    monkeypatch.setattr(minimax.httpx, "AsyncClient", FakeClient)
    with pytest.raises(minimax.MiniMaxTeacherVoiceError) as captured:
        asyncio.run(minimax.synthesize_minimax_teacher_voice("你好", "relaxed"))
    assert captured.value.code == "VOICE_MINIMAX_AUTH_FAILED"
    assert "must-not-leak" not in str(captured.value)


def test_casual_voice_minimax_integration_returns_audio_without_key(client, monkeypatch):
    monkeypatch.setenv("MENTORFLOW_TEACHER_VOICE_PROVIDER", "minimax")

    async def synthesize(_text: str, _style: str):
        return minimax.MiniMaxSynthesis(
            b"ID3" + b"x" * 2048,
            "audio/mpeg",
            "private-profile",
            "speech-2.8-hd",
            "trace-hidden",
            1.0,
        )

    monkeypatch.setattr(casual_chat, "synthesize_minimax_teacher_voice", synthesize)
    response = client.post("/api/casual-chat/voice/synthesize", json={"text": "开始吧"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("audio/mpeg")
    assert response.headers["x-mentorflow-voice-engine"] == "minimax"
    assert "trace-hidden" not in json.dumps(dict(response.headers))


def test_casual_voice_minimax_warm_is_read_only(client, monkeypatch):
    monkeypatch.setenv("MENTORFLOW_TEACHER_VOICE_PROVIDER", "minimax")
    monkeypatch.setattr(
        casual_chat,
        "minimax_teacher_voice_status",
        lambda: {
            "ok": True,
            "warmed": False,
            "engine": "minimax",
            "profile": "private-profile",
            "release": "minimax:test",
            "reason": "server_credentials_ready",
        },
    )
    response = client.post("/api/casual-chat/voice/warm")
    assert response.status_code == 200
    assert response.json()["engine"] == "minimax"
    assert response.json()["warmed"] is False
