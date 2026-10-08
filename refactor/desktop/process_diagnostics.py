"""Default desktop lifetime diagnostics; never records tokens or widget titles.

An OS hard kill cannot run Python hooks. A per-process marker lets the next
launch report that case as unexplained, rather than inventing an exit reason.
"""
from __future__ import annotations

import atexit
import ctypes
import faulthandler
import json
import os
from pathlib import Path
import re
import signal
import sys
import threading

from PySide6.QtCore import QEvent, QObject, QTimer, Qt, qInstallMessageHandler
from PySide6.QtWidgets import QWidget

from refactor.runtime_logging import event as diagnostic_event, log_directory, enable_crash_dump


def _qt_text(message):
    text = re.sub(r'(?:https?|file)://\S+', '[URL]', str(message))
    text = re.sub(r'(?i)(bearer\s+|(?:token|password|api[_-]?key)\s*[:=]\s*)[^\s,;]+',
                  r'\1[REDACTED]', text)
    text = re.sub(r'\bsk-[A-Za-z0-9_-]+', '[REDACTED]', text)
    return text[:1000]


def process_sample():
    """Windows working set and native thread count (including Chromium/Qt)."""
    if os.name != 'nt':
        return dict(memory_bytes=0, threads=threading.active_count(), python_threads=threading.active_count())
    from ctypes import wintypes as W
    class Memory(ctypes.Structure):
        _fields_ = [('cb', W.DWORD), ('PageFaultCount', W.DWORD)] + [
            (name, ctypes.c_size_t) for name in ('PeakWorkingSetSize', 'WorkingSetSize',
                'QuotaPeakPagedPoolUsage', 'QuotaPagedPoolUsage', 'QuotaPeakNonPagedPoolUsage',
                'QuotaNonPagedPoolUsage', 'PagefileUsage', 'PeakPagefileUsage')]
    class Thread(ctypes.Structure):
        _fields_ = [('dwSize', W.DWORD), ('cntUsage', W.DWORD), ('th32ThreadID', W.DWORD),
                    ('th32OwnerProcessID', W.DWORD), ('tpBasePri', W.LONG), ('tpDeltaPri', W.LONG), ('dwFlags', W.DWORD)]
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    psapi = ctypes.WinDLL('psapi', use_last_error=True)
    kernel.GetCurrentProcess.restype = W.HANDLE
    psapi.GetProcessMemoryInfo.argtypes = [W.HANDLE, ctypes.POINTER(Memory), W.DWORD]
    sample = Memory(); sample.cb = ctypes.sizeof(sample)
    if not psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(sample), sample.cb):
        raise OSError(ctypes.get_last_error())
    kernel.CreateToolhelp32Snapshot.argtypes = [W.DWORD, W.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = W.HANDLE
    kernel.Thread32First.argtypes = kernel.Thread32Next.argtypes = [W.HANDLE, ctypes.POINTER(Thread)]
    kernel.CloseHandle.argtypes = [W.HANDLE]
    snapshot = kernel.CreateToolhelp32Snapshot(4, 0)
    count = 0
    if snapshot == ctypes.c_void_p(-1).value:
        raise OSError(ctypes.get_last_error())
    try:
        thread = Thread(); thread.dwSize = ctypes.sizeof(thread)
        found = kernel.Thread32First(snapshot, ctypes.byref(thread))
        while found:
            count += int(thread.th32OwnerProcessID == os.getpid())
            found = kernel.Thread32Next(snapshot, ctypes.byref(thread))
    finally:
        kernel.CloseHandle(snapshot)
    return dict(memory_bytes=sample.WorkingSetSize, threads=count, python_threads=threading.active_count())


def _alive(pid):
    if os.name != 'nt':
        try: os.kill(pid, 0); return True
        except OSError: return False
    from ctypes import wintypes as W
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]; kernel.OpenProcess.restype = W.HANDLE
    kernel.GetExitCodeProcess.argtypes = [W.HANDLE, ctypes.POINTER(W.DWORD)]
    kernel.CloseHandle.argtypes = [W.HANDLE]
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle: return ctypes.get_last_error() == 5  # access denied is not evidence of death
    try:
        code = W.DWORD()
        return not kernel.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value == 259
    finally: kernel.CloseHandle(handle)


class ProcessDiagnostics(QObject):
    def __init__(self, data_root):
        super().__init__()
        directory = log_directory(data_root)
        directory.mkdir(parents=True, exist_ok=True)
        self.directory, self.app, self.reason = directory, None, None
        self.last_window = 'UNKNOWN'
        self.last_window_id = 0
        self._window_sequence = 0
        self.primary = None
        self._finished = False
        self._marker = directory / f'process-{os.getpid()}.json'
        self._fault_was_enabled = faulthandler.is_enabled()
        self._crash = enable_crash_dump(data_root)
        self._previous_qt = qInstallMessageHandler(self.qt_message)
        self._previous_signals = {}
        for number in (signal.SIGINT, signal.SIGTERM):
            self._previous_signals[number] = signal.signal(number, self.signal_received)
        self._console_callback = None
        self._console_kernel = None
        self._install_console_handler()
        atexit.register(self.python_exit)

    def attach(self, app):
        self.app = app
        app._mentorflow_diagnostics = self
        for marker in self.directory.glob('process-*.json'):
            try:
                pid = int(json.loads(marker.read_text(encoding='utf-8'))['pid'])
                if pid != os.getpid() and not _alive(pid):
                    diagnostic_event('exit', reason='PREVIOUS_PROCESS_UNEXPLAINED', previous_pid=pid)
                    marker.unlink()
            except (OSError, ValueError, KeyError, TypeError):
                diagnostic_event('process', code='MARKER_READ_FAILED')
        self._marker.write_text(json.dumps(dict(pid=os.getpid())), encoding='utf-8')
        app.installEventFilter(self)
        app.lastWindowClosed.connect(self.last_window_closed)
        app.aboutToQuit.connect(self.about_to_quit)
        app.commitDataRequest.connect(self.session_end)
        app.setQuitOnLastWindowClosed(False)
        self.timer = QTimer(self)
        self.timer.setInterval(300_000)
        self.timer.timeout.connect(self.sample)
        self.timer.start()
        self.sample()

    def set_primary(self, window, label):
        self.primary = window
        window.setProperty('mf_window_role', label)

    @staticmethod
    def window_label(window):
        return window.property('mf_window_role') or type(window).__name__

    def eventFilter(self, watched, event):  # noqa: N802
        kind = event.type()
        if kind == QEvent.Type.Quit and not self.reason:
            self.record_exit('UNREQUESTED_QT_QUIT_BLOCKED')
            return True
        if isinstance(watched, QWidget) and watched.isWindow():
            label = self.window_label(watched)
            identifier = watched.property('mf_diagnostic_window_id')
            if not identifier:
                self._window_sequence += 1
                identifier = self._window_sequence
                watched.setProperty('mf_diagnostic_window_id', identifier)
            if kind == QEvent.Type.Close:
                self.last_window = label
                self.last_window_id = identifier
                diagnostic_event('window', state='CLOSE', window=label, window_id=identifier,
                                 reason='NATIVE_CLOSE' if event.spontaneous() else 'PROGRAMMATIC_CLOSE')
                if watched is self.primary and not self.reason:
                    if event.spontaneous():
                        self.request_exit('USER_MAIN_CLOSE')
                    else:
                        # Background code must retire a window only after moving
                        # primary to its visible replacement, never close primary.
                        self.record_exit('PROGRAMMATIC_MAIN_CLOSE_BLOCKED', window=label, window_id=identifier)
                        event.ignore()
                        return True
            elif kind in (QEvent.Type.Hide, QEvent.Type.Show):
                diagnostic_event('window', state='HIDDEN' if kind == QEvent.Type.Hide else 'SHOWN', window=label,
                                 window_id=identifier)
        return False

    def record_exit(self, reason, **fields):
        diagnostic_event('exit', reason=reason, pid=os.getpid(), **fields)

    def request_exit(self, reason):
        self.reason = reason
        self.record_exit(reason)
        if self.app is not None:
            # Queue until the close handler has accepted/finished disposal.
            QTimer.singleShot(0, self.app.quit)

    def last_window_closed(self):
        self.record_exit('LAST_WINDOW_CLOSED', window=self.last_window, window_id=self.last_window_id)

    def about_to_quit(self):
        self.record_exit('ABOUT_TO_QUIT', code=self.reason or 'UNREQUESTED')
        self._marker.unlink(missing_ok=True)

    def session_end(self, _manager):
        # On Windows, loading user32 (as Qt does) means console handlers may
        # not receive logoff/shutdown. Qt's session notification covers that.
        self.reason = 'WINDOWS_SESSION_END'
        self.record_exit(self.reason)

    def python_exit(self):
        if self._finished: return
        self._finished = True
        self.record_exit('ATEXIT', code=self.reason or 'PYTHON_EXIT_WITHOUT_QT_QUIT')
        self._marker.unlink(missing_ok=True)
        # Keep the crash descriptor alive through all other atexit handlers.

    def signal_received(self, number, _frame):
        self.request_exit('SIGNAL_TERM' if number == signal.SIGTERM else 'SIGNAL_INTERRUPT')

    def console_event(self, number):
        reason = {0: 'CONSOLE_INTERRUPT', 1: 'CONSOLE_BREAK', 2: 'CONSOLE_CLOSE',
                  5: 'WINDOWS_LOGOFF', 6: 'WINDOWS_SHUTDOWN'}.get(number)
        if reason:
            self.reason = reason
            self.record_exit(reason)
        # Let Windows perform its normal shutdown after synchronously flushing
        # the diagnostic. Do not call Qt from the console control thread.
        return False

    def _install_console_handler(self):
        if os.name != 'nt': return
        from ctypes import wintypes as W
        self._console_callback = ctypes.WINFUNCTYPE(W.BOOL, W.DWORD)(self.console_event)
        self._console_kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        self._console_kernel.SetConsoleCtrlHandler.argtypes = [ctypes.c_void_p, W.BOOL]
        if not self._console_kernel.SetConsoleCtrlHandler(self._console_callback, True):
            diagnostic_event('process', code='CONSOLE_HANDLER_UNAVAILABLE')

    def qt_message(self, kind, _context, message):
        name = getattr(kind, 'name', '')
        if name in {'QtWarningMsg', 'QtCriticalMsg', 'QtFatalMsg'}:
            diagnostic_event('qt', state={'QtWarningMsg':'WARNING', 'QtCriticalMsg':'CRITICAL',
                             'QtFatalMsg':'FATAL'}[name], message=_qt_text(message))
        if self._previous_qt is not None:
            self._previous_qt(kind, _context, message)

    def sample(self):
        try: diagnostic_event('process', pid=os.getpid(), state='ALIVE', **process_sample())
        except Exception: diagnostic_event('process', pid=os.getpid(), code='SAMPLE_FAILED', exc_info=True)

    def uninstall(self):
        """For isolated tests only; production keeps hooks for process lifetime."""
        if self.app:
            self.app.removeEventFilter(self)
            self.app.lastWindowClosed.disconnect(self.last_window_closed)
            self.app.aboutToQuit.disconnect(self.about_to_quit)
            self.app.commitDataRequest.disconnect(self.session_end)
            del self.app._mentorflow_diagnostics
            self.timer.stop()
        qInstallMessageHandler(self._previous_qt)
        for number, handler in self._previous_signals.items(): signal.signal(number, handler)
        if self._console_callback: self._console_kernel.SetConsoleCtrlHandler(self._console_callback, False)
        atexit.unregister(self.python_exit)
        if self._fault_was_enabled and sys.__stderr__ is not None:
            faulthandler.enable(file=sys.__stderr__, all_threads=True)
        else:
            faulthandler.disable()
        self._crash.close()
        self._marker.unlink(missing_ok=True)


def watch_renderer(page, role):
    """Called when each page is created, including transient link/auth pages."""
    page.renderProcessTerminated.connect(lambda status, code: diagnostic_event(
        'renderer', state=getattr(status, 'name', 'UNKNOWN'), exit_code=code, window=role))
