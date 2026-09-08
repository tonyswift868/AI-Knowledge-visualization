"""Deny-only session signal held across GUI exit by the update coordinator.

An untrusted event can only defer; it never authorizes an update.
"""
import ctypes
from ctypes import wintypes
import os
import json
import re
import threading
import time
from pathlib import Path


def record_session_deferred(stage, pid, release_hash):
    """UI retry hint only. Never an authorization or an executable path."""
    from .update_download import safe_entry
    from .update_protocol import SecurityError
    root = Path(stage).parent
    path = root / f'ui-session-{int(pid)}.json'
    try:
        safe_entry(path, root)
        with path.open('w', encoding='utf-8') as stream:
            json.dump(dict(release=release_hash, deferred_at_ns=time.time_ns()), stream)
            stream.flush()
            os.fsync(stream.fileno())
        return True
    except (OSError, SecurityError):
        return False  # Never obstruct OS exit or transaction cleanup for a hint.


def was_session_deferred(runtime_root, release_hash, pid, attempted_at_ns):
    """Bounded advisory read; callers must still check signed metadata/hashes."""
    from .update_download import safe_entry
    from .update_protocol import SecurityError
    try:
        if not re.fullmatch('[0-9a-f]{64}', str(release_hash)):
            return False
        pid, attempted_at_ns = int(pid), int(attempted_at_ns)
        if not 0 < pid <= 0xffffffff or not 0 < attempted_at_ns <= time.time_ns():
            return False
        root = Path(runtime_root)
        path = root / release_hash[:12] / f'ui-session-{pid}.json'
        if not safe_entry(path, root) or path.stat().st_size > 1024:
            return False
        with path.open(encoding='utf-8') as stream:
            doc = json.loads(stream.read(1025))
        deferred = doc.get('deferred_at_ns')
        return (doc.get('release') == release_hash and type(deferred) is int and
                attempted_at_ns <= deferred <= time.time_ns())
    except (OSError, ValueError, TypeError, AttributeError, SecurityError):
        return False


class SessionEnding(RuntimeError):
    pass


class UnavailableSession:
    """Guard setup failure disables apply, not ordinary application startup."""
    def defer(self):
        pass

    def require_running(self):
        raise SessionEnding('Update session guard unavailable')

    def close(self):
        pass


class UpdateSession:
    def __init__(self, pid=None):
        self._ending = threading.Event()
        self._handle = None
        self._monitor = None
        self._monitor_window = None
        self._monitor_stop = threading.Event()
        if os.name == 'nt':
            self._kernel = ctypes.WinDLL('kernel32', use_last_error=True)
            self._kernel.CreateEventW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
            self._kernel.CreateEventW.restype = wintypes.HANDLE
            self._kernel.OpenEventW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
            self._kernel.OpenEventW.restype = wintypes.HANDLE
            self._kernel.SetEvent.argtypes = [wintypes.HANDLE]
            self._kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
            self._kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            name = f'Local\\MentorFlow.UpdateSessionEnding.{pid or os.getpid()}'
            self._handle = (self._kernel.OpenEventW(0x100002, False, name) if pid
                            else self._kernel.CreateEventW(None, True, False, name))
            if not self._handle:
                raise OSError('Update session guard unavailable')
            if pid:
                # BASE may have already exited when Windows begins logoff.
                # A hidden top-level (not message-only) window still receives
                # session broadcasts and can veto only the update, not logoff.
                ready = threading.Event()
                self._monitor = threading.Thread(target=self._watch_session, args=(ready,), daemon=True,
                                                 name='mentorflow-update-session')
                self._monitor.start()
                if not ready.wait(2) or self._monitor_window is None:
                    self.close()
                    raise OSError('Update session monitor unavailable')

    def _watch_session(self, ready):
        user = ctypes.WinDLL('user32', use_last_error=True)
        callback_type = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT,
                                          wintypes.WPARAM, wintypes.LPARAM)
        class WindowClass(ctypes.Structure):
            _fields_ = [('style', wintypes.UINT), ('procedure', callback_type),
                        ('class_extra', ctypes.c_int), ('window_extra', ctypes.c_int),
                        ('instance', wintypes.HINSTANCE), ('icon', wintypes.HICON),
                        ('cursor', wintypes.HANDLE), ('brush', wintypes.HANDLE),
                        ('menu', wintypes.LPCWSTR), ('name', wintypes.LPCWSTR)]
        user.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        user.DefWindowProcW.restype = ctypes.c_ssize_t
        user.RegisterClassW.argtypes = [ctypes.POINTER(WindowClass)]
        user.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR,
            wintypes.DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, ctypes.c_void_p]
        user.CreateWindowExW.restype = wintypes.HWND
        user.DestroyWindow.argtypes = [wintypes.HWND]
        user.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
        user.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
        user.UnregisterClassW.argtypes = [wintypes.LPCWSTR, wintypes.HINSTANCE]
        self._kernel.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        self._kernel.GetModuleHandleW.restype = wintypes.HMODULE
        instance = self._kernel.GetModuleHandleW(None)
        name = f'MentorFlowUpdateSession_{os.getpid()}_{id(self)}'
        @callback_type
        def procedure(window, message, wp, lp):
            if message == 0x11 or message == 0x16 and wp:
                self.defer()
                return 1
            if message == 0x10:
                user.DestroyWindow(window)
                return 0
            if message == 2:
                user.PostQuitMessage(0)
                return 0
            return user.DefWindowProcW(window, message, wp, lp)
        registered = False
        try:
            cls = WindowClass(procedure=procedure, instance=instance, name=name)
            registered = bool(user.RegisterClassW(ctypes.byref(cls)))
            if not registered:
                return
            self._monitor_window = user.CreateWindowExW(0, name, '', 0, 0, 0, 0, 0, None, None, instance, None)
            ready.set()
            message = wintypes.MSG()
            while self._monitor_window and not self._monitor_stop.is_set():
                result = user.GetMessageW(ctypes.byref(message), None, 0, 0)
                if result <= 0:
                    break
                user.DispatchMessageW(ctypes.byref(message))
        finally:
            ready.set()
            if self._monitor_window:
                user.DestroyWindow(self._monitor_window)
                self._monitor_window = None
            if registered:
                user.UnregisterClassW(name, instance)

    def defer(self):
        self._ending.set()
        if self._handle:
            self._kernel.SetEvent(self._handle)

    def require_running(self):
        ending = self._ending.is_set()
        if os.name == 'nt':
            ending = ending or bool(ctypes.WinDLL('user32').GetSystemMetrics(0x2000))
            ending = ending or not self._handle or self._kernel.WaitForSingleObject(self._handle, 0) != 258
        if ending:
            raise SessionEnding('Update deferred until next normal launch')

    def close(self):
        self._monitor_stop.set()
        if self._monitor_window:
            user = ctypes.WinDLL('user32')
            user.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
            user.PostMessageW(self._monitor_window, 0x10, 0, 0)
        if self._monitor is not None:
            self._monitor.join(timeout=2)
        if self._handle:
            self._kernel.CloseHandle(self._handle)
            self._handle = None
