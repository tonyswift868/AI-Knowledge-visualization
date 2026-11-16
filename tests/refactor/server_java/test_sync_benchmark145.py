"""Optional local 20 x 100 HTTP batch benchmark; enable with SYNC145_BENCHMARK=1."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import time

import pytest

from contract_support145 import TASK_ROOT, ORG, WORKSPACE, sync145_backends
from test_sync_http_contract145 import BASE, _envelope, _entity_id, _headers, _wire_json


def _memory145(pid):
    class Counters(ctypes.Structure):
        _fields_ = [
            ("cb", ctypes.c_ulong), ("PageFaultCount", ctypes.c_ulong),
            ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t),
            ("PrivateUsage", ctypes.c_size_t),
        ]
    process_query_information = 0x0400
    process_vm_read = 0x0010
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    psapi.GetProcessMemoryInfo.argtypes = (wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD)
    psapi.GetProcessMemoryInfo.restype = ctypes.c_int
    handle = kernel.OpenProcess(process_query_information | process_vm_read, False, int(pid))
    if not handle:
        raise OSError(ctypes.get_last_error(), "OpenProcess failed")
    try:
        counters = Counters()
        counters.cb = ctypes.sizeof(counters)
        if not psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
            raise OSError(ctypes.get_last_error(), "GetProcessMemoryInfo failed")
        return {"working_set_bytes": int(counters.WorkingSetSize),
                "peak_working_set_bytes": int(counters.PeakWorkingSetSize),
                "private_bytes": int(counters.PrivateUsage)}
    finally:
        kernel.CloseHandle(handle)


@pytest.mark.skipif(os.environ.get("SYNC145_BENCHMARK") != "1",
                    reason="set SYNC145_BENCHMARK=1 for the requested local benchmark")
def test_sync145_batch_100_about_1mb_20_runs_per_backend(sync145_backends):
    results = {}
    for backend_name in ("java",):
        client = sync145_backends[backend_name]
        token = sync145_backends["jwks"].token(subject="user-145-a")
        latencies = []
        byte_count = 0
        for batch_index in range(20):
            records = []
            for row_index in range(100):
                record_id = f"benchmark145-{batch_index}-{row_index}"
                records.append({
                    "kind": "learning_run", "entity_id": _entity_id(record_id),
                    "request_id": f"benchmark-{backend_name}-{batch_index}-{row_index}",
                    "expected_revision": 0,
                    "payload": _envelope(record_id, title="批量性能样本",
                                         padding=9_700),
                })
            body = _wire_json({"records": records})
            if 900 * 1024 > len(body) or len(body) > 1_100 * 1024:
                raise AssertionError(f"benchmark body is not near 1 MiB: {len(body)}")
            byte_count += len(body)
            started = time.perf_counter()
            response = client.post(f"{BASE}/entities/bulk",
                headers=_headers(token, f"benchmark-{backend_name}-{batch_index}"), content=body)
            latencies.append((time.perf_counter() - started) * 1000)
            assert response.status_code == 200, response.text[:1000]
            assert all(row["status"] == 200 for row in response.json()["results"])
        results[backend_name] = {
            "runs": len(latencies), "records_per_run": 100,
            "request_bytes": byte_count // len(latencies),
            "total_bytes": byte_count,
            "average_ms": round(sum(latencies) / len(latencies), 3),
            "samples_ms": [round(value, 3) for value in latencies],
        }
    results["java"]["memory_after_benchmark"] = _memory145(
        sync145_backends["java_endpoint"].process.pid)
    output = TASK_ROOT / "build" / "benchmark145.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(results, ensure_ascii=False))
