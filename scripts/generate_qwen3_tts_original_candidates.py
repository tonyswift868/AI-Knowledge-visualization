from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import math
import os
import platform
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


RUNTIME_ROOT = Path(r"E:\Kristina_Runtime")
MODEL_DIR = (
    RUNTIME_ROOT
    / "models"
    / "qwen3-tts"
    / "Qwen3-TTS-12Hz-1.7B-VoiceDesign"
)
OUTPUT_DIR = (
    RUNTIME_ROOT
    / "outputs"
    / "mentorflow-teacher-voice"
    / "original-candidates-20260807"
)
MODEL_REPO = "Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign"
MODEL_REVISION = "5ecdb67327fd37bb2e042aab12ff7391903235d3"
BASE_MODEL_DIR = (
    RUNTIME_ROOT / "models" / "qwen3-tts" / "Qwen3-TTS-12Hz-0.6B-Base"
)
BASE_MODEL_REPO = "Qwen/Qwen3-TTS-12Hz-0.6B-Base"
BASE_MODEL_REVISION = "5d83992436eae1d760afd27aff78a71d676296fc"

TEACHING_TEXT = (
    "我们先别急着记答案。把链表想成一列依次牵手的节点，每个节点只需要记住自己的数据，"
    "以及下一个节点在哪里。"
)
ENCOURAGEMENT_TEXT = (
    "很好，你已经抓住关键关系了！再把指针移动的顺序走一遍，这道题就会变得很清楚。"
)

COMMON_VOICE_DESCRIPTION = (
    "完全原创、无特定真人或角色指向的年轻成年女性普通话声线。"
    "音色温暖清晰、略偏中低、自然松弛而有表现力，气息稳定，带轻微笑意；"
    "咬字清楚但不过度播音腔，明确成年感，不幼态、不撒娇、不使用夹子音，"
    "不机械，也不模仿任何现有人物。"
)

CANDIDATES = (
    {
        "id": "candidate_01_warm_scholar",
        "seed": 26080701,
        "description": (
            "温润知性的方向，胸腔共鸣柔和，亲近而可靠，像耐心陪伴学习的年轻教师。"
        ),
    },
    {
        "id": "candidate_02_clear_companion",
        "seed": 26080702,
        "description": (
            "清爽陪伴的方向，明亮度略高但不尖锐，语气真诚，听感轻盈又稳重。"
        ),
    },
    {
        "id": "candidate_03_grounded_mentor",
        "seed": 26080703,
        "description": (
            "沉静导师的方向，中低音质感更明显，语尾放松，节奏从容，给人安全感。"
        ),
    },
    {
        "id": "candidate_04_lively_teacher",
        "seed": 26080704,
        "description": (
            "自然灵动的方向，节奏稍轻快，情绪起伏清楚但克制，保持成熟与耐听。"
        ),
    },
    {
        "id": "candidate_05_gentle_narrator",
        "seed": 26080705,
        "description": (
            "成熟温柔的方向，带一点柔和磁性，叙述层次清楚，长时间聆听不疲劳。"
        ),
    },
)

SEGMENTS = (
    {
        "id": "teaching",
        "text": TEACHING_TEXT,
        "style": (
            "本段用于自然教学讲解：专注、耐心、口语化，停顿合理，重点词稍加重，"
            "不要像广告或新闻播报。"
        ),
    },
    {
        "id": "encouragement",
        "text": ENCOURAGEMENT_TEXT,
        "style": (
            "本段用于轻快鼓励：真诚开心、带明显但克制的笑意，能量比讲解段略高，"
            "不要尖叫、卖萌或夸张表演。"
        ),
    },
)

GENERATION = {
    "do_sample": True,
    "temperature": 0.9,
    "top_p": 1.0,
    "top_k": 50,
    "repetition_penalty": 1.05,
    "subtalker_dosample": True,
    "subtalker_temperature": 0.9,
    "subtalker_top_p": 1.0,
    "subtalker_top_k": 50,
    "max_new_tokens": 1536,
}


def configure_e_drive_caches() -> dict[str, str]:
    cache_root = RUNTIME_ROOT / "cache" / "downloads"
    locations = {
        "HF_HOME": cache_root / "huggingface",
        "HUGGINGFACE_HUB_CACHE": cache_root / "huggingface" / "hub",
        "HF_XET_CACHE": cache_root / "huggingface" / "xet",
        "TRANSFORMERS_CACHE": cache_root / "huggingface" / "transformers",
        "TORCH_HOME": cache_root / "torch",
        "XDG_CACHE_HOME": cache_root / "xdg",
        "NUMBA_CACHE_DIR": cache_root / "numba",
        "TEMP": cache_root / "tmp",
        "TMP": cache_root / "tmp",
    }
    resolved: dict[str, str] = {}
    for name, path in locations.items():
        path.mkdir(parents=True, exist_ok=True)
        if path.drive.upper() != "E:":
            raise RuntimeError(f"cache path escaped E: for {name}: {path}")
        os.environ[name] = str(path)
        resolved[name] = str(path)
    sox_dir = RUNTIME_ROOT / "tools" / "sox-14.4.2" / "sox-14.4.2"
    if sox_dir.is_dir():
        os.environ["PATH"] = f"{sox_dir}{os.pathsep}{os.environ.get('PATH', '')}"
        resolved["SOX_DIR"] = str(sox_dir)
    return resolved


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def audio_metrics(waveform: Any, sample_rate: int) -> dict[str, Any]:
    import numpy as np

    audio = np.asarray(waveform, dtype=np.float32).reshape(-1)
    if audio.size == 0:
        raise RuntimeError("model returned an empty waveform")
    peak = float(np.max(np.abs(audio)))
    rms = float(np.sqrt(np.mean(np.square(audio), dtype=np.float64)))
    frame_length = max(1, int(sample_rate * 0.02))
    frame_count = math.ceil(audio.size / frame_length)
    padded = np.pad(audio, (0, frame_count * frame_length - audio.size))
    frames = padded.reshape(frame_count, frame_length)
    frame_rms = np.sqrt(np.mean(np.square(frames), axis=1, dtype=np.float64))
    silence_threshold = 10 ** (-45.0 / 20.0)
    return {
        "samples": int(audio.size),
        "duration_seconds": round(float(audio.size / sample_rate), 6),
        "peak_linear": round(peak, 8),
        "peak_dbfs": None if peak == 0 else round(20.0 * math.log10(peak), 4),
        "rms_linear": round(rms, 8),
        "rms_dbfs": None if rms == 0 else round(20.0 * math.log10(rms), 4),
        "silence_ratio_frames_below_minus_45_dbfs": round(
            float(np.mean(frame_rms < silence_threshold)), 6
        ),
        "clipped_sample_ratio": round(float(np.mean(np.abs(audio) >= 0.999)), 8),
    }


def write_manifest(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_suffix(".json.tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    os.replace(temporary, path)


def build_model_inventory() -> dict[str, Any]:
    models = (
        ("voice_design", MODEL_DIR, MODEL_REPO, MODEL_REVISION),
        ("base", BASE_MODEL_DIR, BASE_MODEL_REPO, BASE_MODEL_REVISION),
    )
    records: list[dict[str, Any]] = []
    for role, model_dir, repository, revision in models:
        if not model_dir.is_dir():
            raise FileNotFoundError(model_dir)
        file_records: list[dict[str, Any]] = []
        tree_digest = hashlib.sha256()
        for path in sorted(model_dir.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(model_dir)
            if relative.parts and relative.parts[0] == ".cache":
                continue
            digest = sha256_file(path)
            size = path.stat().st_size
            relative_posix = relative.as_posix()
            tree_digest.update(
                f"{relative_posix}\0{size}\0{digest}\n".encode("utf-8")
            )
            file_records.append(
                {"relative_path": relative_posix, "bytes": size, "sha256": digest}
            )
        records.append(
            {
                "role": role,
                "repository": repository,
                "url": f"https://huggingface.co/{repository}",
                "revision": revision,
                "license": "Apache-2.0",
                "local_dir": str(model_dir),
                "file_count_excluding_huggingface_cache": len(file_records),
                "bytes_excluding_huggingface_cache": sum(
                    item["bytes"] for item in file_records
                ),
                "tree_sha256": tree_digest.hexdigest(),
                "files": file_records,
            }
        )
    return {
        "schema_version": 1,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_owner": "Qwen",
        "license": "Apache-2.0",
        "qwen_tts_package": importlib.metadata.version("qwen-tts"),
        "cache_metadata_excluded": True,
        "models": records,
    }


def verify_manifest(manifest_path: Path) -> dict[str, Any]:
    import numpy as np
    import soundfile as sf

    with manifest_path.open("r", encoding="utf-8") as handle:
        manifest = json.load(handle)
    if manifest.get("status") != "complete":
        raise RuntimeError(f"manifest status is not complete: {manifest.get('status')}")
    if manifest.get("source", {}).get("revision") != MODEL_REVISION:
        raise RuntimeError("manifest model revision does not match the pinned official revision")
    outputs = manifest.get("outputs", [])
    if len(outputs) != len(CANDIDATES) * len(SEGMENTS):
        raise RuntimeError(f"expected 10 outputs, found {len(outputs)}")
    expected_pairs = {
        (candidate["id"], segment["id"])
        for candidate in CANDIDATES
        for segment in SEGMENTS
    }
    actual_pairs = {(output["candidate_id"], output["segment"]) for output in outputs}
    if actual_pairs != expected_pairs:
        raise RuntimeError("candidate/segment pairs are incomplete or unexpected")
    hashes: set[str] = set()
    durations: list[float] = []
    peaks: list[float] = []
    silence_ratios: list[float] = []
    for output in outputs:
        path = Path(output["path"])
        if not path.is_file():
            raise FileNotFoundError(path)
        actual = sha256_file(path)
        if actual != output["sha256"]:
            raise RuntimeError(f"SHA256 mismatch for {path}: {actual}")
        if actual in hashes:
            raise RuntimeError(f"duplicate audio bytes detected for {path}")
        hashes.add(actual)
        info = sf.info(path)
        audio, sample_rate = sf.read(path, dtype="float32", always_2d=True)
        if sample_rate != int(output["sample_rate"]) or sample_rate != 24000:
            raise RuntimeError(f"unexpected sample rate for {path}: {sample_rate}")
        if info.channels != 1 or audio.shape[1] != 1:
            raise RuntimeError(f"expected mono audio for {path}")
        if not np.all(np.isfinite(audio)):
            raise RuntimeError(f"non-finite audio samples detected in {path}")
        decoded_duration = float(info.frames / info.samplerate)
        if decoded_duration <= 0:
            raise RuntimeError(f"invalid duration for {path}")
        if not 3.0 <= decoded_duration <= 30.0:
            raise RuntimeError(f"unexpected duration for {path}: {decoded_duration}")
        if abs(decoded_duration - float(output["audio"]["duration_seconds"])) > 1 / sample_rate:
            raise RuntimeError(f"manifest duration mismatch for {path}")
        peak = float(np.max(np.abs(audio)))
        clipped_ratio = float(np.mean(np.abs(audio) >= 0.999))
        silence_ratio = float(
            output["audio"]["silence_ratio_frames_below_minus_45_dbfs"]
        )
        if clipped_ratio > 0.001:
            raise RuntimeError(f"excessive clipping in {path}: {clipped_ratio}")
        if silence_ratio > 0.6:
            raise RuntimeError(f"excessive silence in {path}: {silence_ratio}")
        durations.append(decoded_duration)
        peaks.append(peak)
        silence_ratios.append(silence_ratio)
    return {
        "ok": True,
        "manifest": str(manifest_path),
        "outputs": len(outputs),
        "candidate_pairs": len(actual_pairs),
        "unique_sha256": len(hashes),
        "duration_seconds": {
            "minimum": round(min(durations), 6),
            "maximum": round(max(durations), 6),
            "total": round(sum(durations), 6),
        },
        "peak_linear_maximum": round(max(peaks), 8),
        "silence_ratio_maximum": round(max(silence_ratios), 6),
        "checks": [
            "manifest status and pinned revision",
            "complete candidate/segment matrix",
            "file presence and SHA256",
            "unique audio bytes",
            "PCM decode and finite samples",
            "24 kHz mono format",
            "duration bounds and manifest agreement",
            "clipping and silence thresholds",
        ],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate original MentorFlow Chinese teacher voice candidates with official Qwen3-TTS."
    )
    parser.add_argument("--model-dir", type=Path, default=MODEL_DIR)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--inventory-only", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    caches = configure_e_drive_caches()
    manifest_path = args.output_dir / "manifest.json"
    if args.verify_only and args.inventory_only:
        raise ValueError("use either --verify-only or --inventory-only")
    if args.verify_only:
        report = verify_manifest(manifest_path)
        report["verified_at_utc"] = datetime.now(timezone.utc).isoformat()
        report["scope"] = {
            "objective_audio_integrity": "passed",
            "transcription_or_semantic_repetition": "not assessed",
            "subjective_voice_quality": "requires human listening",
        }
        qa_report_path = args.output_dir / "qa-report.json"
        report["qa_report"] = str(qa_report_path)
        write_manifest(qa_report_path, report)
        print(json.dumps(report, ensure_ascii=False))
        return 0
    if args.inventory_only:
        args.output_dir.mkdir(parents=True, exist_ok=True)
        inventory_path = args.output_dir / "model-inventory.json"
        inventory = build_model_inventory()
        inventory["inventory_path"] = str(inventory_path)
        write_manifest(inventory_path, inventory)
        print(
            json.dumps(
                {
                    "ok": True,
                    "inventory": str(inventory_path),
                    "models": [
                        {
                            "repository": model["repository"],
                            "revision": model["revision"],
                            "files": model["file_count_excluding_huggingface_cache"],
                            "bytes": model["bytes_excluding_huggingface_cache"],
                            "tree_sha256": model["tree_sha256"],
                        }
                        for model in inventory["models"]
                    ],
                },
                ensure_ascii=False,
            )
        )
        return 0

    if not args.model_dir.is_dir():
        raise FileNotFoundError(f"official VoiceDesign model is missing: {args.model_dir}")
    if args.output_dir.exists() and any(args.output_dir.iterdir()):
        raise FileExistsError(
            f"refusing to overwrite non-empty output directory: {args.output_dir}"
        )
    args.output_dir.mkdir(parents=True, exist_ok=True)

    import soundfile as sf
    import torch
    from qwen_tts import Qwen3TTSModel

    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is unavailable; refusing to run VoiceDesign on CPU")

    started = time.perf_counter()
    torch.cuda.empty_cache()
    torch.cuda.reset_peak_memory_stats()
    load_started = time.perf_counter()
    model = Qwen3TTSModel.from_pretrained(
        str(args.model_dir),
        device_map="cuda:0",
        dtype=torch.bfloat16,
        attn_implementation="sdpa",
        local_files_only=True,
    )
    torch.cuda.synchronize()
    load_seconds = time.perf_counter() - load_started

    manifest: dict[str, Any] = {
        "schema_version": 1,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "purpose": "Original MentorFlow teacher voice candidates; no identity cloning.",
        "source": {
            "provider": "Qwen official",
            "repository": MODEL_REPO,
            "url": f"https://huggingface.co/{MODEL_REPO}",
            "revision": MODEL_REVISION,
            "license": "Apache-2.0",
            "local_model_dir": str(args.model_dir),
        },
        "runtime": {
            "python": sys.version,
            "platform": platform.platform(),
            "qwen_tts": importlib.metadata.version("qwen-tts"),
            "torch": torch.__version__,
            "device": "cuda:0",
            "gpu": torch.cuda.get_device_name(),
            "dtype": "bfloat16",
            "attention": "sdpa",
            "cache_locations": caches,
            "model_load_seconds": round(load_seconds, 6),
        },
        "generation": GENERATION,
        "audio_postprocessing": "none; raw model waveform saved as PCM_16 WAV",
        "outputs": [],
        "errors": [],
    }
    write_manifest(manifest_path, manifest)

    try:
        for candidate in CANDIDATES:
            candidate_dir = args.output_dir / candidate["id"]
            candidate_dir.mkdir(parents=False, exist_ok=False)
            for segment in SEGMENTS:
                seed = int(candidate["seed"])
                torch.manual_seed(seed)
                torch.cuda.manual_seed_all(seed)
                prompt = "".join(
                    (COMMON_VOICE_DESCRIPTION, candidate["description"], segment["style"])
                )
                torch.cuda.synchronize()
                item_started = time.perf_counter()
                wavs, sample_rate = model.generate_voice_design(
                    text=segment["text"],
                    language="Chinese",
                    instruct=prompt,
                    **GENERATION,
                )
                torch.cuda.synchronize()
                generation_seconds = time.perf_counter() - item_started
                if len(wavs) != 1:
                    raise RuntimeError(f"expected one waveform, received {len(wavs)}")
                output_path = candidate_dir / f"{segment['id']}.wav"
                sf.write(output_path, wavs[0], sample_rate, subtype="PCM_16")
                metrics = audio_metrics(wavs[0], sample_rate)
                info = sf.info(output_path)
                record = {
                    "candidate_id": candidate["id"],
                    "candidate_description": candidate["description"],
                    "segment": segment["id"],
                    "seed": seed,
                    "text": segment["text"],
                    "prompt": prompt,
                    "generation_seconds": round(generation_seconds, 6),
                    "path": str(output_path),
                    "sha256": sha256_file(output_path),
                    "bytes": output_path.stat().st_size,
                    "sample_rate": int(sample_rate),
                    "channels": int(info.channels),
                    "wav_subtype": info.subtype,
                    "audio": metrics,
                }
                manifest["outputs"].append(record)
                manifest["runtime"]["peak_cuda_allocated_bytes"] = int(
                    torch.cuda.max_memory_allocated()
                )
                manifest["runtime"]["peak_cuda_reserved_bytes"] = int(
                    torch.cuda.max_memory_reserved()
                )
                manifest["runtime"]["elapsed_seconds"] = round(
                    time.perf_counter() - started, 6
                )
                write_manifest(manifest_path, manifest)
                print(
                    json.dumps(
                        {
                            "candidate": candidate["id"],
                            "segment": segment["id"],
                            "duration_seconds": metrics["duration_seconds"],
                            "generation_seconds": round(generation_seconds, 3),
                            "path": str(output_path),
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
    except Exception as exc:
        manifest["errors"].append(
            {
                "type": type(exc).__name__,
                "message": str(exc),
                "elapsed_seconds": round(time.perf_counter() - started, 6),
                "peak_cuda_allocated_bytes": int(torch.cuda.max_memory_allocated()),
                "peak_cuda_reserved_bytes": int(torch.cuda.max_memory_reserved()),
            }
        )
        write_manifest(manifest_path, manifest)
        raise

    manifest["completed_at_utc"] = datetime.now(timezone.utc).isoformat()
    manifest["runtime"]["elapsed_seconds"] = round(time.perf_counter() - started, 6)
    manifest["status"] = "complete"
    write_manifest(manifest_path, manifest)
    verify_manifest(manifest_path)
    print(json.dumps({"ok": True, "manifest": str(manifest_path)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
