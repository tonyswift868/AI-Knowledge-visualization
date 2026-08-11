#!/usr/bin/env python3
"""Offline CPU-only Qwen3-TTS tokenizer preparation with immutable evidence.

The parent process validates a signed dataset admission, builds a generation-
scoped plan, and monitors a child running in the pinned Qwen runtime.  The
child hard-disables CUDA, blocks Python socket connections, loads only the
local speech tokenizer, and encodes one admitted training sample at a time.
No training weights are loaded and no training step is executed.
"""

from __future__ import annotations

import argparse
import gc
import hashlib
import itertools
import json
import math
import os
import shutil
import socket
import stat
import subprocess
import sys
import threading
import time
import urllib.request
import uuid
import wave
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping


# The parent also supplies these values in the immutable worker environment.
# Set the CUDA visibility boundary before importing any project, torch, or Qwen
# modules when this process was launched as the encoder worker.
if "--worker" in sys.argv:
    os.environ["CUDA_VISIBLE_DEVICES"] = ""
    os.environ["PYTORCH_NVML_BASED_CUDA_CHECK"] = "1"


def _wait_for_job_bootstrap_gate(argv: list[str]) -> None:
    if "--job-bootstrap" not in argv:
        return
    if os.name != "nt" or "--job-event" not in argv:
        raise SystemExit(125)
    try:
        event_name = argv[argv.index("--job-event") + 1]
    except (IndexError, ValueError):
        raise SystemExit(125)
    if not event_name.startswith("Local\\MentorFlowCpuPrepare-") or len(event_name) > 128:
        raise SystemExit(125)
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenEventW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.OpenEventW.restype = wintypes.HANDLE
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    event = kernel32.OpenEventW(0x00100000, False, event_name)  # SYNCHRONIZE
    if not event:
        raise SystemExit(125)
    try:
        if kernel32.WaitForSingleObject(event, 30_000) != 0:  # WAIT_OBJECT_0
            raise SystemExit(125)
    finally:
        kernel32.CloseHandle(event)


_wait_for_job_bootstrap_gate(sys.argv)


SCRIPT_ROOT = Path(__file__).resolve().parent
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

import orchestrate_qwen3_tts_training as ORCH


PLAN_SCHEMA = "mentorflow.qwen3-tts-cpu-prepare-plan.v2"
WORKER_RESULT_SCHEMA = "mentorflow.qwen3-tts-cpu-prepare-worker-result.v2"
PREPARED_RECEIPT_SCHEMA = "mentorflow.qwen3-tts-cpu-prepared-codes.v2"
ATTEMPT_SCHEMA = "mentorflow.qwen3-tts-cpu-prepare-attempt.v2"
MINIMUM_ADMISSION_PHYSICAL_AVAILABLE_BYTES = 4 * 1024**3
SYSTEM_COMMIT_RESERVE_BYTES = 4 * 1024**3
MINIMUM_RUNTIME_PHYSICAL_AVAILABLE_BYTES = 2 * 1024**3
MINIMUM_RUNTIME_COMMIT_HEADROOM_BYTES = 2 * 1024**3
LAUNCH_MEMORY_SAMPLE_COUNT = 2
LAUNCH_MEMORY_SAMPLE_INTERVAL_SECONDS = 1.0
RUNTIME_LOW_PHYSICAL_CONSECUTIVE_SAMPLES = 2
MINIMUM_DISK_RESERVE_BYTES = 5 * 1024**3
MAXIMUM_AUDIO_DURATION_MS = 30_000
MAXIMUM_AUDIO_FRAMES = 30 * 24_000
MAXIMUM_WAV_BYTES = 2 * 1024**2
MAXIMUM_TEXT_CHARACTERS = 2_048
MAXIMUM_TEXT_UTF8_BYTES = 8 * 1024
MAXIMUM_RAW_JSONL_BYTES = 8 * 1024**2
MAXIMUM_RAW_JSONL_LINE_BYTES = 64 * 1024
MAXIMUM_PREPARED_JSONL_BYTES = 256 * 1024**2
MAXIMUM_PREPARED_JSONL_LINE_BYTES = 2 * 1024**2
MAXIMUM_JSON_DOCUMENT_BYTES = 8 * 1024**2
MAXIMUM_WORKER_LOG_BYTES = 8 * 1024**2
MAXIMUM_TOKENIZER_LOAD_SECONDS = 5 * 60
MAXIMUM_SAMPLE_ENCODE_SECONDS = 2 * 60
MAXIMUM_TOTAL_PREPARE_SECONDS = 4 * 60 * 60
MAXIMUM_WORKER_RSS_BYTES = 5 * 1024**3
MAXIMUM_JOB_COMMIT_BYTES = 5 * 1024**3
MINIMUM_ADMISSION_COMMIT_HEADROOM_BYTES = MAXIMUM_JOB_COMMIT_BYTES + SYSTEM_COMMIT_RESERVE_BYTES
WATCHDOG_INTERVAL_SECONDS = 1.0
WATCHDOG_EXIT_CODE = 124
_EVENT_LOCK = threading.Lock()
TOKENIZER_PINS = {
    path.removeprefix("speech_tokenizer/"): value
    for path, value in ORCH.MODEL_FILES.items()
    if path.startswith("speech_tokenizer/")
}
OFFLINE_VALUES = {
    **ORCH.OFFLINE_VALUES,
    "CUDA_VISIBLE_DEVICES": "",
    "PYTORCH_NVML_BASED_CUDA_CHECK": "1",
    "TOKENIZERS_PARALLELISM": "false",
}


class CpuPrepareError(RuntimeError):
    def __init__(self, code: str, message: str, *, details: Mapping[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.details = dict(details or {})


def _fail(code: str, message: str, **details: Any) -> None:
    raise CpuPrepareError(code, message, details=details)


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


def _plain_file(path: Path, label: str, *, hardlink_allowed: bool = False) -> Path:
    try:
        value = path.lstat()
    except FileNotFoundError:
        _fail("FILE_MISSING", f"{label} is missing: {path}")
    if (
        not stat.S_ISREG(value.st_mode)
        or _is_reparse(path)
        or (not hardlink_allowed and int(value.st_nlink) != 1)
    ):
        _fail("FILE_INVALID", f"{label} is not an admitted plain file: {path}")
    return path


def _plain_dir(path: Path, label: str) -> Path:
    try:
        value = path.lstat()
    except FileNotFoundError:
        _fail("DIRECTORY_MISSING", f"{label} is missing: {path}")
    if not stat.S_ISDIR(value.st_mode) or _is_reparse(path):
        _fail("DIRECTORY_INVALID", f"{label} is not a plain directory: {path}")
    return path


def _plain_directory_chain(path: Path, label: str) -> Path:
    current = path
    while True:
        _plain_dir(current, label)
        parent = current.parent
        if parent == current:
            return path
        current = parent


def _ensure_plain_subdirectory(boundary: Path, path: Path, label: str) -> Path:
    boundary = _plain_directory_chain(boundary, f"{label} boundary")
    try:
        common = os.path.commonpath((str(boundary), str(path)))
    except ValueError:
        _fail("DIRECTORY_INVALID", f"{label} escapes its fixed boundary")
    if os.path.normcase(common) != os.path.normcase(str(boundary)):
        _fail("DIRECTORY_INVALID", f"{label} escapes its fixed boundary")
    relative = Path(os.path.relpath(path, boundary))
    if relative.is_absolute() or ".." in relative.parts:
        _fail("DIRECTORY_INVALID", f"{label} relative path is unsafe")
    current = boundary
    for part in relative.parts:
        if part in ("", "."):
            continue
        current = current / part
        try:
            os.mkdir(current)
        except FileExistsError:
            pass
        _plain_dir(current, label)
    return _plain_dir(path, label)


def _same_windows_path(left: Path, right: Path) -> bool:
    return ORCH._windows_parts(left) == ORCH._windows_parts(right)


def _read_json(
    path: Path,
    label: str,
    *,
    maximum_bytes: int = MAXIMUM_JSON_DOCUMENT_BYTES,
) -> Mapping[str, Any]:
    try:
        path = _plain_file(path, label)
        if path.stat().st_size > maximum_bytes:
            _fail("JSON_TOO_LARGE", f"{label} exceeds its byte limit")
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        _fail("JSON_INVALID", f"{label} is invalid JSON: {type(exc).__name__}")
    if not isinstance(value, Mapping):
        _fail("JSON_INVALID", f"{label} must be an object")
    return value


def _exclusive_write(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())


def _append_event(path: Path, value: Mapping[str, Any]) -> None:
    raw = _canonical_json(value) + b"\n"
    with _EVENT_LOCK:
        with path.open("ab") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())


def _free_ram_bytes() -> int:
    return int(_system_memory_probe()["physical_available_bytes"])


def _system_memory_probe() -> dict[str, int]:
    """Atomically observe Windows physical availability and commit accounting."""
    if os.name != "nt":
        _fail("CPU_MEMORY_OBSERVATION_FAILED", "adaptive CPU memory policy requires Windows")
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
        _fail(
            "CPU_MEMORY_OBSERVATION_FAILED",
            "GetPerformanceInfo failed while querying commit headroom",
            winerror=ctypes.get_last_error(),
        )
    page_size = int(value.PageSize)
    commit_total = int(value.CommitTotal) * page_size
    commit_limit = int(value.CommitLimit) * page_size
    if page_size <= 0 or commit_limit <= 0 or commit_total < 0 or commit_total > commit_limit:
        _fail("CPU_MEMORY_OBSERVATION_FAILED", "Windows commit accounting is invalid")
    return {
        "physical_available_bytes": int(value.PhysicalAvailable) * page_size,
        "physical_total_bytes": int(value.PhysicalTotal) * page_size,
        "commit_total_bytes": commit_total,
        "commit_limit_bytes": commit_limit,
        "commit_headroom_bytes": commit_limit - commit_total,
    }


def _normalise_memory_sample(value: Mapping[str, Any]) -> dict[str, int]:
    keys = (
        "physical_available_bytes",
        "physical_total_bytes",
        "commit_total_bytes",
        "commit_limit_bytes",
        "commit_headroom_bytes",
    )
    if not isinstance(value, Mapping) or any(type(value.get(key)) is not int for key in keys):
        _fail("CPU_MEMORY_OBSERVATION_FAILED", "memory probe omitted required integer accounting")
    sample = {key: int(value[key]) for key in keys}
    if (
        sample["physical_available_bytes"] < 0
        or sample["physical_total_bytes"] <= 0
        or sample["physical_available_bytes"] > sample["physical_total_bytes"]
        or sample["commit_total_bytes"] < 0
        or sample["commit_limit_bytes"] <= 0
        or sample["commit_total_bytes"] > sample["commit_limit_bytes"]
        or sample["commit_headroom_bytes"]
        != sample["commit_limit_bytes"] - sample["commit_total_bytes"]
    ):
        _fail("CPU_MEMORY_OBSERVATION_FAILED", "memory probe accounting is inconsistent")
    return sample


def _stable_launch_memory_gate(
    *,
    probe: Any | None = None,
    sleep: Any | None = None,
) -> dict[str, Any]:
    probe = _system_memory_probe if probe is None else probe
    sleep = time.sleep if sleep is None else sleep
    gc.collect()
    samples: list[dict[str, Any]] = []
    for index in range(LAUNCH_MEMORY_SAMPLE_COUNT):
        try:
            sample = _normalise_memory_sample(probe())
        except CpuPrepareError:
            raise
        except BaseException as exc:
            _fail("CPU_MEMORY_OBSERVATION_FAILED", f"memory probe failed: {type(exc).__name__}")
        samples.append({"sample": index + 1, "recorded_at_utc": datetime.now(timezone.utc).isoformat(), **sample})
        if index + 1 < LAUNCH_MEMORY_SAMPLE_COUNT:
            sleep(LAUNCH_MEMORY_SAMPLE_INTERVAL_SECONDS)
    reasons: list[str] = []
    if any(item["physical_available_bytes"] < MINIMUM_ADMISSION_PHYSICAL_AVAILABLE_BYTES for item in samples):
        reasons.append("physical available memory is below the 4 GiB CPU launch floor")
    if any(item["commit_headroom_bytes"] < MINIMUM_ADMISSION_COMMIT_HEADROOM_BYTES for item in samples):
        reasons.append("commit headroom is below Job cap 5 GiB plus system reserve 4 GiB")
    result = {
        "schema": "mentorflow.qwen3-tts-cpu-adaptive-memory-gate.v1",
        "decision": "GO" if not reasons else "NO_GO",
        "samples": samples,
        "minimum_physical_available_bytes": min(item["physical_available_bytes"] for item in samples),
        "minimum_commit_headroom_bytes": min(item["commit_headroom_bytes"] for item in samples),
        "required_physical_available_bytes": MINIMUM_ADMISSION_PHYSICAL_AVAILABLE_BYTES,
        "required_commit_headroom_bytes": MINIMUM_ADMISSION_COMMIT_HEADROOM_BYTES,
        "job_commit_cap_bytes": MAXIMUM_JOB_COMMIT_BYTES,
        "system_commit_reserve_bytes": SYSTEM_COMMIT_RESERVE_BYTES,
        "reasons": reasons,
    }
    if reasons:
        _fail("CPU_PREPARE_RESOURCE_GATE", "adaptive CPU launch memory gate is NO_GO", **result)
    return result


def _runtime_memory_violation(sample: Mapping[str, Any], consecutive_low_physical: int) -> tuple[int, str | None]:
    value = _normalise_memory_sample(sample)
    if value["commit_headroom_bytes"] < MINIMUM_RUNTIME_COMMIT_HEADROOM_BYTES:
        return consecutive_low_physical, "runtime commit headroom dropped below 2 GiB"
    consecutive_low_physical = (
        consecutive_low_physical + 1
        if value["physical_available_bytes"] < MINIMUM_RUNTIME_PHYSICAL_AVAILABLE_BYTES
        else 0
    )
    if consecutive_low_physical >= RUNTIME_LOW_PHYSICAL_CONSECUTIVE_SAMPLES:
        return consecutive_low_physical, "runtime physical available memory stayed below 2 GiB"
    return consecutive_low_physical, None


def _process_working_set_bytes(process_id: int) -> int | None:
    if os.name != "nt":
        return None
    import ctypes

    class ProcessMemoryCounters(ctypes.Structure):
        _fields_ = [
            ("cb", ctypes.c_ulong),
            ("PageFaultCount", ctypes.c_ulong),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    handle = ctypes.windll.kernel32.OpenProcess(0x0400 | 0x0010, False, process_id)
    if not handle:
        return None
    try:
        counters = ProcessMemoryCounters()
        counters.cb = ctypes.sizeof(counters)
        if not ctypes.windll.psapi.GetProcessMemoryInfo(
            handle, ctypes.byref(counters), ctypes.sizeof(counters)
        ):
            return None
        return int(counters.WorkingSetSize)
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)


def _windows_process_parents() -> dict[int, int]:
    if os.name != "nt":
        return {}
    import ctypes
    from ctypes import wintypes

    class ProcessEntry32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry32W)]
    kernel32.Process32FirstW.restype = wintypes.BOOL
    kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry32W)]
    kernel32.Process32NextW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    snapshot = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
    if snapshot == ctypes.c_void_p(-1).value:
        _fail("CPU_PREPARE_PROCESS_SNAPSHOT_FAILED", "CreateToolhelp32Snapshot failed")
    parents: dict[int, int] = {}
    try:
        entry = ProcessEntry32W()
        entry.dwSize = ctypes.sizeof(entry)
        success = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        if not success:
            _fail("CPU_PREPARE_PROCESS_SNAPSHOT_FAILED", "Process32FirstW failed")
        while success:
            parents[int(entry.th32ProcessID)] = int(entry.th32ParentProcessID)
            success = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
        if ctypes.get_last_error() not in (0, 18):  # ERROR_NO_MORE_FILES
            _fail("CPU_PREPARE_PROCESS_SNAPSHOT_FAILED", "Process32NextW failed")
    finally:
        kernel32.CloseHandle(snapshot)
    return parents


def _process_tree_ids(root_process_id: int) -> list[int]:
    if os.name != "nt":
        return [root_process_id]
    parents = _windows_process_parents()
    tree = [root_process_id]
    known = {root_process_id}
    changed = True
    while changed:
        changed = False
        for process_id, parent_id in parents.items():
            if parent_id in known and process_id not in known:
                tree.append(process_id)
                known.add(process_id)
                changed = True
    return tree


def _process_tree_working_set_bytes(root_process_id: int) -> int | None:
    values: list[int] = []
    for process_id in _process_tree_ids(root_process_id):
        value = _process_working_set_bytes(process_id)
        if value is None:
            # A short-lived descendant may exit between the snapshot and RSS
            # query.  Only ignore it if a fresh snapshot proves it is gone;
            # an unreadable live PID is a fail-closed observation failure.
            if process_id in _windows_process_parents():
                return None
            continue
        values.append(value)
    return sum(values) if values else None


def _terminate_owned_process_tree(
    process: subprocess.Popen[Any],
    job: Any | None = None,
) -> None:
    if process.poll() is not None:
        return
    if job is not None:
        job.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            _fail("CPU_PREPARE_PROCESS_STOP_FAILED", "owned CPU prepare job did not stop")
        return
    if os.name != "nt":
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=15)
        return
    import ctypes

    # The root PID is the exact subprocess created by this orchestrator.  Stop
    # only that owned tree, deepest descendants first, never unrelated jobs.
    for process_id in reversed(_process_tree_ids(process.pid)):
        handle = ctypes.windll.kernel32.OpenProcess(0x0001, False, process_id)
        if not handle:
            continue
        try:
            ctypes.windll.kernel32.TerminateProcess(handle, 1)
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    try:
        process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        _fail("CPU_PREPARE_PROCESS_STOP_FAILED", "owned CPU prepare process tree did not stop")


class _DatasetReadLocks:
    def __init__(self, handles: list[int]):
        self._handles = handles

    @property
    def count(self) -> int:
        return len(self._handles)

    def close(self) -> None:
        if os.name != "nt":
            self._handles.clear()
            return
        import ctypes

        for handle in reversed(self._handles):
            ctypes.windll.kernel32.CloseHandle(handle)
        self._handles.clear()


def _acquire_dataset_read_locks(
    dataset_root: Path,
    admitted_files: list[Mapping[str, Any]],
    *,
    extra_files: Iterable[Path] = (),
) -> _DatasetReadLocks:
    if os.name != "nt":
        _fail("DATASET_READ_ONLY_UNSUPPORTED", "CPU prepare requires enforceable Windows read locks")
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.windll.kernel32
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    paths: list[Path] = []
    for index, record in enumerate(admitted_files):
        relative = record.get("path") if isinstance(record, Mapping) else None
        if not isinstance(relative, str) or not relative or Path(relative).is_absolute() or ".." in Path(relative).parts:
            _fail("DATASET_READ_ONLY_INVALID", f"admitted file record {index} is malformed")
        paths.append(dataset_root / Path(relative))
    paths.append(dataset_root / ORCH.QA_APPROVAL_NAME)
    paths.extend(_plain_file(Path(path), "extra read-locked trust file") for path in extra_files)
    if len(paths) != len(set(os.path.normcase(str(path)) for path in paths)):
        _fail("DATASET_READ_ONLY_INVALID", "admitted file lock paths collide")
    handles: list[int] = []
    invalid_handle = ctypes.c_void_p(-1).value
    try:
        for path in sorted(paths, key=lambda value: str(value).casefold()):
            path = _plain_file(path, f"read-locked file {path}")
            handle = kernel32.CreateFileW(
                str(path),
                0x80000000,  # GENERIC_READ
                0x00000001,  # FILE_SHARE_READ only: deny writes and delete/rename
                None,
                3,  # OPEN_EXISTING
                0x08000000,  # FILE_FLAG_SEQUENTIAL_SCAN
                None,
            )
            if handle == invalid_handle:
                _fail(
                    "DATASET_READ_ONLY_LOCK_FAILED",
                    f"could not acquire deny-write/delete read lock: {path}",
                    winerror=ctypes.get_last_error(),
                )
            handles.append(handle)
    except BaseException:
        _DatasetReadLocks(handles).close()
        raise
    return _DatasetReadLocks(handles)


class _WindowsBootstrapGate:
    def __init__(self):
        if os.name != "nt":
            _fail("CPU_JOB_UNSUPPORTED", "CPU prepare bootstrap gate requires Windows")
        import ctypes
        from ctypes import wintypes

        self._ctypes = ctypes
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._kernel32.CreateEventW.argtypes = [
            wintypes.LPVOID,
            wintypes.BOOL,
            wintypes.BOOL,
            wintypes.LPCWSTR,
        ]
        self._kernel32.CreateEventW.restype = wintypes.HANDLE
        self._kernel32.SetEvent.argtypes = [wintypes.HANDLE]
        self._kernel32.SetEvent.restype = wintypes.BOOL
        self._kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        self._kernel32.CloseHandle.restype = wintypes.BOOL
        self.name = f"Local\\MentorFlowCpuPrepare-{uuid.uuid4().hex}"
        self._handle = self._kernel32.CreateEventW(None, True, False, self.name)
        if not self._handle:
            _fail("CPU_JOB_GATE_FAILED", "CreateEventW failed for CPU prepare bootstrap")

    def release(self) -> None:
        if not self._kernel32.SetEvent(self._handle):
            _fail(
                "CPU_JOB_GATE_FAILED",
                "SetEvent failed for CPU prepare bootstrap",
                winerror=self._ctypes.get_last_error(),
            )

    def close(self) -> None:
        if self._handle:
            self._kernel32.CloseHandle(self._handle)
            self._handle = None


class _WindowsJob:
    """Owned Windows job with a kernel-enforced whole-tree commit limit."""

    def __init__(self, memory_limit_bytes: int):
        if os.name != "nt":
            _fail("CPU_JOB_UNSUPPORTED", "CPU prepare requires a Windows Job Object")
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
        self._kernel32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            wintypes.LPVOID,
            wintypes.DWORD,
        ]
        self._kernel32.SetInformationJobObject.restype = wintypes.BOOL
        self._kernel32.QueryInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            wintypes.LPVOID,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
        ]
        self._kernel32.QueryInformationJobObject.restype = wintypes.BOOL
        self._kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self._kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        self._kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        self._kernel32.TerminateJobObject.restype = wintypes.BOOL
        self._kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        self._kernel32.CloseHandle.restype = wintypes.BOOL
        self._handle = self._kernel32.CreateJobObjectW(None, None)
        if not self._handle:
            _fail("CPU_JOB_CREATE_FAILED", "CreateJobObjectW failed", winerror=ctypes.get_last_error())
        self.memory_limit_bytes = int(memory_limit_bytes)
        information = ExtendedLimitInformation()
        information.BasicLimitInformation.LimitFlags = 0x00000100 | 0x00000200 | 0x00002000
        information.ProcessMemoryLimit = self.memory_limit_bytes
        information.JobMemoryLimit = self.memory_limit_bytes
        if not self._kernel32.SetInformationJobObject(
            self._handle,
            9,  # JobObjectExtendedLimitInformation
            ctypes.byref(information),
            ctypes.sizeof(information),
        ):
            error = ctypes.get_last_error()
            self.close()
            _fail("CPU_JOB_LIMIT_FAILED", "SetInformationJobObject failed", winerror=error)

    def assign(self, process: subprocess.Popen[Any]) -> None:
        process_handle = self._wintypes.HANDLE(int(process._handle))
        if not self._kernel32.AssignProcessToJobObject(self._handle, process_handle):
            _fail(
                "CPU_JOB_ASSIGN_FAILED",
                "AssignProcessToJobObject failed for gated owned worker",
                winerror=self._ctypes.get_last_error(),
            )

    def query_memory(self) -> dict[str, int]:
        information = self._extended_type()
        returned = self._wintypes.DWORD()
        if not self._kernel32.QueryInformationJobObject(
            self._handle,
            9,
            self._ctypes.byref(information),
            self._ctypes.sizeof(information),
            self._ctypes.byref(returned),
        ):
            _fail("CPU_JOB_QUERY_FAILED", "QueryInformationJobObject failed")
        return {
            "job_memory_limit_bytes": int(information.JobMemoryLimit),
            "process_memory_limit_bytes": int(information.ProcessMemoryLimit),
            "peak_job_memory_used_bytes": int(information.PeakJobMemoryUsed),
            "peak_process_memory_used_bytes": int(information.PeakProcessMemoryUsed),
        }

    def terminate(self, exit_code: int = 1) -> None:
        if self._handle and not self._kernel32.TerminateJobObject(self._handle, exit_code):
            _fail("CPU_JOB_TERMINATE_FAILED", "TerminateJobObject failed")

    def close(self) -> None:
        if self._handle:
            self._kernel32.CloseHandle(self._handle)
            self._handle = None


def _tokenizer_inventory(root: Path) -> tuple[list[dict[str, Any]], str]:
    root = _plain_dir(root, "speech tokenizer root")
    expected_files = set(TOKENIZER_PINS)
    actual_files = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and not _is_reparse(path)
    }
    if actual_files != expected_files:
        _fail("TOKENIZER_FILE_SET_MISMATCH", "local speech tokenizer exact file set changed")
    records: list[dict[str, Any]] = []
    for relative, (expected_bytes, expected_sha) in TOKENIZER_PINS.items():
        path = _plain_file(root.joinpath(*relative.split("/")), f"speech tokenizer/{relative}")
        if path.stat().st_size != expected_bytes or _sha256_file(path) != expected_sha:
            _fail("TOKENIZER_PIN_MISMATCH", f"local speech tokenizer pin differs: {relative}")
        records.append({"path": relative, "bytes": expected_bytes, "sha256": expected_sha})
    tree = _sha256_bytes(
        b"".join(
            f"{item['path']}\0{item['bytes']}\0{item['sha256']}\n".encode("utf-8")
            for item in records
        )
    )
    return records, tree


def _limits_policy() -> dict[str, int | float]:
    return {
        "maximum_audio_duration_ms": MAXIMUM_AUDIO_DURATION_MS,
        "maximum_audio_frames": MAXIMUM_AUDIO_FRAMES,
        "maximum_wav_bytes": MAXIMUM_WAV_BYTES,
        "maximum_text_characters": MAXIMUM_TEXT_CHARACTERS,
        "maximum_text_utf8_bytes": MAXIMUM_TEXT_UTF8_BYTES,
        "maximum_raw_jsonl_bytes": MAXIMUM_RAW_JSONL_BYTES,
        "maximum_raw_jsonl_line_bytes": MAXIMUM_RAW_JSONL_LINE_BYTES,
        "maximum_prepared_jsonl_bytes": MAXIMUM_PREPARED_JSONL_BYTES,
        "maximum_prepared_jsonl_line_bytes": MAXIMUM_PREPARED_JSONL_LINE_BYTES,
        "maximum_worker_log_bytes": MAXIMUM_WORKER_LOG_BYTES,
        "maximum_tokenizer_load_seconds": MAXIMUM_TOKENIZER_LOAD_SECONDS,
        "maximum_sample_encode_seconds": MAXIMUM_SAMPLE_ENCODE_SECONDS,
        "maximum_total_prepare_seconds": MAXIMUM_TOTAL_PREPARE_SECONDS,
        "maximum_worker_rss_bytes": MAXIMUM_WORKER_RSS_BYTES,
        "maximum_job_commit_bytes": MAXIMUM_JOB_COMMIT_BYTES,
        "minimum_launch_physical_available_bytes": MINIMUM_ADMISSION_PHYSICAL_AVAILABLE_BYTES,
        "minimum_launch_commit_headroom_bytes": MINIMUM_ADMISSION_COMMIT_HEADROOM_BYTES,
        "system_commit_reserve_bytes": SYSTEM_COMMIT_RESERVE_BYTES,
        "minimum_runtime_physical_available_bytes": MINIMUM_RUNTIME_PHYSICAL_AVAILABLE_BYTES,
        "minimum_runtime_commit_headroom_bytes": MINIMUM_RUNTIME_COMMIT_HEADROOM_BYTES,
        "runtime_low_physical_consecutive_samples": RUNTIME_LOW_PHYSICAL_CONSECUTIVE_SAMPLES,
        "launch_memory_sample_count": LAUNCH_MEMORY_SAMPLE_COUNT,
        "launch_memory_sample_interval_seconds": LAUNCH_MEMORY_SAMPLE_INTERVAL_SECONDS,
        "watchdog_interval_seconds": WATCHDOG_INTERVAL_SECONDS,
    }


def _load_plan(path: Path) -> Mapping[str, Any]:
    plan = _read_json(path, "CPU prepare plan")
    if plan.get("schema") != PLAN_SCHEMA or not isinstance(plan.get("plan_sha256"), str):
        _fail("PLAN_INVALID", "CPU prepare plan schema/hash is invalid")
    unsigned = dict(plan)
    declared = unsigned.pop("plan_sha256")
    if declared != _sha256_bytes(_canonical_json(unsigned)):
        _fail("PLAN_INVALID", "CPU prepare plan canonical SHA-256 is invalid")
    if plan.get("network_policy") != {
        "mode": "zero-traffic-local-only",
        "python_socket_connections_blocked": True,
        "python_urlopen_blocked": True,
        "python_network_audit_hook": True,
        "downloads_allowed": False,
    }:
        _fail("PLAN_INVALID", "CPU prepare plan network policy is invalid")
    if plan.get("device_policy") != {
        "device": "cpu",
        "cuda_visible_devices": "",
        "gpu_use_allowed": False,
        "batch_size": 1,
        "dtype": "float32",
        "device_map": {"": "cpu"},
        "attention_implementation": "sdpa",
        "transformers_kernels": False,
    }:
        _fail("PLAN_INVALID", "CPU prepare device policy is invalid")
    if plan.get("limits") != _limits_policy():
        _fail("PLAN_INVALID", "CPU prepare hard limits differ from the worker contract")
    dataset_generation_id = ORCH._safe_id(plan.get("dataset_generation_id"), "dataset generation id")
    prepare_generation_id = ORCH._safe_id(plan.get("prepare_generation_id"), "prepare generation id")
    paths = plan.get("paths")
    if not isinstance(paths, Mapping):
        _fail("PLAN_INVALID", "CPU prepare plan paths are malformed")
    expected_paths = {
        "dataset_root": ORCH.TRAINING_ROOT / "generations" / dataset_generation_id / "dataset",
        "qa_registry": ORCH.TRAINING_ROOT
        / "private-trust"
        / dataset_generation_id
        / "qa-trust-registry.json",
        "prepare_root": ORCH.TRAINING_ROOT / "prepared-generations" / prepare_generation_id,
        "tokenizer_root": ORCH.DEFAULT_MODEL_ROOT / "speech_tokenizer",
        "python_executable": ORCH.DEFAULT_PYTHON,
    }
    if any(
        not isinstance(paths.get(name), str)
        or not _same_windows_path(Path(str(paths[name])), expected)
        for name, expected in expected_paths.items()
    ):
        _fail("PLAN_INVALID", "CPU prepare plan paths escape fixed production roots")
    if plan.get("worker_sha256") != _sha256_file(Path(__file__).resolve()):
        _fail("PLAN_INVALID", "CPU prepare worker changed after plan creation")
    return plan


def _block_python_network() -> None:
    original_socket = socket.socket

    class OfflineSocket(original_socket):
        def connect(self, *args: Any, **kwargs: Any) -> Any:
            raise OSError("network disabled by MentorFlow CPU prepare worker")

        def connect_ex(self, *args: Any, **kwargs: Any) -> int:
            raise OSError("network disabled by MentorFlow CPU prepare worker")

        def sendto(self, *args: Any, **kwargs: Any) -> int:
            raise OSError("network disabled by MentorFlow CPU prepare worker")

    def blocked_connection(*args: Any, **kwargs: Any) -> Any:
        raise OSError("network disabled by MentorFlow CPU prepare worker")

    # Preserve the native platform capability surface.  Adding ``sendmsg`` on
    # Windows makes asyncio mistake the socket for a POSIX implementation and
    # call the Windows-missing os.sysconf while torch imports unittest.mock.
    if hasattr(original_socket, "sendmsg"):
        OfflineSocket.sendmsg = blocked_connection  # type: ignore[attr-defined]

    socket.socket = OfflineSocket
    socket.create_connection = blocked_connection
    socket.getaddrinfo = blocked_connection
    socket.gethostbyname = blocked_connection
    socket.gethostbyname_ex = blocked_connection
    urllib.request.urlopen = blocked_connection
    urllib.request.OpenerDirector.open = blocked_connection

    blocked_audit_events = {
        "socket.connect",
        "socket.getaddrinfo",
        "socket.gethostbyaddr",
        "socket.gethostbyname",
        "socket.sendto",
    }

    def block_network_audit(event: str, args: tuple[Any, ...]) -> None:
        del args
        if event in blocked_audit_events:
            raise OSError("network disabled by MentorFlow CPU prepare audit hook")

    sys.addaudithook(block_network_audit)


def _assert_worker_environment(plan: Mapping[str, Any]) -> None:
    expected = plan["offline_environment"]
    if any(os.environ.get(str(name)) != str(value) for name, value in expected.items()):
        _fail("OFFLINE_ENV_INVALID", "worker environment differs from its immutable offline plan")
    if os.environ.get("CUDA_VISIBLE_DEVICES") != "":
        _fail("GPU_NOT_DISABLED", "CUDA_VISIBLE_DEVICES must be the empty string")


def _iter_bounded_jsonl(
    path: Path,
    label: str,
    *,
    maximum_total_bytes: int,
    maximum_line_bytes: int,
) -> Iterable[tuple[int, Mapping[str, Any]]]:
    path = _plain_file(path, label)
    if path.stat().st_size > maximum_total_bytes:
        _fail("JSONL_TOO_LARGE", f"{label} exceeds its total byte limit")
    total_bytes = 0
    try:
        with path.open("rb") as handle:
            for line_number, raw in enumerate(handle, start=1):
                total_bytes += len(raw)
                if total_bytes > maximum_total_bytes or len(raw) > maximum_line_bytes:
                    _fail("JSONL_TOO_LARGE", f"{label} line {line_number} exceeds its byte limit")
                if not raw.strip():
                    _fail("JSONL_INVALID", f"{label} contains blank line {line_number}")
                value = json.loads(raw.decode("utf-8"))
                if not isinstance(value, Mapping):
                    _fail("JSONL_INVALID", f"{label} line {line_number} is not an object")
                yield line_number, value
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        _fail("JSONL_INVALID", f"{label} is invalid: {type(exc).__name__}")


def _assert_no_reparse_ancestry(path: Path, root: Path, label: str) -> None:
    current = path.parent
    root_key = os.path.normcase(str(root))
    while True:
        if _is_reparse(current):
            _fail("INPUT_INVALID", f"{label} traverses a reparse-point directory")
        if os.path.normcase(str(current)) == root_key:
            return
        parent = current.parent
        if parent == current:
            _fail("INPUT_INVALID", f"{label} ancestry does not reach the admitted dataset")
        current = parent


def _local_dataset_wav(
    raw_value: Any,
    *,
    dataset_root: Path,
    expected: Mapping[str, Any],
    label: str,
) -> Path:
    if (
        not isinstance(raw_value, str)
        or raw_value != raw_value.strip()
        or "\x00" in raw_value
        or "://" in raw_value
        or raw_value.casefold().startswith("data:")
        or raw_value.startswith(("\\\\", "//"))
    ):
        _fail("INPUT_INVALID", f"{label} is not a plain local path")
    path = Path(raw_value)
    if not path.is_absolute() or path.suffix.casefold() != ".wav" or ".." in path.parts:
        _fail("INPUT_INVALID", f"{label} is not an absolute WAV path")
    try:
        common = os.path.commonpath((str(dataset_root), str(path)))
    except ValueError:
        _fail("INPUT_INVALID", f"{label} escapes the admitted dataset")
    if os.path.normcase(common) != os.path.normcase(str(dataset_root)):
        _fail("INPUT_INVALID", f"{label} escapes the admitted dataset")
    _assert_no_reparse_ancestry(path, dataset_root, label)
    path = _plain_file(path, label)
    if (
        str(path) != str(expected.get("absolute_path"))
        or path.stat().st_size != expected.get("bytes")
        or path.stat().st_size > MAXIMUM_WAV_BYTES
        or _sha256_file(path) != expected.get("sha256")
    ):
        _fail("INPUT_DRIFT", f"{label} differs from its signed pin or hard byte limit")
    return path


def _inspect_pcm16_wav(
    path: Path,
    *,
    expected: Mapping[str, Any],
    label: str,
    read_samples: bool = False,
) -> tuple[int, bytes | None]:
    try:
        with wave.open(str(path), "rb") as audio:
            if (
                audio.getnchannels() != 1
                or audio.getsampwidth() != 2
                or audio.getframerate() != 24_000
                or audio.getcomptype() != "NONE"
            ):
                _fail("INPUT_INVALID", f"{label} is not mono PCM16 24 kHz WAV")
            frames = audio.getnframes()
            if frames <= 0 or frames > MAXIMUM_AUDIO_FRAMES:
                _fail("INPUT_INVALID", f"{label} exceeds the 30-second frame bound")
            duration_ms = round(frames * 1000 / 24_000)
            if duration_ms > MAXIMUM_AUDIO_DURATION_MS or duration_ms != expected.get("duration_ms"):
                _fail("INPUT_DRIFT", f"{label} duration differs from its signed pin or 30-second bound")
            samples = audio.readframes(frames) if read_samples else None
            if samples is not None and len(samples) != frames * 2:
                _fail("INPUT_INVALID", f"{label} PCM payload is truncated")
    except (OSError, EOFError, wave.Error):
        _fail("INPUT_INVALID", f"{label} is not a readable admitted WAV")
    return frames, samples


def _validate_text(value: Any, expected_sha256: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        _fail("INPUT_INVALID", f"{label} is empty or malformed")
    raw = value.encode("utf-8")
    if len(value) > MAXIMUM_TEXT_CHARACTERS or len(raw) > MAXIMUM_TEXT_UTF8_BYTES:
        _fail("INPUT_INVALID", f"{label} exceeds its character/UTF-8 byte limit")
    if _sha256_bytes(raw) != expected_sha256:
        _fail("INPUT_DRIFT", f"{label} differs from its signed pin")
    return value


def _tokenizer_contract_from_config(root: Path) -> dict[str, Any]:
    config = _read_json(root / "config.json", "speech tokenizer config", maximum_bytes=1024 * 1024)
    encoder = config.get("encoder_config")
    if not isinstance(encoder, Mapping):
        _fail("TOKENIZER_CONTRACT_INVALID", "speech tokenizer encoder config is malformed")
    contract = {
        "model_type": config.get("model_type"),
        "input_sample_rate": config.get("input_sample_rate"),
        "encode_downsample_rate": config.get("encode_downsample_rate"),
        "quantizers": config.get("encoder_valid_num_quantizers"),
        "codebook_size": encoder.get("codebook_size"),
    }
    if (
        contract["input_sample_rate"] != 24_000
        or type(contract["encode_downsample_rate"]) is not int
        or not 1 <= contract["encode_downsample_rate"] <= 24_000
        or type(contract["quantizers"]) is not int
        or not 1 <= contract["quantizers"] <= 64
        or type(contract["codebook_size"]) is not int
        or not 2 <= contract["codebook_size"] <= 65_536
        or not isinstance(contract["model_type"], str)
    ):
        _fail("TOKENIZER_CONTRACT_INVALID", "speech tokenizer config contract is unsafe")
    return contract


def _runtime_tokenizer_contract(tokenizer: Any) -> dict[str, Any]:
    encoder = getattr(tokenizer.config, "encoder_config", None)
    return {
        "model_type": getattr(tokenizer.config, "model_type", None),
        "input_sample_rate": int(getattr(tokenizer.config, "input_sample_rate", -1)),
        "encode_downsample_rate": int(tokenizer.model.get_encode_downsample_rate()),
        "quantizers": int(getattr(tokenizer.config, "encoder_valid_num_quantizers", -1)),
        "codebook_size": int(getattr(encoder, "codebook_size", -1)),
    }


def _start_worker_watchdog(
    *,
    progress_jsonl: Path,
    failure_directory: Path,
) -> tuple[dict[str, Any], threading.Lock, threading.Event, threading.Thread]:
    state_lock = threading.Lock()
    stopped = threading.Event()
    now = time.monotonic()
    initial_memory = _system_memory_probe()
    state: dict[str, Any] = {
        "process_started": now,
        "phase": "tokenizer_load",
        "phase_started": now,
        "sample_index": None,
        "maximum_rss_bytes": 0,
        "minimum_free_ram_bytes": initial_memory["physical_available_bytes"],
        "minimum_commit_headroom_bytes": initial_memory["commit_headroom_bytes"],
        "maximum_consecutive_low_physical_samples": 0,
    }

    def monitor() -> None:
        consecutive_low_physical = 0
        while not stopped.wait(WATCHDOG_INTERVAL_SECONDS):
            observed_at = time.monotonic()
            rss = _process_working_set_bytes(os.getpid())
            try:
                memory = _system_memory_probe()
                consecutive_low_physical, memory_reason = _runtime_memory_violation(
                    memory, consecutive_low_physical
                )
            except BaseException as exc:
                memory = None
                memory_reason = f"runtime memory observation failed: {type(exc).__name__}"
            with state_lock:
                if rss is not None:
                    state["maximum_rss_bytes"] = max(int(state["maximum_rss_bytes"]), rss)
                if memory is not None:
                    state["minimum_free_ram_bytes"] = min(
                        int(state["minimum_free_ram_bytes"]), memory["physical_available_bytes"]
                    )
                    state["minimum_commit_headroom_bytes"] = min(
                        int(state["minimum_commit_headroom_bytes"]), memory["commit_headroom_bytes"]
                    )
                state["maximum_consecutive_low_physical_samples"] = max(
                    int(state["maximum_consecutive_low_physical_samples"]), consecutive_low_physical
                )
                snapshot = dict(state)
            phase_elapsed = observed_at - float(snapshot["phase_started"])
            total_elapsed = observed_at - float(snapshot["process_started"])
            reason: str | None = None
            if rss is None:
                reason = "worker working set observation failed"
            elif memory_reason is not None:
                reason = memory_reason
            elif rss > MAXIMUM_WORKER_RSS_BYTES:
                reason = "worker working set exceeded 5 GiB"
            elif total_elapsed > MAXIMUM_TOTAL_PREPARE_SECONDS:
                reason = "worker exceeded total preparation timeout"
            elif snapshot["phase"] == "tokenizer_load" and phase_elapsed > MAXIMUM_TOKENIZER_LOAD_SECONDS:
                reason = "speech tokenizer load exceeded 300 seconds"
            elif snapshot["phase"] == "sample_encode" and phase_elapsed > MAXIMUM_SAMPLE_ENCODE_SECONDS:
                reason = "single sample encode exceeded 120 seconds"
            if reason is None:
                continue
            failure = {
                "schema": "mentorflow.qwen3-tts-cpu-prepare-watchdog-failure.v1",
                "status": "failed",
                "failed_at_utc": datetime.now(timezone.utc).isoformat(),
                "reason": reason,
                "phase": snapshot["phase"],
                "sample_index": snapshot["sample_index"],
                "phase_elapsed_seconds": round(phase_elapsed, 3),
                "total_elapsed_seconds": round(total_elapsed, 3),
                "physical_available_bytes": None if memory is None else memory["physical_available_bytes"],
                "commit_headroom_bytes": None if memory is None else memory["commit_headroom_bytes"],
                "consecutive_low_physical_samples": consecutive_low_physical,
                "worker_rss_bytes": rss,
                "maximum_worker_rss_bytes": MAXIMUM_WORKER_RSS_BYTES,
            }
            try:
                _exclusive_write(failure_directory / "WORKER_WATCHDOG_FAILURE.json", _pretty_json(failure))
                _append_event(progress_jsonl, {"event": "watchdog_failure", **failure})
            finally:
                os._exit(WATCHDOG_EXIT_CODE)

    thread = threading.Thread(target=monitor, name="cpu-prepare-watchdog", daemon=True)
    thread.start()
    return state, state_lock, stopped, thread


def _load_cpu_tokenizer(torch: Any, tokenizer_root: Path) -> Any:
    from qwen_tts import Qwen3TTSTokenizer
    from qwen_tts.core import (
        Qwen3TTSTokenizerV1Config,
        Qwen3TTSTokenizerV1Model,
        Qwen3TTSTokenizerV2Config,
        Qwen3TTSTokenizerV2Model,
    )
    from transformers import AutoConfig, AutoFeatureExtractor, AutoModel

    AutoConfig.register("qwen3_tts_tokenizer_25hz", Qwen3TTSTokenizerV1Config, exist_ok=True)
    AutoModel.register(Qwen3TTSTokenizerV1Config, Qwen3TTSTokenizerV1Model, exist_ok=True)
    AutoConfig.register("qwen3_tts_tokenizer_12hz", Qwen3TTSTokenizerV2Config, exist_ok=True)
    AutoModel.register(Qwen3TTSTokenizerV2Config, Qwen3TTSTokenizerV2Model, exist_ok=True)

    tokenizer = Qwen3TTSTokenizer()
    tokenizer.feature_extractor = AutoFeatureExtractor.from_pretrained(
        str(tokenizer_root),
        local_files_only=True,
        trust_remote_code=False,
    )
    tokenizer.model = AutoModel.from_pretrained(
        str(tokenizer_root),
        device_map={"": "cpu"},
        dtype=torch.float32,
        attn_implementation="sdpa",
        use_kernels=False,
        local_files_only=True,
        trust_remote_code=False,
        use_safetensors=True,
        weights_only=True,
    )
    tokenizer.config = tokenizer.model.config
    tokenizer.device = torch.device("cpu")
    return tokenizer


def _validate_code_matrix(codes: Any, contract: Mapping[str, Any], label: str) -> tuple[int, int, int]:
    quantizers = int(contract["quantizers"])
    codebook_size = int(contract["codebook_size"])
    if not isinstance(codes, list) or not codes:
        _fail("TOKENIZER_OUTPUT_INVALID", f"{label} has no audio-code frames")
    minimum = codebook_size
    maximum = -1
    for frame in codes:
        if not isinstance(frame, list) or len(frame) != quantizers:
            _fail("TOKENIZER_OUTPUT_INVALID", f"{label} audio-code quantizer shape is invalid")
        for code in frame:
            if type(code) is not int or not 0 <= code < codebook_size:
                _fail("TOKENIZER_OUTPUT_INVALID", f"{label} audio-code value is outside model contract")
            minimum = min(minimum, code)
            maximum = max(maximum, code)
    return len(codes), minimum, maximum


def _worker(plan_path: Path, input_jsonl: Path, output_jsonl: Path, progress_jsonl: Path) -> dict[str, Any]:
    plan = _load_plan(plan_path)
    prepare_root = Path(str(plan["paths"]["prepare_root"]))
    expected_worker_paths = {
        "plan": prepare_root / "PREPARE_PLAN.json",
        "input": prepare_root / "input" / "train_raw.jsonl",
        "output": prepare_root
        / "attempts"
        / "attempt-0001"
        / "prepared.partial"
        / "train_with_codes.jsonl",
        "progress": prepare_root / "attempts" / "attempt-0001" / "PROGRESS.jsonl",
    }
    supplied_worker_paths = {
        "plan": plan_path,
        "input": input_jsonl,
        "output": output_jsonl,
        "progress": progress_jsonl,
    }
    if any(
        not path.is_absolute() or not _same_windows_path(path, expected_worker_paths[name])
        for name, path in supplied_worker_paths.items()
    ):
        _fail("WORKER_PATH_INVALID", "CPU worker paths differ from immutable generation layout")
    _plain_directory_chain(prepare_root, "CPU prepare generation root")
    _assert_worker_environment(plan)
    _block_python_network()
    input_jsonl = _plain_file(input_jsonl, "raw CPU prepare input")
    if _sha256_file(input_jsonl) != plan["input"]["sha256"]:
        _fail("INPUT_DRIFT", "raw CPU prepare input changed")
    output_jsonl.parent.mkdir(parents=True, exist_ok=False)
    progress_jsonl.parent.mkdir(parents=True, exist_ok=True)
    worker_start_memory = _system_memory_probe()
    worker_start_reasons = []
    if worker_start_memory["physical_available_bytes"] < MINIMUM_ADMISSION_PHYSICAL_AVAILABLE_BYTES:
        worker_start_reasons.append("physical available memory is below 4 GiB")
    if worker_start_memory["commit_headroom_bytes"] < MINIMUM_ADMISSION_COMMIT_HEADROOM_BYTES:
        worker_start_reasons.append("commit headroom is below 9 GiB")
    _append_event(
        progress_jsonl,
        {
            "event": "worker_start",
            "at_utc": datetime.now(timezone.utc).isoformat(),
            **worker_start_memory,
        },
    )
    if worker_start_reasons:
        _fail(
            "CPU_PREPARE_RESOURCE_GATE",
            "worker start adaptive memory gate is NO_GO",
            reasons=worker_start_reasons,
        )
    tokenizer_root = Path(str(plan["paths"]["tokenizer_root"]))
    files, tree = _tokenizer_inventory(tokenizer_root)
    if files != plan["tokenizer"]["files"] or tree != plan["tokenizer"]["tree_sha256"]:
        _fail("TOKENIZER_DRIFT", "local tokenizer changed after CPU prepare planning")
    import numpy as np
    import torch

    torch.set_num_threads(int(plan["cpu"]["torch_threads"]))
    torch.set_num_interop_threads(1)
    if hasattr(torch.backends, "cuda"):
        torch.backends.cuda.enable_flash_sdp(False)
        torch.backends.cuda.enable_mem_efficient_sdp(False)
        torch.backends.cuda.enable_math_sdp(True)
    if torch.cuda.is_available() or torch.cuda.device_count() != 0:
        _fail("GPU_NOT_DISABLED", "torch still exposes CUDA inside the CPU-only worker")
    watchdog_state, watchdog_lock, watchdog_stop, watchdog_thread = _start_worker_watchdog(
        progress_jsonl=progress_jsonl,
        failure_directory=output_jsonl.parent,
    )
    _append_event(
        progress_jsonl,
        {
            "event": "tokenizer_load_start",
            "at_utc": datetime.now(timezone.utc).isoformat(),
            **_system_memory_probe(),
        },
    )
    tokenizer = _load_cpu_tokenizer(torch, tokenizer_root)
    runtime_contract = _runtime_tokenizer_contract(tokenizer)
    if runtime_contract != plan["tokenizer"].get("contract"):
        _fail("TOKENIZER_CONTRACT_INVALID", "loaded tokenizer contract differs from pinned config")
    parameter_devices = sorted({str(parameter.device) for parameter in tokenizer.model.parameters()})
    buffer_devices = sorted({str(buffer.device) for buffer in tokenizer.model.buffers()})
    floating_dtypes = sorted(
        {str(value.dtype) for value in itertools.chain(tokenizer.model.parameters(), tokenizer.model.buffers()) if value.is_floating_point()}
    )
    if (
        parameter_devices != ["cpu"]
        or any(device != "cpu" for device in buffer_devices)
        or floating_dtypes != ["torch.float32"]
        or bool(getattr(tokenizer.model, "use_kernels", False))
        or getattr(tokenizer.config, "_attn_implementation", None) != "sdpa"
    ):
        _fail("CPU_MODEL_POLICY_INVALID", "speech tokenizer is not strict CPU float32 SDPA/no-kernels")
    with watchdog_lock:
        watchdog_state["phase"] = "input_validation"
        watchdog_state["phase_started"] = time.monotonic()
    _append_event(
        progress_jsonl,
        {
            "event": "tokenizer_load_completed",
            "at_utc": datetime.now(timezone.utc).isoformat(),
            "free_ram_bytes": _free_ram_bytes(),
            "parameter_devices": parameter_devices,
            "buffer_devices": buffer_devices,
            "floating_dtypes": floating_dtypes,
            "tokenizer_contract": runtime_contract,
        },
    )
    dataset_root = _plain_dir(Path(str(plan["paths"]["dataset_root"])), "admitted dataset root")
    declared = plan["input"].get("train_audio")
    reference = plan["input"].get("reference")
    expected_rows = int(plan["input"]["train_rows"])
    if not isinstance(declared, list) or len(declared) != expected_rows or not isinstance(reference, Mapping):
        _fail("INPUT_INVALID", "CPU prepare path pins are malformed")
    reference_path = _local_dataset_wav(
        reference.get("absolute_path"),
        dataset_root=dataset_root,
        expected=reference,
        label="CPU prepare reference audio",
    )
    _inspect_pcm16_wav(reference_path, expected=reference, label="CPU prepare reference audio")
    initial_runtime_memory = _system_memory_probe()
    minimum_free_ram = initial_runtime_memory["physical_available_bytes"]
    minimum_commit_headroom = initial_runtime_memory["commit_headroom_bytes"]
    started = time.monotonic()
    completed_rows = 0
    code_frames_total = 0
    code_frames_minimum: int | None = None
    code_frames_maximum = 0
    code_value_minimum = int(runtime_contract["codebook_size"])
    code_value_maximum = -1
    empirical_offsets: set[int] = set()
    prepared_bytes = 0
    prepared_digest = hashlib.sha256()
    relation_digest = hashlib.sha256()
    with output_jsonl.open("xb") as output:
        for line_number, row in _iter_bounded_jsonl(
            input_jsonl,
            "raw CPU prepare input",
            maximum_total_bytes=MAXIMUM_RAW_JSONL_BYTES,
            maximum_line_bytes=MAXIMUM_RAW_JSONL_LINE_BYTES,
        ):
            index = line_number
            if index > expected_rows:
                _fail("INPUT_INVALID", "raw CPU prepare contains more rows than its plan")
            pin = declared[index - 1]
            if not isinstance(pin, Mapping) or set(row) != {"audio", "text", "ref_audio"}:
                _fail("INPUT_INVALID", f"CPU prepare row {index} fields/pin are malformed")
            if row.get("ref_audio") != str(reference_path):
                _fail("INPUT_DRIFT", f"CPU prepare row {index} reference path differs from its signed pin")
            _validate_text(row.get("text"), pin.get("text_sha256"), f"CPU prepare row {index} text")
            audio_path = _local_dataset_wav(
                row.get("audio"),
                dataset_root=dataset_root,
                expected=pin,
                label=f"CPU prepare row {index} audio",
            )
            audio_frames, pcm_bytes = _inspect_pcm16_wav(
                audio_path,
                expected=pin,
                label=f"CPU prepare row {index} audio",
                read_samples=True,
            )
            assert pcm_bytes is not None
            before_memory = _system_memory_probe()
            minimum_free_ram = min(minimum_free_ram, before_memory["physical_available_bytes"])
            minimum_commit_headroom = min(
                minimum_commit_headroom, before_memory["commit_headroom_bytes"]
            )
            if before_memory["commit_headroom_bytes"] < MINIMUM_RUNTIME_COMMIT_HEADROOM_BYTES:
                _fail("CPU_PREPARE_RESOURCE_GATE", "runtime commit headroom dropped below 2 GiB")
            waveform = np.frombuffer(pcm_bytes, dtype="<i2").astype(np.float32) / np.float32(32768.0)
            del pcm_bytes
            with watchdog_lock:
                watchdog_state["phase"] = "sample_encode"
                watchdog_state["phase_started"] = time.monotonic()
                watchdog_state["sample_index"] = index
            with torch.inference_mode():
                encoded = tokenizer.encode([waveform], sr=24_000)
            del waveform
            with watchdog_lock:
                watchdog_state["phase"] = "sample_commit"
                watchdog_state["phase_started"] = time.monotonic()
            if len(encoded.audio_codes) != 1:
                _fail("TOKENIZER_OUTPUT_INVALID", "speech tokenizer returned an unexpected batch")
            source_tensor = encoded.audio_codes[0]
            if (
                source_tensor.dtype != torch.long
                or source_tensor.device.type != "cpu"
                or source_tensor.ndim != 2
                or int(source_tensor.shape[1]) != int(runtime_contract["quantizers"])
            ):
                _fail("TOKENIZER_OUTPUT_INVALID", "speech tokenizer tensor shape differs from loaded contract")
            tensor = source_tensor.detach()
            codes = tensor.tolist()
            code_frames, sample_code_minimum, sample_code_maximum = _validate_code_matrix(
                codes, runtime_contract, f"CPU prepare row {index}"
            )
            expected_base = math.ceil(audio_frames / int(runtime_contract["encode_downsample_rate"]))
            offset = code_frames - expected_base
            if abs(offset) > 1:
                _fail("TOKENIZER_OUTPUT_INVALID", "audio-code frames are implausible for loaded tokenizer contract")
            empirical_offsets.add(offset)
            relation_digest.update(
                f"{pin['sha256']}\0{audio_frames}\0{code_frames}\0{offset}\n".encode("ascii")
            )
            row = dict(row)
            row["audio_codes"] = codes
            raw = json.dumps(row, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"
            prepared_bytes += len(raw)
            if len(raw) > MAXIMUM_PREPARED_JSONL_LINE_BYTES or prepared_bytes > MAXIMUM_PREPARED_JSONL_BYTES:
                _fail("PREPARED_TOO_LARGE", "prepared JSONL exceeds its hard row/total byte limit")
            output.write(raw)
            prepared_digest.update(raw)
            output.flush()
            os.fsync(output.fileno())
            completed_rows = index
            code_frames_total += code_frames
            code_frames_minimum = (
                code_frames if code_frames_minimum is None else min(code_frames_minimum, code_frames)
            )
            code_frames_maximum = max(code_frames_maximum, code_frames)
            code_value_minimum = min(code_value_minimum, sample_code_minimum)
            code_value_maximum = max(code_value_maximum, sample_code_maximum)
            after_memory = _system_memory_probe()
            minimum_free_ram = min(minimum_free_ram, after_memory["physical_available_bytes"])
            minimum_commit_headroom = min(
                minimum_commit_headroom, after_memory["commit_headroom_bytes"]
            )
            if after_memory["commit_headroom_bytes"] < MINIMUM_RUNTIME_COMMIT_HEADROOM_BYTES:
                _fail("CPU_PREPARE_RESOURCE_GATE", "runtime commit headroom dropped below 2 GiB")
            _append_event(
                progress_jsonl,
                {
                    "event": "sample_completed",
                    "at_utc": datetime.now(timezone.utc).isoformat(),
                    "completed": index,
                    "total": expected_rows,
                    "sample_audio_sha256": pin["sha256"],
                    "code_frames": code_frames,
                    "code_frame_relation_offset": offset,
                    **after_memory,
                    "worker_rss_bytes": _process_working_set_bytes(os.getpid()),
                    "elapsed_seconds": round(time.monotonic() - started, 3),
                },
            )
            with watchdog_lock:
                watchdog_state["phase"] = "between_samples"
                watchdog_state["phase_started"] = time.monotonic()
                watchdog_state["sample_index"] = None
            del encoded, source_tensor, tensor, codes, row
            gc.collect()
    if completed_rows != expected_rows:
        _fail("INPUT_INVALID", "raw CPU prepare row count differs from plan")
    watchdog_stop.set()
    watchdog_thread.join(timeout=2)
    with watchdog_lock:
        watchdog_metrics = dict(watchdog_state)
    del tokenizer
    gc.collect()
    result = {
        "schema": WORKER_RESULT_SCHEMA,
        "status": "completed",
        "plan_sha256": plan["plan_sha256"],
        "rows": completed_rows,
        "minimum_free_ram_bytes": min(minimum_free_ram, int(watchdog_metrics["minimum_free_ram_bytes"])),
        "minimum_commit_headroom_bytes": min(
            minimum_commit_headroom, int(watchdog_metrics["minimum_commit_headroom_bytes"])
        ),
        "maximum_consecutive_low_physical_samples": int(
            watchdog_metrics["maximum_consecutive_low_physical_samples"]
        ),
        "maximum_worker_rss_bytes": int(watchdog_metrics["maximum_rss_bytes"]),
        "code_frames_total": code_frames_total,
        "code_frames_minimum": code_frames_minimum,
        "code_frames_maximum": code_frames_maximum,
        "code_value_minimum": code_value_minimum,
        "code_value_maximum": code_value_maximum,
        "empirical_code_frame_offsets": sorted(empirical_offsets),
        "prepared_bytes": prepared_bytes,
        "prepared_sha256": prepared_digest.hexdigest(),
        "sample_relation_sha256": relation_digest.hexdigest(),
        "tokenizer_contract": runtime_contract,
        "torch_cuda_available": False,
        "torch_cuda_device_count": 0,
        "parameter_devices": parameter_devices,
        "buffer_devices": buffer_devices,
        "floating_dtypes": floating_dtypes,
        "batch_size": 1,
        "network_socket_connections_blocked": True,
        "network_urlopen_blocked": True,
        "network_audit_hook_enabled": True,
        "network_used": False,
        "model_loaded": "speech_tokenizer_only",
        "training_model_loaded": False,
        "training_started": False,
        "elapsed_seconds": round(time.monotonic() - started, 3),
    }
    _exclusive_write(output_jsonl.parent / "WORKER_RESULT.json", _pretty_json(result))
    _append_event(
        progress_jsonl,
        {"event": "worker_completed", "at_utc": datetime.now(timezone.utc).isoformat(), **result},
    )
    return result


def validate_prepared_output(
    *,
    raw_jsonl: Path,
    prepared_jsonl: Path,
    worker_result_path: Path,
    plan: Mapping[str, Any],
) -> dict[str, Any]:
    worker_result = _read_json(worker_result_path, "CPU prepare worker result")
    contract = worker_result.get("tokenizer_contract")
    expected_contract = plan.get("tokenizer", {}).get("contract")
    worker_minimum_free_ram = worker_result.get("minimum_free_ram_bytes")
    worker_minimum_commit_headroom = worker_result.get("minimum_commit_headroom_bytes")
    worker_maximum_low_physical = worker_result.get("maximum_consecutive_low_physical_samples")
    worker_maximum_rss = worker_result.get("maximum_worker_rss_bytes")
    if (
        worker_result.get("schema") != WORKER_RESULT_SCHEMA
        or worker_result.get("status") != "completed"
        or worker_result.get("plan_sha256") != plan["plan_sha256"]
        or worker_result.get("torch_cuda_available") is not False
        or worker_result.get("torch_cuda_device_count") != 0
        or worker_result.get("training_model_loaded") is not False
        or worker_result.get("training_started") is not False
        or worker_result.get("network_used") is not False
        or worker_result.get("network_socket_connections_blocked") is not True
        or worker_result.get("network_urlopen_blocked") is not True
        or worker_result.get("network_audit_hook_enabled") is not True
        or worker_result.get("batch_size") != 1
        or worker_result.get("floating_dtypes") != ["torch.float32"]
        or contract != expected_contract
        or not isinstance(contract, Mapping)
        or type(worker_minimum_free_ram) is not int
        or worker_minimum_free_ram <= 0
        or type(worker_minimum_commit_headroom) is not int
        or worker_minimum_commit_headroom < MINIMUM_RUNTIME_COMMIT_HEADROOM_BYTES
        or type(worker_maximum_low_physical) is not int
        or not 0 <= worker_maximum_low_physical < RUNTIME_LOW_PHYSICAL_CONSECUTIVE_SAMPLES
        or type(worker_maximum_rss) is not int
        or not 0 < worker_maximum_rss <= MAXIMUM_WORKER_RSS_BYTES
    ):
        _fail("PREPARED_INVALID", "CPU prepare worker result/policy is invalid")
    expected_rows = int(plan["input"]["train_rows"])
    dataset_root = _plain_dir(Path(str(plan["paths"]["dataset_root"])), "admitted dataset root")
    declared = plan["input"].get("train_audio")
    reference = plan["input"].get("reference")
    if not isinstance(declared, list) or len(declared) != expected_rows or not isinstance(reference, Mapping):
        _fail("PREPARED_INVALID", "CPU prepare plan input pins are malformed")
    reference_path = _local_dataset_wav(
        reference.get("absolute_path"),
        dataset_root=dataset_root,
        expected=reference,
        label="prepared-output reference audio",
    )
    _inspect_pcm16_wav(reference_path, expected=reference, label="prepared-output reference audio")
    sentinel = object()
    raw_rows = _iter_bounded_jsonl(
        raw_jsonl,
        "raw CPU prepare input",
        maximum_total_bytes=MAXIMUM_RAW_JSONL_BYTES,
        maximum_line_bytes=MAXIMUM_RAW_JSONL_LINE_BYTES,
    )
    prepared_rows = _iter_bounded_jsonl(
        prepared_jsonl,
        "prepared CPU codes",
        maximum_total_bytes=MAXIMUM_PREPARED_JSONL_BYTES,
        maximum_line_bytes=MAXIMUM_PREPARED_JSONL_LINE_BYTES,
    )
    rows = 0
    code_frames_total = 0
    code_frames_minimum: int | None = None
    code_frames_maximum = 0
    code_value_minimum = int(contract["codebook_size"])
    code_value_maximum = -1
    empirical_offsets: set[int] = set()
    relation_digest = hashlib.sha256()
    for pair in itertools.zip_longest(raw_rows, prepared_rows, fillvalue=sentinel):
        raw_item, prepared_item = pair
        if raw_item is sentinel or prepared_item is sentinel:
            _fail("PREPARED_INVALID", "raw/prepared JSONL row counts differ")
        assert raw_item is not sentinel and prepared_item is not sentinel
        raw_line, raw = raw_item
        prepared_line, prepared = prepared_item
        if raw_line != prepared_line:
            _fail("PREPARED_INVALID", "raw/prepared JSONL line order differs")
        index = raw_line
        if index > expected_rows:
            _fail("PREPARED_INVALID", "prepared JSONL has more rows than its plan")
        pin = declared[index - 1]
        if not isinstance(pin, Mapping) or set(raw) != {"audio", "text", "ref_audio"}:
            _fail("PREPARED_INVALID", f"raw row {index} fields/pin are malformed")
        if raw.get("ref_audio") != str(reference_path):
            _fail("PREPARED_INVALID", f"raw row {index} reference differs from signed pin")
        _validate_text(raw.get("text"), pin.get("text_sha256"), f"raw row {index} text")
        audio_path = _local_dataset_wav(
            raw.get("audio"),
            dataset_root=dataset_root,
            expected=pin,
            label=f"raw row {index} audio",
        )
        if set(prepared) != set(raw) | {"audio_codes"}:
            _fail("PREPARED_INVALID", f"prepared row {index} fields are invalid")
        if any(prepared.get(key) != value for key, value in raw.items()):
            _fail("PREPARED_INVALID", f"prepared row {index} changed admitted input fields")
        codes = prepared.get("audio_codes")
        try:
            code_frames, sample_minimum, sample_maximum = _validate_code_matrix(
                codes, contract, f"prepared row {index}"
            )
        except CpuPrepareError as exc:
            _fail("PREPARED_INVALID", f"prepared row {index} audio code shape/range is invalid", cause=exc.code)
        audio_frames, _ = _inspect_pcm16_wav(
            audio_path,
            expected=pin,
            label=f"prepared row {index} audio",
        )
        expected_base = math.ceil(audio_frames / int(contract["encode_downsample_rate"]))
        offset = code_frames - expected_base
        if abs(offset) > 1:
            _fail("PREPARED_INVALID", f"prepared row {index} code length is inconsistent with audio duration")
        empirical_offsets.add(offset)
        relation_digest.update(
            f"{pin['sha256']}\0{audio_frames}\0{code_frames}\0{offset}\n".encode("ascii")
        )
        rows += 1
        code_frames_total += code_frames
        code_frames_minimum = (
            code_frames if code_frames_minimum is None else min(code_frames_minimum, code_frames)
        )
        code_frames_maximum = max(code_frames_maximum, code_frames)
        code_value_minimum = min(code_value_minimum, sample_minimum)
        code_value_maximum = max(code_value_maximum, sample_maximum)
    if (
        rows != expected_rows
        or worker_result.get("rows") != rows
        or worker_result.get("code_frames_total") != code_frames_total
        or worker_result.get("code_frames_minimum") != code_frames_minimum
        or worker_result.get("code_frames_maximum") != code_frames_maximum
        or worker_result.get("code_value_minimum") != code_value_minimum
        or worker_result.get("code_value_maximum") != code_value_maximum
        or worker_result.get("empirical_code_frame_offsets") != sorted(empirical_offsets)
        or worker_result.get("prepared_bytes") != prepared_jsonl.stat().st_size
        or worker_result.get("prepared_sha256") != _sha256_file(prepared_jsonl)
        or worker_result.get("sample_relation_sha256") != relation_digest.hexdigest()
    ):
        _fail("PREPARED_INVALID", "worker aggregates/row count differ from streamed prepared JSONL")
    return {
        "rows": rows,
        "bytes": prepared_jsonl.stat().st_size,
        "sha256": _sha256_file(prepared_jsonl),
        "code_frames_total": code_frames_total,
        "code_frames_minimum": code_frames_minimum,
        "code_frames_maximum": code_frames_maximum,
        "code_value_minimum": code_value_minimum,
        "code_value_maximum": code_value_maximum,
        "empirical_code_frame_offsets": sorted(empirical_offsets),
        "sample_relation_sha256": relation_digest.hexdigest(),
        "tokenizer_contract": dict(contract),
        "worker_result": worker_result,
    }


def _read_utf8_tail(path: Path, maximum_bytes: int = 16 * 1024) -> str:
    path = _plain_file(path, "CPU prepare worker log")
    with path.open("rb") as handle:
        size = path.stat().st_size
        handle.seek(max(0, size - maximum_bytes))
        return handle.read(maximum_bytes).decode("utf-8", errors="replace")


def _cache_environment(root: Path, training_root: Path) -> dict[str, str]:
    paths = {
        "HF_HOME": root / "huggingface",
        "HF_DATASETS_CACHE": root / "datasets",
        "TRANSFORMERS_CACHE": root / "transformers",
        "TORCH_HOME": root / "torch",
        "PIP_CACHE_DIR": root / "pip",
    }
    _ensure_plain_subdirectory(training_root, root, "CPU prepare cache root")
    for path in paths.values():
        _ensure_plain_subdirectory(training_root, path, f"CPU prepare cache directory {path.name}")
    return {**OFFLINE_VALUES, **{name: str(path) for name, path in paths.items()}}


def _preparation_input_metadata(
    manifest: Mapping[str, Any],
    dataset_root: Path,
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    reference = manifest.get("reference")
    train = manifest.get("train")
    if not isinstance(reference, Mapping) or not isinstance(train, list) or not train:
        _fail("INPUT_INVALID", "admitted manifest train/reference are malformed")

    def pin(item: Mapping[str, Any], label: str) -> dict[str, Any]:
        value = {
            "absolute_path": str(dataset_root / Path(str(item.get("audio")))),
            "bytes": item.get("bytes"),
            "sha256": item.get("sha256"),
            "text_sha256": item.get("text_sha256"),
            "duration_ms": item.get("duration_ms"),
        }
        path = _local_dataset_wav(
            value["absolute_path"],
            dataset_root=dataset_root,
            expected=value,
            label=label,
        )
        _inspect_pcm16_wav(path, expected=value, label=label)
        if int(value["bytes"]) > MAXIMUM_WAV_BYTES or int(value["duration_ms"]) > MAXIMUM_AUDIO_DURATION_MS:
            _fail("INPUT_INVALID", f"{label} exceeds CPU prepare audio bounds")
        return value

    reference_pin = pin(reference, "manifest reference audio")
    train_pins: list[dict[str, Any]] = []
    digest = hashlib.sha256()
    total_bytes = 0
    maximum_line_bytes = 0
    for index, item in enumerate(train, start=1):
        if not isinstance(item, Mapping):
            _fail("INPUT_INVALID", f"manifest train item {index} is malformed")
        item_pin = pin(item, f"manifest train item {index} audio")
        text = _validate_text(item.get("text"), item_pin["text_sha256"], f"manifest train item {index} text")
        raw = _canonical_json(
            {
                "audio": item_pin["absolute_path"],
                "text": text,
                "ref_audio": reference_pin["absolute_path"],
            }
        ) + b"\n"
        if len(raw) > MAXIMUM_RAW_JSONL_LINE_BYTES:
            _fail("INPUT_INVALID", f"manifest train item {index} exceeds raw JSONL line limit")
        total_bytes += len(raw)
        if total_bytes > MAXIMUM_RAW_JSONL_BYTES:
            _fail("INPUT_INVALID", "manifest train input exceeds raw JSONL total limit")
        maximum_line_bytes = max(maximum_line_bytes, len(raw))
        digest.update(raw)
        train_pins.append(item_pin)
    return train_pins, reference_pin, {
        "rows": len(train_pins),
        "bytes": total_bytes,
        "maximum_line_bytes": maximum_line_bytes,
        "sha256": digest.hexdigest(),
    }


def _write_raw_input_stream(
    path: Path,
    manifest: Mapping[str, Any],
    train_pins: list[Mapping[str, Any]],
    reference_pin: Mapping[str, Any],
    expected_metadata: Mapping[str, Any],
) -> None:
    digest = hashlib.sha256()
    total_bytes = 0
    rows = 0
    with path.open("xb") as output:
        for index, (item, item_pin) in enumerate(zip(manifest["train"], train_pins), start=1):
            text = _validate_text(item.get("text"), item_pin["text_sha256"], f"raw input row {index} text")
            raw = _canonical_json(
                {
                    "audio": item_pin["absolute_path"],
                    "text": text,
                    "ref_audio": reference_pin["absolute_path"],
                }
            ) + b"\n"
            if len(raw) > MAXIMUM_RAW_JSONL_LINE_BYTES:
                _fail("INPUT_INVALID", f"raw input row {index} exceeds line limit during write")
            output.write(raw)
            digest.update(raw)
            total_bytes += len(raw)
            rows += 1
        output.flush()
        os.fsync(output.fileno())
    actual = {"rows": rows, "bytes": total_bytes, "sha256": digest.hexdigest()}
    if any(actual[key] != expected_metadata[key] for key in actual):
        _fail("INPUT_DRIFT", "streamed raw input differs from precomputed metadata")


def _admission_validation_sha256(admission: Mapping[str, Any]) -> str:
    return _sha256_bytes(_canonical_json(admission))


def _build_plan(
    *,
    dataset_generation_id: str,
    prepare_generation_id: str,
    dataset_root: Path,
    registry: Path,
    prepare_root: Path,
    admission: Mapping[str, Any],
    runtime: Mapping[str, Any],
    tokenizer_files: list[dict[str, Any]],
    tokenizer_tree: str,
    tokenizer_contract: Mapping[str, Any],
    train_pins: list[Mapping[str, Any]],
    reference_pin: Mapping[str, Any],
    raw_metadata: Mapping[str, Any],
    resource: Mapping[str, Any],
    offline_environment: Mapping[str, str],
) -> dict[str, Any]:
    manifest = admission["manifest_value"]
    plan = {
        "schema": PLAN_SCHEMA,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "dataset_generation_id": dataset_generation_id,
        "prepare_generation_id": prepare_generation_id,
        "paths": {
            "dataset_root": str(dataset_root),
            "qa_registry": str(registry),
            "prepare_root": str(prepare_root),
            "tokenizer_root": str(ORCH.DEFAULT_MODEL_ROOT / "speech_tokenizer"),
            "python_executable": str(ORCH.DEFAULT_PYTHON),
        },
        "dataset": {
            key: admission[key]
            for key in (
                "generation_id",
                "approval_payload_sha256",
                "approval_sha256",
                "manifest_sha256",
                "tree_sha256",
                "qa_registry_sha256",
                "qa_signer_key_id",
                "valid_from",
                "valid_until",
                "training_scope",
                "train_samples",
                "validation_samples",
                "heldout_samples",
                "reference_sha256",
                "reference_text_sha256",
            )
        },
        "voice_profile": manifest["voice_profile"],
        "input": {
            "train_rows": admission["train_samples"],
            "source_total_samples": admission["train_samples"]
            + admission["validation_samples"]
            + admission["heldout_samples"]
            + 1,
            "split_counts": {
                "train": admission["train_samples"],
                "validation": admission["validation_samples"],
                "heldout": admission["heldout_samples"],
                "reference": 1,
            },
            "bytes": raw_metadata["bytes"],
            "maximum_line_bytes": raw_metadata["maximum_line_bytes"],
            "sha256": raw_metadata["sha256"],
            "train_audio": list(train_pins),
            "reference": dict(reference_pin),
        },
        "tokenizer": {
            "model_id": ORCH.BASE_MODEL_ID,
            "revision": ORCH.BASE_MODEL_REVISION,
            "files": tokenizer_files,
            "tree_sha256": tokenizer_tree,
            "contract": dict(tokenizer_contract),
        },
        "runtime": runtime,
        "official_prepare_source": {
            "repository": ORCH.SOURCE_REPOSITORY,
            "commit": ORCH.SOURCE_COMMIT,
            "path": "finetuning/prepare_data.py",
            "sha256": ORCH.SOURCE_FILES["finetuning/prepare_data.py"][1],
            "compatibility_changes": [
                "device is fixed to CPU",
                "batch size is reduced from 32 to 1",
                "local_files_only is explicit for model weights",
                "Python socket connections are blocked",
                "urllib open and Python socket audit events are blocked",
                "RAM is gated before/throughout execution",
                "audio/text/JSONL/RSS/time limits are enforced",
                "output is staged and atomically committed",
            ],
        },
        "device_policy": {
            "device": "cpu",
            "cuda_visible_devices": "",
            "gpu_use_allowed": False,
            "batch_size": 1,
            "dtype": "float32",
            "device_map": {"": "cpu"},
            "attention_implementation": "sdpa",
            "transformers_kernels": False,
        },
        "cpu": {"torch_threads": max(1, min(8, (os.cpu_count() or 2) // 2))},
        "network_policy": {
            "mode": "zero-traffic-local-only",
            "python_socket_connections_blocked": True,
            "python_urlopen_blocked": True,
            "python_network_audit_hook": True,
            "downloads_allowed": False,
        },
        "limits": _limits_policy(),
        "offline_environment": dict(offline_environment),
        "resource_admission": dict(resource),
        "artifact_policy": {
            "training_scope": ORCH.LOCAL_PRIVATE_TRAINING_SCOPE,
            "non_exportable": True,
            "public_release_eligible": False,
        },
        "worker_sha256": _sha256_file(Path(__file__).resolve()),
        "training_started": False,
    }
    plan["dataset"]["admission_validation_sha256"] = _admission_validation_sha256(admission)
    plan["plan_sha256"] = _sha256_bytes(_canonical_json(plan))
    return plan


def prepare_cpu_only(
    *,
    dataset_generation_id: str,
    prepare_generation_id: str,
    training_root: Path,
) -> dict[str, Any]:
    dataset_generation_id = ORCH._safe_id(dataset_generation_id, "dataset generation id")
    prepare_generation_id = ORCH._safe_id(prepare_generation_id, "prepare generation id")
    if not _same_windows_path(training_root, ORCH.TRAINING_ROOT):
        _fail("TRAINING_ROOT_INVALID", "production CPU prepare root is fixed to ORCH.TRAINING_ROOT")
    training_root = _plain_directory_chain(training_root, "training root")
    dataset_root = _plain_dir(
        training_root / "generations" / dataset_generation_id / "dataset", "admitted dataset"
    )
    registry = _plain_file(
        training_root / "private-trust" / dataset_generation_id / "qa-trust-registry.json",
        "local-private QA registry",
    )
    admission = ORCH.validate_dataset_qa(dataset_root, dataset_generation_id, registry)
    if admission.get("training_scope") != ORCH.LOCAL_PRIVATE_TRAINING_SCOPE:
        _fail("ADMISSION_SCOPE_INVALID", "CPU prepare requires the signed local-private admission")
    total_samples = int(admission["train_samples"])
    reference_bound_sha256 = str(admission["reference_sha256"])
    manifest = admission["manifest_value"]
    dataset_before_files, dataset_before_tree = ORCH._inventory(
        dataset_root, excluded=frozenset({ORCH.QA_APPROVAL_NAME.casefold()})
    )
    if dataset_before_tree != admission["tree_sha256"] or dataset_before_files != admission["files"]:
        _fail("DATASET_DRIFT", "admitted dataset inventory changed before CPU prepare")
    train_pins, reference_pin, raw_metadata = _preparation_input_metadata(manifest, dataset_root)
    if raw_metadata["rows"] != admission["train_samples"]:
        _fail("INPUT_INVALID", "streamed raw-input metadata row count differs from signed train split")
    tokenizer_root = ORCH.DEFAULT_MODEL_ROOT / "speech_tokenizer"
    tokenizer_files, tokenizer_tree = _tokenizer_inventory(tokenizer_root)
    tokenizer_contract = _tokenizer_contract_from_config(tokenizer_root)
    runtime = ORCH._runtime_probe(ORCH.DEFAULT_PYTHON)
    source = ORCH._validate_source(ORCH.DEFAULT_SOURCE_ROOT, ORCH.DEFAULT_SOURCE_RECEIPT)
    if not source:
        _fail("SOURCE_INVALID", "pinned official source validation returned empty evidence")
    memory_admission = _stable_launch_memory_gate()
    free_ram = int(memory_admission["minimum_physical_available_bytes"])
    disk = shutil.disk_usage(training_root)
    required_disk = MINIMUM_DISK_RESERVE_BYTES + 2 * sum(item["bytes"] for item in tokenizer_files)
    reasons: list[str] = []
    if disk.free < required_disk:
        reasons.append(f"free disk {disk.free} bytes is below {required_disk} bytes")
    resource = {
        "schema": "mentorflow.qwen3-tts-cpu-prepare-resource-gate.v2",
        "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
        "decision": "GO" if not reasons else "NO_GO",
        "adaptive_memory": memory_admission,
        "minimum_runtime_physical_available_bytes": MINIMUM_RUNTIME_PHYSICAL_AVAILABLE_BYTES,
        "minimum_runtime_commit_headroom_bytes": MINIMUM_RUNTIME_COMMIT_HEADROOM_BYTES,
        "disk_free_bytes": disk.free,
        "required_disk_bytes": required_disk,
        "gpu_temperature_gate_required": False,
        "gpu_use_allowed": False,
        "gpu_observation_only": ORCH._gpu_probe(),
        "reasons": reasons,
    }
    if reasons:
        _fail("CPU_PREPARE_RESOURCE_GATE", "CPU prepare entry resource gate is NO_GO", reasons=reasons)
    prepared_generations_root = training_root / "prepared-generations"
    if not prepared_generations_root.exists():
        os.mkdir(prepared_generations_root)
    prepared_generations_root = _plain_dir(prepared_generations_root, "prepared generations root")
    prepare_root = prepared_generations_root / prepare_generation_id
    try:
        os.mkdir(prepare_root)
    except FileExistsError:
        _fail("PREPARE_GENERATION_EXISTS", "CPU prepare generation already exists")
    for relative in ("attempts", "input", "tmp"):
        os.mkdir(prepare_root / relative)
    cache_root = training_root / "cache" / "cpu-prepare"
    offline_environment = _cache_environment(cache_root, training_root)
    offline_environment.update(
        {
            "TMP": str(prepare_root / "tmp"),
            "TEMP": str(prepare_root / "tmp"),
            "TMPDIR": str(prepare_root / "tmp"),
            "PYTHONHASHSEED": "20260808",
        }
    )
    plan = _build_plan(
        dataset_generation_id=dataset_generation_id,
        prepare_generation_id=prepare_generation_id,
        dataset_root=dataset_root,
        registry=registry,
        prepare_root=prepare_root,
        admission=admission,
        runtime=runtime,
        tokenizer_files=tokenizer_files,
        tokenizer_tree=tokenizer_tree,
        tokenizer_contract=tokenizer_contract,
        train_pins=train_pins,
        reference_pin=reference_pin,
        raw_metadata=raw_metadata,
        resource=resource,
        offline_environment=offline_environment,
    )
    plan_path = prepare_root / "PREPARE_PLAN.json"
    input_path = prepare_root / "input" / "train_raw.jsonl"
    _exclusive_write(plan_path, _pretty_json(plan))
    _exclusive_write(prepare_root / "PREPARE_PLAN.sha256", (plan["plan_sha256"] + "\n").encode("ascii"))
    _write_raw_input_stream(input_path, manifest, train_pins, reference_pin, raw_metadata)
    attempt = prepare_root / "attempts" / "attempt-0001"
    os.mkdir(attempt)
    partial = attempt / "prepared.partial"
    progress = attempt / "PROGRESS.jsonl"
    _exclusive_write(
        attempt / "START.json",
        _pretty_json(
            {
                "schema": ATTEMPT_SCHEMA,
                "status": "started",
                "started_at_utc": datetime.now(timezone.utc).isoformat(),
                "plan_sha256": plan["plan_sha256"],
                "dataset_tree_sha256": admission["tree_sha256"],
                "device": "cpu",
                "training_started": False,
            }
        ),
    )
    env = os.environ.copy()
    env.update({name: str(value) for name, value in offline_environment.items()})
    command_prefix = [
        str(ORCH.DEFAULT_PYTHON),
        "-u",
        str(Path(__file__).resolve()),
    ]
    stdout_path, stderr_path = attempt / "worker.stdout.log", attempt / "worker.stderr.log"
    started = time.monotonic()
    minimum_free_ram = free_ram
    minimum_commit_headroom = int(memory_admission["minimum_commit_headroom_bytes"])
    dataset_read_locks: _DatasetReadLocks | None = None
    cpu_job: _WindowsJob | None = None
    bootstrap_gate: _WindowsBootstrapGate | None = None
    process: subprocess.Popen[Any] | None = None
    parent_maximum_tree_rss_bytes = 0
    job_memory: dict[str, int] | None = None
    post_cleanup_memory: dict[str, Any] | None = None
    try:
        dataset_read_locks = _acquire_dataset_read_locks(
            dataset_root,
            admission["files"],
            extra_files=(registry,),
        )
        locked_files, locked_tree = ORCH._inventory(
            dataset_root, excluded=frozenset({ORCH.QA_APPROVAL_NAME.casefold()})
        )
        if locked_files != dataset_before_files or locked_tree != dataset_before_tree:
            _fail("DATASET_DRIFT", "admitted dataset changed before deny-write locks completed")
        _exclusive_write(
            attempt / "DATASET_READ_LOCKS.json",
            _pretty_json(
                {
                    "schema": "mentorflow.qwen3-tts-dataset-read-locks.v1",
                    "status": "held",
                    "locked_at_utc": datetime.now(timezone.utc).isoformat(),
                    "mode": "GENERIC_READ+FILE_SHARE_READ; write/delete sharing denied",
                    "locked_files": dataset_read_locks.count,
                    "qa_registry_locked": True,
                    "signed_tree_sha256": locked_tree,
                    "dataset_writes_allowed": False,
                }
            ),
        )
        del admission, manifest, train_pins, reference_pin, raw_metadata
        del runtime, tokenizer_files, tokenizer_tree, tokenizer_contract, source, locked_files
        gc.collect()
        post_cleanup_memory = _stable_launch_memory_gate()
        minimum_free_ram = min(
            minimum_free_ram, int(post_cleanup_memory["minimum_physical_available_bytes"])
        )
        minimum_commit_headroom = min(
            minimum_commit_headroom, int(post_cleanup_memory["minimum_commit_headroom_bytes"])
        )
        _exclusive_write(
            attempt / "LAUNCH_RESOURCE_GATE.json",
            _pretty_json(post_cleanup_memory),
        )
        with stdout_path.open("xb") as stdout, stderr_path.open("xb") as stderr:
            cpu_job = _WindowsJob(MAXIMUM_JOB_COMMIT_BYTES)
            bootstrap_gate = _WindowsBootstrapGate()
            command = [
                *command_prefix,
                "--job-bootstrap",
                "--job-event",
                bootstrap_gate.name,
                "--worker",
                "--plan",
                str(plan_path),
                "--input-jsonl",
                str(input_path),
                "--output-jsonl",
                str(partial / "train_with_codes.jsonl"),
                "--progress-jsonl",
                str(progress),
            ]
            process = subprocess.Popen(
                command,
                cwd=SCRIPT_ROOT.parent,
                env=env,
                stdout=stdout,
                stderr=stderr,
                shell=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000),
            )
            try:
                cpu_job.assign(process)
            except BaseException:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=15)
                raise
            job_memory = cpu_job.query_memory()
            if (
                job_memory["job_memory_limit_bytes"] != MAXIMUM_JOB_COMMIT_BYTES
                or job_memory["process_memory_limit_bytes"] != MAXIMUM_JOB_COMMIT_BYTES
            ):
                _terminate_owned_process_tree(process, cpu_job)
                _fail("CPU_JOB_LIMIT_FAILED", "queried Job Object memory limit differs from plan")
            bootstrap_gate.release()
            last_heartbeat = 0.0
            parent_consecutive_low_physical = 0
            while process.poll() is None:
                try:
                    current_memory = _system_memory_probe()
                    parent_consecutive_low_physical, memory_reason = _runtime_memory_violation(
                        current_memory, parent_consecutive_low_physical
                    )
                except BaseException as exc:
                    _terminate_owned_process_tree(process, cpu_job)
                    _fail(
                        "CPU_MEMORY_OBSERVATION_FAILED",
                        f"parent runtime memory observation failed: {type(exc).__name__}",
                    )
                minimum_free_ram = min(
                    minimum_free_ram, current_memory["physical_available_bytes"]
                )
                minimum_commit_headroom = min(
                    minimum_commit_headroom, current_memory["commit_headroom_bytes"]
                )
                if memory_reason is not None:
                    _terminate_owned_process_tree(process, cpu_job)
                    _fail("CPU_PREPARE_RESOURCE_GATE", memory_reason)
                elapsed = time.monotonic() - started
                tree_rss = _process_tree_working_set_bytes(process.pid)
                if tree_rss is None:
                    _terminate_owned_process_tree(process, cpu_job)
                    _fail("CPU_PREPARE_RSS_OBSERVATION_FAILED", "owned CPU worker tree RSS was unreadable")
                parent_maximum_tree_rss_bytes = max(parent_maximum_tree_rss_bytes, tree_rss)
                if tree_rss > MAXIMUM_WORKER_RSS_BYTES:
                    _terminate_owned_process_tree(process, cpu_job)
                    _fail(
                        "CPU_PREPARE_RESOURCE_GATE",
                        "parent monitor observed CPU worker tree above 5 GiB",
                        worker_tree_rss_bytes=tree_rss,
                    )
                if elapsed > MAXIMUM_TOTAL_PREPARE_SECONDS:
                    _terminate_owned_process_tree(process, cpu_job)
                    _fail("CPU_PREPARE_TIMEOUT", "parent monitor observed total preparation timeout")
                if stdout_path.stat().st_size > MAXIMUM_WORKER_LOG_BYTES or stderr_path.stat().st_size > MAXIMUM_WORKER_LOG_BYTES:
                    _terminate_owned_process_tree(process, cpu_job)
                    _fail("CPU_PREPARE_LOG_LIMIT", "CPU worker log exceeded its hard byte limit")
                job_memory = cpu_job.query_memory()
                if (
                    job_memory["job_memory_limit_bytes"] != MAXIMUM_JOB_COMMIT_BYTES
                    or job_memory["process_memory_limit_bytes"] != MAXIMUM_JOB_COMMIT_BYTES
                    or job_memory["peak_job_memory_used_bytes"] > MAXIMUM_JOB_COMMIT_BYTES
                ):
                    _terminate_owned_process_tree(process, cpu_job)
                    _fail("CPU_JOB_LIMIT_FAILED", "Job Object memory accounting violated its hard limit")
                if elapsed - last_heartbeat >= 30:
                    completed = 0
                    worker_reported_rss: int | None = None
                    if progress.exists():
                        try:
                            for _, event in _iter_bounded_jsonl(
                                progress,
                                "CPU prepare progress",
                                maximum_total_bytes=32 * 1024**2,
                                maximum_line_bytes=256 * 1024,
                            ):
                                if event.get("event") == "sample_completed":
                                    completed += 1
                                    value = event.get("worker_rss_bytes")
                                    worker_reported_rss = value if type(value) is int else worker_reported_rss
                        except (OSError, UnicodeError, CpuPrepareError):
                            completed = -1
                    print(
                        json.dumps(
                            {
                                "status": "cpu_prepare_running",
                                "elapsed_seconds": round(elapsed, 1),
                                "completed_samples": completed,
                                "total_samples": total_samples,
                                **current_memory,
                                "consecutive_low_physical_samples": parent_consecutive_low_physical,
                                "worker_rss_bytes": worker_reported_rss,
                                "worker_tree_rss_bytes": tree_rss,
                                **job_memory,
                            },
                            ensure_ascii=False,
                        ),
                        flush=True,
                    )
                    last_heartbeat = elapsed
                time.sleep(2)
            returncode = process.returncode
            job_memory = cpu_job.query_memory()
        if returncode != 0:
            stderr_tail = _read_utf8_tail(stderr_path, 4000)
            _fail("CPU_PREPARE_WORKER_FAILED", "CPU prepare worker exited nonzero", returncode=returncode, stderr=stderr_tail)
        validation = validate_prepared_output(
            raw_jsonl=input_path,
            prepared_jsonl=partial / "train_with_codes.jsonl",
            worker_result_path=partial / "WORKER_RESULT.json",
            plan=plan,
        )
        dataset_after_files, dataset_after_tree = ORCH._inventory(
            dataset_root, excluded=frozenset({ORCH.QA_APPROVAL_NAME.casefold()})
        )
        if dataset_after_files != dataset_before_files or dataset_after_tree != dataset_before_tree:
            _fail("DATASET_DRIFT", "admitted dataset changed during CPU prepare")
        admission_after = ORCH.validate_dataset_qa(dataset_root, dataset_generation_id, registry)
        admission_after_sha = _admission_validation_sha256(admission_after)
        if admission_after_sha != plan["dataset"]["admission_validation_sha256"]:
            _fail("ADMISSION_DRIFT", "signed admission/trust evidence changed during CPU prepare")
        gpu_after = ORCH._gpu_probe()
        receipt = {
            "schema": PREPARED_RECEIPT_SCHEMA,
            "status": "completed",
            "completed_at_utc": datetime.now(timezone.utc).isoformat(),
            "plan_sha256": plan["plan_sha256"],
            "dataset": plan["dataset"],
            "input": plan["input"],
            "prepared": {key: validation[key] for key in validation if key != "worker_result"},
            "worker_result": validation["worker_result"],
            "parent_monitor_minimum_free_ram_bytes": minimum_free_ram,
            "parent_monitor_minimum_commit_headroom_bytes": minimum_commit_headroom,
            "post_cleanup_launch_memory_gate": post_cleanup_memory,
            "parent_maximum_tree_rss_bytes": parent_maximum_tree_rss_bytes,
            "windows_job_memory": job_memory,
            "dataset_tree_before_after": dataset_before_tree,
            "dataset_writes": 0,
            "dataset_read_lock_mode": "GENERIC_READ+FILE_SHARE_READ; write/delete sharing denied",
            "dataset_read_locked_files": dataset_read_locks.count,
            "qa_registry_read_locked": True,
            "admission_revalidated_sha256": admission_after_sha,
            "reference_encoded_as_training_target": False,
            "reference_bound_sha256": reference_bound_sha256,
            "gpu_observation_after": gpu_after,
            "gpu_used": False,
            "network_used": False,
            "training_model_loaded": False,
            "training_started": False,
            "commit_method": "attempt-partial-directory-same-volume-rename",
        }
        _exclusive_write(partial / "PREPARED_RECEIPT.json", _pretty_json(receipt))
        final = prepare_root / "final"
        os.rename(partial, final)
        _exclusive_write(
            attempt / "SUCCESS.json",
            _pretty_json(
                {
                    "schema": ATTEMPT_SCHEMA,
                    "status": "completed",
                    "completed_at_utc": datetime.now(timezone.utc).isoformat(),
                    "plan_sha256": plan["plan_sha256"],
                    "final": str(final),
                    "prepared_sha256": validation["sha256"],
                    "training_started": False,
                }
            ),
        )
        return {
            "status": "cpu_prepare_completed",
            "prepare_generation": str(prepare_root),
            "final": str(final),
            "prepared_jsonl": str(final / "train_with_codes.jsonl"),
            "prepared_receipt": str(final / "PREPARED_RECEIPT.json"),
            "rows": validation["rows"],
            "sha256": validation["sha256"],
            "code_frames_total": validation["code_frames_total"],
            "minimum_free_ram_bytes": min(
                minimum_free_ram, int(validation["worker_result"]["minimum_free_ram_bytes"])
            ),
            "minimum_commit_headroom_bytes": min(
                minimum_commit_headroom,
                int(validation["worker_result"]["minimum_commit_headroom_bytes"]),
            ),
            "gpu_used": False,
            "network_used": False,
            "training_started": False,
        }
    except BaseException as exc:
        if process is not None and process.poll() is None:
            try:
                _terminate_owned_process_tree(process, cpu_job)
            except BaseException:
                pass
        _exclusive_write(
            attempt / "FAILURE.json",
            _pretty_json(
                {
                    "schema": ATTEMPT_SCHEMA,
                    "status": "failed",
                    "failed_at_utc": datetime.now(timezone.utc).isoformat(),
                    "plan_sha256": plan["plan_sha256"],
                    "error_type": type(exc).__name__,
                    "error_code": getattr(exc, "code", "UNEXPECTED"),
                    "message": str(exc),
                    "evidence_preserved": True,
                    "training_started": False,
                }
            ),
        )
        raise
    finally:
        if bootstrap_gate is not None:
            bootstrap_gate.close()
        if cpu_job is not None:
            cpu_job.close()
        if dataset_read_locks is not None:
            dataset_read_locks.close()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-generation-id")
    parser.add_argument("--prepare-generation-id")
    parser.add_argument("--training-root", type=Path, default=ORCH.TRAINING_ROOT)
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--job-bootstrap", action="store_true")
    parser.add_argument("--job-event")
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--input-jsonl", type=Path)
    parser.add_argument("--output-jsonl", type=Path)
    parser.add_argument("--progress-jsonl", type=Path)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.worker:
            if not args.job_bootstrap or not args.job_event:
                _fail("CPU_JOB_GATE_REQUIRED", "worker must enter through the parent-assigned Job Object gate")
            if not all((args.plan, args.input_jsonl, args.output_jsonl, args.progress_jsonl)):
                _fail("ARGUMENT_MISSING", "worker plan/input/output/progress paths are required")
            result = _worker(args.plan, args.input_jsonl, args.output_jsonl, args.progress_jsonl)
        else:
            if not args.dataset_generation_id or not args.prepare_generation_id:
                _fail("ARGUMENT_MISSING", "dataset and prepare generation ids are required")
            result = prepare_cpu_only(
                dataset_generation_id=args.dataset_generation_id,
                prepare_generation_id=args.prepare_generation_id,
                training_root=args.training_root,
            )
    except (CpuPrepareError, ORCH.TrainingGateError) as exc:
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
            flush=True,
        )
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
