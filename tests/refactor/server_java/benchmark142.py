"""Measure local Python/Java contract-process RSS and catalog HTTP latency.

Run the Python and Java HTTP fixture processes first, then pass their URLs/PIDs.
The fixture uses a fake local JWKS issuer and fake external membership/health
state, so measurements include HTTP, JWT verification and catalog reads but do
not represent a PostgreSQL-backed production request.
"""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import time

import httpx


CATALOG_PATH = "/api/enterprise/catalog/model-prices"
REQUEST_COUNT = 1000


def _single_process_rss_bytes(pid: int) -> int:
    """Read RSS using the OS API, without adding a psutil dependency."""
    if os.name == "nt":
        class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t),
            ]
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESS_MEMORY_COUNTERS), wintypes.DWORD]
        psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            raise OSError(ctypes.get_last_error(), "OpenProcess failed", pid)
        try:
            counters = PROCESS_MEMORY_COUNTERS()
            counters.cb = ctypes.sizeof(counters)
            if not psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
                raise OSError(ctypes.get_last_error(), "GetProcessMemoryInfo failed", pid)
            return int(counters.WorkingSetSize)
        finally:
            kernel32.CloseHandle(handle)
    statm = Path(f"/proc/{pid}/statm").read_text(encoding="ascii").split()
    return int(statm[1]) * os.sysconf("SC_PAGE_SIZE")


def process_tree_pids(pid: int) -> list[int]:
    """Include the real worker behind Windows' virtualenv python.exe redirector."""
    parents = {}
    if os.name == "nt":
        class PROCESSENTRY32W(ctypes.Structure):
            _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
                ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", wintypes.LONG),
                ("dwFlags", wintypes.DWORD), ("szExeFile", wintypes.WCHAR * 260)]
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        kernel.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
        kernel.Process32FirstW.restype = wintypes.BOOL
        kernel.Process32NextW.argtypes = kernel.Process32FirstW.argtypes
        kernel.Process32NextW.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        snapshot = kernel.CreateToolhelp32Snapshot(2, 0)
        if snapshot == wintypes.HANDLE(-1).value:
            raise OSError(ctypes.get_last_error(), "process snapshot failed")
        try:
            entry = PROCESSENTRY32W(); entry.dwSize = ctypes.sizeof(entry)
            found = kernel.Process32FirstW(snapshot, ctypes.byref(entry))
            while found:
                parents[int(entry.th32ProcessID)] = int(entry.th32ParentProcessID)
                found = kernel.Process32NextW(snapshot, ctypes.byref(entry))
        finally:
            kernel.CloseHandle(snapshot)
    else:
        for path in Path("/proc").glob("[0-9]*/stat"):
            try:
                fields = path.read_text().rsplit(")", 1)[1].split()
                parents[int(path.parent.name)] = int(fields[1])
            except (OSError, ValueError, IndexError):
                continue
    selected = {pid}
    while True:
        expanded = selected | {child for child, parent in parents.items() if parent in selected}
        if expanded == selected:
            return sorted(selected)
        selected = expanded


def process_rss_bytes(pid: int) -> int:
    return sum(_single_process_rss_bytes(child) for child in process_tree_pids(pid))


def measure(base_url: str, pid: int, headers: dict[str, str], *, requests: int = REQUEST_COUNT) -> dict[str, object]:
    """Warm the endpoint, record idle RSS, then sample RSS through sequential GETs."""
    url = base_url.rstrip("/") + CATALOG_PATH
    with httpx.Client(timeout=10, trust_env=False, headers=headers) as client:
        for _ in range(20):
            response = client.get(url)
            if response.status_code != 200:
                raise RuntimeError(f"warm-up returned HTTP {response.status_code}: {response.content[:300]!r}")
        time.sleep(0.25)
        idle_rss = process_rss_bytes(pid)
        idle_processes = {str(child): _single_process_rss_bytes(child)
                          for child in process_tree_pids(pid)}
        peak_rss = idle_rss
        elapsed: list[float] = []
        for _ in range(requests):
            started = time.perf_counter()
            response = client.get(url)
            elapsed.append(time.perf_counter() - started)
            if response.status_code != 200:
                raise RuntimeError(f"catalog returned HTTP {response.status_code}: {response.content[:300]!r}")
            peak_rss = max(peak_rss, process_rss_bytes(pid))
        after_rss = process_rss_bytes(pid)
    return {
        "url": base_url.rstrip("/") + CATALOG_PATH,
        "pid": pid,
        "rss_scope": "sum of server process and descendants (includes virtualenv redirector)",
        "idle_process_rss_bytes": idle_processes,
        "requests": requests,
        "warmup_requests_excluded": 20,
        "idle_rss_bytes": idle_rss,
        "peak_sampled_rss_bytes": peak_rss,
        "after_rss_bytes": after_rss,
        "mean_latency_ms": sum(elapsed) * 1000 / len(elapsed),
        "min_latency_ms": min(elapsed) * 1000,
        "max_latency_ms": max(elapsed) * 1000,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--java-url", required=True)
    parser.add_argument("--java-pid", type=int, required=True)
    parser.add_argument("--headers-file", required=True, help="JSON file containing synthetic fixture request headers")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    headers = json.loads(args.headers_file and Path(args.headers_file).read_text(encoding="utf-8"))
    if not isinstance(headers, dict) or not isinstance(headers.get("Authorization"), str):
        parser.error("headers-file must contain an Authorization header")
    results = {
        "measurement_scope": "local HTTP + JWT + catalog; fake JWKS issuer and external membership/health state; no PostgreSQL request",
        "java": measure(args.java_url, args.java_pid, headers),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
