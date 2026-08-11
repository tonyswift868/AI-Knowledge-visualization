#!/usr/bin/env python3
"""Immutable, offline, resource-bounded Qwen3-TTS audition generation."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

SCRIPT_ROOT = Path(__file__).resolve().parent
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

import orchestrate_qwen3_tts_bounded_probe as PROBE
import orchestrate_qwen3_tts_training as ORCH

SCHEMA = "mentorflow.qwen3-tts-offline-audition-plan.v1"
RESULT_SCHEMA = "mentorflow.qwen3-tts-offline-audition-result.v1"
RECEIPT_SCHEMA = "mentorflow.qwen3-tts-offline-audition-receipt.v1"
CHECKPOINT = Path(r"E:\Downloads\椿.training\runs\tsubaki-local-private-neutral-20260808t021233z-v1-curriculum-group011-20step-v2-warm-g010-margin512\checkpoints\checkpoint-step-00000020")
TRAINING_PLAN_SHA256 = "984dc153a9c4fd8e0bbda01c800c6114238d5ef98b7e25f151c20637c7b378e2"
CHECKPOINT_RECEIPT_SHA256 = "31c7a2194f93566d99ad9ad5d3bd29486dea45f1b0a35e8ec83789af1ca2528a"
CHECKPOINT_TREE_SHA256 = "8d1244bd64c46eff0aeb5f284844ef4b89a624805e3dfa8dbdaa0045176af1ca"
REFERENCE = Path(r"E:\Downloads\椿.training\generations\tsubaki-local-private-neutral-20260808t021233z-v1\dataset\reference\tsubaki-reference.wav")
REFERENCE_SHA256 = "b140a46550f738434074e4df5e3e735b38ea386ebf11bee57a9e222d9514bd45"
OUTPUT_PARENT = Path(r"E:\Downloads\椿.training\inference-generations")
QWEN_PYTHON = Path(r"E:\Kristina_Runtime\runtimes\qwen3-tts\.venv\Scripts\python.exe")
TEXT = "同学你好，我们先从最关键的一步开始。"
TEXTS = [TEXT]
GENERATION_PREFIX = "tsubaki-group011v2-offline-audition"
AUDITION_SCOPE = "private_local_only_nonrelease_audition"
AUDITION_EXTRA: dict[str, Any] = {}
MAX_NEW_TOKENS = 512
REPETITION_PENALTY = 1.05
GENERATION_SEED = 20260808
TEMPERATURE = 0.8
TOP_P = 0.9
MAX_OUTPUT_DURATION_SECONDS = 30.0
SPEAKER = "tsubaki_teacher"
LANGUAGE = "Chinese"
MAXIMUM_SECONDS = 20 * 60


class AuditionError(RuntimeError):
    pass


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _pretty(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8") + b"\n"


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_new(path: Path, value: bytes) -> None:
    with path.open("xb") as handle:
        handle.write(value)
        handle.flush()
        os.fsync(handle.fileno())


def _read_json(path: Path) -> Mapping[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise AuditionError(f"expected JSON object: {path}")
    return value


def _offline_env() -> dict[str, str]:
    env = os.environ.copy()
    cache = Path(r"E:\Kristina_Runtime\cache")
    values = {
        **__import__("qwen3_tts_sft_safe").OFFLINE_VALUES,
        "HF_HOME": str(cache / "huggingface"),
        "HF_DATASETS_CACHE": str(cache / "huggingface" / "datasets"),
        "TRANSFORMERS_CACHE": str(cache / "huggingface" / "transformers"),
        "TORCH_HOME": str(cache / "torch"),
        "PIP_CACHE_DIR": str(cache / "pip"),
        "TMP": str(cache / "tmp"),
        "TEMP": str(cache / "tmp"),
        "CUDA_VISIBLE_DEVICES": "0",
        "TOKENIZERS_PARALLELISM": "false",
        "PYTHONNOUSERSITE": "1",
    }
    for path in (cache / "huggingface", cache / "huggingface" / "datasets", cache / "huggingface" / "transformers", cache / "torch", cache / "pip", cache / "tmp"):
        path.mkdir(parents=True, exist_ok=True)
    env.update(values)
    return env


def _verify_source() -> Mapping[str, Any]:
    import qwen3_tts_sft_safe as SAFE

    if _sha(CHECKPOINT / "CHECKPOINT_RECEIPT.json") != CHECKPOINT_RECEIPT_SHA256:
        raise AuditionError("final checkpoint receipt hash mismatch")
    receipt = SAFE._verify_checkpoint(CHECKPOINT, TRAINING_PLAN_SHA256)
    if receipt.get("tree_sha256") != CHECKPOINT_TREE_SHA256:
        raise AuditionError("final checkpoint tree mismatch")
    if _sha(REFERENCE) != REFERENCE_SHA256:
        raise AuditionError("authorized reference hash mismatch")
    config = _read_json(CHECKPOINT / "model" / "config.json")
    talker = config.get("talker_config")
    if not isinstance(talker, Mapping) or talker.get("spk_id", {}).get(SPEAKER) != 3000 or talker.get("spk_is_dialect", {}).get(SPEAKER) is not False:
        raise AuditionError("final custom speaker config mismatch")
    return receipt


def _worker(plan_path: Path) -> int:
    for key, value in __import__("qwen3_tts_sft_safe").OFFLINE_VALUES.items():
        if os.environ.get(key) != value:
            raise AuditionError(f"offline environment mismatch: {key}")
    import qwen3_tts_sft_safe as SAFE

    SAFE._assert_offline_environment()
    SAFE._block_python_network()
    plan = _read_json(plan_path)
    supplied_sha = str(plan.get("plan_sha256") or "")
    unsigned = dict(plan)
    unsigned.pop("plan_sha256", None)
    if supplied_sha != hashlib.sha256(_canonical(unsigned)).hexdigest():
        raise AuditionError("plan hash mismatch")
    generation = Path(str(plan["generation_path"]))
    if plan_path != generation / "INFERENCE_PLAN.json":
        raise AuditionError("plan path mismatch")
    _verify_source()

    import numpy as np
    import soundfile as sf
    import torch
    from qwen_tts import Qwen3TTSModel
    from safetensors import safe_open

    if not torch.cuda.is_available() or torch.cuda.device_count() != 1:
        raise AuditionError("exactly one visible CUDA device is required")
    total_mib = torch.cuda.get_device_properties(0).total_memory // (1024 * 1024)
    cap_mib = int(plan["resource_policy"]["allocator_cap_mib"])
    torch.cuda.set_per_process_memory_fraction(cap_mib / total_mib, device=0)
    torch.manual_seed(int(plan["generation"]["seed"]))
    torch.cuda.manual_seed_all(int(plan["generation"]["seed"]))

    wrapper = Qwen3TTSModel.from_pretrained(
        str(CHECKPOINT / "model"),
        device_map={"": "cuda:0"},
        dtype=torch.bfloat16,
        attn_implementation="sdpa",
        local_files_only=True,
        trust_remote_code=False,
    )
    if wrapper.model.tts_model_type != "custom_voice" or SPEAKER.lower() not in {v.lower() for v in wrapper.get_supported_speakers()}:
        raise AuditionError("loaded model custom speaker contract mismatch")

    trainer_state = _read_json(CHECKPOINT / "TRAINER_STATE.json")
    tensor_receipt = trainer_state["checkpoint_writer"]["model_tensor_receipt"]
    loaded = wrapper.model.state_dict()
    checked = 0
    with safe_open(str(CHECKPOINT / "model" / "model.safetensors"), framework="pt", device="cpu") as source:
        source_keys = list(source.keys())
        if source_keys != [str(item["name"]) for item in tensor_receipt["tensors"]]:
            raise AuditionError("checkpoint tensor order/inventory mismatch")
        for item in tensor_receipt["tensors"]:
            name = str(item["name"])
            tensor = loaded.get(name)
            dtype_name = {
                torch.bfloat16: "BF16", torch.float32: "F32", torch.float16: "F16",
                torch.int64: "I64", torch.int32: "I32", torch.int16: "I16",
                torch.int8: "I8", torch.uint8: "U8", torch.bool: "BOOL",
            }.get(tensor.dtype if tensor is not None else None)
            if tensor is None or list(tensor.shape) != list(item["shape"]) or dtype_name != str(item["dtype"]):
                raise AuditionError(f"loaded tensor schema mismatch: {name}")
            checked += 1
        target = source.get_tensor("talker.model.codec_embedding.weight")[3000]
    loaded_target = loaded["talker.model.codec_embedding.weight"][3000].detach().cpu()
    if not torch.equal(loaded_target, target):
        raise AuditionError("loaded speaker slot 3000 differs from checkpoint")

    outputs = []
    for index, text in enumerate(plan["generation"]["texts"], start=1):
        wavs, sample_rate = wrapper.generate_custom_voice(
            text=str(text), language=LANGUAGE, speaker=SPEAKER, do_sample=True,
            top_k=50, top_p=TOP_P, temperature=TEMPERATURE, repetition_penalty=REPETITION_PENALTY,
            max_new_tokens=MAX_NEW_TOKENS,
        )
        if len(wavs) != 1:
            raise AuditionError("model returned unexpected audio batch")
        audio = np.asarray(wavs[0], dtype=np.float32)
        if audio.ndim != 1 or audio.size == 0 or not np.isfinite(audio).all():
            raise AuditionError("model returned invalid audio")
        partial = generation / f"teacher_short_{index:02d}.wav.partial"
        final = generation / f"teacher_short_{index:02d}.wav"
        if partial.exists() or final.exists():
            raise AuditionError("refusing to overwrite audition audio")
        sf.write(str(partial), audio, int(sample_rate), subtype="PCM_16", format="WAV")
        os.replace(partial, final)
        outputs.append({"path": str(final), "bytes": final.stat().st_size, "sha256": _sha(final), "sample_rate": int(sample_rate), "text": str(text)})
    result = {
        "schema": RESULT_SCHEMA,
        "status": "success",
        "plan_sha256": supplied_sha,
        "output": outputs[0],
        "outputs": outputs,
        "loaded_model": {"tensor_count_checked": checked, "speaker": SPEAKER, "speaker_slot": 3000, "speaker_slot_exact": True, "dtype": "bfloat16", "attention": "sdpa", "device": "cuda:0"},
        "network_used": False,
        "service_started": False,
        "port_8000_touched": False,
    }
    _write_new(generation / "WORKER_RESULT.json", _pretty(result))
    return 0


def _qa(path: Path) -> Mapping[str, Any]:
    import numpy as np
    import soundfile as sf

    info = sf.info(str(path))
    audio, sample_rate = sf.read(str(path), dtype="float32", always_2d=False)
    value = np.asarray(audio)
    peak = float(np.max(np.abs(value)))
    rms = float(np.sqrt(np.mean(np.square(value, dtype=np.float64))))
    clipped_fraction = float(np.mean(np.abs(value) >= 0.999))
    duration = float(value.shape[0] / sample_rate)
    finite = bool(np.isfinite(value).all())
    silence = rms < 10 ** (-50 / 20)
    passed = info.channels == 1 and sample_rate == 24000 and 0.25 <= duration <= MAX_OUTPUT_DURATION_SECONDS and finite and not silence and clipped_fraction == 0.0 and peak <= 1.0
    return {
        "passed": passed, "format": info.format, "subtype": info.subtype, "channels": info.channels,
        "sample_rate": sample_rate, "frames": int(value.shape[0]), "duration_seconds": round(duration, 6),
        "finite": finite, "peak_absolute": round(peak, 8), "rms": round(rms, 8),
        "silence": silence, "clipped_fraction": round(clipped_fraction, 8),
    }


def _run() -> int:
    _verify_source()
    samples = []
    for index in range(2):
        sample = PROBE._sample_resources()
        policy = PROBE.bounded_policy(sample)
        samples.append({"sample": sample, "policy": policy})
        if policy["decision"] != "GO":
            raise AuditionError("resource launch gate blocked: " + "; ".join(policy["reasons"]))
        if index == 0:
            time.sleep(2)
    cap = min(int(item["policy"]["allocator_cap_mib"]) for item in samples)
    generation_id = GENERATION_PREFIX + "-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    OUTPUT_PARENT.mkdir(parents=True, exist_ok=True)
    generation = OUTPUT_PARENT / generation_id
    os.mkdir(generation)
    plan: dict[str, Any] = {
        "schema": SCHEMA, "created_at_utc": datetime.now(timezone.utc).isoformat(), "generation_id": generation_id,
        "generation_path": str(generation), "scope": AUDITION_SCOPE,
        "checkpoint": {"path": str(CHECKPOINT), "training_plan_sha256": TRAINING_PLAN_SHA256, "receipt_sha256": CHECKPOINT_RECEIPT_SHA256, "tree_sha256": CHECKPOINT_TREE_SHA256, "model_safetensors_sha256": _sha(CHECKPOINT / "model" / "model.safetensors")},
        "authorized_reference": {"path": str(REFERENCE), "sha256": REFERENCE_SHA256, "used_as_embedded_speaker_source": True, "passed_to_inference_api": False},
        "generation": {"text": TEXTS[0], "texts": TEXTS, "language": LANGUAGE, "speaker": SPEAKER, "speaker_slot": 3000, "seed": GENERATION_SEED, "sampling": {"do_sample": True, "top_k": 50, "top_p": TOP_P, "temperature": TEMPERATURE, "repetition_penalty": REPETITION_PENALTY, "max_new_tokens": MAX_NEW_TOKENS}},
        "resource_policy": {"source": "existing_bounded_gpu_policy", "allocator_cap_mib": cap, "launch_samples": samples, "watcher_interval_seconds": PROBE.WATCHER_INTERVAL_SECONDS, "runtime_minimum_free_vram_mib": PROBE.RUNTIME_MINIMUM_SYSTEM_FREE_VRAM_MIB, "runtime_maximum_temperature_c": PROBE.RUNTIME_MAXIMUM_TEMPERATURE_C, "runtime_minimum_ram_bytes": PROBE.RUNTIME_MINIMUM_RAM_BYTES, "runtime_minimum_commit_headroom_bytes": PROBE.RUNTIME_MINIMUM_COMMIT_HEADROOM_BYTES, "owned_job_kill_on_close": True, "job_memory_limit_enabled": False},
        "execution": {"offline": True, "network_forbidden": True, "service_forbidden": True, "port_8000_forbidden": True, "output_create_new": True},
    }
    plan["plan_sha256"] = hashlib.sha256(_canonical(plan)).hexdigest()
    _write_new(generation / "INFERENCE_PLAN.json", _pretty(plan))
    persisted_plan = _read_json(generation / "INFERENCE_PLAN.json")
    if persisted_plan.get("generation", {}).get("texts") != TEXTS:
        raise AuditionError("persisted UTF-8 audition texts differ from the exact requested texts")

    env = _offline_env()
    command = [str(QWEN_PYTHON), "-I", str(Path(__file__).resolve()), "--worker", "--plan", str(generation / "INFERENCE_PLAN.json")]
    stdout_path, stderr_path = generation / "worker.stdout.log", generation / "worker.stderr.log"
    gate = PROBE._WindowsGate()
    job = PROBE._WindowsGpuJob()
    process = None
    watcher_samples: list[Mapping[str, Any]] = []
    violation = None
    started = time.monotonic()
    launch = PROBE._sample_resources()
    launch_policy = PROBE.bounded_policy(launch)
    if launch_policy["decision"] != "GO" or int(launch_policy["allocator_cap_mib"]) < cap:
        raise AuditionError("atomic launch resource sample no longer satisfies plan")
    policy = {**launch_policy, "thresholds": {**launch_policy["thresholds"], "runtime_maximum_total_gpu_used_mib": int(launch["start_used_vram_mib"]) + cap + PROBE.WATCHER_MARGIN_MIB}}
    low_ram = 0
    job_info = None
    try:
        with stdout_path.open("xb") as stdout, stderr_path.open("xb") as stderr:
            gated = [command[0], "-I", "-c", PROBE._GATED_BOOTSTRAP, gate.name, *command]
            process = subprocess.Popen(gated, cwd=ORCH.WORKSPACE_ROOT, env=env, stdout=stdout, stderr=stderr, shell=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000))
            job.assign(process)
            job_info = job.query()
            gate.release()
            while process.poll() is None:
                try:
                    sample = PROBE._sample_resources()
                    reason = PROBE.watcher_violation(policy, sample)
                    low_ram, memory_reason = PROBE.memory_watcher_violation(sample, low_ram)
                    reason = reason or memory_reason
                    job_info = job.query()
                except BaseException as exc:
                    sample = {"observation_error": type(exc).__name__}
                    reason = "resource observation missing or invalid"
                watcher_samples.append({"elapsed_seconds": round(time.monotonic() - started, 3), **sample})
                if reason or time.monotonic() - started > MAXIMUM_SECONDS:
                    violation = reason or "inference timeout"
                    job.terminate()
                    break
                time.sleep(PROBE.WATCHER_INTERVAL_SECONDS)
            returncode = process.wait(timeout=15)
            job_info = job.query()
    finally:
        if process is not None and process.poll() is None:
            job.terminate()
        gate.close()
        job.close()

    watcher = {"status": "passed" if returncode == 0 and violation is None else "failed", "launch": launch, "samples": watcher_samples, "violation": violation, "worker_returncode": returncode, "gpu_job": job_info}
    _write_new(generation / "WATCHER_RECEIPT.json", _pretty(watcher))
    if watcher["status"] != "passed":
        raise AuditionError(f"worker failed: {violation or returncode}; see {stderr_path}")
    result = _read_json(generation / "WORKER_RESULT.json")
    outputs = []
    for item in result.get("outputs", []):
        wav = Path(str(item["path"]))
        if item.get("sha256") != _sha(wav):
            raise AuditionError("worker output hash mismatch")
        outputs.append({**item, "qa": _qa(wav)})
    if len(outputs) != len(TEXTS):
        raise AuditionError("worker output count mismatch")
    manifest = {
        "schema": "mentorflow.qwen3-tts-offline-audition-manifest.v1",
        "generation_id": generation_id,
        "plan_sha256": plan["plan_sha256"],
        "scope": plan["scope"],
        "speaker": SPEAKER,
        "texts": TEXTS,
        "outputs": outputs,
    }
    _write_new(generation / "INFERENCE_MANIFEST.json", _pretty(manifest))
    receipt = {"schema": RECEIPT_SCHEMA, "status": "passed" if all(item["qa"]["passed"] for item in outputs) else "failed", "created_at_utc": datetime.now(timezone.utc).isoformat(), "plan_sha256": plan["plan_sha256"], "manifest_sha256": _sha(generation / "INFERENCE_MANIFEST.json"), "checkpoint_verification": {"receipt_sha256": CHECKPOINT_RECEIPT_SHA256, "tree_sha256": CHECKPOINT_TREE_SHA256, "model_safetensors_sha256": _sha(CHECKPOINT / "model" / "model.safetensors"), "all_checkpoint_tensors_hash_verified": True, "loaded_tensor_schema_count": result["loaded_model"]["tensor_count_checked"], "loaded_speaker_slot_3000_exact": result["loaded_model"]["speaker_slot_exact"]}, "outputs": outputs, "watcher": {"status": watcher["status"], "sample_count": len(watcher_samples), "gpu_job": job_info}, "network_used": False, "service_started": False, "port_8000_touched": False, "perceptual_quality_human_review_pending": True, **AUDITION_EXTRA}
    _write_new(generation / "INFERENCE_RECEIPT.json", _pretty(receipt))
    print(json.dumps({"status": receipt["status"], "generation": str(generation), "wavs": [item["path"] for item in outputs], "qa": [item["qa"] for item in outputs]}, ensure_ascii=False), flush=True)
    return 0 if receipt["status"] == "passed" else 2


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--plan", type=Path)
    args = parser.parse_args()
    if args.worker:
        if args.plan is None:
            raise AuditionError("--plan is required for worker")
        return _worker(args.plan.resolve())
    return _run()


if __name__ == "__main__":
    raise SystemExit(main())
