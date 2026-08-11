#!/usr/bin/env python3
"""Build a true speech-normalized 1.0x audition by extending phoneme audio."""
from __future__ import annotations

import hashlib
import json
import math
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

import librosa
import numpy as np
import soundfile as sf

SOURCE = Path(r"E:\EV\20260808_164409.training\auditions\tsubaki-fullstyle-v4-step100-normalized1x-phrases-v2-20260808T113729Z-ac63a908")
OUTPUT_PARENT = Path(r"E:\EV\20260808_164409.training\auditions")
TEXT = "别怕，我只是想把你留在身边，再久一点点。"
PHRASES = ["别怕……", "我只是想……把你留在身边。", "再久一点点……"]
CHAR_COUNT = 17
SAMPLE_RATE = 24000
TOTAL_STRETCH_RATE = 0.40
STAGE_RATE = math.sqrt(TOTAL_STRETCH_RATE)
LEAD_SECONDS = 0.05
INTER_SECONDS = 0.35
TAIL_SECONDS = 0.10


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_new(path: Path, payload: bytes) -> None:
    with path.open("xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())


def silence(seconds: float) -> np.ndarray:
    return np.zeros(round(seconds * SAMPLE_RATE), dtype=np.float32)


def median_f0(y: np.ndarray) -> float | None:
    f0, voiced, _ = librosa.pyin(
        y, fmin=70.0, fmax=700.0, sr=SAMPLE_RATE,
        frame_length=1024, hop_length=256,
    )
    values = f0[np.asarray(voiced, dtype=bool) & np.isfinite(f0)]
    return None if values.size == 0 else float(np.median(values))


def stretch_two_stage(y: np.ndarray) -> np.ndarray:
    first = librosa.effects.time_stretch(y, rate=STAGE_RATE, n_fft=1024, hop_length=256)
    second = librosa.effects.time_stretch(first, rate=STAGE_RATE, n_fft=1024, hop_length=256)
    return np.asarray(second, dtype=np.float32)


def main() -> int:
    manifest_path = SOURCE / "INFERENCE_MANIFEST.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("texts") != PHRASES:
        raise RuntimeError("source phrases mismatch")
    if manifest.get("scope") != "private_local_nonrelease_audition_only_salvage_from_failed_owner":
        raise RuntimeError("source scope mismatch")

    sources = [SOURCE / f"teacher_short_{i:02d}.wav" for i in range(1, 4)]
    trimmed: list[np.ndarray] = []
    stretched: list[np.ndarray] = []
    phrase_receipts = []
    for path, item in zip(sources, manifest["outputs"], strict=True):
        if sha(path) != item.get("sha256") or not item.get("qa", {}).get("passed"):
            raise RuntimeError("source hash/QA mismatch")
        audio, sr = sf.read(str(path), dtype="float32", always_2d=False)
        audio = np.asarray(audio, dtype=np.float32)
        if sr != SAMPLE_RATE or audio.ndim != 1 or not np.isfinite(audio).all():
            raise RuntimeError("invalid source audio")
        clean, trim_indices = librosa.effects.trim(audio, top_db=40, frame_length=512, hop_length=128)
        changed = stretch_two_stage(np.asarray(clean, dtype=np.float32))
        if changed.size == 0 or not np.isfinite(changed).all():
            raise RuntimeError("invalid stretched phrase")
        trimmed.append(clean)
        stretched.append(changed)
        phrase_receipts.append({
            "path": str(path), "sha256": sha(path),
            "trim_samples": [int(trim_indices[0]), int(trim_indices[1])],
            "trimmed_seconds": round(clean.size / SAMPLE_RATE, 6),
            "stretched_seconds": round(changed.size / SAMPLE_RATE, 6),
        })

    inserted_silence_seconds = LEAD_SECONDS + 2 * INTER_SECONDS + TAIL_SECONDS
    if inserted_silence_seconds > 1.2:
        raise RuntimeError("inserted silence exceeds contract")
    carrier = np.concatenate(stretched)
    carrier_seconds = carrier.size / SAMPLE_RATE
    voiced_cps = CHAR_COUNT / carrier_seconds
    final_audio = np.concatenate([
        silence(LEAD_SECONDS), stretched[0], silence(INTER_SECONDS),
        stretched[1], silence(INTER_SECONDS), stretched[2], silence(TAIL_SECONDS),
    ]).astype(np.float32, copy=False)
    peak = float(np.max(np.abs(final_audio)))
    if peak > 0.98:
        final_audio *= np.float32(0.98 / peak)

    generation_id = "tsubaki-fullstyle-v4-true-speech-normalized-1x-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    generation = OUTPUT_PARENT / generation_id
    os.mkdir(generation)
    partial = generation / "tsubaki_true_1x.wav.partial"
    output = generation / "tsubaki_true_1x.wav"
    sf.write(str(partial), final_audio, SAMPLE_RATE, subtype="PCM_16", format="WAV")
    os.replace(partial, output)

    rendered, sr = sf.read(str(output), dtype="float32", always_2d=False)
    rendered = np.asarray(rendered, dtype=np.float32)
    info = sf.info(str(output))
    duration = rendered.size / sr
    total_cps = CHAR_COUNT / duration
    source_f0 = median_f0(np.concatenate(trimmed))
    stretched_f0 = median_f0(carrier)
    semitones = None if source_f0 is None or stretched_f0 is None else 12.0 * math.log2(stretched_f0 / source_f0)
    clipped = float(np.mean(np.abs(rendered) >= 0.999))
    qa = {
        "passed": bool(
            info.format == "WAV" and info.subtype == "PCM_16" and info.channels == 1 and sr == SAMPLE_RATE
            and 2.8 <= voiced_cps <= 3.5 and 2.4 <= total_cps <= 3.0
            and inserted_silence_seconds <= 1.2 and np.isfinite(rendered).all() and clipped == 0.0
            and semitones is not None and abs(semitones) <= 0.35
        ),
        "format": info.format, "subtype": info.subtype, "channels": info.channels,
        "sample_rate": sr, "frames": int(rendered.size), "duration_seconds": round(duration, 6),
        "character_count": CHAR_COUNT, "voiced_phoneme_carrier_seconds": round(carrier_seconds, 6),
        "voiced_characters_per_second": round(voiced_cps, 6),
        "total_characters_per_second": round(total_cps, 6),
        "inserted_silence_seconds": round(inserted_silence_seconds, 6),
        "finite": bool(np.isfinite(rendered).all()),
        "peak_absolute": round(float(np.max(np.abs(rendered))), 8),
        "clipped_fraction": round(clipped, 8),
        "source_median_f0_hz": None if source_f0 is None else round(source_f0, 6),
        "stretched_median_f0_hz": None if stretched_f0 is None else round(stretched_f0, 6),
        "median_f0_shift_semitones": None if semitones is None else round(semitones, 6),
    }
    receipt = {
        "schema": "mentorflow.qwen3-tts-true-speech-normalized-1x-audition.v1",
        "status": "passed" if qa["passed"] else "failed",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "generation_id": generation_id,
        "scope": "private_local_nonrelease_audition_only_salvage_from_failed_owner",
        "audition_only": True, "release_eligible": False,
        "human_perceptual_review_pending": True,
        "artifact_risk": "material two-stage phase-vocoder stretch; listen before acceptance",
        "source_generation": str(SOURCE), "source_manifest_sha256": sha(manifest_path),
        "old_audition_audio_reused": False, "training_performed": False,
        "text": TEXT, "phrases": PHRASES, "phrase_sources": phrase_receipts,
        "normalization": {
            "meaning_of_1x": "actual_phoneme_duration_normalized_to_source_like_rate",
            "method": "two_stage_pitch_preserving_phase_vocoder",
            "stage_rate": STAGE_RATE, "effective_rate": TOTAL_STRETCH_RATE,
            "lead_seconds": LEAD_SECONDS, "inter_phrase_seconds": INTER_SECONDS,
            "tail_seconds": TAIL_SECONDS, "internal_inserted_silence_seconds": 0.0,
            "total_inserted_silence_seconds": inserted_silence_seconds,
        },
        "output": {"path": str(output), "bytes": output.stat().st_size, "sha256": sha(output), "qa": qa},
        "network_used": False, "service_started": False, "port_8000_touched": False,
    }
    receipt_path = generation / "TRUE_SPEECH_NORMALIZED_1X_RECEIPT.json"
    write_new(receipt_path, json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8") + b"\n")
    print(json.dumps({"status": receipt["status"], "output": receipt["output"], "receipt": str(receipt_path)}, ensure_ascii=False), flush=True)
    return 0 if qa["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
