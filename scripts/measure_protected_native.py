"""Three native gate processes under the verified same-user normal token."""
import ctypes as c
import hashlib
from ctypes import wintypes as w
import json
from pathlib import Path
import statistics
import time
from scripts.test_windows_install_trust import normal_token
from scripts import windows_install_trust as trust


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--exe', type=Path, default=Path("E:/MentorFlow-TrustTest-20260906/native-runner/MentorFlowInstallGate.exe"))
    parser.add_argument('--output', type=Path, default=Path("E:/Codex/Temp/E107-TrustBoundary/native-gate-results.json"))
    options = parser.parse_args()
    exe = options.exe
    trust.assert_safe_parents(exe)
    trust.assert_protected(exe)
    root = Path("E:/MentorFlow-TrustTest-20260906/large")
    adv, kernel = trust._api()
    primary = w.HANDLE()
    with normal_token() as token:
        # Controller creates the process; the child receives the normal token.
        adv.RevertToSelf()
        if not adv.DuplicateTokenEx(token, 0xF01FF, None, 2, 1, c.byref(primary)):
            raise RuntimeError(f"primary normal token unavailable: {c.get_last_error()}")
    class Startup(c.Structure):
        _fields_ = [("cb", w.DWORD), ("reserved", w.LPWSTR), ("desktop", w.LPWSTR), ("title", w.LPWSTR), ("x", w.DWORD), ("y", w.DWORD), ("xs", w.DWORD), ("ys", w.DWORD), ("xc", w.DWORD), ("yc", w.DWORD), ("fill", w.DWORD), ("flags", w.DWORD), ("show", w.WORD), ("reserved_size", w.WORD), ("reserved_ptr", c.c_void_p), ("stdin", w.HANDLE), ("stdout", w.HANDLE), ("stderr", w.HANDLE)]
    class Process(c.Structure):
        _fields_ = [("process", w.HANDLE), ("thread", w.HANDLE), ("pid", w.DWORD), ("tid", w.DWORD)]
    adv.CreateProcessWithTokenW.argtypes = [w.HANDLE, w.DWORD, w.LPCWSTR, w.LPWSTR, w.DWORD, c.c_void_p, w.LPCWSTR, c.POINTER(Startup), c.POINTER(Process)]
    kernel.WaitForSingleObject.argtypes = [w.HANDLE, w.DWORD]
    kernel.GetExitCodeProcess.argtypes = [w.HANDLE, c.POINTER(w.DWORD)]
    runs = []
    try:
        for _ in range(3):
            args = c.create_unicode_buffer('"' + str(exe) + '" gate --install-root "' + str(root) + '" --manifest-sha256 a786bd894abea739b3b3f1a8a797b160cc8b56e88cf4f40ce580d8ad78ba9380 --source-sha 1531c65116063ec606ed1c623acd29fb68e9e8b7 --tree-sha c441dbfd48fa4bedcac1ee7212866728bc8ac03c')
            startup, process = Startup(cb=c.sizeof(Startup)), Process()
            began = time.perf_counter()
            if not adv.CreateProcessWithTokenW(primary, 0, str(exe), args, 0x08000000, None, str(root / "App"), c.byref(startup), c.byref(process)):
                raise RuntimeError(f"normal native process creation failed: {c.get_last_error()}")
            try:
                if kernel.WaitForSingleObject(process.process, 60000) != 0:
                    raise RuntimeError(f"native gate still running; retain PID {process.pid} for follow-up")
                code = w.DWORD()
                if not kernel.GetExitCodeProcess(process.process, c.byref(code)):
                    raise RuntimeError("native exit code unavailable")
                row = {"pid": process.pid, "exit_code": code.value, "seconds": time.perf_counter() - began, "normal_token": True}
                runs.append(row)
                print(json.dumps(row), flush=True)
                if code.value != 0:
                    raise RuntimeError("native gate rejected installation")
            finally:
                kernel.CloseHandle(process.thread)
                kernel.CloseHandle(process.process)
    finally:
        kernel.CloseHandle(primary)
    times = [row["seconds"] for row in runs]
    result = {"runs": runs, "p50": statistics.median(times), "maximum": max(times), "scope": "native security gate process start-to-exit, not launch-click-to-form"}
    result['executable'] = str(exe)
    result['executable_sha256'] = hashlib.sha256(exe.read_bytes()).hexdigest()
    options.output.write_text(json.dumps(result, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
