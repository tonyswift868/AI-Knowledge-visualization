"""Native Windows UIA Invoke adapter for missing .NET proxy patterns.

ABI: Microsoft UIAutomationClient.h; no SendInput, focus or window messages.
Only called after the existing UIA driver selects an enabled owned control.
"""
import argparse
import ctypes as c
from ctypes import wintypes as w
import json
import os
from pathlib import Path
import uuid


class GUID(c.Structure):
    _fields_ = [('data', c.c_byte*16)]


def guid(value):
    return GUID.from_buffer_copy(uuid.UUID(value).bytes_le)


def method(pointer, index, *arguments):
    table = c.cast(pointer, c.POINTER(c.POINTER(c.c_void_p))).contents
    return c.WINFUNCTYPE(c.c_long, c.c_void_p, *arguments)(table[index])


def check(result):
    if result < 0:
        raise OSError('UIA HRESULT '+hex(result & 0xffffffff))


def invoke(pid, hwnd):
    root = Path(os.environ['MF_UAT_ROOT']).resolve()
    user, kernel, ole = c.WinDLL('user32'), c.WinDLL('kernel32'), c.OleDLL('ole32')
    owner = w.DWORD()
    user.GetWindowThreadProcessId(w.HWND(hwnd), c.byref(owner))
    if owner.value != pid:
        raise ValueError('control is not owned by the requested preview process')
    kernel.OpenProcess.restype = w.HANDLE
    kernel.CloseHandle.argtypes = [w.HANDLE]
    kernel.QueryFullProcessImageNameW.argtypes = [w.HANDLE, w.DWORD, w.LPWSTR, c.POINTER(w.DWORD)]
    handle = kernel.OpenProcess(0x1000, False, pid)
    try:
        image = c.create_unicode_buffer(32768); size = w.DWORD(len(image))
        if not kernel.QueryFullProcessImageNameW(handle, 0, image, c.byref(size)):
            raise OSError('cannot inspect preview image')
        if not Path(image.value).resolve().is_relative_to(root):
            raise ValueError('not an owned preview process')
    finally:
        if handle: kernel.CloseHandle(handle)
    check(ole.CoInitializeEx(None, 0))
    owned = []
    try:
        factory = guid('ff48dba4-60ef-4201-aa87-54103eef594e')
        interface = guid('30cbe57d-d9d0-452a-ab13-7ac5ac4825ee')
        auto = c.c_void_p()
        check(ole.CoCreateInstance(c.byref(factory), None, 1, c.byref(interface), c.byref(auto)))
        owned.append(auto)
        element = c.c_void_p()
        check(method(auto, 6, w.HWND, c.POINTER(c.c_void_p))(auto, hwnd, c.byref(element)))
        owned.append(element)
        pattern = c.c_void_p()
        check(method(element, 16, c.c_int, c.POINTER(c.c_void_p))(element, 10000, c.byref(pattern)))
        if not pattern.value: raise ValueError('InvokePattern unavailable')
        owned.append(pattern)
        iid = guid('fb377fbe-8ea6-46d5-9c73-6499642d3059')
        action = c.c_void_p()
        check(method(pattern, 0, c.POINTER(GUID), c.POINTER(c.c_void_p))(pattern, c.byref(iid), c.byref(action)))
        owned.append(action)
        check(method(action, 3)(action))
    finally:
        for pointer in reversed(owned): method(pointer, 2)(pointer)
        ole.CoUninitialize()
    return dict(method='Windows COM UIA InvokePattern', pid=pid, hwnd=hwnd)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--pid', type=int, required=True)
    parser.add_argument('--hwnd', type=int, required=True)
    args = parser.parse_args()
    print(json.dumps(invoke(args.pid, args.hwnd)))
