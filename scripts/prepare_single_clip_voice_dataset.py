#!/usr/bin/env python3
"""Create a new, auditable private voice dataset from one user MP3.

The program is intentionally single-purpose for the explicitly authorized
``20260808_164409.mp3`` source.  It consumes an existing offline whisper.cpp
JSON transcript, deterministically slices/energy-trims the source, resamples
to 24 kHz mono PCM16, and writes a create-new generation.  It never reads a
previous voice dataset or checkpoint and performs no network activity.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import stat
import unicodedata
import wave
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf
import soxr


SOURCE_SHA256 = "9661a4600c7c1a110417408eb8ddc48beafdef9bb6e8a0aea6a6725bf2c58775"
OUTPUT_RATE = 24_000
FRAME_MS = 20
ACTIVE_DBFS = -50.0
TRIM_PAD_MS = 180

# Human-reviewable corrections of the local Whisper Small transcript.  Only
# these utterances are admitted.  Ambiguous first/last lines and pure laughter
# are deliberately absent.
REVIEWED_TEXT: dict[int, str] = {
    3: "哦，找到你啦，哈哈哈。",
    4: "你真是充满惊喜。",
    5: "这是什么，被捉住就要乖乖回答问题的游戏吗？",
    6: "啊，生气了吗？",
    7: "激怒你，似乎会让我不由自主地兴奋起来。",
    8: "你是第一个让我产生这样兴趣的人。",
    9: "你很有潜力。",
    10: "好嘛，我会收敛一些的。",
    11: "毕竟你是黑海岸交给我的任务目标。",
    12: "是啊，我是黑海岸的执花。",
    13: "这朵黑花就是执花的标志。",
    14: "也象征着执花旧日的职责与使命。",
    15: "黑海岸的规矩就是这样喽。",
    16: "黑海岸的目的……",
    17: "啊，是什么来着？",
    18: "尽可能招募更多的贤能。",
    19: "拯救危在旦夕的世界。",
    20: "怎么样，你有兴趣吗？",
    21: "我对这个确实没什么兴趣啦。",
    22: "如果世界明天就爆炸。",
    23: "那为何不趁着今日尽兴？",
    24: "无拘无束地追求强大。",
    25: "肆意地追逐、猎杀猎物。",
    26: "顺从欲望，找点乐子。",
    27: "啊，虽然我是很想这么做啦。",
    28: "但人家也有不得不背负的使命呀。",
    29: "花园中孕育着无数青涩的果实。",
    30: "你将会是这座花园中最为特殊的种子。",
    31: "而我的使命就是保护、培育这样的种子。",
    32: "让其蓬勃生长。",
    33: "这样，待到果实成熟。",
    34: "才能够亲自采撷。",
    35: "品尝到果实的甜美。",
    36: "和泰缇斯所说的一样。",
    37: "我能感受到你身体内蓄势待发的爆发力。",
    38: "未来的某天。",
    39: "你必定会结出丰饶美丽的果实。",
    41: "我喜欢你冷漠的语气。",
    42: "这样让我又想要激怒你了。",
    43: "好了好了，我不会乱来的。",
    44: "总之，你不用担心我对你做些什么。",
    45: "嗯，出格的事。相反……",
    46: "这段时间我可是有好好保护你。",
    47: "替你清除各种暗中的障碍啊。",
    48: "可别把我跟残星会相提并论。",
    49: "我并不在乎他们的立场。",
    50: "但如果他们妄图把手伸到你身上。",
    51: "哼，你可是我看重的人。",
    52: "只是用了点小伎俩。",
    53: "就把他们的人耍得团团转。",
    55: "哎呀，难道你没发现？",
    56: "你和那个龙女的深夜会面能顺利进行。",
    57: "是我帮忙拖住了残星会的家伙。",
    58: "回答了你这么多，该轮到我了吧？",
    59: "哼哼，我并没有什么想知道的。",
    60: "只是你用这种眼神看着我。",
    61: "不，没什么。",
    62: "啊，啊，总之……",
    63: "我并不是想从你这儿得知什么。",
    64: "我会继续关注着你啊。",
    65: "你是与众不同的种子。",
    66: "我很确信这一点。",
    67: "啊，你身上有一种熟悉的气息。",
    68: "我是椿。",
    69: "下次，别那么快再被我抓住喽。",
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def pretty(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def write_new(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def plain_file(path: Path) -> Path:
    value = path.lstat()
    attrs = int(getattr(value, "st_file_attributes", 0))
    if not stat.S_ISREG(value.st_mode) or attrs & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400) or int(value.st_nlink) != 1:
        raise RuntimeError(f"plain single-link file required: {path}")
    return path


def token_confidence(segment: dict[str, Any]) -> tuple[float, float, int]:
    values = [
        float(token["p"])
        for token in segment.get("tokens", [])
        if str(token.get("text", "")).strip() and not str(token.get("text", "")).startswith("[_")
    ]
    return (float(np.mean(values)), float(np.min(values)), len(values)) if values else (0.0, 0.0, 0)


def trim_active(audio: np.ndarray, sample_rate: int) -> tuple[np.ndarray, dict[str, Any]]:
    frame = max(1, round(sample_rate * FRAME_MS / 1000))
    count = int(math.ceil(audio.size / frame))
    padded = np.pad(audio, (0, count * frame - audio.size))
    rms = np.sqrt(np.mean(padded.reshape(count, frame).astype(np.float64) ** 2, axis=1))
    db = 20 * np.log10(np.maximum(rms, 1e-12))
    active = np.flatnonzero(db >= ACTIVE_DBFS)
    if active.size == 0:
        return audio[:0], {"active_frames": 0}
    pad = round(sample_rate * TRIM_PAD_MS / 1000)
    start = max(0, int(active[0] * frame) - pad)
    end = min(audio.size, int((active[-1] + 1) * frame) + pad)
    return audio[start:end], {
        "active_frames": int(active.size),
        "total_frames_20ms": int(count),
        "trim_start_samples": start,
        "trim_end_samples": int(audio.size - end),
        "active_fraction": round(float(active.size / count), 8),
    }


def wav_bytes(audio: np.ndarray) -> bytes:
    import io

    buffer = io.BytesIO()
    pcm = np.clip(np.rint(audio * 32767.0), -32768, 32767).astype("<i2")
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(OUTPUT_RATE)
        handle.writeframes(pcm.tobytes())
    return buffer.getvalue()


def tree_digest(rows: list[dict[str, Any]]) -> str:
    digest = hashlib.sha256()
    for row in sorted(rows, key=lambda item: item["path"]):
        digest.update(f"{row['path']}\0{row['bytes']}\0{row['sha256']}\n".encode("utf-8"))
    return digest.hexdigest()


def run(args: argparse.Namespace) -> None:
    source = plain_file(args.source.resolve())
    transcript_path = plain_file(args.whisper_json.resolve())
    if sha256_file(source) != SOURCE_SHA256:
        raise RuntimeError("source SHA-256 differs from the authorized MP3")
    if args.output.exists():
        raise RuntimeError(f"refusing to overwrite generation: {args.output}")
    os.makedirs(args.output / "dataset" / "audio")
    os.makedirs(args.output / "evidence")

    decoded, sample_rate = sf.read(str(source), dtype="float32", always_2d=True)
    if sample_rate != 44_100 or decoded.shape[1] != 2:
        raise RuntimeError("unexpected source format")
    if not np.isfinite(decoded).all():
        raise RuntimeError("source contains non-finite samples")
    mono = decoded.mean(axis=1, dtype=np.float32)
    transcript = json.loads(transcript_path.read_text(encoding="utf-8"))
    segments = transcript.get("transcription")
    if not isinstance(segments, list) or len(segments) != 70:
        raise RuntimeError("expected the verified 70-segment offline transcript")

    manifest: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    inventory: list[dict[str, Any]] = []
    for index, segment in enumerate(segments, start=1):
        raw_text = unicodedata.normalize("NFC", str(segment.get("text") or "").strip())
        start_ms = int(segment["offsets"]["from"])
        end_ms = int(segment["offsets"]["to"])
        mean_p, min_p, tokens = token_confidence(segment)
        base = {
            "segment_index": index,
            "source_start_ms": start_ms,
            "source_end_ms": end_ms,
            "whisper_text": raw_text,
            "whisper_mean_token_probability": round(mean_p, 8),
            "whisper_min_token_probability": round(min_p, 8),
            "whisper_lexical_token_count": tokens,
        }
        reviewed = raw_text if args.raw_small_text and index in REVIEWED_TEXT else REVIEWED_TEXT.get(index)
        if reviewed is None:
            excluded.append({**base, "decision": "exclude", "reason": "review_ambiguous_or_nonlexical"})
            continue
        left = max(0, round(start_ms * sample_rate / 1000))
        right = min(mono.size, round(end_ms * sample_rate / 1000))
        trimmed, trim = trim_active(mono[left:right], sample_rate)
        if trimmed.size == 0:
            excluded.append({**base, "decision": "exclude", "reason": "no_active_audio"})
            continue
        audio = soxr.resample(trimmed, sample_rate, OUTPUT_RATE, quality="VHQ").astype(np.float32)
        duration = audio.size / OUTPUT_RATE
        peak = float(np.max(np.abs(audio)))
        rms = float(np.sqrt(np.mean(audio.astype(np.float64) ** 2)))
        if not 0.5 <= duration <= 15.0 or rms < 10 ** (-42 / 20) or peak > 1.0:
            excluded.append({**base, **trim, "decision": "exclude", "reason": "objective_audio_gate"})
            continue
        raw_wav = wav_bytes(audio)
        audio_sha = hashlib.sha256(raw_wav).hexdigest()
        relative = f"audio/{audio_sha}.wav"
        write_new(args.output / "dataset" / relative, raw_wav)
        inventory.append({"path": f"dataset/{relative}", "bytes": len(raw_wav), "sha256": audio_sha})
        text_sha = hashlib.sha256(reviewed.encode("utf-8")).hexdigest()
        manifest.append({
            **base,
            **trim,
            "decision": "admit",
            "sample_id": audio_sha,
            "audio": relative,
            "audio_sha256": audio_sha,
            "audio_bytes": len(raw_wav),
            "sample_rate": OUTPUT_RATE,
            "channels": 1,
            "sample_width_bytes": 2,
            "duration_ms": round(duration * 1000),
            "rms_dbfs": round(20 * math.log10(max(rms, 1e-12)), 6),
            "peak_dbfs": round(20 * math.log10(max(peak, 1e-12)), 6),
            "near_clipped_fraction": round(float(np.mean(np.abs(audio) >= 0.99)), 10),
            "text": reviewed,
            "text_sha256": text_sha,
            "transcript_review": "whisper_small_raw_nfc_whitespace_only" if args.raw_small_text else "manual_semantic_review_of_offline_whisper_small; punctuation_and_obvious_homophone_corrections",
        })

    if len(manifest) < 48:
        raise RuntimeError(f"insufficient admitted segments after deterministic gates: {len(manifest)}")
    manifest_raw = b"".join(canonical(row) for row in manifest)
    excluded_raw = b"".join(canonical(row) for row in excluded)
    write_new(args.output / "dataset" / "trainer-neutral-manifest.jsonl", manifest_raw)
    write_new(args.output / "evidence" / "EXCLUDED_MANIFEST.jsonl", excluded_raw)
    inventory.extend([
        {"path": "dataset/trainer-neutral-manifest.jsonl", "bytes": len(manifest_raw), "sha256": hashlib.sha256(manifest_raw).hexdigest()},
        {"path": "evidence/EXCLUDED_MANIFEST.jsonl", "bytes": len(excluded_raw), "sha256": hashlib.sha256(excluded_raw).hexdigest()},
    ])
    report = {
        "schema": "mentorflow.single-clip-curated-qa.v1",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "generation_id": args.output.name,
        "source": {"path": str(source), "bytes": source.stat().st_size, "sha256": SOURCE_SHA256, "duration_seconds": round(mono.size / sample_rate, 9), "sample_rate": sample_rate, "channels": 2},
        "asr": {"engine": "whisper.cpp-v1.9.1/ggml-small.bin", "vad": "silero-v6.2.0", "path": str(transcript_path), "sha256": sha256_file(transcript_path), "segments": len(segments), "network_used": False},
        "curation": {"admitted": len(manifest), "excluded": len(excluded), "admitted_duration_seconds": round(sum(row["duration_ms"] for row in manifest) / 1000, 6), "format": "WAV PCM16 mono 24000 Hz", "minimum_duration_seconds": 0.5, "maximum_duration_seconds": 15.0},
        "source_acoustics": {"rms_dbfs": round(20 * math.log10(max(float(np.sqrt(np.mean(mono.astype(np.float64) ** 2))), 1e-12)), 6), "peak_dbfs": round(20 * math.log10(max(float(np.max(np.abs(mono))), 1e-12)), 6), "exact_clipped_samples": int(np.sum(np.abs(decoded) >= 0.999969)), "stereo_correlation": round(float(np.corrcoef(decoded[:, 0], decoded[:, 1])[0, 1]), 9)},
        "limitations": {"speaker_consistency_pending": True, "background_music_classifier": "not_available; spectral_and_embedding audit pending", "overlap_classifier": "not_available; embedding/outlier and manual spot-check pending", "public_release_eligible": False},
        "label_policy": "whisper_small_raw_nfc_whitespace_only" if args.raw_small_text else "manual_semantic_review",
        "style_retention_priority": bool(args.raw_small_text),
        "old_dataset_read": False,
        "old_checkpoint_read": False,
        "network_used": False,
    }
    write_new(args.output / "evidence" / "CURATED_QA_REPORT.json", pretty(report))
    inventory.append({"path": "evidence/CURATED_QA_REPORT.json", "bytes": (args.output / "evidence" / "CURATED_QA_REPORT.json").stat().st_size, "sha256": sha256_file(args.output / "evidence" / "CURATED_QA_REPORT.json")})
    receipt = {
        "schema": "mentorflow.single-clip-curation-receipt.v1",
        "status": "curated_pending_speaker_consistency_gate",
        "generation_id": args.output.name,
        "source_sha256": SOURCE_SHA256,
        "asr_sha256": sha256_file(transcript_path),
        "admitted_samples": len(manifest),
        "excluded_samples": len(excluded),
        "admitted_duration_seconds": report["curation"]["admitted_duration_seconds"],
        "files": sorted(inventory, key=lambda item: item["path"]),
        "payload_tree_sha256": tree_digest(sorted(inventory, key=lambda item: item["path"])),
        "offline": True,
        "private_local_only": True,
        "old_dataset_reused": False,
        "old_checkpoint_reused": False,
    }
    write_new(args.output / "CURATION_RECEIPT.json", pretty(receipt))
    print(json.dumps(receipt, ensure_ascii=False, indent=2))


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("--source", type=Path, required=True)
    value.add_argument("--whisper-json", type=Path, required=True)
    value.add_argument("--raw-small-text", action="store_true", help="Use only raw Whisper Small text with NFC/outer whitespace normalization; no semantic rewrite.")
    value.add_argument("--output", type=Path, required=True)
    return value


if __name__ == "__main__":
    run(parser().parse_args())
