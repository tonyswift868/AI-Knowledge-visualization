#!/usr/bin/env python3
"""Offline speaker-consistency and acoustic audit for a curated clip set."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def pretty(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def write_new(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def percentiles(values: np.ndarray) -> dict[str, float]:
    return {f"p{q:02d}": round(float(np.percentile(values, q)), 8) for q in (0, 1, 5, 10, 25, 50, 75, 90, 95, 99, 100)}


def spectral_metrics(audio: np.ndarray, sample_rate: int) -> dict[str, float]:
    import librosa

    stft = np.abs(librosa.stft(audio, n_fft=1024, hop_length=256, win_length=1024)) + 1e-10
    flatness = librosa.feature.spectral_flatness(S=stft)[0]
    centroid = librosa.feature.spectral_centroid(S=stft, sr=sample_rate)[0]
    rolloff = librosa.feature.spectral_rolloff(S=stft, sr=sample_rate, roll_percent=0.95)[0]
    harmonic, percussive = librosa.effects.hpss(audio)
    energy = float(np.sum(audio.astype(np.float64) ** 2)) + 1e-12
    return {
        "spectral_flatness_median": round(float(np.median(flatness)), 8),
        "spectral_centroid_hz_median": round(float(np.median(centroid)), 4),
        "spectral_rolloff95_hz_median": round(float(np.median(rolloff)), 4),
        "harmonic_energy_fraction": round(float(np.sum(harmonic.astype(np.float64) ** 2) / energy), 8),
        "percussive_energy_fraction": round(float(np.sum(percussive.astype(np.float64) ** 2) / energy), 8),
    }


def run(args: argparse.Namespace) -> None:
    for name in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "HF_DATASETS_OFFLINE", "PIP_NO_INDEX"):
        if os.environ.get(name) != "1":
            raise RuntimeError(f"offline environment missing: {name}")
    if args.output.exists():
        raise RuntimeError(f"refusing to overwrite: {args.output}")
    os.makedirs(args.output)
    receipt_path = args.curated / "CONSENSUS_CURATED_RECEIPT.json"
    if not receipt_path.exists():
        receipt_path = args.curated / "CURATION_RECEIPT.json"
    curated_receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    rows = [json.loads(line) for line in (args.curated / "dataset" / "trainer-neutral-manifest.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    if curated_receipt.get("status") not in {"curated_pending_speaker_consistency_gate", "consensus_curated_pending_new_speaker_audit", "experimental_incremental_consensus_curated_pending_new_speaker_audit"} or len(rows) != int(curated_receipt.get("admitted_samples", curated_receipt.get("admitted", -1))):
        raise RuntimeError("curated receipt/manifest mismatch")

    import torch
    from qwen_tts import Qwen3TTSModel
    from qwen_tts.core.models.modeling_qwen3_tts import mel_spectrogram

    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is required for the bounded speaker audit")
    torch.cuda.set_per_process_memory_fraction(min(0.62, 7168 / (torch.cuda.get_device_properties(0).total_memory / 1024**2)), 0)
    wrapper = Qwen3TTSModel.from_pretrained(
        str(args.model), device_map={"": "cuda:0"}, torch_dtype=torch.bfloat16,
        attn_implementation="sdpa", local_files_only=True,
    )
    model = wrapper.model
    model.eval()
    vectors: list[np.ndarray] = []
    audit_rows: list[dict[str, Any]] = []
    with torch.inference_mode():
        for row in rows:
            audio_path = args.curated / "dataset" / str(row["audio"])
            if sha(audio_path) != row["audio_sha256"]:
                raise RuntimeError(f"curated audio drift: {audio_path.name}")
            audio, sr = sf.read(str(audio_path), dtype="float32", always_2d=False)
            if sr != 24000 or audio.ndim != 1:
                raise RuntimeError("curated audio format drift")
            mel = mel_spectrogram(
                torch.from_numpy(audio).unsqueeze(0), n_fft=1024, num_mels=128,
                sampling_rate=24000, hop_size=256, win_size=1024, fmin=0, fmax=12000,
            ).transpose(1, 2)
            embedding = model.speaker_encoder(mel.to("cuda:0", dtype=torch.bfloat16)).float().cpu().numpy().reshape(-1)
            embedding /= max(float(np.linalg.norm(embedding)), 1e-12)
            vectors.append(embedding)
            audit_rows.append({
                "sample_id": row["sample_id"], "segment_index": row["segment_index"],
                "audio_sha256": row["audio_sha256"], "duration_ms": row["duration_ms"],
                "whisper_mean_token_probability": row.get("whisper_mean_token_probability", row.get("small_mean_probability")),
                "spectral": spectral_metrics(audio, sr),
            })
            del mel
    matrix = np.stack(vectors).astype(np.float32)
    similarities = matrix @ matrix.T
    pairwise = similarities[np.triu_indices(len(matrix), 1)]
    centroid = matrix.mean(axis=0)
    centroid /= max(float(np.linalg.norm(centroid)), 1e-12)
    centroid_cos = matrix @ centroid
    median = float(np.median(centroid_cos))
    mad = float(np.median(np.abs(centroid_cos - median)))
    robust_floor = max(0.70, median - 5.0 * max(mad, 0.005))
    admitted: list[dict[str, Any]] = []
    outliers: list[dict[str, Any]] = []
    for row, cosine in zip(audit_rows, centroid_cos.tolist(), strict=True):
        row["cosine_to_robust_centroid"] = round(float(cosine), 8)
        row["speaker_consistency_floor"] = round(robust_floor, 8)
        if cosine >= robust_floor:
            row["decision"] = "admit"
            admitted.append(row)
        else:
            row["decision"] = "exclude_speaker_embedding_outlier"
            outliers.append(row)

    spectral_flatness = np.array([row["spectral"]["spectral_flatness_median"] for row in audit_rows])
    percussive = np.array([row["spectral"]["percussive_energy_fraction"] for row in audit_rows])
    minimum_consistent_samples = 20 if str(curated_receipt.get("status", "")).startswith("experimental_incremental_") else 48
    report = {
        "schema": "mentorflow.single-clip-speaker-acoustic-audit.v1",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "curated_generation": str(args.curated),
        "curation_receipt_sha256": sha(receipt_path),
        "base_model": str(args.model),
        "base_model_config_sha256": sha(args.model / "config.json"),
        "speaker_encoder": {"source": "same Qwen3-TTS 0.6B Base used for fresh training", "embedding_dimension": int(matrix.shape[1]), "normalization": "L2", "samples": len(matrix)},
        "pairwise_cosine": percentiles(pairwise),
        "centroid_cosine": percentiles(centroid_cos),
        "robust_gate": {"median": round(median, 8), "mad": round(mad, 8), "floor": round(robust_floor, 8), "rule": "max(0.70, median-5*max(MAD,0.005))"},
        "admitted_after_embedding_gate": len(admitted),
        "outliers": outliers,
        "spectral_flatness_median_distribution": percentiles(spectral_flatness),
        "percussive_energy_fraction_distribution": percentiles(percussive),
        "background_music_review": {"automated_music_classifier_available": False, "evidence": "tight VAD/energy trims, high ASR coverage, per-segment spectral metrics, no embedding subcluster/outlier subject to gate", "requires_human_listening_for_public_claim": True},
        "overlap_review": {"automated_overlap_classifier_available": False, "evidence": "single-source chronology plus speaker-embedding robust outlier gate", "requires_human_listening_for_public_claim": True},
        "network_used": False,
        "old_dataset_read": False,
        "old_checkpoint_read": False,
        "minimum_consistent_samples": minimum_consistent_samples,
        "status": "passed_experimental_incremental_private_local_training_gate" if len(admitted) >= minimum_consistent_samples and len(outliers) <= max(3, len(rows) // 10) else "failed",
    }
    rows_raw = b"".join((canonical(row) + b"\n") for row in audit_rows)
    write_new(args.output / "SPEAKER_AUDIT_ROWS.jsonl", rows_raw)
    write_new(args.output / "SPEAKER_ACOUSTIC_AUDIT.json", pretty(report))
    write_new(args.output / "centroid.f32", centroid.astype("<f4").tobytes())
    receipt = {
        "schema": "mentorflow.single-clip-speaker-audit-receipt.v1",
        "status": report["status"],
        "curation_receipt_sha256": report["curation_receipt_sha256"],
        "report_sha256": sha(args.output / "SPEAKER_ACOUSTIC_AUDIT.json"),
        "rows_sha256": sha(args.output / "SPEAKER_AUDIT_ROWS.jsonl"),
        "centroid_sha256": sha(args.output / "centroid.f32"),
        "admitted": len(admitted),
        "outliers": len(outliers),
        "pairwise_cosine_p01": report["pairwise_cosine"]["p01"],
        "pairwise_cosine_median": report["pairwise_cosine"]["p50"],
        "offline": True,
    }
    write_new(args.output / "SPEAKER_AUDIT_RECEIPT.json", pretty(receipt))
    print(json.dumps(receipt, ensure_ascii=False, indent=2))


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("--curated", type=Path, required=True)
    value.add_argument("--model", type=Path, required=True)
    value.add_argument("--output", type=Path, required=True)
    return value


if __name__ == "__main__":
    run(parser().parse_args())
