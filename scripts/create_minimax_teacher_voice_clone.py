#!/usr/bin/env python3
"""Create one private MiniMax voice clone and auditable audition files.

The API key is read from a caller-provided file and is never written to an
artifact or included in an exception message.  Output generations are
CREATE_NEW so a failed or partial attempt is never silently reused.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import requests


API_ROOT = "https://api.minimaxi.com/v1"
MODEL = "speech-2.8-hd"
VOICE_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{6,254}[A-Za-z0-9]$")


class CloneError(RuntimeError):
    pass


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_new(path: Path, value: dict[str, Any]) -> None:
    raw = (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    with path.open("xb") as handle:
        handle.write(raw)


def checked_json(response: requests.Response, label: str) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError as exc:
        raise CloneError(f"{label} returned non-JSON HTTP {response.status_code}") from exc
    base = payload.get("base_resp") if isinstance(payload, dict) else None
    code = base.get("status_code") if isinstance(base, dict) else None
    if response.status_code >= 400 or code not in (None, 0):
        message = str(base.get("status_msg") if isinstance(base, dict) else "request failed")
        raise CloneError(f"{label} failed: HTTP {response.status_code}, code={code}, message={message[:300]}")
    if not isinstance(payload, dict):
        raise CloneError(f"{label} response is not an object")
    return payload


def tts(
    session: requests.Session,
    *,
    voice_id: str,
    text: str,
    speed: float,
    destination: Path,
) -> dict[str, Any]:
    response = session.post(
        f"{API_ROOT}/t2a_v2",
        json={
            "model": MODEL,
            "text": text,
            "stream": False,
            "voice_setting": {"voice_id": voice_id, "speed": speed, "vol": 1.0, "pitch": 0},
            "audio_setting": {"sample_rate": 32000, "bitrate": 128000, "format": "mp3", "channel": 1},
            "language_boost": "Chinese",
            "subtitle_enable": False,
            "output_format": "hex",
            "aigc_watermark": False,
        },
        timeout=240,
    )
    payload = checked_json(response, f"TTS {destination.name}")
    data = payload.get("data")
    audio_hex = data.get("audio") if isinstance(data, dict) else None
    if not isinstance(audio_hex, str) or not audio_hex:
        raise CloneError(f"TTS {destination.name} returned no audio")
    try:
        audio = bytes.fromhex(audio_hex)
    except ValueError as exc:
        raise CloneError(f"TTS {destination.name} returned invalid audio encoding") from exc
    if len(audio) < 1024:
        raise CloneError(f"TTS {destination.name} returned implausibly small audio")
    with destination.open("xb") as handle:
        handle.write(audio)
    return {
        "path": destination.name,
        "text": text,
        "speed": speed,
        "bytes": len(audio),
        "sha256": sha256(destination),
        "trace_id": payload.get("trace_id"),
        "extra_info": payload.get("extra_info"),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--key-file", type=Path, required=True)
    parser.add_argument("--audio", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--voice-id")
    args = parser.parse_args()

    key = args.key_file.read_text(encoding="utf-8-sig").strip()
    if not key or any(character.isspace() for character in key):
        raise CloneError("API key file must contain one non-empty token")
    audio = args.audio.resolve(strict=True)
    if not audio.is_file() or audio.suffix.lower() not in {".mp3", ".m4a", ".wav"}:
        raise CloneError("clone audio must be an MP3, M4A, or WAV file")
    if audio.stat().st_size > 20_000_000:
        raise CloneError("clone audio exceeds MiniMax 20 MB limit")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    voice_id = args.voice_id or f"MentorFlowTsubaki{stamp}{uuid4().hex[:6]}"
    if not VOICE_ID.fullmatch(voice_id):
        raise CloneError("voice_id does not satisfy MiniMax naming rules")
    generation = args.output_root.resolve(strict=False) / f"minimax-clone-{stamp}-{uuid4().hex[:8]}"
    generation.mkdir(parents=True, exist_ok=False)

    session = requests.Session()
    session.headers.update({"Authorization": f"Bearer {key}"})
    try:
        with audio.open("rb") as handle:
            upload_response = session.post(
                f"{API_ROOT}/files/upload",
                data={"purpose": "voice_clone"},
                files={"file": (audio.name, handle, "audio/mpeg")},
                timeout=300,
            )
        upload = checked_json(upload_response, "voice upload")
        uploaded = upload.get("file")
        file_id = uploaded.get("file_id") if isinstance(uploaded, dict) else None
        if isinstance(file_id, bool) or not isinstance(file_id, int):
            raise CloneError("voice upload returned no file_id")

        clone_text = "别怕。<#0.45#>我只是想把你留在身边。<#0.65#>再久一点点。"
        clone_response = session.post(
            f"{API_ROOT}/voice_clone",
            json={
                "file_id": file_id,
                "voice_id": voice_id,
                "text": clone_text,
                "model": MODEL,
                "language_boost": "Chinese",
                "need_noise_reduction": False,
                "need_volume_normalization": False,
                "aigc_watermark": False,
            },
            timeout=300,
        )
        clone = checked_json(clone_response, "voice clone")

        outputs = [
            tts(session, voice_id=voice_id, text="别怕，我只是想把你留在身边，再久一点点。", speed=1.0, destination=generation / "01_native_1x.mp3"),
            tts(session, voice_id=voice_id, text=clone_text, speed=0.80, destination=generation / "02_slow_080x.mp3"),
            tts(session, voice_id=voice_id, text="你可以去任何地方。<#0.70#>只要最后，还是回到我这里。", speed=0.80, destination=generation / "03_style_080x.mp3"),
        ]
        receipt = {
            "schema": "mentorflow.minimax-private-voice-clone.v1",
            "status": "passed",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "provider": "MiniMax",
            "api_root": API_ROOT,
            "model": MODEL,
            "voice_id": voice_id,
            "scope": "private_local_evaluation",
            "source": {"path": str(audio), "bytes": audio.stat().st_size, "sha256": sha256(audio)},
            "upload": {"file_id": file_id, "purpose": "voice_clone"},
            "clone": {
                "base_resp": clone.get("base_resp"),
                "input_sensitive": clone.get("input_sensitive"),
                "input_sensitive_type": clone.get("input_sensitive_type"),
                "demo_audio_present": bool(clone.get("demo_audio")),
                "extra_info": clone.get("extra_info"),
            },
            "outputs": outputs,
            "api_key_persisted": False,
            "human_listening_review": "pending",
        }
        write_new(generation / "CLONE_RECEIPT.json", receipt)
        print(json.dumps({"status": "passed", "generation": str(generation), "voice_id": voice_id, "outputs": outputs}, ensure_ascii=False))
        return 0
    except Exception as exc:
        write_new(
            generation / "FAILURE.json",
            {
                "schema": "mentorflow.minimax-private-voice-clone-failure.v1",
                "status": "failed",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "source_sha256": sha256(audio),
                "voice_id": voice_id,
                "error_type": type(exc).__name__,
                "error": str(exc)[:1000],
                "api_key_persisted": False,
            },
        )
        raise
    finally:
        session.close()
        key = ""


if __name__ == "__main__":
    raise SystemExit(main())
