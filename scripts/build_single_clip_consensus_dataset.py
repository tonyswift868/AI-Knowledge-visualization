#!/usr/bin/env python3
"""Build a create-new dataset from independent Whisper Small/Base consensus.

No manually reconstructed transcript is used.  The Small text is admitted
verbatim (apart from Unicode/whitespace normalization) only when both local
models meet confidence and normalized edit-agreement gates.
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import math
import os
import re
import unicodedata
import wave
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf
import soxr



SOURCE_SHA = "9661a4600c7c1a110417408eb8ddc48beafdef9bb6e8a0aea6a6725bf2c58775"
ALWAYS_EXCLUDE = {14, 18, 21, 27, 35, 45, 53, 57, 69}
PROPER_NOUN_REVIEW = {1, 11, 12, 13, 15, 16, 18, 36, 48, 57, 68}
TRAD_TO_SIMPLE = str.maketrans({
    "來": "来", "個": "个", "讓": "让", "產": "产", "興": "兴", "務": "务", "標": "标",
    "誌": "志", "則": "则", "規": "规", "儘": "尽", "將": "将", "為": "为", "護": "护",
    "種": "种", "園": "园", "實": "实", "發": "发", "會": "会", "氣": "气", "滿": "满",
    "驚": "惊", "愛": "爱", "親": "亲", "這": "这", "麼": "么", "遊": "游", "戲": "戏",
    "嗎": "吗", "裡": "里", "點": "点", "樂": "乐", "後": "后", "與": "与", "見": "见",
    "順": "顺", "進": "进", "幫": "帮", "轉": "转", "現": "现", "隻": "只", "並": "并",
    "從": "从", "繼": "继", "續": "续", "關": "关", "確": "确", "體": "体", "內": "内",
    "勢": "势", "獵": "猎", "殺": "杀", "強": "强", "該": "该", "緊": "紧", "題": "题",
    "開": "开", "長": "长", "時": "时", "間": "间", "離": "离", "險": "险", "穩": "稳",
})


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def pretty(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def write_new(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(raw); handle.flush(); os.fsync(handle.fileno())


def normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).translate(TRAD_TO_SIMPLE).casefold()
    return "".join(c for c in text if unicodedata.category(c)[0] in {"L", "N"})


def display_text(text: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", text).strip())


def confidence(segments: list[dict[str, Any]]) -> tuple[float, float, int]:
    values = [float(t["p"]) for seg in segments for t in seg.get("tokens", []) if str(t.get("text", "")).strip() and not str(t.get("text", "")).startswith("[_")]
    return (float(np.mean(values)), float(np.min(values)), len(values)) if values else (0.0, 0.0, 0)


def trim(audio: np.ndarray, sr: int) -> np.ndarray:
    frame = round(sr * 0.02); count = int(math.ceil(audio.size / frame)); padded = np.pad(audio, (0, count * frame - audio.size))
    rms = np.sqrt(np.mean(padded.reshape(count, frame).astype(np.float64) ** 2, axis=1)); active = np.flatnonzero(20 * np.log10(np.maximum(rms, 1e-12)) >= -50)
    if not active.size: return audio[:0]
    pad = round(sr * 0.18); return audio[max(0, int(active[0] * frame) - pad):min(audio.size, int((active[-1] + 1) * frame) + pad)]


def wav_raw(audio: np.ndarray) -> bytes:
    import io
    buf = io.BytesIO(); pcm = np.clip(np.rint(audio * 32767), -32768, 32767).astype("<i2")
    with wave.open(buf, "wb") as h:
        h.setnchannels(1); h.setsampwidth(2); h.setframerate(24000); h.writeframes(pcm.tobytes())
    return buf.getvalue()


def run(args: argparse.Namespace) -> None:
    if args.output.exists(): raise RuntimeError("refusing to overwrite consensus generation")
    if sha(args.source) != SOURCE_SHA: raise RuntimeError("source drift")
    os.makedirs(args.output / "dataset" / "audio"); os.makedirs(args.output / "evidence")
    small_doc = json.loads(args.small_json.read_text(encoding="utf-8")); small = small_doc["transcription"]
    if len(small) != 70: raise RuntimeError("Small transcript is not fixed 70")
    base_paths = sorted(args.base_dir.glob("segment-*.wav.json"))
    if len(base_paths) != 70: raise RuntimeError("Base transcript is not fixed 70")
    base_by_index = {int(path.name.split("-")[1]): (path, json.loads(path.read_text(encoding="utf-8"))) for path in base_paths}
    decoded, sr = sf.read(str(args.source), dtype="float32", always_2d=True); mono = decoded.mean(axis=1)
    decisions: list[dict[str, Any]] = []; admitted: list[dict[str, Any]] = []; inventory: list[dict[str, Any]] = []
    for index, sseg in enumerate(small, 1):
        bpath, bdoc = base_by_index[index]
        stext = display_text(str(sseg.get("text") or "")); btext = display_text("".join(str(seg.get("text") or "") for seg in bdoc.get("transcription", [])))
        sn, bn = normalize_text(stext), normalize_text(btext)
        similarity = difflib.SequenceMatcher(None, sn, bn).ratio() if sn and bn else 0.0
        sm, smin, scount = confidence([sseg]); bm, bmin, bcount = confidence(bdoc.get("transcription", []))
        protected = index in PROPER_NOUN_REVIEW
        threshold = {"small_mean_probability": 0.70, "base_mean_probability": 0.70, "small_min_probability": 0.20, "base_min_probability": 0.20, "normalized_similarity": 0.70}
        reasons: list[str] = []
        if index in ALWAYS_EXCLUDE: reasons.append("mandatory_independent_audit_exclusion")
        if not sn or not bn: reasons.append("one_asr_has_no_lexical_speech")
        if sm < 0.70: reasons.append("small_mean_probability_below_0.70")
        if bm < 0.70: reasons.append("base_mean_probability_below_0.70")
        if smin < 0.20: reasons.append("small_min_probability_below_0.20")
        if bmin < 0.20: reasons.append("base_min_probability_below_0.20")
        if similarity < 0.70: reasons.append("normalized_small_base_similarity_below_0.70")
        row = {"segment_index": index, "source_start_ms": int(sseg["offsets"]["from"]), "source_end_ms": int(sseg["offsets"]["to"]), "small_text": stext, "base_text": btext, "small_normalized": sn, "base_normalized": bn, "small_mean_probability": round(sm, 8), "small_min_probability": round(smin, 8), "small_tokens": scount, "base_mean_probability": round(bm, 8), "base_min_probability": round(bmin, 8), "base_tokens": bcount, "normalized_similarity": round(similarity, 8), "proper_noun_strict_gate": protected, "thresholds": threshold, "base_json_sha256": sha(bpath)}
        if reasons:
            decisions.append({**row, "decision": "exclude", "reasons": reasons}); continue
        left = round(row["source_start_ms"] * sr / 1000); right = round(row["source_end_ms"] * sr / 1000)
        audio = soxr.resample(trim(mono[left:right], sr), sr, 24000, quality="VHQ").astype(np.float32)
        duration = audio.size / 24000
        if not 0.5 <= duration <= 15 or not np.isfinite(audio).all():
            decisions.append({**row, "decision": "exclude", "reasons": ["audio_gate"]}); continue
        raw = wav_raw(audio); audio_sha = hashlib.sha256(raw).hexdigest(); relative = f"audio/{audio_sha}.wav"; write_new(args.output / "dataset" / relative, raw)
        consensus_text = stext
        final = {**row, "decision": "admit", "reasons": [], "sample_id": audio_sha, "audio": relative, "audio_sha256": audio_sha, "audio_bytes": len(raw), "duration_ms": round(duration * 1000), "sample_rate": 24000, "channels": 1, "text": consensus_text, "text_source": "verbatim_whisper_small_after_strict_independent_base_consensus; NFC_whitespace_only_no_character_rewrite", "text_sha256": hashlib.sha256(consensus_text.encode()).hexdigest()}
        decisions.append(final); admitted.append(final); inventory.append({"path": f"dataset/{relative}", "bytes": len(raw), "sha256": audio_sha})
    if not admitted: raise RuntimeError("strict consensus yielded no samples")
    manifest_raw = b"".join(canonical(row) for row in admitted); decisions_raw = b"".join(canonical(row) for row in decisions)
    write_new(args.output / "dataset" / "trainer-neutral-manifest.jsonl", manifest_raw); write_new(args.output / "evidence" / "CONSENSUS_DECISIONS.jsonl", decisions_raw)
    inventory += [{"path": "dataset/trainer-neutral-manifest.jsonl", "bytes": len(manifest_raw), "sha256": hashlib.sha256(manifest_raw).hexdigest()}, {"path": "evidence/CONSENSUS_DECISIONS.jsonl", "bytes": len(decisions_raw), "sha256": hashlib.sha256(decisions_raw).hexdigest()}]
    base_hashes = [{"path": path.name, "bytes": path.stat().st_size, "sha256": sha(path)} for path in base_paths]
    admitted_seconds = round(sum(x["duration_ms"] for x in admitted)/1000, 6)
    receipt = {"schema": "mentorflow.single-clip-small-base-strict-consensus-curation.v1", "status": "experimental_incremental_consensus_curated_pending_new_speaker_audit" if admitted_seconds >= 80 else "insufficient_strict_consensus_duration_no_training", "created_at_utc": datetime.now(timezone.utc).isoformat(), "generation_id": args.output.name, "source_sha256": SOURCE_SHA, "small_asr": {"engine": "whisper.cpp small", "json_sha256": sha(args.small_json), "segments": 70}, "base_asr": {"engine": "whisper.cpp base-q5_1", "fixed_boundaries": True, "token_offsets_used": False, "files": base_hashes, "tree_sha256": hashlib.sha256(b"".join(f"{x['path']}\0{x['bytes']}\0{x['sha256']}\n".encode() for x in base_hashes)).hexdigest()}, "admitted": len(admitted), "excluded": 70-len(admitted), "admitted_duration_seconds": admitted_seconds, "training_minimum_duration_seconds": 80, "scope": "experimental_incremental_private_local_awaiting_future_user_pack", "manual_listening_claimed": False, "text_corrections": "none; Small text NFC and whitespace normalization only; no character rewrite", "strict_gates": {"small_mean_probability_minimum": 0.70, "base_mean_probability_minimum": 0.70, "small_min_probability_minimum": 0.20, "base_min_probability_minimum": 0.20, "normalized_similarity_minimum": 0.70, "proper_noun_semantic_correction": False}, "mandatory_exclusions": sorted(ALWAYS_EXCLUDE), "inventory": sorted(inventory,key=lambda x:x["path"]), "network_used": False, "old_dataset_read": False, "old_checkpoint_read": False}
    receipt["inventory_tree_sha256"] = hashlib.sha256(b"".join(f"{x['path']}\0{x['bytes']}\0{x['sha256']}\n".encode() for x in receipt["inventory"])).hexdigest()
    write_new(args.output / "CONSENSUS_CURATED_RECEIPT.json", pretty(receipt)); print(json.dumps(receipt, ensure_ascii=False, indent=2))


def parser() -> argparse.ArgumentParser:
    p=argparse.ArgumentParser(description=__doc__); p.add_argument("--source",type=Path,required=True); p.add_argument("--small-json",type=Path,required=True); p.add_argument("--base-dir",type=Path,required=True); p.add_argument("--output",type=Path,required=True); return p


if __name__ == "__main__": run(parser().parse_args())
