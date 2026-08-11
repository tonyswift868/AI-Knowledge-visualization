#!/usr/bin/env python3
"""Deterministic SDPA trainer used only by the admitted offline orchestrator.

This is a compatibility derivative of QwenLM/Qwen3-TTS ``sft_12hz.py`` at
commit ``022e286b98fbec7e1e916cb940cdf532cd9f488e``.  It keeps the upstream
dataset and loss path, but removes the unavailable TensorBoard/FlashAttention
requirements and adds deterministic seeds, complete resumable state, and
same-volume staged checkpoint commits.  It is not a standalone downloader or
an API service.
"""

from __future__ import annotations

import argparse
import gc
import hashlib
import importlib.util
import json
import os
import random
import re
import shutil
import socket
import stat
import subprocess
import sys
import traceback
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

# ``python -I`` intentionally excludes the script directory from ``sys.path``.
# Load the two pinned, sibling-only training helpers from this audited directory.
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))


PLAN_SCHEMA = "mentorflow.qwen3-tts-training-run-plan.v1"
CHECKPOINT_SCHEMA = "mentorflow.qwen3-tts-training-checkpoint.v1"
PROBE_RESULT_SCHEMA = "mentorflow.qwen3-tts-formal-like-probe.v1"
BOUNDED_PROBE_RESULT_SCHEMA = "mentorflow.qwen3-tts-user-authorized-bounded-probe.v1"
FORMAL_RESULT_SCHEMA = "mentorflow.qwen3-tts-safe-sft-result.v1"
CHECKPOINT_RE = re.compile(r"^checkpoint-step-(\d{8})$")
OFFLINE_VALUES = {
    "HF_HUB_OFFLINE": "1",
    "TRANSFORMERS_OFFLINE": "1",
    "HF_DATASETS_OFFLINE": "1",
    "PIP_NO_INDEX": "1",
    "HF_HUB_DISABLE_TELEMETRY": "1",
    "DO_NOT_TRACK": "1",
    "WANDB_DISABLED": "true",
    "NO_PROXY": "*",
    "no_proxy": "*",
}


class SafeTrainerError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _fail(code: str, message: str) -> None:
    raise SafeTrainerError(code, message)


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _pretty_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8") + b"\n"


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _is_reparse(path: Path) -> bool:
    value = path.lstat()
    attrs = int(getattr(value, "st_file_attributes", 0))
    return stat.S_ISLNK(value.st_mode) or bool(attrs & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def _plain_file(path: Path, field: str, *, reject_hardlink: bool = True) -> Path:
    try:
        value = path.lstat()
    except FileNotFoundError:
        _fail("FILE_MISSING", f"{field} is missing: {path}")
    if not stat.S_ISREG(value.st_mode) or _is_reparse(path):
        _fail("FILE_INVALID", f"{field} is not a plain file: {path}")
    if reject_hardlink and int(value.st_nlink) != 1:
        _fail("HARDLINK_FORBIDDEN", f"{field} is hardlinked: {path}")
    return path


def _plain_dir(path: Path, field: str) -> Path:
    try:
        value = path.lstat()
    except FileNotFoundError:
        _fail("DIRECTORY_MISSING", f"{field} is missing: {path}")
    if not stat.S_ISDIR(value.st_mode) or _is_reparse(path):
        _fail("DIRECTORY_INVALID", f"{field} is not a plain directory: {path}")
    return path


def _exclusive_write(path: Path, content: bytes) -> None:
    with path.open("xb") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())


def _read_json(path: Path, field: str) -> Mapping[str, Any]:
    path = _plain_file(path, field)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        _fail("JSON_INVALID", f"{field} is invalid JSON: {type(exc).__name__}")
    if not isinstance(value, Mapping):
        _fail("JSON_INVALID", f"{field} must be an object")
    return value


def _assert_offline_environment(environment: Mapping[str, str] | None = None) -> None:
    values = environment or os.environ
    for name, expected in OFFLINE_VALUES.items():
        if values.get(name) != expected:
            _fail("OFFLINE_ENV_INVALID", f"{name} must equal {expected!r}")
    for name in ("HF_HOME", "HF_DATASETS_CACHE", "TRANSFORMERS_CACHE", "TORCH_HOME", "PIP_CACHE_DIR", "TMP", "TEMP"):
        value = str(values.get(name) or "")
        if not value or Path(value).drive.upper() != "E:":
            _fail("OFFLINE_CACHE_INVALID", f"{name} must be an absolute E: path")


def _block_python_network() -> None:
    original_socket = socket.socket

    class OfflineSocket(original_socket):
        def connect(self, *args: Any, **kwargs: Any) -> Any:
            raise OSError("network disabled by bounded Qwen training worker")

        def connect_ex(self, *args: Any, **kwargs: Any) -> int:
            raise OSError("network disabled by bounded Qwen training worker")

        def sendto(self, *args: Any, **kwargs: Any) -> int:
            raise OSError("network disabled by bounded Qwen training worker")

    def blocked(*args: Any, **kwargs: Any) -> Any:
        raise OSError("network disabled by bounded Qwen training worker")

    if hasattr(original_socket, "sendmsg"):
        OfflineSocket.sendmsg = blocked  # type: ignore[attr-defined]
    socket.socket = OfflineSocket
    socket.create_connection = blocked
    socket.getaddrinfo = blocked
    socket.gethostbyname = blocked
    socket.gethostbyname_ex = blocked
    urllib.request.urlopen = blocked
    urllib.request.OpenerDirector.open = blocked
    blocked_events = {
        "socket.connect",
        "socket.getaddrinfo",
        "socket.gethostbyaddr",
        "socket.gethostbyname",
        "socket.sendto",
        "socket.sendmsg",
    }

    def audit(event: str, args: tuple[Any, ...]) -> None:
        del args
        if event in blocked_events:
            raise OSError("network disabled by bounded Qwen training audit hook")

    sys.addaudithook(audit)


def _bounded_allocator_policy(plan: Mapping[str, Any], torch: Any) -> dict[str, Any] | None:
    if plan.get("mode") != "bounded-probe":
        return None
    policy = plan.get("bounded_probe_policy")
    if not isinstance(policy, Mapping):
        _fail("PLAN_INVALID", "bounded probe allocator policy is missing")
    plan_cap = policy.get("allocator_cap_mib")
    cap = int(os.environ.get("MENTORFLOW_BOUNDED_ALLOCATOR_CAP_MIB", str(plan_cap)))
    total = int(os.environ.get("MENTORFLOW_BOUNDED_START_TOTAL_VRAM_MIB", str(policy.get("start_total_vram_mib"))))
    start_free = int(os.environ.get("MENTORFLOW_BOUNDED_START_FREE_VRAM_MIB", str(policy.get("start_free_vram_mib"))))
    headroom = policy.get("system_free_vram_headroom_mib")
    if (
        type(plan_cap) is not int
        or type(total) is not int
        or type(start_free) is not int
        or headroom != 1_024
        or not 7_168 <= cap <= 8_704
        or cap > int(plan_cap)
        or total <= 0
        or cap != min(int(plan_cap), 8_704, start_free - int(headroom))
    ):
        _fail("PLAN_INVALID", "bounded probe allocator cap is invalid")
    fraction = cap / total
    if not 0.0 < fraction < 1.0:
        _fail("PLAN_INVALID", "bounded probe allocator fraction is invalid")
    torch.cuda.set_per_process_memory_fraction(fraction, device=0)
    return {
        "allocator_cap_mib": cap,
        "plan_allocator_cap_upper_bound_mib": int(plan_cap),
        "start_free_vram_mib": start_free,
        "start_total_vram_mib": total,
        "allocator_fraction": fraction,
    }


def _load_plan(path: Path) -> Mapping[str, Any]:
    plan = _read_json(path, "run plan")
    if plan.get("schema") != PLAN_SCHEMA or not isinstance(plan.get("plan_sha256"), str):
        _fail("PLAN_INVALID", "run plan schema/hash is invalid")
    unsigned = dict(plan)
    declared = unsigned.pop("plan_sha256")
    if declared != _sha256_bytes(_canonical_json(unsigned)):
        _fail("PLAN_INVALID", "run plan canonical SHA-256 is invalid")
    if (plan.get("network_policy") or {}).get("mode") != "zero-traffic-local-only":
        _fail("PLAN_INVALID", "run plan is not zero-traffic")
    if plan.get("offline_environment") != {name: os.environ.get(name) for name in plan["offline_environment"]}:
        _fail("OFFLINE_ENV_INVALID", "process environment differs from the immutable offline plan")
    training = plan.get("training")
    if not isinstance(training, Mapping) or (
        training.get("batch_size") != 1
        or training.get("gradient_accumulation_steps") != 8
        or training.get("dataloader_workers") != 0
        or training.get("attention_implementation") != "sdpa"
        or training.get("mixed_precision") != "bf16"
        or training.get("gradient_checkpointing") != {"enabled": True, "use_reentrant": False}
        or training.get("use_cache") is not False
        or training.get("optimizer")
        != {
            "name": "ChunkedAdamW",
            "base_math": "AdamW",
            "chunk_bytes": 32 * 1024 * 1024,
            "foreach": False,
            "fused": False,
            "amsgrad": False,
            "capturable": False,
            "differentiable": False,
            "implementation_sha256": training.get("optimizer", {}).get("implementation_sha256"),
        }
        or training.get("lora") is not False
        or training.get("eight_bit_optimizer") is not False
        or training.get("cpu_offload") is not False
    ):
        _fail("PLAN_INVALID", "run plan training profile is not the admitted low-VRAM full-SFT profile")
    if training.get("safe_trainer_sha256") != _sha256_file(Path(__file__).resolve()):
        _fail("PLAN_INVALID", "safe trainer bytes differ from the immutable run plan")
    optimizer_path = _plain_file(Path(str(plan["paths"]["optimizer_implementation"])), "chunked AdamW implementation")
    if training["optimizer"].get("implementation_sha256") != _sha256_file(optimizer_path):
        _fail("PLAN_INVALID", "chunked AdamW bytes differ from the immutable run plan")
    scope = (plan.get("artifact_policy") or {}).get("training_scope")
    if scope == "private_local_only" and plan.get("artifact_policy") != {
        "training_scope": "private_local_only",
        "non_exportable": True,
        "public_release_eligible": False,
        "commercial_use_eligible": False,
        "release_upgrade_requires_new_signed_release_ready_manifest": True,
    }:
        _fail("PLAN_INVALID", "local-private artifact policy is not fail-closed")
    return plan


def _verify_plan_materials(plan: Mapping[str, Any]) -> None:
    for section_name, root_key in (("source", "source_root"), ("base_model", "model_root")):
        section = plan.get(section_name)
        if not isinstance(section, Mapping) or not isinstance(section.get("files"), list):
            _fail("PLAN_INVALID", f"{section_name} pin inventory is malformed")
        root = _plain_dir(Path(str(plan["paths"][root_key])), f"{section_name} root")
        declared_paths: set[str] = set()
        for item in section["files"]:
            if not isinstance(item, Mapping):
                _fail("PLAN_INVALID", f"{section_name} pin entry is malformed")
            relative = str(item.get("path") or "")
            if (
                not relative
                or "\\" in relative
                or ":" in relative
                or relative.startswith("/")
                or Path(relative).is_absolute()
                or any(part in {"", ".", ".."} for part in Path(relative).parts)
            ):
                _fail("PLAN_INVALID", f"{section_name} pin path is unsafe")
            path = _plain_file(root.joinpath(*relative.split("/")), f"{section_name}/{relative}")
            if path.stat().st_size != item.get("bytes") or _sha256_file(path) != item.get("sha256"):
                _fail("MATERIAL_DRIFT", f"{section_name}/{relative} changed after admission")
            declared_paths.add(relative)
        if section_name == "base_model":
            actual = {path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()}
            if actual != declared_paths:
                _fail("MATERIAL_DRIFT", "base model exact file set changed after admission")


def _inventory(root: Path, *, excluded_name: str | None = None) -> tuple[list[dict[str, Any]], str]:
    root = _plain_dir(root, "checkpoint staging root")
    records: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix().casefold()):
        relative = path.relative_to(root).as_posix()
        if _is_reparse(path):
            _fail("CHECKPOINT_REPARSE", f"checkpoint contains a reparse point: {relative}")
        value = path.lstat()
        if stat.S_ISDIR(value.st_mode):
            continue
        if not stat.S_ISREG(value.st_mode) or int(value.st_nlink) != 1:
            _fail("CHECKPOINT_FILE_INVALID", f"checkpoint contains a non-plain file: {relative}")
        if excluded_name is not None and relative.casefold() == excluded_name.casefold():
            continue
        records.append({"path": relative, "bytes": int(value.st_size), "sha256": _sha256_file(path)})
    tree = _sha256_bytes(
        b"".join(f"{item['path']}\0{item['bytes']}\0{item['sha256']}\n".encode("utf-8") for item in records)
    )
    return records, tree


def _verify_checkpoint(path: Path, plan_sha256: str) -> Mapping[str, Any]:
    path = _plain_dir(path, "checkpoint")
    receipt = _read_json(path / "CHECKPOINT_RECEIPT.json", "checkpoint receipt")
    match = CHECKPOINT_RE.fullmatch(path.name)
    if (
        not match
        or receipt.get("schema") != CHECKPOINT_SCHEMA
        or receipt.get("plan_sha256") != plan_sha256
        or receipt.get("global_optimizer_step") != int(match.group(1))
    ):
        _fail("CHECKPOINT_INVALID", f"checkpoint identity is invalid: {path.name}")
    files, tree = _inventory(path, excluded_name="CHECKPOINT_RECEIPT.json")
    if receipt.get("files") != files or receipt.get("tree_sha256") != tree:
        _fail("CHECKPOINT_INVALID", f"checkpoint inventory is invalid: {path.name}")
    trainer_state_path = path / "TRAINER_STATE.json"
    # Generic commit tests and pre-streaming historical receipts do not carry
    # a model artifact.  New production checkpoints always bind a writer
    # contract and therefore take the strict semantic path below.
    receipt_state = receipt.get("trainer_state")
    if not trainer_state_path.exists() and (
        not isinstance(receipt_state, Mapping) or not isinstance(receipt_state.get("checkpoint_writer"), Mapping)
    ):
        return receipt
    trainer_state = _read_json(trainer_state_path, "checkpoint trainer state")
    if receipt.get("trainer_state") != trainer_state:
        _fail("CHECKPOINT_INVALID", "checkpoint trainer state differs from receipt")
    writer = trainer_state.get("checkpoint_writer")
    if not isinstance(writer, Mapping):
        _fail("CHECKPOINT_INVALID", "checkpoint streaming writer receipt is missing")
    model_receipt = writer.get("model_tensor_receipt")
    if not isinstance(model_receipt, Mapping):
        _fail("CHECKPOINT_INVALID", "checkpoint model tensor receipt is missing")
    config = _read_json(path / "model" / "config.json", "checkpoint model config")
    speaker_name = str(trainer_state.get("speaker_name") or "tsubaki_teacher")
    talker_config = config.get("talker_config")
    if (
        config.get("tts_model_type") != "custom_voice"
        or not isinstance(talker_config, Mapping)
        or talker_config.get("spk_id", {}).get(speaker_name) != 3000
        or talker_config.get("spk_is_dialect", {}).get(speaker_name) is not False
    ):
        _fail("CHECKPOINT_INVALID", "checkpoint custom speaker config is invalid")
    from safetensors import safe_open
    from streaming_safetensors import verify_streaming_safetensors

    with safe_open(str(path / "target_speaker.safetensors"), framework="pt", device="cpu") as handle:
        if list(handle.keys()) != ["embedding"]:
            _fail("CHECKPOINT_INVALID", "target speaker safetensors inventory is invalid")
        target_embedding = handle.get_tensor("embedding")
    verify_streaming_safetensors(
        path / "model" / "model.safetensors",
        model_receipt,
        expected_speaker_embedding=target_embedding,
    )
    return receipt


def _latest_checkpoint(root: Path, plan_sha256: str) -> tuple[Path | None, Mapping[str, Any] | None]:
    root = _plain_dir(root, "checkpoint root")
    candidates: list[tuple[int, Path, Mapping[str, Any]]] = []
    for child in root.iterdir():
        match = CHECKPOINT_RE.fullmatch(child.name)
        if match:
            receipt = _verify_checkpoint(child, plan_sha256)
            candidates.append((int(match.group(1)), child, receipt))
    if not candidates:
        return None, None
    _, path, receipt = max(candidates, key=lambda item: item[0])
    return path, receipt


def _copy_model_static(source: Path, target: Path) -> None:
    source = _plain_dir(source, "base model root")
    os.mkdir(target)
    for path in sorted(source.rglob("*"), key=lambda item: item.relative_to(source).as_posix().casefold()):
        relative = path.relative_to(source)
        destination = target / relative
        if path.is_dir():
            os.mkdir(destination)
            continue
        _plain_file(path, f"base model/{relative.as_posix()}")
        if relative.as_posix() == "model.safetensors":
            continue
        shutil.copy2(path, destination)
        if destination.stat().st_size != path.stat().st_size or _sha256_file(destination) != _sha256_file(path):
            _fail("CHECKPOINT_COPY_FAILED", f"base model copy verification failed: {relative.as_posix()}")


def _commit_checkpoint(
    *,
    stage: Path,
    checkpoint_root: Path,
    plan_sha256: str,
    global_step: int,
    state: Mapping[str, Any],
) -> Path:
    if global_step < 1:
        _fail("CHECKPOINT_INVALID", "cannot commit checkpoint before an optimizer step")
    files, tree = _inventory(stage)
    receipt = {
        "schema": CHECKPOINT_SCHEMA,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "plan_sha256": plan_sha256,
        "global_optimizer_step": global_step,
        "trainer_state": dict(state),
        "files": files,
        "tree_sha256": tree,
        "commit_method": "same-volume-stage-then-rename",
    }
    _exclusive_write(stage / "CHECKPOINT_RECEIPT.json", _pretty_json(receipt))
    final = checkpoint_root / f"checkpoint-step-{global_step:08d}"
    if final.exists():
        existing = _verify_checkpoint(final, plan_sha256)
        if existing != receipt:
            _fail("CHECKPOINT_EXISTS", f"refusing to replace checkpoint {final.name}")
        return final
    os.rename(stage, final)
    _verify_checkpoint(final, plan_sha256)
    return final


def _free_ram_bytes() -> int:
    if os.name == "nt":
        import ctypes

        class MemoryStatus(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        status = MemoryStatus()
        status.dwLength = ctypes.sizeof(status)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            _fail("RUNTIME_RESOURCE_GATE", "GlobalMemoryStatusEx failed during training")
        return int(status.ullAvailPhys)
    return int(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_AVPHYS_PAGES"))


def _gpu_runtime_sample() -> dict[str, int]:
    nvidia = shutil.which("nvidia-smi.exe") or shutil.which("nvidia-smi")
    if not nvidia:
        _fail("RUNTIME_RESOURCE_GATE", "nvidia-smi disappeared during training")
    completed = subprocess.run(
        [
            nvidia,
            "--query-gpu=temperature.gpu,memory.used",
            "--format=csv,noheader,nounits",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
        shell=False,
    )
    values = [item.strip() for item in completed.stdout.strip().split(",")]
    try:
        temperature, used = int(values[0]), int(values[1])
    except (IndexError, ValueError):
        _fail("RUNTIME_RESOURCE_GATE", "GPU runtime probe returned invalid data")
    if completed.returncode != 0:
        _fail("RUNTIME_RESOURCE_GATE", "GPU runtime probe failed")
    return {"temperature_c": temperature, "total_gpu_used_mib": used}


def _runtime_gate(plan: Mapping[str, Any], *, peak_reserved_mib: int) -> dict[str, int]:
    maximum = int(plan["resource_admission"]["thresholds"]["runtime_maximum_temperature_c"])
    maximum_used = int(plan["resource_admission"]["thresholds"]["runtime_maximum_total_gpu_used_mib"])
    maximum_reserved = int(plan["resource_admission"]["thresholds"]["runtime_maximum_peak_reserved_mib"])
    minimum_ram = int(plan["resource_admission"]["thresholds"]["runtime_minimum_free_ram_bytes"])
    sample = _gpu_runtime_sample()
    free_ram = _free_ram_bytes()
    if sample["temperature_c"] > maximum:
        _fail("RUNTIME_RESOURCE_GATE", f"GPU temperature {sample['temperature_c']} C exceeds {maximum} C")
    if sample["total_gpu_used_mib"] > maximum_used:
        _fail(
            "RUNTIME_RESOURCE_GATE",
            f"total GPU used {sample['total_gpu_used_mib']} MiB exceeds {maximum_used} MiB",
        )
    if peak_reserved_mib > maximum_reserved:
        _fail("RUNTIME_RESOURCE_GATE", f"peak reserved {peak_reserved_mib} MiB exceeds {maximum_reserved} MiB")
    if free_ram < minimum_ram:
        _fail("RUNTIME_RESOURCE_GATE", f"free RAM {free_ram} bytes is below {minimum_ram} bytes")
    run_root = Path(str(plan["paths"]["run_root"]))
    free = shutil.disk_usage(run_root).free
    if free < 10 * 1024**3:
        _fail("RUNTIME_RESOURCE_GATE", "less than 10 GiB free space remains on the run volume")
    return {**sample, "free_ram_bytes": free_ram, "peak_reserved_mib": peak_reserved_mib}


def _checkpoint_resource_gate(plan: Mapping[str, Any], *, peak_reserved_mib: int) -> dict[str, int]:
    sample = _runtime_gate(plan, peak_reserved_mib=peak_reserved_mib)
    minimum_checkpoint_ram = int(
        plan["resource_admission"]["thresholds"]["formal_checkpoint_minimum_free_ram_bytes"]
    )
    checkpoint_free_ram = _free_ram_bytes()
    if checkpoint_free_ram < minimum_checkpoint_ram:
        _fail(
            "CHECKPOINT_RESOURCE_GATE",
            f"free RAM {checkpoint_free_ram} bytes is below the formal checkpoint minimum "
            f"{minimum_checkpoint_ram} bytes",
        )
    return {**sample, "checkpoint_free_ram_bytes": checkpoint_free_ram}


def _load_dataset_module(source_root: Path):
    path = _plain_file(source_root / "finetuning" / "dataset.py", "pinned upstream dataset.py")
    spec = importlib.util.spec_from_file_location("mentorflow_pinned_qwen_dataset", path)
    if spec is None or spec.loader is None:
        _fail("UPSTREAM_IMPORT_FAILED", "cannot load pinned upstream dataset.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_model_only_warm_start(model: Any, policy: Mapping[str, Any]) -> dict[str, Any]:
    import torch
    from safetensors import safe_open

    if (
        policy.get("continuation_kind") != "model_weights_only_warm_start"
        or policy.get("exact_resume") is not False
        or policy.get("optimizer_state_reset") is not True
    ):
        _fail("WARM_START_INVALID", "warm-start continuation policy is invalid")
    promotion = _plain_file(Path(str(policy["promotion_receipt"])), "warm-start promotion receipt")
    model_path = _plain_file(Path(str(policy["model_path"])), "warm-start model weights")
    if (
        _sha256_file(promotion) != policy.get("promotion_receipt_sha256")
        or model_path.stat().st_size != policy.get("model_bytes")
        or _sha256_file(model_path) != policy.get("model_sha256")
    ):
        _fail("WARM_START_DRIFT", "warm-start receipt or model weights drifted")
    state = model.state_dict()
    expected = sorted(name for name in state if not name.startswith("speaker_encoder"))
    schema_digest = hashlib.sha256()
    loaded_count = 0
    with safe_open(str(model_path), framework="pt", device="cpu") as handle:
        names = sorted(handle.keys())
        if names != expected or len(names) != int(policy["model_tensor_count"]):
            _fail("WARM_START_INVALID", "warm-start tensor inventory differs from the live model")
        with torch.no_grad():
            for name in names:
                target = state[name]
                source = handle.get_tensor(name)
                if tuple(source.shape) != tuple(target.shape) or source.dtype != target.dtype:
                    _fail("WARM_START_INVALID", f"warm-start tensor schema mismatch: {name}")
                target.copy_(source.to(device=target.device), non_blocking=False)
                descriptor = f"{name}\0{list(source.shape)}\0{source.dtype}\n".encode("utf-8")
                schema_digest.update(descriptor)
                loaded_count += 1
                del source
    return {
        "continuation_kind": "model_weights_only_warm_start",
        "exact_resume": False,
        "optimizer_state_reset": True,
        "promotion_receipt_sha256": policy["promotion_receipt_sha256"],
        "parent_checkpoint_receipt_sha256": policy["parent_checkpoint_receipt_sha256"],
        "parent_checkpoint_tree_sha256": policy["parent_checkpoint_tree_sha256"],
        "parent_global_optimizer_step": policy["parent_global_optimizer_step"],
        "model_sha256": policy["model_sha256"],
        "tensors_loaded_and_schema_verified": loaded_count,
        "tensor_schema_digest_sha256": schema_digest.hexdigest(),
        "loaded_from_parent_checkpoint_not_base": True,
    }


def _seed_everything(seed: int) -> None:
    import numpy as np
    import torch

    random.seed(seed)
    np.random.seed(seed % (2**32))
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)
    torch.backends.cudnn.benchmark = False
    torch.backends.cudnn.deterministic = True
    torch.use_deterministic_algorithms(True)


def _tensor_descriptor(value: Any) -> dict[str, Any]:
    return {
        "shape": [int(dimension) for dimension in value.shape],
        "dtype": str(value.dtype),
        "device": str(value.device),
    }


def train(plan: Mapping[str, Any], prepared_jsonl: Path, checkpoint_root: Path, attempt_root: Path, *, resume: bool) -> dict[str, Any]:
    _assert_offline_environment()
    _verify_plan_materials(plan)
    _block_python_network()
    import torch
    allocator_policy = _bounded_allocator_policy(plan, torch)
    from accelerate import Accelerator
    from qwen_tts.inference.qwen3_tts_model import Qwen3TTSModel
    from safetensors.torch import load_file, save_file
    from torch.utils.data import DataLoader, Subset
    from transformers import AutoConfig
    from chunked_adamw import ChunkedAdamW

    mode = str(plan["mode"])
    if mode in {"dry-run", "bounded-probe"} and resume:
        _fail("PROBE_RESUME_FORBIDDEN", "the formal-like probe never creates a resumable formal checkpoint")
    configuration = plan["training"]
    seed = int(configuration["seed"])
    _seed_everything(seed)
    source_root = _plain_dir(Path(str(plan["paths"]["source_root"])), "source root")
    model_root = _plain_dir(Path(str(plan["paths"]["model_root"])), "model root")
    prepared_jsonl = _plain_file(prepared_jsonl, "prepared JSONL")
    checkpoint_root = _plain_dir(checkpoint_root, "checkpoint root")
    attempt_root = _plain_dir(attempt_root, "attempt evidence root")
    baseline_gpu = _gpu_runtime_sample()
    dataset_module = _load_dataset_module(source_root)
    accelerator = Accelerator(
        gradient_accumulation_steps=8,
        mixed_precision="bf16",
    )
    qwen = Qwen3TTSModel.from_pretrained(
        str(model_root),
        torch_dtype=torch.bfloat16,
        attn_implementation="sdpa",
        local_files_only=True,
    )
    floating_parameter_dtypes = sorted(
        {str(parameter.dtype) for parameter in qwen.model.parameters() if parameter.is_floating_point()}
    )
    if floating_parameter_dtypes != ["torch.bfloat16"]:
        _fail("MODEL_DTYPE_INVALID", "bounded/full SFT model parameters are not uniformly BF16")
    warm_start_policy = plan.get("warm_start")
    warm_start_evidence = (
        _load_model_only_warm_start(qwen.model, warm_start_policy)
        if isinstance(warm_start_policy, Mapping)
        else None
    )
    if not hasattr(qwen.model, "gradient_checkpointing_enable"):
        _fail("GRADIENT_CHECKPOINTING_UNAVAILABLE", "the pinned model lacks gradient checkpointing support")
    qwen.model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})
    qwen.model.config.use_cache = False
    if hasattr(qwen.model, "talker") and hasattr(qwen.model.talker, "config"):
        qwen.model.talker.config.use_cache = False
    model_config = AutoConfig.from_pretrained(str(model_root), local_files_only=True)
    rows = [json.loads(line) for line in prepared_jsonl.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not rows or any(set(row) != {"audio", "text", "ref_audio", "audio_codes"} for row in rows):
        _fail("PREPARED_INVALID", "prepared JSONL rows/fields are invalid")
    dataset = dataset_module.TTSDataset(rows, qwen.processor, model_config)
    optimizer = ChunkedAdamW(
        qwen.model.parameters(),
        lr=float(configuration["learning_rate"]),
        weight_decay=0.01,
        chunk_bytes=int(configuration["optimizer"]["chunk_bytes"]),
        foreach=False,
    )
    if len(optimizer.state) != 0:
        _fail("WARM_START_INVALID", "new curriculum optimizer did not start with empty state")
    if warm_start_evidence is not None:
        warm_start_evidence["optimizer_state_entries_before_first_step"] = 0
    model, optimizer = accelerator.prepare(qwen.model, optimizer)
    torch.cuda.reset_peak_memory_stats()
    start_epoch, start_batch, global_step = 0, 0, 0
    target_speaker_embedding = None
    latest_path, latest_receipt = _latest_checkpoint(checkpoint_root, str(plan["plan_sha256"]))
    if resume:
        if latest_path is None or latest_receipt is None:
            _fail("RESUME_CHECKPOINT_MISSING", "--resume requires a committed checkpoint")
        accelerator.load_state(str(latest_path / "accelerator"))
        state = latest_receipt["trainer_state"]
        start_epoch = int(state["next_epoch"])
        start_batch = int(state["next_batch"])
        global_step = int(latest_receipt["global_optimizer_step"])
        target_speaker_embedding = load_file(str(latest_path / "target_speaker.safetensors"))["embedding"]
    elif latest_path is not None:
        _fail("CHECKPOINT_EXISTS", "committed checkpoint exists; explicit --resume is required")
    epochs = int(configuration["epochs"])
    max_steps = configuration.get("max_optimizer_steps")
    max_steps = int(max_steps) if max_steps is not None else None
    if mode == "dry-run" and max_steps != 20:
        _fail("PLAN_INVALID", "formal-like dry-run must execute exactly 20 optimizer steps")
    if mode == "bounded-probe" and max_steps not in {1, 20}:
        _fail("PLAN_INVALID", "bounded probe must execute exactly 1 or 20 optimizer steps")
    completed = False
    last_checkpoint: Path | None = latest_path
    longest_relative = str(plan["dataset"]["longest_train_sample"]["audio"])
    longest_absolute = os.path.normcase(
        os.path.normpath(str(Path(str(plan["paths"]["dataset_root"])) / Path(longest_relative)))
    )
    longest_index = next(
        (index for index, row in enumerate(rows) if os.path.normcase(os.path.normpath(str(row["audio"]))) == longest_absolute),
        None,
    )
    bounded_value = plan.get("bounded_probe") if mode == "bounded-probe" else None
    curriculum_group = bounded_value.get("curriculum_group") if isinstance(bounded_value, Mapping) else None
    if longest_index is None and not isinstance(curriculum_group, Mapping):
        _fail("LONGEST_SAMPLE_MISSING", "prepared JSONL does not contain the QA-bound longest training sample")
    probe_index = longest_index
    curriculum_sample: Mapping[str, Any] | None = None
    if mode == "bounded-probe":
        bounded = plan.get("bounded_probe")
        curriculum_sample = bounded.get("curriculum_sample") if isinstance(bounded, Mapping) else None
        if not isinstance(curriculum_sample, Mapping) or bounded.get("curriculum_probe_shortest_non_release") is not True:
            _fail("PLAN_INVALID", "bounded one-step probe lacks its shortest-train curriculum binding")
        curriculum_absolute = os.path.normcase(os.path.normpath(str(curriculum_sample.get("absolute_path"))))
        probe_index = next(
            (
                index
                for index, row in enumerate(rows)
                if os.path.normcase(os.path.normpath(str(row["audio"]))) == curriculum_absolute
            ),
            None,
        )
        if probe_index is None or str(rows[probe_index]["ref_audio"]) == str(rows[probe_index]["audio"]):
            _fail("CURRICULUM_SAMPLE_MISSING", "shortest non-reference train sample is absent")
    longest_sample_covered = False
    curriculum_sample_covered = False
    loss_and_grad_finite = True
    peak_reserved_mib = 0
    maximum_temperature_c = baseline_gpu["temperature_c"]
    maximum_total_gpu_used_mib = baseline_gpu["total_gpu_used_mib"]
    minimum_free_ram_bytes = _free_ram_bytes()
    shape_diagnostic_written = False
    for epoch in range(start_epoch, epochs):
        generator = torch.Generator(device="cpu")
        generator.manual_seed(seed + epoch)
        order = torch.randperm(len(dataset), generator=generator).tolist()
        if mode in {"dry-run", "bounded-probe"} and probe_index in order:
            order.remove(probe_index)
            order.insert(0, probe_index)
        subset = Subset(dataset, order)
        loader = DataLoader(
            subset,
            batch_size=1,
            shuffle=False,
            num_workers=0,
            collate_fn=dataset.collate_fn,
        )
        loader = accelerator.prepare_data_loader(loader)
        model.train()
        next_batch = 0
        for batch_index, batch in enumerate(loader):
            if epoch == start_epoch and batch_index < start_batch:
                continue
            if longest_index is not None and order[batch_index] == longest_index:
                longest_sample_covered = True
            if order[batch_index] == probe_index:
                curriculum_sample_covered = True
            with accelerator.accumulate(model):
                input_ids = batch["input_ids"]
                codec_ids = batch["codec_ids"]
                ref_mels = batch["ref_mels"]
                text_embedding_mask = batch["text_embedding_mask"]
                codec_embedding_mask = batch["codec_embedding_mask"]
                attention_mask = batch["attention_mask"]
                codec_0_labels = batch["codec_0_labels"]
                codec_mask = batch["codec_mask"]
                speaker_embedding = model.speaker_encoder(ref_mels.to(model.device).to(model.dtype)).detach()
                if target_speaker_embedding is None:
                    target_speaker_embedding = speaker_embedding.detach().to("cpu")
                input_text_ids = input_ids[:, :, 0]
                input_codec_ids = input_ids[:, :, 1]
                raw_text_embedding = model.talker.get_text_embeddings()(input_text_ids)
                input_text_embedding = model.talker.text_projection(raw_text_embedding) * text_embedding_mask
                input_codec_embedding = model.talker.get_input_embeddings()(input_codec_ids) * codec_embedding_mask
                if input_text_embedding.shape != input_codec_embedding.shape:
                    _fail(
                        "TRAINING_EMBEDDING_SHAPE_MISMATCH",
                        f"projected text embedding {tuple(input_text_embedding.shape)} does not match "
                        f"codec embedding {tuple(input_codec_embedding.shape)}",
                    )
                if speaker_embedding.shape[-1] != input_codec_embedding.shape[-1]:
                    _fail(
                        "SPEAKER_EMBEDDING_SHAPE_MISMATCH",
                        f"speaker embedding width {speaker_embedding.shape[-1]} does not match "
                        f"talker hidden width {input_codec_embedding.shape[-1]}",
                    )
                if not shape_diagnostic_written:
                    _exclusive_write(
                        attempt_root / "SHAPE_DIAGNOSTIC.json",
                        _pretty_json(
                            {
                                "schema": "mentorflow.qwen3-tts-training-shape-diagnostic.v1",
                                "status": "passed",
                                "sample": dict(plan["bounded_probe"]["curriculum_sample"]),
                                "source_commit": plan["source"]["commit"],
                                "model_revision": plan["base_model"]["revision"],
                                "model_config_sha256": _sha256_file(model_root / "config.json"),
                                "projection_required": raw_text_embedding.shape[-1] != input_codec_embedding.shape[-1],
                                "projection_applied": True,
                                "input_ids": _tensor_descriptor(input_ids),
                                "codec_ids": _tensor_descriptor(codec_ids),
                                "ref_mels": _tensor_descriptor(ref_mels),
                                "speaker_embedding": _tensor_descriptor(speaker_embedding),
                                "raw_text_embedding": _tensor_descriptor(raw_text_embedding),
                                "projected_text_embedding": _tensor_descriptor(input_text_embedding),
                                "codec_embedding": _tensor_descriptor(input_codec_embedding),
                                "attention_mask": _tensor_descriptor(attention_mask),
                            }
                        ),
                    )
                    shape_diagnostic_written = True
                input_codec_embedding[:, 6, :] = speaker_embedding
                input_embeddings = input_text_embedding + input_codec_embedding
                for codec_index in range(1, 16):
                    embedding = model.talker.code_predictor.get_input_embeddings()[codec_index - 1](
                        codec_ids[:, :, codec_index]
                    )
                    input_embeddings = input_embeddings + embedding * codec_mask.unsqueeze(-1)
                outputs = model.talker(
                    inputs_embeds=input_embeddings[:, :-1, :],
                    attention_mask=attention_mask[:, :-1],
                    labels=codec_0_labels[:, 1:],
                    output_hidden_states=True,
                )
                hidden_states = outputs.hidden_states[0][-1]
                talker_hidden_states = hidden_states[codec_mask[:, :-1]]
                talker_codec_ids = codec_ids[codec_mask]
                _, sub_talker_loss = model.talker.forward_sub_talker_finetune(talker_codec_ids, talker_hidden_states)
                loss = outputs.loss + 0.3 * sub_talker_loss
                if not bool(torch.isfinite(loss).all().item()):
                    loss_and_grad_finite = False
                    _fail("NONFINITE_LOSS", "training loss became NaN or infinite")
                accelerator.backward(loss)
                if accelerator.sync_gradients:
                    grad_norm = accelerator.clip_grad_norm_(model.parameters(), 1.0)
                    if not bool(torch.isfinite(torch.as_tensor(grad_norm)).all().item()):
                        loss_and_grad_finite = False
                        _fail("NONFINITE_GRADIENT", "training gradient norm became NaN or infinite")
                optimizer.step()
                optimizer.zero_grad()
            next_batch = batch_index + 1
            if accelerator.sync_gradients:
                global_step += 1
                accelerator.print(f"epoch={epoch} batch={batch_index} optimizer_step={global_step} loss={loss.item():.6f}")
                peak_reserved_mib = max(
                    peak_reserved_mib,
                    int((torch.cuda.max_memory_reserved() + 1024**2 - 1) // 1024**2),
                )
                resource = _runtime_gate(plan, peak_reserved_mib=peak_reserved_mib)
                maximum_temperature_c = max(maximum_temperature_c, resource["temperature_c"])
                maximum_total_gpu_used_mib = max(maximum_total_gpu_used_mib, resource["total_gpu_used_mib"])
                minimum_free_ram_bytes = min(minimum_free_ram_bytes, resource["free_ram_bytes"])
                if bool(plan.get("fullstyle_v4")) and global_step % 20 == 0:
                    _exclusive_write(
                        attempt_root / f"EVAL_RESOURCE_STEP_{global_step:08d}.json",
                        _pretty_json({
                            "schema": "mentorflow.single-clip-fullstyle-v4-step-evidence.v1",
                            "optimizer_step": global_step,
                            "loss": float(loss.item()),
                            "resource": resource,
                            "peak_reserved_mib": peak_reserved_mib,
                            "loss_and_grad_finite": loss_and_grad_finite,
                        }),
                    )
                if max_steps is not None and global_step >= max_steps:
                    completed = True
                    break
        if completed:
            break
        start_batch = 0
        if mode == "formal" and not bool(plan.get("fullstyle_v4")):
            last_checkpoint = _save_checkpoint(
                accelerator=accelerator,
                model=model,
                model_root=model_root,
                checkpoint_root=checkpoint_root,
                attempt_root=attempt_root,
                plan=plan,
                global_step=global_step,
                target_speaker_embedding=target_speaker_embedding,
                next_epoch=epoch + 1,
                next_batch=0,
                save_file=save_file,
                peak_reserved_mib=peak_reserved_mib,
            )
    if mode == "dry-run" and (global_step != 20 or not longest_sample_covered):
        _fail("PROBE_INCOMPLETE", "formal-like probe did not complete 20 steps with the longest sample")
    if mode == "bounded-probe" and (global_step != max_steps or not curriculum_sample_covered):
        _fail("PROBE_INCOMPLETE", "bounded probe did not complete its exact steps with the bound curriculum sample")
    if mode == "formal" and bool(plan.get("fullstyle_v4")):
        last_checkpoint = _save_checkpoint(
            accelerator=accelerator,
            model=model,
            model_root=model_root,
            checkpoint_root=checkpoint_root,
            attempt_root=attempt_root,
            plan=plan,
            global_step=global_step,
            target_speaker_embedding=target_speaker_embedding,
            next_epoch=epoch,
            next_batch=next_batch,
            save_file=save_file,
            peak_reserved_mib=peak_reserved_mib,
        )
    probe_checkpoint: Path | None = None
    if mode == "bounded-probe" and bool(plan["bounded_probe"].get("probe_checkpoint_required")):
        probe_checkpoint = _save_checkpoint(
            accelerator=accelerator,
            model=model,
            model_root=model_root,
            checkpoint_root=checkpoint_root,
            attempt_root=attempt_root,
            plan=plan,
            global_step=global_step,
            target_speaker_embedding=target_speaker_embedding,
            next_epoch=epoch,
            next_batch=next_batch,
            save_file=save_file,
            peak_reserved_mib=peak_reserved_mib,
        )
    model, optimizer, loader = accelerator.free_memory(model, optimizer, loader)
    del (
        accelerator,
        attention_mask,
        batch,
        codec_0_labels,
        codec_embedding_mask,
        codec_ids,
        codec_mask,
        dataset,
        embedding,
        grad_norm,
        hidden_states,
        input_codec_embedding,
        input_codec_ids,
        input_embeddings,
        input_ids,
        input_text_embedding,
        input_text_ids,
        loader,
        loss,
        model,
        optimizer,
        outputs,
        qwen,
        raw_text_embedding,
        ref_mels,
        speaker_embedding,
        subset,
        talker_codec_ids,
        talker_hidden_states,
        text_embedding_mask,
    )
    gc.collect()
    gc.collect()
    torch.cuda.empty_cache()
    torch.cuda.synchronize()
    exit_gpu = _gpu_runtime_sample()
    exit_delta = max(0, exit_gpu["total_gpu_used_mib"] - baseline_gpu["total_gpu_used_mib"])
    allowed_exit_delta = int(plan["resource_admission"]["thresholds"]["maximum_exit_gpu_baseline_delta_mib"])
    # A live CUDA worker retains its CUDA context even after every tensor and
    # allocator block is released.  For the bounded probe, only the external
    # owner can measure the authoritative post-process return to baseline.
    # The orchestrator still enforces the same delta after this process exits.
    if mode != "bounded-probe" and exit_delta > allowed_exit_delta:
        _fail(
            "GPU_NOT_RELEASED",
            f"GPU use remained {exit_delta} MiB above baseline; allowed delta is {allowed_exit_delta} MiB",
        )
    if mode in {"dry-run", "bounded-probe"}:
        result = {
            "schema": PROBE_RESULT_SCHEMA if mode == "dry-run" else BOUNDED_PROBE_RESULT_SCHEMA,
            "status": "passed",
            "completed_at_utc": datetime.now(timezone.utc).isoformat(),
            "plan_sha256": plan["plan_sha256"],
            "probe_contract_sha256": plan["probe_contract_sha256"],
            "optimizer_steps_completed": global_step,
            "longest_sample_covered": longest_sample_covered,
            "curriculum_sample_covered": curriculum_sample_covered,
            "curriculum_probe_shortest_non_release": mode == "bounded-probe",
            "curriculum_sample": dict(curriculum_sample) if curriculum_sample is not None else None,
            "longest_train_sample": plan["dataset"]["longest_train_sample"],
            "loss_and_grad_finite": loss_and_grad_finite,
            "peak_reserved_mib": peak_reserved_mib,
            "maximum_total_gpu_used_mib": maximum_total_gpu_used_mib,
            "maximum_temperature_c": maximum_temperature_c,
            "minimum_free_ram_bytes": minimum_free_ram_bytes,
            "baseline_gpu_used_mib": baseline_gpu["total_gpu_used_mib"],
            "exit_gpu_used_mib": exit_gpu["total_gpu_used_mib"],
            "exit_gpu_baseline_delta_mib": exit_delta,
            "worker_pre_exit_gpu_check_authoritative": mode != "bounded-probe",
            "external_post_exit_gpu_check_required": mode == "bounded-probe",
            "formal_checkpoint_written": False,
            "probe_checkpoint_written": probe_checkpoint is not None,
            "probe_checkpoint": str(probe_checkpoint) if probe_checkpoint is not None else None,
            "probe_checkpoint_receipt_sha256": (
                _sha256_file(probe_checkpoint / "CHECKPOINT_RECEIPT.json") if probe_checkpoint is not None else None
            ),
            "network_used": False,
            "offline_environment_verified": True,
            "floating_parameter_dtypes": floating_parameter_dtypes,
            "bounded_allocator": allocator_policy,
            "optimizer": dict(configuration["optimizer"]),
            "warm_start": warm_start_evidence,
        }
        result_path = attempt_root / (
            "PROBE_RESULT.json" if mode == "dry-run" else "BOUNDED_PROBE_RESULT.json"
        )
    else:
        result = {
            "schema": FORMAL_RESULT_SCHEMA,
            "status": "completed",
            "completed_at_utc": datetime.now(timezone.utc).isoformat(),
            "plan_sha256": plan["plan_sha256"],
            "global_optimizer_steps": global_step,
            "last_checkpoint": str(last_checkpoint) if last_checkpoint else None,
            "network_used": False,
            "offline_environment_verified": True,
        }
        result_path = attempt_root / "TRAINER_RESULT.json"
    _exclusive_write(result_path, _pretty_json(result))
    return result


def _save_checkpoint(
    *,
    accelerator: Any,
    model: Any,
    model_root: Path,
    checkpoint_root: Path,
    attempt_root: Path,
    plan: Mapping[str, Any],
    global_step: int,
    target_speaker_embedding: Any,
    next_epoch: int,
    next_batch: int,
    save_file: Any,
    peak_reserved_mib: int,
) -> Path:
    checkpoint_resource = _checkpoint_resource_gate(plan, peak_reserved_mib=peak_reserved_mib)
    if target_speaker_embedding is None:
        _fail("CHECKPOINT_INVALID", "target speaker embedding was not established")
    stage = attempt_root / f"checkpoint-stage-step-{global_step:08d}"
    os.mkdir(stage)
    target = target_speaker_embedding.detach().to("cpu")
    save_file({"embedding": target}, str(stage / "target_speaker.safetensors"))
    output_model = stage / "model"
    # Build the official custom-voice model artifact first.  A bounded probe is
    # intentionally non-resumable, so it must not ask Accelerate to emit a
    # second complete model state dict under accelerator/.
    _copy_model_static(model_root, output_model)
    config_path = output_model / "config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    speaker_name = str(plan["training"]["speaker_name"])
    config["tts_model_type"] = "custom_voice"
    config.setdefault("talker_config", {})["spk_id"] = {speaker_name: 3000}
    config["talker_config"]["spk_is_dialect"] = {speaker_name: False}
    config["mentorflow_artifact_policy"] = dict(plan["artifact_policy"])
    config_path.write_bytes(_pretty_json(config))
    unwrapped = accelerator.unwrap_model(model)
    # state_dict() returns references to the live tensors.  Do not move the
    # complete mapping to CPU: the streaming writer performs bounded GPU to
    # pinned-host transfers and verifies every output byte range.
    state_dict = unwrapped.state_dict()
    writer_path = Path(__file__).resolve().parent / "streaming_safetensors.py"
    writer_sha256 = _sha256_file(_plain_file(writer_path, "streaming safetensors writer"))
    expected_writer = plan["checkpoint_writer"]
    if (
        expected_writer.get("implementation_sha256") != writer_sha256
        or expected_writer.get("chunk_bytes") != 32 * 1024 * 1024
        or expected_writer.get("format") != "safetensors"
    ):
        _fail("CHECKPOINT_WRITER_DRIFT", "streaming checkpoint writer differs from admitted plan")
    from streaming_safetensors import verify_streaming_safetensors, write_streaming_safetensors

    model_tensor_receipt = write_streaming_safetensors(
        state_dict,
        output_model / "model.safetensors",
        target_speaker_embedding=target,
        speaker_slot=3000,
        chunk_bytes=int(expected_writer["chunk_bytes"]),
    )
    model_tensor_verification = verify_streaming_safetensors(
        output_model / "model.safetensors",
        model_tensor_receipt,
        expected_speaker_embedding=target,
    )
    accelerator_state_written = False
    if str(plan["mode"]) != "bounded-probe":
        accelerator_state = stage / "accelerator"
        accelerator.save_state(str(accelerator_state))
        accelerator_state_written = True
    trainer_state = {
        "next_epoch": next_epoch,
        "next_batch": next_batch,
        "seed": int(plan["training"]["seed"]),
        "dataset_tree_sha256": plan["dataset"]["tree_sha256"],
        "source_commit": plan["source"]["commit"],
        "model_revision": plan["base_model"]["revision"],
        "speaker_name": str(plan["training"]["speaker_name"]),
        "artifact_policy": plan["artifact_policy"],
        "curriculum_group": plan.get("bounded_probe", {}).get("curriculum_group"),
        "warm_start": plan.get("warm_start"),
        "optimizer": plan["training"]["optimizer"],
        "checkpoint_writer": {
            **dict(expected_writer),
            "resource_gate": checkpoint_resource,
            "model_tensor_receipt": model_tensor_receipt,
            "model_tensor_verification": model_tensor_verification,
        },
        "accelerator_state_written": accelerator_state_written,
        "resume_eligible": accelerator_state_written,
    }
    _exclusive_write(stage / "TRAINER_STATE.json", _pretty_json(trainer_state))
    return _commit_checkpoint(
        stage=stage,
        checkpoint_root=checkpoint_root,
        plan_sha256=str(plan["plan_sha256"]),
        global_step=global_step,
        state=trainer_state,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--prepared-jsonl", type=Path, required=True)
    parser.add_argument("--checkpoint-root", type=Path, required=True)
    parser.add_argument("--attempt-evidence-root", type=Path, required=True)
    parser.add_argument("--resume", action="store_true")
    return parser


def _record_trainer_failure(path: Path, code: str, exc: BaseException) -> None:
    try:
        root = _plain_dir(path, "attempt evidence root")
        _exclusive_write(
            root / "TRAINER_FAILURE.json",
            _pretty_json(
                {
                    "schema": "mentorflow.qwen3-tts-trainer-failure.v1",
                    "status": "failed",
                    "failed_at_utc": datetime.now(timezone.utc).isoformat(),
                    "code": code,
                    "error_type": type(exc).__name__,
                    "message": str(exc)[:4000],
                    "traceback": "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))[-16000:],
                    "cuda_oom": code == "CUDA_OOM",
                    "nonfinite": code in {"NONFINITE_LOSS", "NONFINITE_GRADIENT"},
                    "formal_checkpoint_written": False,
                }
            ),
        )
    except BaseException:
        pass


def main(argv: Iterable[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        _assert_offline_environment()
        plan = _load_plan(args.plan)
        expected_run_root = Path(str(plan["paths"]["run_root"]))
        if args.plan != expected_run_root / "RUN_PLAN.json":
            _fail("PATH_MISMATCH", "--plan is not the admitted run-plan path")
        if args.checkpoint_root != expected_run_root / "checkpoints":
            _fail("PATH_MISMATCH", "--checkpoint-root is outside the admitted run")
        if args.prepared_jsonl != expected_run_root / "prepared" / "train_with_codes.jsonl":
            _fail("PATH_MISMATCH", "--prepared-jsonl is outside the admitted run")
        expected_attempts_root = expected_run_root / "attempts"
        if (
            args.attempt_evidence_root.parent != expected_attempts_root
            or re.fullmatch(r"attempt-\d{4}", args.attempt_evidence_root.name) is None
        ):
            _fail("PATH_MISMATCH", "--attempt-evidence-root is outside the admitted run")
        _plain_dir(args.attempt_evidence_root, "attempt evidence root")
        result = train(
            plan,
            args.prepared_jsonl,
            args.checkpoint_root,
            args.attempt_evidence_root,
            resume=args.resume,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except SafeTrainerError as exc:
        _record_trainer_failure(args.attempt_evidence_root, exc.code, exc)
        print(
            json.dumps({"status": "blocked", "code": exc.code, "message": str(exc)}, ensure_ascii=False),
            file=sys.stderr,
        )
        return 2
    except BaseException as exc:
        message = str(exc).casefold()
        code = "CUDA_OOM" if "out of memory" in message or type(exc).__name__ == "OutOfMemoryError" else "UNEXPECTED"
        _record_trainer_failure(args.attempt_evidence_root, code, exc)
        print(
            json.dumps(
                {"status": "blocked", "code": code, "error_type": type(exc).__name__, "message": str(exc)[:4000]},
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
