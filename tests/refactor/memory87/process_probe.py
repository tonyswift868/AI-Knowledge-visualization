"""Read-only Windows memory/thread evidence for an owned test process."""
import ctypes
from ctypes import wintypes as w
from collections import Counter
import os
from pathlib import Path
import threading


def snapshot(pid=None):
    pid = pid or os.getpid()
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    psapi = ctypes.WinDLL('psapi', use_last_error=True)
    kernel.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
    kernel.OpenProcess.restype = w.HANDLE
    kernel.CloseHandle.argtypes = [w.HANDLE]
    kernel.GetExitCodeProcess.argtypes = [w.HANDLE, ctypes.POINTER(w.DWORD)]
    class Memory(ctypes.Structure):
        _fields_ = [('cb', w.DWORD), ('faults', w.DWORD)] + [(name, ctypes.c_size_t) for name in (
            'peak_working_set', 'working_set', 'peak_paged_pool', 'paged_pool', 'peak_nonpaged_pool',
            'nonpaged_pool', 'pagefile', 'peak_pagefile', 'private_bytes')]
    psapi.GetProcessMemoryInfo.argtypes = [w.HANDLE, ctypes.c_void_p, w.DWORD]
    process = kernel.OpenProcess(0x410, False, pid)
    if not process:
        return {'pid': pid, 'exited': True}
    try:
        exit_code = w.DWORD()
        if kernel.GetExitCodeProcess(process, ctypes.byref(exit_code)) and exit_code.value != 259:
            return {'pid': pid, 'exited': True, 'exit_code': exit_code.value}
        memory = Memory(cb=ctypes.sizeof(Memory))
        if not psapi.GetProcessMemoryInfo(process, ctypes.byref(memory), memory.cb):
            raise ctypes.WinError(ctypes.get_last_error())
        result = dict(pid=pid, private_bytes=memory.private_bytes, working_set=memory.working_set)
    finally:
        kernel.CloseHandle(process)
    class ThreadEntry(ctypes.Structure):
        _fields_ = [(name, w.DWORD) for name in ('size', 'usage', 'tid', 'pid')] + [
            ('base', w.LONG), ('delta', w.LONG), ('flags', w.DWORD)]
    kernel.CreateToolhelp32Snapshot.argtypes = [w.DWORD, w.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = w.HANDLE
    kernel.Thread32First.argtypes = kernel.Thread32Next.argtypes = [w.HANDLE, ctypes.POINTER(ThreadEntry)]
    kernel.OpenThread.argtypes = [w.DWORD, w.BOOL, w.DWORD]
    kernel.OpenThread.restype = w.HANDLE
    ntdll = ctypes.WinDLL('ntdll')
    ntdll.NtQueryInformationThread.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.ULONG, ctypes.c_void_p]
    psapi.EnumProcessModules.argtypes = [w.HANDLE, ctypes.c_void_p, w.DWORD, ctypes.POINTER(w.DWORD)]
    psapi.GetModuleInformation.argtypes = [w.HANDLE, w.HMODULE, ctypes.c_void_p, w.DWORD]
    psapi.GetModuleFileNameExW.argtypes = [w.HANDLE, w.HMODULE, w.LPWSTR, w.DWORD]
    class ModuleInfo(ctypes.Structure):
        _fields_ = [('base', ctypes.c_void_p), ('size', w.DWORD), ('entry', ctypes.c_void_p)]
    process = kernel.OpenProcess(0x410, False, pid)
    modules = []
    if process:
        try:
            handles = (w.HMODULE * 2048)()
            needed = w.DWORD()
            if psapi.EnumProcessModules(process, handles, ctypes.sizeof(handles), ctypes.byref(needed)):
                for module in handles[:min(2048, needed.value // ctypes.sizeof(w.HMODULE))]:
                    info, path = ModuleInfo(), ctypes.create_unicode_buffer(32768)
                    if psapi.GetModuleInformation(process, module, ctypes.byref(info), ctypes.sizeof(info)):
                        psapi.GetModuleFileNameExW(process, module, path, len(path))
                        modules.append((info.base, info.base + info.size, Path(path.value).name))
        finally:
            kernel.CloseHandle(process)
    kernel.GetThreadDescription.argtypes = [w.HANDLE, ctypes.POINTER(w.LPWSTR)]
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    listing = kernel.CreateToolhelp32Snapshot(4, 0)
    names, starts = [], []
    try:
        entry = ThreadEntry(size=ctypes.sizeof(ThreadEntry))
        found = kernel.Thread32First(listing, ctypes.byref(entry))
        while found:
            if entry.pid == pid:
                name = ''
                handle = kernel.OpenThread(0x840, False, entry.tid)
                if handle:
                    try:
                        pointer = w.LPWSTR()
                        if kernel.GetThreadDescription(handle, ctypes.byref(pointer)) == 0:
                            name = pointer.value or ''
                            kernel.LocalFree(ctypes.cast(pointer, ctypes.c_void_p))
                        address = ctypes.c_void_p()
                        if ntdll.NtQueryInformationThread(handle, 9, ctypes.byref(address), ctypes.sizeof(address), None) == 0:
                            starts.append(next((name for low, high, name in modules if low <= (address.value or 0) < high), '(unknown start module)'))
                    finally:
                        kernel.CloseHandle(handle)
                names.append(name or '(unnamed native thread)')
            found = kernel.Thread32Next(listing, ctypes.byref(entry))
    finally:
        kernel.CloseHandle(listing)
    result.update(native_threads=len(names), native_names=dict(Counter(names)), start_modules=dict(Counter(starts)))
    if pid == os.getpid():
        result['python_threads'] = [{'name': thread.name, 'native_id': thread.native_id}
                                    for thread in threading.enumerate()]
    return result


def tree_snapshot():
    """Current test parent and its descendants only; never kills a process."""
    import subprocess
    import json
    # CIM is used only as a read-only process tree inventory.
    result = subprocess.run(['powershell', '-NoProfile', '-Command',
        'Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId | ConvertTo-Json -Compress'],
        capture_output=True, encoding='utf-8', check=True, creationflags=0x08000000)
    records = json.loads(result.stdout)
    owned = {os.getpid()}
    changed = True
    while changed:
        before = len(owned)
        owned.update(int(row['ProcessId']) for row in records if int(row['ParentProcessId']) in owned)
        changed = len(owned) != before
    values = [snapshot(pid) for pid in sorted(owned)]
    values = [value for value in values if not value.get('exited')]
    return {'processes': values, 'private_bytes': sum(value['private_bytes'] for value in values),
            'working_set': sum(value['working_set'] for value in values)}
