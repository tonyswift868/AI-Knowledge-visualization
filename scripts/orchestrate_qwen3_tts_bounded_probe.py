#!/usr/bin/env python3
"""User-authorized, non-release bounded Qwen3-TTS optimizer probe.

This path never weakens the formal release gate.  It imports an already
verified CPU-prepared generation, runs exactly 1 or 20 optimizer steps under a
CUDA allocator cap, and never writes a formal checkpoint.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
import wave
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

SCRIPT_ROOT = Path(__file__).resolve().parent
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

import orchestrate_qwen3_tts_training as ORCH
import build_qwen3_tts_curriculum_manifests as CURRICULUM

SCHEMA = "mentorflow.qwen3-tts-user-authorized-bounded-probe-plan.v1"
WATCHER_SCHEMA = "mentorflow.qwen3-tts-user-authorized-bounded-probe-watcher.v1"
PERSISTENT_BUDGET_MIB = 7_168
SYSTEM_FREE_VRAM_HEADROOM_MIB = 1_024
RUNTIME_MINIMUM_SYSTEM_FREE_VRAM_MIB = 512
MAXIMUM_ALLOCATOR_CAP_MIB = 8_704
WATCHER_MARGIN_MIB = 256
GROUP_011_TOTAL_USED_JITTER_MARGIN_MIB = 512
START_MAXIMUM_TEMPERATURE_C = 65
RUNTIME_MAXIMUM_TEMPERATURE_C = 80
START_MINIMUM_RAM_BYTES = 8 * 1024**3
GPU_WORKER_LAUNCH_COMMIT_BUDGET_BYTES = 12 * 1024**3
START_SYSTEM_COMMIT_RESERVE_BYTES = 2_684_354_560  # 2.5 GiB
START_MINIMUM_COMMIT_HEADROOM_BYTES = GPU_WORKER_LAUNCH_COMMIT_BUDGET_BYTES + START_SYSTEM_COMMIT_RESERVE_BYTES
RUNTIME_MINIMUM_RAM_BYTES = 2_684_354_560  # 2.5 GiB, two consecutive watcher samples
RUNTIME_MINIMUM_COMMIT_HEADROOM_BYTES = 2 * 1024**3
RUNTIME_LOW_RAM_CONSECUTIVE_SAMPLES = 2
WATCHER_INTERVAL_SECONDS = 1.5
MAXIMUM_RUN_SECONDS = 90 * 60


class BoundedProbeError(RuntimeError):
    def __init__(self, code: str, message: str, **details: Any):
        super().__init__(message)
        self.code = code
        self.details = details


def _fail(code: str, message: str, **details: Any) -> None:
    raise BoundedProbeError(code, message, **details)


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


def _sample_resources() -> dict[str, int]:
    try:
        gpu = ORCH._gpu_probe()
        memory = _system_memory_probe()
        values = {
            "gpu_index": int(gpu["index"]),
            "start_total_vram_mib": int(gpu["memory_total_mib"]),
            "start_used_vram_mib": int(gpu["memory_used_mib"]),
            "start_free_vram_mib": int(gpu["memory_free_mib"]),
            "temperature_c": int(gpu["temperature_c"]),
            "free_ram_bytes": int(memory["physical_available_bytes"]),
            "physical_available_bytes": int(memory["physical_available_bytes"]),
            "physical_total_bytes": int(memory["physical_total_bytes"]),
            "commit_total_bytes": int(memory["commit_total_bytes"]),
            "commit_limit_bytes": int(memory["commit_limit_bytes"]),
            "commit_headroom_bytes": int(memory["commit_headroom_bytes"]),
        }
    except BaseException as exc:
        _fail("RESOURCE_OBSERVATION_FAILED", f"resource observation failed: {type(exc).__name__}")
    if values["gpu_index"] != 0 or any(values[key] < 0 for key in values):
        _fail("RESOURCE_OBSERVATION_FAILED", "resource observation is inconsistent")
    return values


def _system_memory_probe() -> dict[str, int]:
    if os.name != "nt":
        _fail("RESOURCE_OBSERVATION_FAILED", "GPU bounded probe Job policy requires Windows")
    import ctypes
    from ctypes import wintypes

    class PerformanceInformation(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("CommitTotal", ctypes.c_size_t),
            ("CommitLimit", ctypes.c_size_t),
            ("CommitPeak", ctypes.c_size_t),
            ("PhysicalTotal", ctypes.c_size_t),
            ("PhysicalAvailable", ctypes.c_size_t),
            ("SystemCache", ctypes.c_size_t),
            ("KernelTotal", ctypes.c_size_t),
            ("KernelPaged", ctypes.c_size_t),
            ("KernelNonpaged", ctypes.c_size_t),
            ("PageSize", ctypes.c_size_t),
            ("HandleCount", wintypes.DWORD),
            ("ProcessCount", wintypes.DWORD),
            ("ThreadCount", wintypes.DWORD),
        ]
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    psapi.GetPerformanceInfo.argtypes = [ctypes.POINTER(PerformanceInformation), wintypes.DWORD]
    psapi.GetPerformanceInfo.restype = wintypes.BOOL
    value = PerformanceInformation()
    value.cb = ctypes.sizeof(value)
    if not psapi.GetPerformanceInfo(ctypes.byref(value), value.cb):
        _fail("RESOURCE_OBSERVATION_FAILED", "GetPerformanceInfo failed", winerror=ctypes.get_last_error())
    page = int(value.PageSize)
    total = int(value.CommitTotal) * page
    limit = int(value.CommitLimit) * page
    result = {
        "physical_available_bytes": int(value.PhysicalAvailable) * page,
        "physical_total_bytes": int(value.PhysicalTotal) * page,
        "commit_total_bytes": total,
        "commit_limit_bytes": limit,
        "commit_headroom_bytes": limit - total,
    }
    if page <= 0 or result["physical_total_bytes"] <= 0 or not 0 <= total <= limit or result["physical_available_bytes"] < 0:
        _fail("RESOURCE_OBSERVATION_FAILED", "GetPerformanceInfo returned inconsistent accounting")
    return result


def bounded_policy(sample: Mapping[str, Any]) -> dict[str, Any]:
    required = (
        "gpu_index",
        "start_total_vram_mib",
        "start_used_vram_mib",
        "start_free_vram_mib",
        "temperature_c",
        "free_ram_bytes",
        "physical_available_bytes",
        "physical_total_bytes",
        "commit_total_bytes",
        "commit_limit_bytes",
        "commit_headroom_bytes",
    )
    if any(type(sample.get(key)) is not int for key in required):
        _fail("RESOURCE_OBSERVATION_FAILED", "bounded probe sample is incomplete")
    value = {key: int(sample[key]) for key in required}
    allocator_cap = min(MAXIMUM_ALLOCATOR_CAP_MIB, value["start_free_vram_mib"] - SYSTEM_FREE_VRAM_HEADROOM_MIB)
    reasons: list[str] = []
    if value["temperature_c"] > START_MAXIMUM_TEMPERATURE_C:
        reasons.append("GPU temperature exceeds 65 C")
    if value["free_ram_bytes"] < START_MINIMUM_RAM_BYTES:
        reasons.append("free RAM is below 8 GiB")
    if value["commit_headroom_bytes"] < START_MINIMUM_COMMIT_HEADROOM_BYTES:
        reasons.append("commit headroom is below 12 GiB worker budget plus 2.5 GiB reserve")
    if value["start_free_vram_mib"] < PERSISTENT_BUDGET_MIB + SYSTEM_FREE_VRAM_HEADROOM_MIB:
        reasons.append("free VRAM is below persistent budget 7168 MiB plus 1024 MiB headroom")
    if allocator_cap < PERSISTENT_BUDGET_MIB:
        reasons.append("computed allocator cap is below 7168 MiB")
    thresholds = {
        "runtime_maximum_temperature_c": RUNTIME_MAXIMUM_TEMPERATURE_C,
        "runtime_maximum_total_gpu_used_mib": value["start_used_vram_mib"] + allocator_cap + WATCHER_MARGIN_MIB,
        "runtime_minimum_system_free_vram_mib": RUNTIME_MINIMUM_SYSTEM_FREE_VRAM_MIB,
        "runtime_maximum_peak_reserved_mib": allocator_cap,
        "runtime_minimum_free_ram_bytes": RUNTIME_MINIMUM_RAM_BYTES,
        "runtime_low_ram_consecutive_samples": RUNTIME_LOW_RAM_CONSECUTIVE_SAMPLES,
        "runtime_minimum_commit_headroom_bytes": RUNTIME_MINIMUM_COMMIT_HEADROOM_BYTES,
        # The streaming writer bounds host staging to 32 MiB.  Eight GiB is
        # the admitted checkpoint entry gate; the existing runtime floor
        # remains four GiB and is enforced by both worker and watcher.
        "formal_checkpoint_minimum_free_ram_bytes": 8 * 1024**3,
        "maximum_exit_gpu_baseline_delta_mib": WATCHER_MARGIN_MIB,
    }
    return {
        "schema": "mentorflow.qwen3-tts-user-authorized-bounded-resource.v1",
        "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
        "decision": "GO" if not reasons else "NO_GO",
        **value,
        "persistent_budget_mib": PERSISTENT_BUDGET_MIB,
        "system_free_vram_headroom_mib": SYSTEM_FREE_VRAM_HEADROOM_MIB,
        "allocator_cap_mib": allocator_cap,
        "watcher_margin_mib": WATCHER_MARGIN_MIB,
        "runtime_total_used_jitter_margin_mib": WATCHER_MARGIN_MIB,
        "gpu_worker_launch_commit_budget_bytes": GPU_WORKER_LAUNCH_COMMIT_BUDGET_BYTES,
        "start_system_commit_reserve_bytes": START_SYSTEM_COMMIT_RESERVE_BYTES,
        "start_minimum_commit_headroom_bytes": START_MINIMUM_COMMIT_HEADROOM_BYTES,
        "thresholds": thresholds,
        "formal_release_gate_unchanged": True,
        "formal_checkpoint_written": False,
        "user_authorized_nonrelease_probe": True,
        "reasons": reasons,
    }


def bind_curriculum_runtime_margin(policy: Mapping[str, Any], prepared: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(policy)
    result["thresholds"] = dict(policy["thresholds"])
    group_id = (prepared.get("curriculum_group") or {}).get("group_id")
    margin = GROUP_011_TOTAL_USED_JITTER_MARGIN_MIB if group_id == "group-011" else WATCHER_MARGIN_MIB
    result["runtime_total_used_jitter_margin_mib"] = margin
    result["thresholds"]["runtime_maximum_total_gpu_used_mib"] = (
        int(result["start_used_vram_mib"]) + int(result["allocator_cap_mib"]) + margin
    )
    result.pop("policy_sha256", None)
    result["policy_sha256"] = hashlib.sha256(_canonical(result)).hexdigest()
    return result


def watcher_violation(policy: Mapping[str, Any], sample: Mapping[str, Any]) -> str | None:
    try:
        used = int(sample["start_used_vram_mib"])
        free = int(sample["start_free_vram_mib"])
        temperature = int(sample["temperature_c"])
        maximum_used = int(policy["thresholds"]["runtime_maximum_total_gpu_used_mib"])
    except (KeyError, TypeError, ValueError):
        return "resource observation missing or invalid"
    if used > maximum_used:
        return "total GPU used exceeds start used plus allocator cap and watcher margin"
    if free < RUNTIME_MINIMUM_SYSTEM_FREE_VRAM_MIB:
        return "system free VRAM is below 512 MiB"
    if temperature > RUNTIME_MAXIMUM_TEMPERATURE_C:
        return "GPU temperature exceeds 80 C"
    return None


def memory_watcher_violation(sample: Mapping[str, Any], consecutive_low_ram: int) -> tuple[int, str | None]:
    try:
        physical = int(sample["physical_available_bytes"])
        commit_headroom = int(sample["commit_headroom_bytes"])
    except (KeyError, TypeError, ValueError):
        return consecutive_low_ram, "memory accounting observation missing or invalid"
    if commit_headroom < RUNTIME_MINIMUM_COMMIT_HEADROOM_BYTES:
        return consecutive_low_ram, "runtime commit headroom is below 2 GiB"
    consecutive = consecutive_low_ram + 1 if physical < RUNTIME_MINIMUM_RAM_BYTES else 0
    if consecutive >= RUNTIME_LOW_RAM_CONSECUTIVE_SAMPLES:
        return consecutive, "physical available RAM stayed below 2.5 GiB for two consecutive samples"
    return consecutive, None


def _terminate_owned(process: subprocess.Popen[Any]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)


class _WindowsGate:
    def __init__(self) -> None:
        if os.name != "nt":
            _fail("GPU_JOB_UNSUPPORTED", "bounded GPU Job requires Windows")
        import ctypes
        from ctypes import wintypes

        self._ctypes = ctypes
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._kernel32.CreateEventW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
        self._kernel32.CreateEventW.restype = wintypes.HANDLE
        self._kernel32.SetEvent.argtypes = [wintypes.HANDLE]
        self._kernel32.SetEvent.restype = wintypes.BOOL
        self._kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        self._kernel32.CloseHandle.restype = wintypes.BOOL
        self.name = f"Local\\MentorFlowGpuTrain-{uuid.uuid4().hex}"
        self._handle = self._kernel32.CreateEventW(None, True, False, self.name)
        if not self._handle:
            _fail("GPU_JOB_GATE_FAILED", "CreateEventW failed for GPU worker gate")

    def release(self) -> None:
        if not self._kernel32.SetEvent(self._handle):
            _fail("GPU_JOB_GATE_FAILED", "SetEvent failed for GPU worker gate", winerror=self._ctypes.get_last_error())

    def close(self) -> None:
        if self._handle:
            self._kernel32.CloseHandle(self._handle)
            self._handle = None


class _WindowsGpuJob:
    """Owned process tree with kernel-enforced kill-on-close semantics."""

    def __init__(self) -> None:
        if os.name != "nt":
            _fail("GPU_JOB_UNSUPPORTED", "bounded GPU Job requires Windows")
        import ctypes
        from ctypes import wintypes

        class BasicLimitInformation(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class IoCounters(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_ulonglong),
                ("WriteOperationCount", ctypes.c_ulonglong),
                ("OtherOperationCount", ctypes.c_ulonglong),
                ("ReadTransferCount", ctypes.c_ulonglong),
                ("WriteTransferCount", ctypes.c_ulonglong),
                ("OtherTransferCount", ctypes.c_ulonglong),
            ]

        class ExtendedLimitInformation(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", BasicLimitInformation),
                ("IoInfo", IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        self._ctypes = ctypes
        self._wintypes = wintypes
        self._extended_type = ExtendedLimitInformation
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
        self._kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        self._kernel32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
        self._kernel32.SetInformationJobObject.restype = wintypes.BOOL
        self._kernel32.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
        self._kernel32.QueryInformationJobObject.restype = wintypes.BOOL
        self._kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self._kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        self._kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        self._kernel32.TerminateJobObject.restype = wintypes.BOOL
        self._kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        self._kernel32.CloseHandle.restype = wintypes.BOOL
        self._handle = self._kernel32.CreateJobObjectW(None, None)
        if not self._handle:
            _fail("GPU_JOB_CREATE_FAILED", "CreateJobObjectW failed", winerror=ctypes.get_last_error())
        information = ExtendedLimitInformation()
        information.BasicLimitInformation.LimitFlags = 0x00002000  # KILL_ON_CLOSE only
        if not self._kernel32.SetInformationJobObject(self._handle, 9, ctypes.byref(information), ctypes.sizeof(information)):
            error = ctypes.get_last_error()
            self.close()
            _fail("GPU_JOB_LIMIT_FAILED", "SetInformationJobObject failed", winerror=error)

    def assign(self, process: subprocess.Popen[Any]) -> None:
        if not self._kernel32.AssignProcessToJobObject(self._handle, self._wintypes.HANDLE(int(process._handle))):
            _fail("GPU_JOB_ASSIGN_FAILED", "AssignProcessToJobObject failed", winerror=self._ctypes.get_last_error())

    def query(self) -> dict[str, int]:
        value = self._extended_type()
        returned = self._wintypes.DWORD()
        if not self._kernel32.QueryInformationJobObject(self._handle, 9, self._ctypes.byref(value), self._ctypes.sizeof(value), self._ctypes.byref(returned)):
            _fail("GPU_JOB_QUERY_FAILED", "QueryInformationJobObject failed")
        flags = int(value.BasicLimitInformation.LimitFlags)
        if int(value.JobMemoryLimit) != 0 or flags & 0x00000200 or not flags & 0x00002000:
            _fail("GPU_JOB_QUERY_FAILED", "GPU Job limits differ from policy")
        return {
            "job_memory_limit_bytes": int(value.JobMemoryLimit),
            "job_memory_limit_enabled": False,
            "peak_job_memory_used_bytes": int(value.PeakJobMemoryUsed),
            "limit_flags": flags,
        }

    def terminate(self, exit_code: int = 1) -> None:
        if self._handle and not self._kernel32.TerminateJobObject(self._handle, exit_code):
            _fail("GPU_JOB_TERMINATE_FAILED", "TerminateJobObject failed")

    def close(self) -> None:
        if self._handle:
            self._kernel32.CloseHandle(self._handle)
            self._handle = None


_GATED_BOOTSTRAP = """
import ctypes, subprocess, sys
from ctypes import wintypes
k = ctypes.WinDLL('kernel32', use_last_error=True)
k.OpenEventW.argtypes = [wintypes.DWORD, wintypes.BOOL,wintypes.LPCWSTR]
k.OpenEventW.restype = wintypes.HANDLE
k.WaitForSingleObject.argtypes = [wintypes.HANDLE,wintypes.DWORD]
k.WaitForSingleObject.restype = wintypes.DWORD
k.CloseHandle.argtypes = [wintypes.HANDLE]
h = k.OpenEventW(0x00100000, False, sys.argv[1])
if not h: raise SystemExit(120)
r = k.WaitForSingleObject(h, 30000)
k.CloseHandle(h)
if r != 0: raise SystemExit(121)
raise SystemExit(subprocess.run(sys.argv[2:], shell=False, check=False).returncode)
"""


def _validate_prepared(generation: Path) -> dict[str, Any]:
    final = ORCH._plain_dir(generation / "final", "CPU prepared final")
    prepared = ORCH._plain_file(final / "train_with_codes.jsonl", "CPU prepared JSONL")
    receipt_path = ORCH._plain_file(final / "PREPARED_RECEIPT.json", "CPU prepared receipt")
    receipt = ORCH._read_json(receipt_path, "CPU prepared receipt")
    source_plan_path = ORCH._plain_file(generation / "PREPARE_PLAN.json", "CPU prepare plan")
    source_plan = ORCH._read_json(source_plan_path, "CPU prepare plan")
    prepared_value = receipt.get("prepared")
    if (
        not isinstance(prepared_value, Mapping)
        or prepared_value.get("rows") != 324
        or prepared_value.get("bytes") != prepared.stat().st_size
        or prepared_value.get("sha256") != _sha(prepared)
        or receipt.get("gpu_used") is not False
        or receipt.get("network_used") is not False
        or receipt.get("training_started") is not False
    ):
        _fail("PREPARED_INVALID", "CPU prepared v3 receipt/file binding is invalid")
    pins = source_plan.get("input", {}).get("train_audio")
    reference = source_plan.get("input", {}).get("reference")
    if not isinstance(pins, list) or len(pins) != 324 or not isinstance(reference, Mapping):
        _fail("PREPARED_INVALID", "CPU prepared train/reference pins are invalid")
    shortest: dict[str, Any] | None = None
    with prepared.open("rb") as handle:
        for index, raw in enumerate(handle, start=1):
            row = json.loads(raw)
            pin = pins[index - 1]
            codes = row.get("audio_codes")
            if not isinstance(pin, Mapping) or not isinstance(codes, list) or not codes:
                _fail("PREPARED_INVALID", "CPU prepared curriculum row is invalid")
            audio = ORCH._plain_file(Path(str(pin["absolute_path"])), "curriculum train WAV")
            with wave.open(str(audio), "rb") as wav:
                frames = wav.getnframes()
                if wav.getframerate() != 24_000 or wav.getnchannels() != 1 or wav.getsampwidth() != 2:
                    _fail("PREPARED_INVALID", "curriculum train WAV contract is invalid")
            candidate = {
                "train_index": index,
                "absolute_path": str(audio),
                "audio_sha256": str(pin["sha256"]),
                "text_sha256": str(pin["text_sha256"]),
                "audio_bytes": int(pin["bytes"]),
                "audio_duration_ms": int(pin["duration_ms"]),
                "audio_frames": frames,
                "code_frames": len(codes),
                "prepared_row_sha256": hashlib.sha256(raw).hexdigest(),
            }
            if shortest is None or (
                candidate["audio_frames"], candidate["code_frames"], candidate["audio_sha256"]
            ) < (
                shortest["audio_frames"], shortest["code_frames"], shortest["audio_sha256"]
            ):
                shortest = candidate
    if shortest is None or shortest["audio_sha256"] == reference.get("sha256"):
        _fail("PREPARED_INVALID", "shortest curriculum sample is missing or is the reference")
    return {
        "generation": str(generation),
        "prepared_path": str(prepared),
        "receipt_path": str(receipt_path),
        "receipt_sha256": _sha(receipt_path),
        "bytes": prepared.stat().st_size,
        "sha256": _sha(prepared),
        "rows": 324,
        "source_plan_sha256": _sha(source_plan_path),
        "curriculum_shortest_train_sample": shortest,
    }


def _validate_curriculum_group(curriculum_root: Path, group_id: str) -> dict[str, Any]:
    verified = CURRICULUM.verify(curriculum_root)
    match = re.fullmatch(r"group-(\d{3})", group_id)
    group_number = int(match.group(1)) if match else 0
    if verified.get("status") != "PASS" or not 1 <= group_number <= 11:
        _fail("CURRICULUM_INVALID", "curriculum group must be independently verified group-001..group-011")
    root_manifest = ORCH._read_json(curriculum_root / "CURRICULUM_MANIFEST.json", "curriculum manifest")
    group_root = curriculum_root / group_id
    group_manifest_path = ORCH._plain_file(group_root / "GROUP_MANIFEST.json", "curriculum group manifest")
    group_receipt_path = ORCH._plain_file(group_root / "GROUP_RECEIPT.json", "curriculum group receipt")
    prepared = ORCH._plain_file(group_root / "train_with_codes.jsonl", "curriculum group JSONL")
    group_manifest = ORCH._read_json(group_manifest_path, "curriculum group manifest")
    samples = group_manifest.get("samples")
    expected_first = (group_number - 1) * 32 + 1
    expected_last = min(group_number * 32, 324)
    expected_rows = expected_last - expected_first + 1
    if (
        not isinstance(samples, list)
        or len(samples) != expected_rows
        or group_manifest.get("rank_first") != expected_first
        or group_manifest.get("rank_last") != expected_last
    ):
        _fail("CURRICULUM_INVALID", f"{group_id} must contain ranks {expected_first}..{expected_last}")
    sample = dict(samples[0])
    rows = [json.loads(line) for line in prepared.read_text("utf-8").splitlines() if line]
    first = rows[0]
    sample.update(
        {
            "absolute_path": str(first["audio"]),
            "audio_bytes": Path(str(first["audio"])).stat().st_size,
            "audio_duration_ms": int(sample["duration_ms"]),
            "audio_frames": int(sample["duration_ms"]) * 24,
            "code_frames": len(first["audio_codes"]),
            "train_index": int(sample["prepared_index"]),
        }
    )
    return {
        "generation": str(curriculum_root),
        "prepared_path": str(prepared),
        "receipt_path": str(group_receipt_path),
        "receipt_sha256": _sha(group_receipt_path),
        "bytes": prepared.stat().st_size,
        "sha256": _sha(prepared),
        "rows": expected_rows,
        "source_plan_sha256": root_manifest["prepared_source_sha256"],
        "curriculum_shortest_train_sample": sample,
        "curriculum_group": {
            "group_id": group_id,
            "root_manifest_sha256": _sha(curriculum_root / "CURRICULUM_MANIFEST.json"),
            "group_manifest_sha256": _sha(group_manifest_path),
            "group_receipt_sha256": _sha(group_receipt_path),
            "rows": expected_rows,
            "rank_first": expected_first,
            "rank_last": expected_last,
            "checkpoint_required": True,
        },
    }


def _promote_model_only_warm_start(
    parent_checkpoint: Path, prepared: Mapping[str, Any], *, promotion_generation: str
) -> dict[str, Any]:
    group = prepared.get("curriculum_group")
    if not isinstance(group, Mapping):
        _fail("WARM_START_INVALID", "curriculum warm start target is missing")
    target_match = re.fullmatch(r"group-(\d{3})", str(group.get("group_id")))
    target_number = int(target_match.group(1)) if target_match else 0
    if not 2 <= target_number <= 11:
        _fail("WARM_START_INVALID", "a model-only warm start is admitted only for group-002..group-011")
    expected_parent_group = f"group-{target_number - 1:03d}"
    parent_checkpoint = ORCH._plain_dir(parent_checkpoint, "parent curriculum checkpoint")
    if parent_checkpoint.name != "checkpoint-step-00000020":
        _fail("WARM_START_INVALID", "parent checkpoint must be committed optimizer step 20")
    parent_run = parent_checkpoint.parent.parent
    parent_plan_path = ORCH._plain_file(parent_run / "RUN_PLAN.json", "parent run plan")
    parent_plan = ORCH._read_json(parent_plan_path, "parent run plan")
    verifier_code = (
        "import json,sys;from pathlib import Path;"
        f"sys.path.insert(0,{str(SCRIPT_ROOT)!r});"
        "import qwen3_tts_sft_safe as q;"
        f"p=Path({str(parent_checkpoint)!r});"
        f"v=q._verify_checkpoint(p,{str(parent_plan.get('plan_sha256'))!r});"
        "print(json.dumps({'step':v['global_optimizer_step'],'tree':v['tree_sha256'],'plan':v['plan_sha256']}))"
    )
    verified = subprocess.run(
        [str(parent_plan["paths"]["python_executable"]), "-I", "-c", verifier_code],
        cwd=ORCH.WORKSPACE_ROOT,
        env={**os.environ, **{str(k): str(v) for k, v in parent_plan["offline_environment"].items()}},
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
        shell=False,
        timeout=180,
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000),
    )
    try:
        clean_result = json.loads(verified.stdout.strip())
    except json.JSONDecodeError:
        clean_result = None
    if (
        verified.returncode != 0
        or not isinstance(clean_result, Mapping)
        or clean_result.get("step") != 20
        or clean_result.get("plan") != parent_plan.get("plan_sha256")
    ):
        _fail("WARM_START_INVALID", "clean parent checkpoint verifier failed")
    parent_receipt = ORCH._read_json(parent_checkpoint / "CHECKPOINT_RECEIPT.json", "parent checkpoint receipt")
    if parent_receipt.get("tree_sha256") != clean_result.get("tree"):
        _fail("WARM_START_INVALID", "clean parent checkpoint tree differs from receipt")
    successes = sorted(parent_run.glob("attempts/attempt-*/SUCCESS.json"))
    if len(successes) != 1:
        _fail("WARM_START_INVALID", "parent run must have exactly one committed SUCCESS receipt")
    parent_success = ORCH._read_json(successes[0], "parent success receipt")
    parent_group = parent_success.get("curriculum_group")
    state = parent_receipt.get("trainer_state")
    writer = state.get("checkpoint_writer") if isinstance(state, Mapping) else None
    model_receipt = writer.get("model_tensor_receipt") if isinstance(writer, Mapping) else None
    model_path = ORCH._plain_file(parent_checkpoint / "model" / "model.safetensors", "parent model weights")
    if (
        parent_success.get("status") != "passed"
        or parent_success.get("optimizer_steps_completed") != 20
        or not isinstance(parent_group, Mapping)
        or parent_group.get("group_id") != expected_parent_group
        or parent_success.get("probe_checkpoint") != str(parent_checkpoint)
        or not isinstance(model_receipt, Mapping)
        or model_receipt.get("sha256") != _sha(model_path)
        or model_receipt.get("bytes") != model_path.stat().st_size
        or state.get("resume_eligible") is not False
        or state.get("accelerator_state_written") is not False
    ):
        _fail("WARM_START_INVALID", "parent v7 checkpoint does not satisfy model-only promotion policy")
    optimizer_contract = parent_plan["training"]["optimizer"]
    optimizer_path = SCRIPT_ROOT / "chunked_adamw.py"
    if optimizer_contract.get("implementation_sha256") != _sha(optimizer_path):
        _fail("WARM_START_INVALID", "ChunkedAdamW implementation drifted after v7")
    trainer_path = SCRIPT_ROOT / "qwen3_tts_sft_safe.py"
    promotion = {
        "schema": "mentorflow.qwen3-tts-curriculum-model-warm-start-promotion.v1",
        "status": "approved",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "promotion_generation": promotion_generation,
        "continuation_kind": "model_weights_only_warm_start",
        "exact_resume": False,
        "optimizer_state_reset": True,
        "source": {
            "run_root": str(parent_run),
            "plan_sha256": parent_plan["plan_sha256"],
            "success_receipt": str(successes[0]),
            "success_receipt_sha256": _sha(successes[0]),
            "checkpoint": str(parent_checkpoint),
            "checkpoint_receipt_sha256": _sha(parent_checkpoint / "CHECKPOINT_RECEIPT.json"),
            "checkpoint_tree_sha256": parent_receipt["tree_sha256"],
            "global_optimizer_step": 20,
            "curriculum_group": dict(parent_group),
            "model_path": str(model_path),
            "model_bytes": model_path.stat().st_size,
            "model_sha256": model_receipt["sha256"],
            "model_tensor_count": model_receipt["tensor_count"],
        },
        "target": {
            "curriculum_group": dict(group),
            "prepared_sha256": prepared["sha256"],
        },
        "contracts": {
            "dataset_tree_sha256": parent_plan["dataset"]["tree_sha256"],
            "source_commit": parent_plan["source"]["commit"],
            "model_revision": parent_plan["base_model"]["revision"],
            "trainer_implementation_sha256": _sha(trainer_path),
            "optimizer": dict(optimizer_contract),
        },
        "forbidden_claims": ["exact_optimizer_resume", "optimizer_state_preserved", "formal_release"],
    }
    promotions = parent_run / "promotions"
    if not promotions.exists():
        os.mkdir(promotions)
    generation_suffix = hashlib.sha256(promotion_generation.encode("utf-8")).hexdigest()[:12]
    promotion_path = promotions / (
        f"{expected_parent_group}-to-{group['group_id']}-model-warm-start-{generation_suffix}.json"
    )
    if promotion_path.exists():
        _fail("WARM_START_EXISTS", "refusing to overwrite an existing curriculum promotion receipt")
    ORCH.immutable_write(promotion_path, _pretty(promotion))
    return {
        "schema": promotion["schema"],
        "continuation_kind": promotion["continuation_kind"],
        "exact_resume": False,
        "optimizer_state_reset": True,
        "promotion_receipt": str(promotion_path),
        "promotion_receipt_sha256": _sha(promotion_path),
        "parent_checkpoint": str(parent_checkpoint),
        "parent_checkpoint_receipt_sha256": promotion["source"]["checkpoint_receipt_sha256"],
        "parent_checkpoint_tree_sha256": promotion["source"]["checkpoint_tree_sha256"],
        "parent_global_optimizer_step": 20,
        "model_path": str(model_path),
        "model_bytes": model_path.stat().st_size,
        "model_sha256": model_receipt["sha256"],
        "model_tensor_count": model_receipt["tensor_count"],
    }


def _build_plan(
    *, dataset_id: str, run_id: str, prepared: Mapping[str, Any], steps: int, warm_start: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    sample = _sample_resources()
    policy = bounded_policy(sample)
    if policy["decision"] != "GO":
        _fail("RESOURCE_GATE_NO_GO", "bounded probe admission is NO_GO", reasons=policy["reasons"])
    registry = ORCH.TRAINING_ROOT / "private-trust" / dataset_id / "qa-trust-registry.json"
    base = ORCH.build_run_plan(
        dataset_generation_id=dataset_id,
        run_id=run_id,
        qa_trust_registry=registry,
        source_root=ORCH.DEFAULT_SOURCE_ROOT,
        source_receipt=ORCH.DEFAULT_SOURCE_RECEIPT,
        model_root=ORCH.DEFAULT_MODEL_ROOT,
        model_receipt=ORCH.DEFAULT_MODEL_RECEIPT,
        python_executable=ORCH.DEFAULT_PYTHON,
        wheelhouse_evidence=ORCH.DEFAULT_WHEELHOUSE_EVIDENCE,
        mode="dry-run",
        seed=20260808,
        batch_size=1,
        gradient_accumulation_steps=8,
        learning_rate=2e-6,
        epochs=1,
        max_optimizer_steps=20,
        speaker_name="tsubaki_teacher",
        layout=ORCH.Layout.production(),
        resource_value=policy,
    )
    fresh_policy = bind_curriculum_runtime_margin(bounded_policy(_sample_resources()), prepared)
    if fresh_policy["decision"] != "GO":
        _fail("RESOURCE_GATE_NO_GO", "post-validation bounded probe admission is NO_GO", reasons=fresh_policy["reasons"])
    plan = dict(base)
    plan["schema"] = ORCH.RUN_PLAN_SCHEMA
    plan["mode"] = "bounded-probe"
    plan["training"] = dict(plan["training"])
    plan["training"]["max_optimizer_steps"] = steps
    if prepared.get("curriculum_group") and steps == 20:
        rows = int(prepared["rows"])
        accumulation = int(plan["training"]["gradient_accumulation_steps"])
        plan["training"]["epochs"] = (steps * accumulation + rows - 1) // rows
    else:
        plan["training"]["epochs"] = 1
    plan["resource_admission"] = fresh_policy
    plan["bounded_probe_policy"] = fresh_policy
    plan["bounded_probe_policy_sha256"] = fresh_policy["policy_sha256"]
    checkpoint_writer_path = Path(__file__).resolve().parent / "streaming_safetensors.py"
    plan["checkpoint_writer"] = {
        "schema": "mentorflow.streaming-safetensors-policy.v1",
        "format": "safetensors",
        "implementation": str(checkpoint_writer_path),
        "implementation_sha256": _sha(checkpoint_writer_path),
        "chunk_bytes": 32 * 1024 * 1024,
        "state_dict_order": "unicode-codepoint-ascending",
        "drop_prefixes": ["speaker_encoder"],
        "speaker_codec_slot": 3000,
        "create_new_partial": True,
        "same_volume_atomic_commit": True,
        "full_tensor_payload_hash_verification": True,
    }
    plan["prepared_source"] = dict(prepared)
    plan["warm_start"] = dict(warm_start) if warm_start is not None else None
    plan["bounded_probe"] = {
        "schema": SCHEMA,
        "optimizer_steps": steps,
        "curriculum_probe_shortest_non_release": True,
        "curriculum_sample": dict(prepared["curriculum_shortest_train_sample"]),
        "longest_prepared_sample_first": False,
        "formal_checkpoint_written": False,
        "formal_release_receipt_eligible": False,
        "curriculum_group": prepared.get("curriculum_group"),
        "probe_checkpoint_required": bool(prepared.get("curriculum_group") and steps == 20),
        "exact_training_contract_sha256": plan["probe_contract_sha256"],
    }
    plan.pop("plan_sha256", None)
    plan["plan_sha256"] = hashlib.sha256(_canonical(plan)).hexdigest()
    return plan


def _create_probe_run(plan: Mapping[str, Any], prepared: Mapping[str, Any]) -> Path:
    run_root = Path(str(plan["paths"]["run_root"]))
    if run_root.exists():
        _fail("RUN_EXISTS", "bounded probe run generation already exists", run_root=str(run_root))
    dataset_root = Path(str(plan["paths"]["dataset_root"]))
    manifest = ORCH._read_json(dataset_root / ORCH.STAGING_MANIFEST_NAME, "training staging manifest")
    ORCH._create_run(plan, manifest, layout=ORCH.Layout.production())
    source = ORCH._plain_file(Path(str(prepared["prepared_path"])), "CPU prepared source")
    destination = run_root / "prepared" / "train_with_codes.jsonl"
    ORCH.immutable_write(destination, source.read_bytes())
    if _sha(destination) != prepared["sha256"]:
        _fail("PREPARED_DRIFT", "copied CPU prepared JSONL hash changed")
    ORCH.immutable_write(
        run_root / "prepared" / "PREPARED_RECEIPT.json",
        _pretty(
            {
                "schema": "mentorflow.qwen3-tts-imported-cpu-prepared-jsonl.v1",
                "path": "train_with_codes.jsonl",
                "bytes": destination.stat().st_size,
                "sha256": _sha(destination),
                "plan_sha256": plan["plan_sha256"],
                "source_generation": prepared["generation"],
                "source_receipt_sha256": prepared["receipt_sha256"],
            }
        ),
    )
    return run_root


def _run_worker(plan: Mapping[str, Any], run_root: Path) -> dict[str, Any]:
    ORCH._revalidate_plan_inputs(plan)
    policy_for_hash = dict(plan["bounded_probe_policy"])
    declared_policy_sha = policy_for_hash.pop("policy_sha256", None)
    if (
        declared_policy_sha != hashlib.sha256(_canonical(policy_for_hash)).hexdigest()
        or plan.get("bounded_probe_policy_sha256") != declared_policy_sha
    ):
        _fail("POLICY_DRIFT", "bounded runtime policy hash is invalid")
    dataset_root = Path(str(plan["paths"]["dataset_root"]))
    before_files, before_tree = ORCH._inventory(dataset_root, excluded=frozenset({ORCH.QA_APPROVAL_NAME.casefold()}))
    attempt_index, attempt = ORCH._next_attempt(run_root)
    env = os.environ.copy()
    env.update({str(key): str(value) for key, value in plan["offline_environment"].items()})
    env["CUDA_VISIBLE_DEVICES"] = "0"
    launch = bounded_policy(_sample_resources())
    plan_cap = int(plan["bounded_probe_policy"]["allocator_cap_mib"])
    effective_cap = min(plan_cap, int(launch["allocator_cap_mib"]))
    total_used_jitter_margin = int(plan["bounded_probe_policy"]["runtime_total_used_jitter_margin_mib"])
    if launch["decision"] != "GO" or not PERSISTENT_BUDGET_MIB <= effective_cap <= plan_cap:
        _fail("RESOURCE_GATE_NO_GO", "launch resource sample cannot honor bounded allocator floor", launch=launch)
    env["MENTORFLOW_BOUNDED_ALLOCATOR_CAP_MIB"] = str(effective_cap)
    env["MENTORFLOW_BOUNDED_START_FREE_VRAM_MIB"] = str(launch["start_free_vram_mib"])
    env["MENTORFLOW_BOUNDED_START_TOTAL_VRAM_MIB"] = str(launch["start_total_vram_mib"])
    launch_policy = {
        "schema": "mentorflow.qwen3-tts-bounded-launch-policy.v1",
        "plan_allocator_cap_upper_bound_mib": plan_cap,
        "bounded_probe_policy_sha256": declared_policy_sha,
        "runtime_total_used_jitter_margin_mib": total_used_jitter_margin,
        "effective_allocator_cap_mib": effective_cap,
        "launch_resource": launch,
        "gpu_job": {
            "schema": "mentorflow.qwen3-tts-gpu-job-policy.v1",
            "whole_job_commit_cap_bytes": None,
            "job_memory_limit_enabled": False,
            "worker_launch_commit_budget_bytes": GPU_WORKER_LAUNCH_COMMIT_BUDGET_BYTES,
            "kill_on_job_close": True,
            "start_minimum_physical_available_bytes": START_MINIMUM_RAM_BYTES,
            "start_minimum_commit_headroom_bytes": START_MINIMUM_COMMIT_HEADROOM_BYTES,
            "runtime_minimum_commit_headroom_bytes": RUNTIME_MINIMUM_COMMIT_HEADROOM_BYTES,
            "runtime_minimum_physical_available_bytes": RUNTIME_MINIMUM_RAM_BYTES,
            "runtime_low_physical_consecutive_samples": RUNTIME_LOW_RAM_CONSECUTIVE_SAMPLES,
        },
        "formal_release_gate_unchanged": True,
    }
    ORCH.immutable_write(
        attempt / "ATTEMPT_START.json",
        _pretty(
            {
                "schema": ORCH.ATTEMPT_SCHEMA,
                "attempt": attempt_index,
                "started_at_utc": datetime.now(timezone.utc).isoformat(),
                "plan_sha256": plan["plan_sha256"],
                "launch_resource": launch,
                "launch_policy": launch_policy,
                "training_started": False,
            }
        ),
    )
    ORCH.immutable_write(attempt / "LAUNCH_POLICY.json", _pretty(launch_policy))
    command = [
        str(plan["paths"]["python_executable"]),
        str(plan["paths"]["trainer"]),
        "--plan",
        str(run_root / "RUN_PLAN.json"),
        "--prepared-jsonl",
        str(run_root / "prepared" / "train_with_codes.jsonl"),
        "--checkpoint-root",
        str(run_root / "checkpoints"),
        "--attempt-evidence-root",
        str(attempt),
    ]
    stdout_path, stderr_path = attempt / "train.stdout.log", attempt / "train.stderr.log"
    process: subprocess.Popen[Any] | None = None
    gate: _WindowsGate | None = None
    job: _WindowsGpuJob | None = None
    job_receipt: dict[str, int] | None = None
    samples: list[dict[str, Any]] = []
    violation: str | None = None
    maximum_used = int(launch["start_used_vram_mib"])
    maximum_temperature = int(launch["temperature_c"])
    minimum_free_gpu = int(launch["start_free_vram_mib"])
    minimum_free_ram = int(launch["free_ram_bytes"])
    minimum_commit_headroom = int(launch["commit_headroom_bytes"])
    consecutive_low_ram = 0
    started = time.monotonic()
    try:
        with stdout_path.open("xb") as stdout, stderr_path.open("xb") as stderr:
            gate = _WindowsGate()
            job = _WindowsGpuJob()
            gated_command = [command[0], "-I", "-c", _GATED_BOOTSTRAP, gate.name, *command]
            process = subprocess.Popen(
                gated_command,
                cwd=ORCH.WORKSPACE_ROOT,
                env=env,
                stdout=stdout,
                stderr=stderr,
                shell=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000),
            )
            try:
                job.assign(process)
                job_receipt = job.query()
                gate.release()
            except BaseException:
                _terminate_owned(process)
                raise
            while process.poll() is None:
                try:
                    sample = _sample_resources()
                    reason = watcher_violation(
                        {
                            **plan["bounded_probe_policy"],
                            "thresholds": {
                                **plan["bounded_probe_policy"]["thresholds"],
                                "runtime_maximum_total_gpu_used_mib": int(launch["start_used_vram_mib"])
                                + effective_cap
                                + total_used_jitter_margin,
                            },
                        },
                        sample,
                    )
                    consecutive_low_ram, memory_reason = memory_watcher_violation(sample, consecutive_low_ram)
                    if reason is None:
                        reason = memory_reason
                    job_receipt = job.query()
                except BaseException as exc:
                    sample = {"observation_error": type(exc).__name__}
                    reason = "resource observation missing or invalid"
                samples.append({"elapsed_seconds": round(time.monotonic() - started, 3), **sample})
                if "start_used_vram_mib" in sample:
                    maximum_used = max(maximum_used, int(sample["start_used_vram_mib"]))
                    maximum_temperature = max(maximum_temperature, int(sample["temperature_c"]))
                    minimum_free_gpu = min(minimum_free_gpu, int(sample["start_free_vram_mib"]))
                    minimum_free_ram = min(minimum_free_ram, int(sample["free_ram_bytes"]))
                    minimum_commit_headroom = min(minimum_commit_headroom, int(sample["commit_headroom_bytes"]))
                if reason is not None:
                    violation = reason
                    job.terminate()
                    break
                if time.monotonic() - started > MAXIMUM_RUN_SECONDS:
                    violation = "bounded probe exceeded 90 minute timeout"
                    job.terminate()
                    break
                if len(samples) == 1 or len(samples) % 20 == 0:
                    print(
                        json.dumps(
                            {
                                "status": "bounded_probe_running",
                                "steps": plan["training"]["max_optimizer_steps"],
                                "elapsed_seconds": samples[-1]["elapsed_seconds"],
                                "gpu_used_mib": sample.get("start_used_vram_mib"),
                                "gpu_free_mib": sample.get("start_free_vram_mib"),
                                "temperature_c": sample.get("temperature_c"),
                            },
                            ensure_ascii=False,
                        ),
                        flush=True,
                    )
                time.sleep(WATCHER_INTERVAL_SECONDS)
            returncode = process.wait(timeout=15)
            job_receipt = job.query()
    finally:
        if process is not None and process.poll() is None:
            if job is not None:
                job.terminate()
            else:
                _terminate_owned(process)
        if gate is not None:
            gate.close()
        if job is not None:
            if job_receipt is None:
                job_receipt = job.query()
            job.close()
    exit_samples: list[dict[str, Any]] = []
    exit_ok = False
    for _ in range(15):
        exit_sample = _sample_resources()
        exit_samples.append(exit_sample)
        if int(exit_sample["start_used_vram_mib"]) <= int(launch["start_used_vram_mib"]) + WATCHER_MARGIN_MIB:
            exit_ok = True
            break
        time.sleep(2)
    watcher = {
        "schema": WATCHER_SCHEMA,
        "status": "passed" if returncode == 0 and violation is None and exit_ok else "failed",
        "plan_sha256": plan["plan_sha256"],
        "launch_resource": launch,
        "allocator_cap_mib": effective_cap,
        "plan_allocator_cap_upper_bound_mib": plan_cap,
        "bounded_probe_policy_sha256": declared_policy_sha,
        "runtime_total_used_jitter_margin_mib": total_used_jitter_margin,
        "maximum_total_gpu_used_mib": maximum_used,
        "maximum_temperature_c": maximum_temperature,
        "minimum_system_free_vram_mib": minimum_free_gpu,
        "minimum_free_ram_bytes": minimum_free_ram,
        "minimum_commit_headroom_bytes": minimum_commit_headroom,
        "gpu_job": {**launch_policy["gpu_job"], **(job_receipt or {})},
        "watcher_interval_seconds": WATCHER_INTERVAL_SECONDS,
        "samples": samples,
        "violation": violation,
        "worker_returncode": returncode,
        "exit_samples": exit_samples,
        "exit_gpu_returned_to_baseline": exit_ok,
        "formal_checkpoint_written": False,
    }
    ORCH.immutable_write(attempt / "EXTERNAL_WATCHER_RECEIPT.json", _pretty(watcher))
    if watcher["status"] != "passed":
        _fail("BOUNDED_PROBE_FAILED", "bounded probe worker/watcher failed", watcher=watcher)
    result_path = ORCH._plain_file(attempt / "BOUNDED_PROBE_RESULT.json", "bounded trainer result")
    result = ORCH._read_json(result_path, "bounded trainer result")
    expected_steps = int(plan["training"]["max_optimizer_steps"])
    checkpoints = list((run_root / "checkpoints").iterdir())
    checkpoint_required = bool(plan["bounded_probe"].get("probe_checkpoint_required"))
    checkpoint_valid = not checkpoints and result.get("probe_checkpoint_written") is False
    if checkpoint_required and len(checkpoints) == 1:
        checkpoint_receipt = checkpoints[0] / "CHECKPOINT_RECEIPT.json"
        checkpoint_valid = (
            result.get("probe_checkpoint_written") is True
            and result.get("probe_checkpoint") == str(checkpoints[0])
            and checkpoint_receipt.is_file()
            and result.get("probe_checkpoint_receipt_sha256") == _sha(checkpoint_receipt)
        )
    warm_policy = plan.get("warm_start")
    warm_result = result.get("warm_start")
    warm_valid = warm_policy is None and warm_result is None
    if isinstance(warm_policy, Mapping) and isinstance(warm_result, Mapping):
        warm_valid = (
            warm_result.get("continuation_kind") == "model_weights_only_warm_start"
            and warm_result.get("exact_resume") is False
            and warm_result.get("optimizer_state_reset") is True
            and warm_result.get("optimizer_state_entries_before_first_step") == 0
            and warm_result.get("loaded_from_parent_checkpoint_not_base") is True
            and warm_result.get("tensors_loaded_and_schema_verified") == warm_policy.get("model_tensor_count")
            and warm_result.get("model_sha256") == warm_policy.get("model_sha256")
            and warm_result.get("promotion_receipt_sha256") == warm_policy.get("promotion_receipt_sha256")
            and warm_result.get("parent_checkpoint_tree_sha256") == warm_policy.get("parent_checkpoint_tree_sha256")
        )
    if (
        result.get("status") != "passed"
        or result.get("optimizer_steps_completed") != expected_steps
        or result.get("curriculum_sample_covered") is not True
        or result.get("curriculum_probe_shortest_non_release") is not True
        or result.get("curriculum_sample") != plan["bounded_probe"]["curriculum_sample"]
        or result.get("loss_and_grad_finite") is not True
        or result.get("formal_checkpoint_written") is not False
        or result.get("network_used") is not False
        or result.get("worker_pre_exit_gpu_check_authoritative") is not False
        or result.get("external_post_exit_gpu_check_required") is not True
        or result.get("optimizer") != plan["training"]["optimizer"]
        or result.get("floating_parameter_dtypes") != ["torch.bfloat16"]
        or (result.get("bounded_allocator") or {}).get("allocator_cap_mib") != effective_cap
        or (result.get("bounded_allocator") or {}).get("plan_allocator_cap_upper_bound_mib") != plan_cap
        or int(result.get("peak_reserved_mib", effective_cap + 1)) > effective_cap
        or not checkpoint_valid
        or not warm_valid
    ):
        _fail("BOUNDED_PROBE_RESULT_INVALID", "bounded trainer result violates its immutable policy")
    after_files, after_tree = ORCH._inventory(dataset_root, excluded=frozenset({ORCH.QA_APPROVAL_NAME.casefold()}))
    if before_files != after_files or before_tree != after_tree:
        _fail("DATASET_DRIFT", "dataset changed during bounded probe")
    success = {
        "schema": "mentorflow.qwen3-tts-user-authorized-bounded-probe-success.v1",
        "status": "passed",
        "completed_at_utc": datetime.now(timezone.utc).isoformat(),
        "plan_sha256": plan["plan_sha256"],
        "optimizer_steps_completed": expected_steps,
        "trainer_result_sha256": _sha(result_path),
        "watcher_receipt_sha256": _sha(attempt / "EXTERNAL_WATCHER_RECEIPT.json"),
        "peak_reserved_mib": result["peak_reserved_mib"],
        "allocator_cap_mib": effective_cap,
        "plan_allocator_cap_upper_bound_mib": plan_cap,
        "exit_gpu_returned_to_baseline": True,
        "curriculum_probe_shortest_non_release": True,
        "curriculum_sample": plan["bounded_probe"]["curriculum_sample"],
        "longest_formal_probe_passed": False,
        "formal_checkpoint_written": False,
        "probe_checkpoint_written": checkpoint_required,
        "probe_checkpoint": result.get("probe_checkpoint"),
        "probe_checkpoint_receipt_sha256": result.get("probe_checkpoint_receipt_sha256"),
        "curriculum_group": plan["bounded_probe"].get("curriculum_group"),
        "warm_start": warm_result,
        "network_used": False,
        "dataset_tree_before_after": before_tree,
    }
    ORCH.immutable_write(attempt / "SUCCESS.json", _pretty(success))
    return {**success, "run_root": str(run_root), "attempt": str(attempt)}


def run(*, dataset_id: str, prepared_generation: Path | None, curriculum_root: Path | None, curriculum_group: str | None, parent_checkpoint: Path | None = None, run_id: str, steps: int) -> dict[str, Any]:
    if steps not in {1, 20}:
        _fail("STEPS_INVALID", "bounded probe steps must be 1 or 20")
    if curriculum_root is not None or curriculum_group is not None:
        if prepared_generation is not None or curriculum_root is None or curriculum_group is None or steps != 20:
            _fail("CURRICULUM_INVALID", "curriculum mode requires --steps 20 and exactly curriculum root/group")
        prepared = _validate_curriculum_group(curriculum_root, curriculum_group)
    else:
        if prepared_generation is None:
            _fail("PREPARED_INVALID", "prepared generation is required")
        prepared = _validate_prepared(prepared_generation)
    group_id = (prepared.get("curriculum_group") or {}).get("group_id")
    if group_id is not None and group_id != "group-001":
        if parent_checkpoint is None:
            _fail("WARM_START_REQUIRED", f"{group_id} requires the immediately preceding promoted checkpoint")
        warm_start = _promote_model_only_warm_start(
            parent_checkpoint, prepared, promotion_generation=run_id
        )
    else:
        if parent_checkpoint is not None:
            _fail("WARM_START_INVALID", "a parent checkpoint is admitted only after group-001")
        warm_start = None
    plan = _build_plan(dataset_id=dataset_id, run_id=run_id, prepared=prepared, steps=steps, warm_start=warm_start)
    run_root = _create_probe_run(plan, prepared)
    try:
        return _run_worker(plan, run_root)
    except BaseException as exc:
        attempts = sorted((run_root / "attempts").glob("attempt-*"))
        if attempts:
            failure = attempts[-1] / "FAILURE.json"
            if not failure.exists():
                ORCH.immutable_write(
                    failure,
                    _pretty(
                        {
                            "schema": "mentorflow.qwen3-tts-user-authorized-bounded-probe-failure.v1",
                            "status": "failed",
                            "failed_at_utc": datetime.now(timezone.utc).isoformat(),
                            "code": getattr(exc, "code", "UNEXPECTED"),
                            "error_type": type(exc).__name__,
                            "message": str(exc)[:4000],
                            "formal_checkpoint_written": False,
                        }
                    ),
                )
        raise


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-generation-id", required=True)
    parser.add_argument("--prepared-generation", type=Path)
    parser.add_argument("--curriculum-root", type=Path)
    parser.add_argument("--curriculum-group")
    parser.add_argument("--parent-checkpoint", type=Path)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--steps", type=int, choices=(1, 20), required=True)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = run(
            dataset_id=args.dataset_generation_id,
            prepared_generation=args.prepared_generation,
            curriculum_root=args.curriculum_root,
            curriculum_group=args.curriculum_group,
            parent_checkpoint=args.parent_checkpoint,
            run_id=args.run_id,
            steps=args.steps,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except (BoundedProbeError, ORCH.TrainingGateError) as exc:
        print(
            json.dumps(
                {
                    "status": "blocked",
                    "code": getattr(exc, "code", type(exc).__name__),
                    "message": str(exc),
                    "details": getattr(exc, "details", {}),
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
