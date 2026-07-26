"""Resume bounded KKS prewarm work without spending browser/GPU resources."""

from __future__ import annotations

import argparse
import contextlib
import ctypes
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Iterator


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.koikatsu_cache_coverage import (
    COVERAGE_PATH,
    lease_pending_cards,
    record_failure,
    record_manifest,
    release_lease_error,
)
from backend.app.koikatsu_card_index import get_indexed_card
from backend.app.koikatsu_teacher_registry import (
    local_cards,
    runtime_cache_pipeline_fingerprint,
    runtime_card_manifest,
)


@contextlib.contextmanager
def _single_worker_lock(path: Path) -> Iterator[None]:
    """Prevent two offline converters from exhausting the machine together."""

    path.parent.mkdir(parents=True, exist_ok=True)
    stream = path.open("a+b")
    try:
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        if os.name == "nt":
            import msvcrt
            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise RuntimeError("another KKS prewarm worker is already running") from exc
        else:  # pragma: no cover - Windows is the supported KKS host
            import fcntl
            try:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise RuntimeError("another KKS prewarm worker is already running") from exc
        yield
    finally:
        try:
            stream.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:  # pragma: no cover
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        stream.close()


def _set_below_normal_priority() -> bool:
    if os.name != "nt":  # pragma: no cover
        try:
            os.nice(10)
            return True
        except OSError:
            return False
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetCurrentProcess.restype = ctypes.c_void_p
        kernel32.SetPriorityClass.argtypes = (ctypes.c_void_p, ctypes.c_ulong)
        kernel32.SetPriorityClass.restype = ctypes.c_int
        handle = kernel32.GetCurrentProcess()
        return bool(kernel32.SetPriorityClass(handle, 0x00004000))
    except (AttributeError, OSError):
        return False


def _available_memory_mib() -> int | None:
    if os.name != "nt":  # pragma: no cover
        return None

    class MemoryStatus(ctypes.Structure):
        _fields_ = [
            ("length", ctypes.c_ulong), ("memory_load", ctypes.c_ulong),
            ("total_physical", ctypes.c_ulonglong), ("available_physical", ctypes.c_ulonglong),
            ("total_page_file", ctypes.c_ulonglong), ("available_page_file", ctypes.c_ulonglong),
            ("total_virtual", ctypes.c_ulonglong), ("available_virtual", ctypes.c_ulonglong),
            ("available_extended_virtual", ctypes.c_ulonglong),
        ]

    value = MemoryStatus()
    value.length = ctypes.sizeof(MemoryStatus)
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GlobalMemoryStatusEx.argtypes = (ctypes.POINTER(MemoryStatus),)
        kernel32.GlobalMemoryStatusEx.restype = ctypes.c_int
        if not kernel32.GlobalMemoryStatusEx(ctypes.byref(value)):
            return None
    except (AttributeError, OSError):
        return None
    return int(value.available_physical // (1024 * 1024))


def _page_items(query: str, offset: int, limit: int) -> list[dict]:
    return list(local_cards(query=query, limit=limit, offset=offset).get("items", []))[:limit]


class _WindowsJobMemoryGuard:
    """Bound one worker and every converter process it starts."""

    def __init__(self, memory_mib: int) -> None:
        self.handle: int | None = None
        if os.name != "nt":  # pragma: no cover - Windows is the KKS host
            return

        class BasicLimit(ctypes.Structure):
            _fields_ = [
                ("per_process_user_time", ctypes.c_longlong),
                ("per_job_user_time", ctypes.c_longlong),
                ("limit_flags", ctypes.c_ulong),
                ("minimum_working_set", ctypes.c_size_t),
                ("maximum_working_set", ctypes.c_size_t),
                ("active_process_limit", ctypes.c_ulong),
                ("affinity", ctypes.c_size_t),
                ("priority_class", ctypes.c_ulong),
                ("scheduling_class", ctypes.c_ulong),
            ]

        class IoCounters(ctypes.Structure):
            _fields_ = [
                ("read_operation_count", ctypes.c_ulonglong),
                ("write_operation_count", ctypes.c_ulonglong),
                ("other_operation_count", ctypes.c_ulonglong),
                ("read_transfer_count", ctypes.c_ulonglong),
                ("write_transfer_count", ctypes.c_ulonglong),
                ("other_transfer_count", ctypes.c_ulonglong),
            ]

        class ExtendedLimit(ctypes.Structure):
            _fields_ = [
                ("basic", BasicLimit),
                ("io", IoCounters),
                ("process_memory_limit", ctypes.c_size_t),
                ("job_memory_limit", ctypes.c_size_t),
                ("peak_process_memory", ctypes.c_size_t),
                ("peak_job_memory", ctypes.c_size_t),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.argtypes = (ctypes.c_void_p, ctypes.c_wchar_p)
        kernel32.CreateJobObjectW.restype = ctypes.c_void_p
        kernel32.SetInformationJobObject.argtypes = (ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_ulong)
        kernel32.SetInformationJobObject.restype = ctypes.c_int
        handle = kernel32.CreateJobObjectW(None, None)
        if not handle:
            raise OSError(ctypes.get_last_error(), "could not create KKS worker job")
        limits = ExtendedLimit()
        # Kill the complete converter tree if the parent closes the job, and
        # cap aggregate committed memory rather than just the Python worker.
        limits.basic.limit_flags = 0x00002000 | 0x00000200
        limits.job_memory_limit = max(512, int(memory_mib)) * 1024 * 1024
        if not kernel32.SetInformationJobObject(handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            error = ctypes.get_last_error()
            kernel32.CloseHandle(handle)
            raise OSError(error, "could not configure KKS worker job")
        self.handle = int(handle)

    def assign(self, process: subprocess.Popen[str]) -> None:
        if self.handle is None:
            return
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.AssignProcessToJobObject.argtypes = (ctypes.c_void_p, ctypes.c_void_p)
        kernel32.AssignProcessToJobObject.restype = ctypes.c_int
        if not kernel32.AssignProcessToJobObject(self.handle, ctypes.c_void_p(process._handle)):  # type: ignore[attr-defined]
            raise OSError(ctypes.get_last_error(), "could not assign KKS worker to memory job")

    def terminate(self, exit_code: int = 124) -> None:
        if self.handle is None:
            return
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.TerminateJobObject(ctypes.c_void_p(self.handle), ctypes.c_uint(exit_code))

    def close(self) -> None:
        if self.handle is None:
            return
        ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle(ctypes.c_void_p(self.handle))
        self.handle = None


def _run_card_worker(card_id: str, *, timeout_seconds: float, memory_mib: int) -> dict:
    command = [sys.executable, str(Path(__file__).resolve()), "--worker-card-id", card_id]
    process = subprocess.Popen(
        command,
        cwd=str(PROJECT_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
        start_new_session=os.name != "nt",
    )
    guard = _WindowsJobMemoryGuard(memory_mib)
    try:
        try:
            guard.assign(process)
        except Exception:
            process.kill()
            process.wait(timeout=5)
            raise
        try:
            stdout, stderr = process.communicate(timeout=max(1.0, float(timeout_seconds)))
        except subprocess.TimeoutExpired as exc:
            guard.terminate()
            process.kill()
            process.communicate(timeout=5)
            raise TimeoutError(f"card prewarm exceeded {timeout_seconds:.1f}s") from exc
        if process.returncode != 0:
            message = stderr.strip().splitlines()[-1] if stderr.strip() else f"worker exited {process.returncode}"
            raise RuntimeError(message)
        payload = json.loads(stdout)
        if not isinstance(payload, dict):
            raise RuntimeError("card prewarm worker returned an invalid manifest")
        return payload
    finally:
        guard.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Prewarm a bounded page or resume the KKS cache journal")
    parser.add_argument("--query", default="", help="optional local-card name filter")
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--limit", type=int, default=12, help="maximum cards this run (1-60)")
    parser.add_argument("--from-coverage", action="store_true", help="lease pending cards from the coverage database")
    parser.add_argument("--coverage-db", type=Path, default=COVERAGE_PATH)
    parser.add_argument("--time-budget-seconds", type=float, default=900.0)
    parser.add_argument("--card-timeout-seconds", type=float, default=120.0)
    parser.add_argument("--max-worker-memory-mib", type=int, default=3072)
    parser.add_argument("--retry-after-seconds", type=float, default=3600.0)
    parser.add_argument("--min-free-memory-mib", type=int, default=4096)
    parser.add_argument("--priority", choices=("below-normal", "normal"), default="below-normal")
    parser.add_argument("--lock-file", type=Path, default=PROJECT_ROOT / "legacy" / "koikatsu-3d" / "resources" / "runtime" / ".prewarm.lock")
    parser.add_argument("--worker-card-id", default="", help=argparse.SUPPRESS)
    args = parser.parse_args()

    if args.worker_card_id:
        # Windows worker subprocesses may inherit a GBK console even though
        # card filenames/display names contain CJK, Korean, or other Unicode.
        # The parent reads UTF-8 explicitly; keep the worker's JSON response
        # on the same encoding so one unusual filename cannot turn a valid
        # conversion into a coverage error.
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass
        print(json.dumps(runtime_card_manifest(str(args.worker_card_id), allow_conversion=True), ensure_ascii=False))
        return

    limit = max(1, min(int(args.limit), 60))
    if args.priority == "below-normal" and not _set_below_normal_priority():
        raise SystemExit("could not lower KKS prewarm process priority")
    fingerprint = runtime_cache_pipeline_fingerprint()
    totals = {"cards": 0, "ready": 0, "partial": 0, "errors": 0, "seconds": 0.0}
    started = time.monotonic()

    with _single_worker_lock(args.lock_file):
        page = [] if args.from_coverage else _page_items(args.query, max(0, args.offset), limit)
        while totals["cards"] < limit:
            if time.monotonic() - started >= max(1.0, float(args.time_budget_seconds)):
                break
            available = _available_memory_mib()
            if available is not None and available < max(512, int(args.min_free_memory_mib)):
                print(json.dumps({"status": "paused-low-memory", "available_mib": available}, ensure_ascii=False), flush=True)
                break
            if args.from_coverage:
                leased = lease_pending_cards(limit=1, path=args.coverage_db)
                if not leased:
                    break
                try:
                    item = get_indexed_card(leased[0])
                except Exception as exc:
                    print(json.dumps({"card_id": leased[0], "status": "error", "error": str(exc)}, ensure_ascii=False), flush=True)
                    release_lease_error(
                        leased[0], str(exc), path=args.coverage_db,
                        retry_after_ns=time.time_ns() + int(max(60.0, args.retry_after_seconds) * 1_000_000_000),
                    )
                    totals["errors"] += 1
                    totals["cards"] += 1
                    continue
            else:
                if totals["cards"] >= len(page):
                    break
                item = page[totals["cards"]]
            card_started = time.perf_counter()
            try:
                remaining = max(1.0, float(args.time_budget_seconds) - (time.monotonic() - started))
                manifest = _run_card_worker(
                    str(item["card_id"]),
                    timeout_seconds=min(max(1.0, float(args.card_timeout_seconds)), remaining),
                    memory_mib=max(512, int(args.max_worker_memory_mib)),
                )
                state = "ready" if str(manifest.get("status") or "partial") == "ready" else "partial"
                totals[state] += 1
                retry_after_ns = 0 if state == "ready" else time.time_ns() + int(max(60.0, args.retry_after_seconds) * 1_000_000_000)
                record_manifest(item, manifest, fingerprint, path=args.coverage_db, retry_after_ns=retry_after_ns)
                report = {
                    "card_id": item["card_id"], "filename": item["filename"], "status": state,
                    "runtime_items": len(manifest.get("items", [])),
                    "face_textures": len(manifest.get("faceTextures", {})),
                    "seconds": round(time.perf_counter() - card_started, 2),
                }
            except Exception as exc:  # one broken card must not discard the batch
                totals["errors"] += 1
                retry_after_ns = time.time_ns() + int(max(60.0, args.retry_after_seconds) * 1_000_000_000)
                record_failure(item, str(exc), fingerprint, path=args.coverage_db, retry_after_ns=retry_after_ns)
                report = {
                    "card_id": item.get("card_id"), "filename": item.get("filename"),
                    "status": "error", "error": str(exc),
                    "seconds": round(time.perf_counter() - card_started, 2),
                }
            totals["cards"] += 1
            print(json.dumps(report, ensure_ascii=False), flush=True)
    totals["seconds"] = round(time.monotonic() - started, 2)
    print(json.dumps({"summary": totals}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
