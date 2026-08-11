#!/usr/bin/env python3
"""Build a source-normalized 1.0x audition from freshly generated v4 phrases."""
from __future__ import annotations

import hashlib
import json
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
SAMPLE_RATE = 24000
STRETCH_RATE = 0.75
INTER_PHRASE_SECONDS = 0.70
INTERNAL_HESITATION_SECONDS = 1.65
LEAD_SECONDS = 0.15
TAIL_SECONDS = 0.25


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_new(path: Path, data: bytes) -> None:
    with path.open("xb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def silence(seconds: float) -> np.ndarray:
    return np.zeros(round(seconds * SAMPLE_RATE), dtype=np.float32)


def median_f0(y: np.ndarray) -> float | None:
    f0, voiced, _ = librosa.pyin(y, fmin=70.0, fmax=700.0, sr=SAMPLE_RATE, frame_length=1024, hop_length=256)
    values = f0[np.asarray(voiced, dtype=bool) & np.isfinite(f0)]
    return None if values.size == 0 else float(np.median(values))


def main() -> int:
    manifest = json.loads((SOURCE / "INFERENCE_MANIFEST.json").read_text(encoding="utf-8"))
    if manifest.get("scope") != "private_local_nonrelease_audition_only_salvage_from_failed_owner":
        raise RuntimeError("source scope mismatch")
    if manifest.get("texts") != PHRASES:
        raise RuntimeError("source phrase inventory mismatch")
    source_paths = [SOURCE / f"teacher_short_{index:02d}.wav" for index in range(1, 4)]
    source_hashes = [sha(path) for path in source_paths]
    for item, expected in zip(manifest["outputs"], source_hashes, strict=True):
        if item.get("sha256") != expected or not item.get("qa", {}).get("passed"):
            raise RuntimeError("source output hash or QA mismatch")

    trimmed: list[np.ndarray] = []
    stretched: list[np.ndarray] = []
    phrase_receipts = []
    for path in source_paths:
        audio, sr = sf.read(str(path), dtype="float32", always_2d=False)
        audio = np.asarray(audio, dtype=np.float32)
        if sr != SAMPLE_RATE or audio.ndim != 1 or not np.isfinite(audio).all():
            raise RuntimeError("invalid source audio")
        clean, indices = librosa.effects.trim(audio, top_db=40, frame_length=512, hop_length=128)
        if clean.size == 0:
            raise RuntimeError("trim produced empty audio")
        changed = librosa.effects.time_stretch(clean, rate=STRETCH_RATE, n_fft=1024, hop_length=256)
        changed = np.asarray(changed, dtype=np.float32)
        if not np.isfinite(changed).all():
            raise RuntimeError("time stretch produced non-finite audio")
        trimmed.append(clean)
        stretched.append(changed)
        phrase_receipts.append({
            "source": str(path), "source_sha256": sha(path),
            "source_duration_seconds": round(audio.size / sr, 6),
            "trim_samples": [int(indices[0]), int(indices[1])],
            "trimmed_duration_seconds": round(clean.size / sr, 6),
            "stretched_duration_seconds": round(changed.size / sr, 6),
        })

    # Insert the dramatic hesitation at the quietest short-time-energy point
    # around the written ellipsis in phrase two (30%-55% of its duration).
    middle = stretched[1]
    rms = librosa.feature.rms(y=middle, frame_length=512, hop_length=128, center=True)[0]
    lo = max(1, round(rms.size * 0.30))
    hi = min(rms.size - 1, round(rms.size * 0.55))
    split_frame = lo + int(np.argmin(rms[lo:hi]))
    split_sample = min(middle.size - 1, split_frame * 128)
    middle_with_hesitation = np.concatenate([
        middle[:split_sample], silence(INTERNAL_HESITATION_SECONDS), middle[split_sample:]
    ])

    final_audio = np.concatenate([
        silence(LEAD_SECONDS), stretched[0], silence(INTER_PHRASE_SECONDS),
        middle_with_hesitation, silence(INTER_PHRASE_SECONDS), stretched[2],
        silence(TAIL_SECONDS),
    ]).astype(np.float32, copy=False)
    peak = float(np.max(np.abs(final_audio)))
    if peak > 0.98:
        final_audio *= np.float32(0.98 / peak)

    generation_id = "tsubaki-fullstyle-v4-source-normalized-1x-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    generation = OUTPUT_PARENT / generation_id
    os.mkdir(generation)
    partial = generation / "tsubaki_normalized_1x.wav.partial"
    output = generation / "tsubaki_normalized_1x.wav"
    sf.write(str(partial), final_audio, SAMPLE_RATE, subtype="PCM_16", format="WAV")
    os.replace(partial, output)

    rendered, sr = sf.read(str(output), dtype="float32", always_2d=False)
    rendered = np.asarray(rendered, dtype=np.float32)
    info = sf.info(str(output))
    duration = rendered.size / sr
    char_count = 17
    chars_per_second = char_count / duration
    source_voiced = np.concatenate(trimmed)
    output_voiced = np.concatenate(stretched)
    source_f0 = median_f0(source_voiced)
    output_f0 = median_f0(output_voiced)
    semitone_shift = None if source_f0 is None or output_f0 is None else 12.0 * np.log2(output_f0 / source_f0)
    clipped_fraction = float(np.mean(np.abs(rendered) >= 0.999))
    qa = {
        "passed": bool(
            info.format == "WAV" and info.subtype == "PCM_16" and info.channels == 1 and sr == SAMPLE_RATE
            and 5.8 <= duration <= 7.2 and 2.4 <= chars_per_second <= 2.8
            and np.isfinite(rendered).all() and clipped_fraction == 0.0
            and semitone_shift is not None and abs(semitone_shift) <= 0.35
        ),
        "format": info.format, "subtype": info.subtype, "channels": info.channels,
        "sample_rate": sr, "frames": int(rendered.size),
        "duration_seconds": round(duration, 6), "character_count": char_count,
        "characters_per_second": round(chars_per_second, 6),
        "finite": bool(np.isfinite(rendered).all()),
        "peak_absolute": round(float(np.max(np.abs(rendered))), 8),
        "clipped_fraction": round(clipped_fraction, 8),
        "source_median_f0_hz": None if source_f0 is None else round(source_f0, 6),
        "output_voiced_median_f0_hz": None if output_f0 is None else round(output_f0, 6),
        "median_f0_shift_semitones": None if semitone_shift is None else round(float(semitone_shift), 6),
    }
    receipt = {
        "schema": "mentorflow.qwen3-tts-source-normalized-1x-audition.v1",
        "status": "passed" if qa["passed"] else "failed",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "generation_id": generation_id,
        "scope": "private_local_nonrelease_audition_only_salvage_from_failed_owner",
        "audition_only": True, "release_eligible": False,
        "source_generation": str(SOURCE), "source_manifest_sha256": sha(SOURCE / "INFERENCE_MANIFEST.json"),
        "old_audition_audio_reused": False, "training_performed": False,
        "checkpoint": str(manifest.get("generation_id")),
        "text": TEXT, "phrases": PHRASES, "phrase_sources": phrase_receipts,
        "normalization": {
            "meaning_of_1x": "source_normalized_normal_reading_rate_not_raw_model_rate",
            "pitch_preserving_phase_vocoder": True, "stretch_rate": STRETCH_RATE,
            "inter_phrase_seconds": INTER_PHRASE_SECONDS,
            "internal_hesitation_seconds": INTERNAL_HESITATION_SECONDS,
            "lead_seconds": LEAD_SECONDS, "tail_seconds": TAIL_SECONDS,
            "phrase_two_split_sample_after_stretch": int(split_sample),
        },
        "output": {"path": str(output), "bytes": output.stat().st_size, "sha256": sha(output), "qa": qa},
        "network_used": False, "service_started": False, "port_8000_touched": False,
        "human_perceptual_review_pending": True,
    }
    write_new(generation / "NORMALIZED_1X_RECEIPT.json", json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8") + b"\n")
    print(json.dumps(receipt, ensure_ascii=False), flush=True)
    return 0 if qa["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
