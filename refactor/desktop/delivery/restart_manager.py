"""Read-only, best-effort Restart Manager lookup for update rename failures."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from datetime import datetime, timezone
import os
from pathlib import Path
import threading
import uuid


_MAX_REGISTERED_FILES = 20_000
_MAX_RM_PROCESSES = 4096
_APP_TYPES = {
    0: 'unknown', 1: 'main_window', 2: 'other_window', 3: 'service',
    4: 'explorer', 5: 'console', 1000: 'critical',
}


class _FileTime(ctypes.Structure):
    _fields_ = [('dwLowDateTime', wintypes.DWORD), ('dwHighDateTime', wintypes.DWORD)]


class _UniqueProcess(ctypes.Structure):
    _fields_ = [('dwProcessId', wintypes.DWORD), ('ProcessStartTime', _FileTime)]


class _ProcessInfo(ctypes.Structure):
    _fields_ = [
        ('Process', _UniqueProcess),
        ('strAppName', wintypes.WCHAR * 256),
        ('strServiceShortName', wintypes.WCHAR * 64),
        ('ApplicationType', ctypes.c_int),
        ('AppStatus', wintypes.ULONG),
        ('TSSessionId', wintypes.DWORD),
        ('bRestartable', wintypes.BOOL),
    ]


def _registered_files(roots):
    """Return bounded file paths (Restart Manager rejects directory resources)."""
    files = []
    seen = set()
    for root in roots:
        root = Path(root)
        if root.is_symlink():
            continue
        if not root.exists():
            continue
        if root.is_file():
            candidates = (root,)
        else:
            candidates = None
            for current, dirs, names in os.walk(root, followlinks=False):
                dirs[:] = [name for name in dirs
                           if not (Path(current) / name).is_symlink()]
                for name in names:
                    path = Path(current) / name
                    try:
                        if not path.is_symlink() and path.is_file():
                            key = os.path.normcase(os.path.abspath(path))
                            if key not in seen:
                                seen.add(key)
                                files.append(os.path.abspath(path))
                                if len(files) > _MAX_REGISTERED_FILES:
                                    raise OverflowError('too many files to register')
                    except OSError:
                        continue
            continue
        for path in candidates:
            key = os.path.normcase(os.path.abspath(path))
            if key not in seen:
                seen.add(key)
                files.append(os.path.abspath(path))
                if len(files) > _MAX_REGISTERED_FILES:
                    raise OverflowError('too many files to register')
    return files


def _process_name(pid: int, fallback: str) -> str:
    """Ask Windows for the image basename; fall back to Restart Manager's label."""
    if not pid:
        return fallback or 'unknown'
    try:
        kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.QueryFullProcessImageNameW.argtypes = (
            wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD),
        )
        kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel32.CloseHandle.restype = wintypes.BOOL
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return fallback or 'unknown'
        try:
            buffer = ctypes.create_unicode_buffer(32768)
            length = wintypes.DWORD(len(buffer))
            if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(length)):
                return Path(buffer.value).name or fallback or 'unknown'
        finally:
            kernel32.CloseHandle(handle)
    except Exception:
        pass
    return fallback or 'unknown'


def _query_windows(files):
    rstrtmgr = ctypes.WinDLL('rstrtmgr.dll', use_last_error=True)
    rstrtmgr.RmStartSession.argtypes = (
        ctypes.POINTER(wintypes.DWORD), wintypes.DWORD, wintypes.LPWSTR,
    )
    rstrtmgr.RmStartSession.restype = wintypes.DWORD
    rstrtmgr.RmRegisterResources.argtypes = (
        wintypes.DWORD, wintypes.UINT, ctypes.POINTER(wintypes.LPCWSTR),
        wintypes.UINT, ctypes.c_void_p, wintypes.UINT, ctypes.c_void_p,
    )
    rstrtmgr.RmRegisterResources.restype = wintypes.DWORD
    rstrtmgr.RmGetList.argtypes = (
        wintypes.DWORD, ctypes.POINTER(wintypes.UINT), ctypes.POINTER(wintypes.UINT),
        ctypes.POINTER(_ProcessInfo), ctypes.POINTER(wintypes.DWORD),
    )
    rstrtmgr.RmGetList.restype = wintypes.DWORD
    rstrtmgr.RmEndSession.argtypes = (wintypes.DWORD,)
    rstrtmgr.RmEndSession.restype = wintypes.DWORD

    session = wintypes.DWORD()
    key = ctypes.create_unicode_buffer(uuid.uuid4().hex)
    result = rstrtmgr.RmStartSession(ctypes.byref(session), 0, key)
    if result:
        raise OSError(result, 'RmStartSession failed')
    try:
        names = (wintypes.LPCWSTR * len(files))(*(str(p) for p in files))
        result = rstrtmgr.RmRegisterResources(session, len(files), names, 0, None, 0, None)
        if result:
            raise OSError(result, 'RmRegisterResources failed')

        needed = wintypes.UINT()
        count = wintypes.UINT()
        reasons = wintypes.DWORD()
        result = rstrtmgr.RmGetList(session, ctypes.byref(needed), ctypes.byref(count),
                                   None, ctypes.byref(reasons))
        if result not in (0, 234):  # ERROR_SUCCESS / ERROR_MORE_DATA
            raise OSError(result, 'RmGetList failed')
        for _ in range(3):
            capacity = min(max(int(needed.value), int(count.value)), _MAX_RM_PROCESSES)
            if capacity == 0:
                return []
            entries = (_ProcessInfo * capacity)()
            count = wintypes.UINT(capacity)
            result = rstrtmgr.RmGetList(session, ctypes.byref(needed), ctypes.byref(count),
                                       entries, ctypes.byref(reasons))
            if result == 234 and needed.value > capacity:
                continue
            if result:
                raise OSError(result, 'RmGetList failed')
            return [
                {
                    'name': _process_name(int(row.Process.dwProcessId), row.strAppName),
                    'pid': int(row.Process.dwProcessId),
                    'type': _APP_TYPES.get(int(row.ApplicationType), 'unknown'),
                }
                for row in entries[:count.value]
            ]
        raise RuntimeError('Restart Manager process list changed repeatedly')
    finally:
        rstrtmgr.RmEndSession(session)


def query_directory_users(*roots, timeout_seconds=1.0):
    """Best-effort lookup; never shuts down apps and returns promptly on failure.

    Restart Manager accepts file names, not directory resources, so files under
    each candidate tree are registered together. The bounded worker keeps this
    diagnostic from holding up the installer's retry budget. A timed-out daemon
    may finish its own RmEndSession later; it cannot change product state.
    """
    if os.name != 'nt':
        return {'status': 'unknown', 'processes': []}
    timeout_seconds = max(0.0, float(timeout_seconds))
    if timeout_seconds == 0:
        return {'status': 'unknown', 'processes': [], 'reason': 'diagnostic_timeout'}
    outcome = {'value': None, 'error': None}

    def run():
        try:
            files = _registered_files(roots)
            if not files:
                raise FileNotFoundError('no files found under registered paths')
            outcome['value'] = _query_windows(files)
        except Exception as error:
            outcome['error'] = error

    worker = threading.Thread(target=run, name='update-lock-diagnostics', daemon=True)
    worker.start()
    worker.join(timeout_seconds)
    if worker.is_alive():
        return {'status': 'unknown', 'processes': [], 'reason': 'diagnostic_timeout'}
    if outcome['error'] is not None:
        return {'status': 'unknown', 'processes': [],
                'reason': type(outcome['error']).__name__}
    return {'status': 'ok', 'processes': outcome['value'] or []}
