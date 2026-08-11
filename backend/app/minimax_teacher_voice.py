"""Server-owned MiniMax teacher voice provider.

The browser never receives the API key.  Configuration is intentionally split
between a non-secret voice profile and a secret key file/environment value.
Readiness checks are local-only and never contact MiniMax.
"""

from __future__ import annotations

import json
import hashlib
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx


API_URL = "https://api.minimaxi.com/v1/t2a_v2"
DEFAULT_MODEL = "speech-2.8-hd"
_VOICE_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{6,254}[A-Za-z0-9]$")
_HEX = re.compile(r"^[0-9a-fA-F]+$")
_MAX_CONFIG_BYTES = 32 * 1024
_MAX_AUDIO_BYTES = 12 * 1024 * 1024
_MAX_JSON_RESPONSE_BYTES = _MAX_AUDIO_BYTES * 2 + 1024 * 1024


class MiniMaxTeacherVoiceError(RuntimeError):
    def __init__(self, code: str, message: str, *, status_code: int = 503) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


@dataclass(frozen=True)
class MiniMaxTeacherVoiceConfig:
    voice_id: str
    model: str
    speed_lively: float
    speed_relaxed: float
    profile: str


@dataclass(frozen=True)
class MiniMaxSynthesis:
    audio: bytes
    media_type: str
    profile: str
    model: str
    trace_id: str
    speed: float


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _config_path() -> Path:
    explicit = str(os.getenv("MENTORFLOW_MINIMAX_VOICE_CONFIG") or "").strip()
    if explicit:
        return Path(explicit).expanduser().resolve()
    return (_project_root() / "backend" / "config" / "voice" / "minimax_teacher_voice.local.json").resolve()


def _key_file_path() -> Path:
    explicit = str(os.getenv("MENTORFLOW_MINIMAX_API_KEY_FILE") or "").strip()
    if explicit:
        return Path(explicit).expanduser().resolve()
    return (_project_root() / "MINIMAX_API_KEY.txt").resolve()


def _read_object(path: Path) -> dict[str, Any]:
    try:
        if not path.is_file() or path.stat().st_size > _MAX_CONFIG_BYTES:
            raise MiniMaxTeacherVoiceError(
                "VOICE_MINIMAX_NOT_CONFIGURED",
                "MiniMax 教师声线尚未配置",
            )
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except MiniMaxTeacherVoiceError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MiniMaxTeacherVoiceError(
            "VOICE_MINIMAX_CONFIG_INVALID",
            "MiniMax 教师声线配置无效",
        ) from exc
    if not isinstance(value, dict):
        raise MiniMaxTeacherVoiceError(
            "VOICE_MINIMAX_CONFIG_INVALID",
            "MiniMax 教师声线配置无效",
        )
    return value


def load_minimax_teacher_voice_config() -> MiniMaxTeacherVoiceConfig:
    raw = _read_object(_config_path())
    if raw.get("schema") != "mentorflow.minimax-teacher-voice.local.v1" or raw.get("enabled") is not True:
        raise MiniMaxTeacherVoiceError(
            "VOICE_MINIMAX_CONFIG_INVALID",
            "MiniMax 教师声线配置无效",
        )
    voice_id = str(raw.get("voice_id") or "").strip()
    model = str(raw.get("model") or DEFAULT_MODEL).strip()
    profile = str(raw.get("profile") or "teacher-private-clone").strip()
    speeds = raw.get("speeds")
    if not _VOICE_ID.fullmatch(voice_id) or model != DEFAULT_MODEL or not profile or not isinstance(speeds, dict):
        raise MiniMaxTeacherVoiceError(
            "VOICE_MINIMAX_CONFIG_INVALID",
            "MiniMax 教师声线配置无效",
        )
    try:
        lively = float(speeds.get("lively", 1.0))
        relaxed = float(speeds.get("relaxed", 0.9))
    except (TypeError, ValueError) as exc:
        raise MiniMaxTeacherVoiceError(
            "VOICE_MINIMAX_CONFIG_INVALID",
            "MiniMax 教师声线速度配置无效",
        ) from exc
    if not (0.5 <= lively <= 2.0 and 0.5 <= relaxed <= 2.0):
        raise MiniMaxTeacherVoiceError(
            "VOICE_MINIMAX_CONFIG_INVALID",
            "MiniMax 教师声线速度配置无效",
        )
    return MiniMaxTeacherVoiceConfig(voice_id, model, lively, relaxed, profile)


def _read_api_key() -> str:
    direct = str(os.getenv("MENTORFLOW_MINIMAX_API_KEY") or "").strip()
    if direct:
        key = direct
    else:
        path = _key_file_path()
        try:
            metadata = os.lstat(path)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or stat.S_ISLNK(metadata.st_mode)
                or int(getattr(metadata, "st_nlink", 1) or 1) > 1
                or metadata.st_size > 4096
            ):
                raise OSError("missing or oversized key file")
            key = path.read_text(encoding="utf-8-sig").strip()
        except (OSError, UnicodeError) as exc:
            raise MiniMaxTeacherVoiceError(
                "VOICE_MINIMAX_KEY_MISSING",
                "服务器尚未配置 MiniMax 语音凭据",
            ) from exc
    if not key or len(key) > 2048 or any(character.isspace() for character in key):
        raise MiniMaxTeacherVoiceError(
            "VOICE_MINIMAX_KEY_INVALID",
            "服务器 MiniMax 语音凭据无效",
        )
    return key


def minimax_teacher_voice_status() -> dict[str, Any]:
    """Return safe local readiness metadata without making a network call."""
    try:
        config = load_minimax_teacher_voice_config()
        _read_api_key()
    except MiniMaxTeacherVoiceError as exc:
        return {
            "ok": False,
            "warmed": False,
            "engine": "minimax",
            "reason": exc.code,
        }
    return {
        "ok": True,
        "warmed": False,
        "engine": "minimax",
        "profile": config.profile,
        # The browser only needs a stable cache revision, not the provider's
        # raw private voice identifier.
        "release": f"minimax:{hashlib.sha256(config.voice_id.encode('utf-8')).hexdigest()[:16]}",
        "reason": "server_credentials_ready",
    }


async def synthesize_minimax_teacher_voice(text: str, style: str) -> MiniMaxSynthesis:
    config = load_minimax_teacher_voice_config()
    key = _read_api_key()
    speed = config.speed_lively if style == "lively" else config.speed_relaxed
    payload = {
        "model": config.model,
        "text": text,
        "stream": False,
        "voice_setting": {
            "voice_id": config.voice_id,
            "speed": speed,
            "vol": 1.0,
            "pitch": 0,
        },
        "audio_setting": {
            "sample_rate": 32000,
            "bitrate": 128000,
            "format": "mp3",
            "channel": 1,
        },
        "language_boost": "Chinese",
        "subtitle_enable": False,
        "output_format": "hex",
        "aigc_watermark": False,
    }
    try:
        # MiniMax's TLS edge can take longer than a local service to establish
        # the first connection. Keep a bounded but realistic connect window;
        # the overall request is still capped at two minutes.
        timeout = httpx.Timeout(120.0, connect=30.0)
        async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
            async with client.stream(
                "POST",
                API_URL,
                headers={"Authorization": f"Bearer {key}"},
                json=payload,
            ) as response:
                response_status = response.status_code
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > _MAX_JSON_RESPONSE_BYTES:
                        raise MiniMaxTeacherVoiceError(
                            "VOICE_MINIMAX_RESPONSE_TOO_LARGE",
                            "MiniMax 返回内容超过安全上限",
                        )
    except (httpx.HTTPError, OSError) as exc:
        raise MiniMaxTeacherVoiceError(
            "VOICE_MINIMAX_UNREACHABLE",
            "MiniMax 教师语音暂时不可用",
        ) from exc
    finally:
        key = ""

    try:
        result = json.loads(bytes(body))
    except (ValueError, UnicodeError, json.JSONDecodeError) as exc:
        raise MiniMaxTeacherVoiceError(
            "VOICE_MINIMAX_RESPONSE_INVALID",
            "MiniMax 返回了无效响应",
        ) from exc
    base = result.get("base_resp") if isinstance(result, dict) else None
    code = base.get("status_code") if isinstance(base, dict) else None
    if response_status in {401, 403}:
        error_code = "VOICE_MINIMAX_AUTH_FAILED"
    elif response_status == 429:
        error_code = "VOICE_MINIMAX_RATE_LIMITED"
    elif response_status >= 500:
        error_code = "VOICE_MINIMAX_UPSTREAM_FAILED"
    else:
        error_code = "VOICE_MINIMAX_REQUEST_FAILED"
    if response_status >= 400 or code != 0:
        raise MiniMaxTeacherVoiceError(
            error_code,
            "MiniMax 教师语音合成失败",
        )
    data = result.get("data") if isinstance(result, dict) else None
    audio_hex = data.get("audio") if isinstance(data, dict) else None
    if (
        not isinstance(audio_hex, str)
        or len(audio_hex) % 2
        or not _HEX.fullmatch(audio_hex)
        or len(audio_hex) > _MAX_AUDIO_BYTES * 2
    ):
        raise MiniMaxTeacherVoiceError(
            "VOICE_MINIMAX_AUDIO_INVALID",
            "MiniMax 返回了无效音频",
        )
    audio = bytes.fromhex(audio_hex)
    mp3_header = audio.startswith(b"ID3") or (
        len(audio) >= 2 and audio[0] == 0xFF and (audio[1] & 0xE0) == 0xE0
    )
    if len(audio) < 1024 or len(audio) > _MAX_AUDIO_BYTES or not mp3_header:
        raise MiniMaxTeacherVoiceError(
            "VOICE_MINIMAX_AUDIO_INVALID",
            "MiniMax 返回了无效音频",
        )
    trace_id = str(result.get("trace_id") or "") if isinstance(result, dict) else ""
    return MiniMaxSynthesis(audio, "audio/mpeg", config.profile, config.model, trace_id, speed)
