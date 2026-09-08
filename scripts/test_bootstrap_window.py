"""Compile synthetic installer UI; show and close without starting installation."""
import ctypes as c
from ctypes import wintypes as w
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
import xml.etree.ElementTree as ET
import pefile
from scripts.build_protected_bootstrap import build


class BootstrapWindowTests(unittest.TestCase):
    def test_compiled_elevation_manifest_and_visible_close(self):
        from scripts.test_native_install_shortcut import NativeShortcutTests
        fixture = NativeShortcutTests()
        fixture.setUp()
        scratch = Path(tempfile.mkdtemp(prefix='e107-bootstrap-window-', dir='E:/Codex/Temp'))
        result = build(fixture.root / 'App', scratch / 'build', **fixture.fixture.kw)
        executable = result['executable']
        with pefile.PE(executable) as binary:
            manifest = next(entry for entry in binary.DIRECTORY_ENTRY_RESOURCE.entries if entry.id == 24)
            location = manifest.directory.entries[0].directory.entries[0].data.struct
            data = binary.get_data(location.OffsetToData, location.Size)
            level = ET.fromstring(data).find('.//{urn:schemas-microsoft-com:asm.v3}requestedExecutionLevel')
            self.assertEqual(level.attrib['level'], 'requireAdministrator')
        user = c.WinDLL('user32', use_last_error=True)
        callback_type = c.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
        user.EnumWindows.argtypes = [callback_type, w.LPARAM]
        user.GetWindowThreadProcessId.argtypes = [w.HWND, c.POINTER(w.DWORD)]
        user.IsWindowVisible.argtypes = [w.HWND]
        user.GetWindowTextW.argtypes = [w.HWND, w.LPWSTR, c.c_int]
        user.PostMessageW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
        process = subprocess.Popen([executable], creationflags=subprocess.CREATE_NO_WINDOW)
        window = None
        try:
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline and process.poll() is None:
                found = []
                @callback_type
                def inspect(hwnd, _):
                    pid = w.DWORD()
                    user.GetWindowThreadProcessId(hwnd, c.byref(pid))
                    if pid.value == process.pid and user.IsWindowVisible(hwnd):
                        title = c.create_unicode_buffer(512)
                        user.GetWindowTextW(hwnd, title, len(title))
                        if title.value.startswith('MentorFlow 安装'):
                            found.append(hwnd)
                    return True
                user.EnumWindows(inspect, 0)
                if found:
                    window = found[0]
                    break
                time.sleep(0.05)
            self.assertIsNotNone(window, 'Expected actual installer window')
        finally:
            if window:
                user.PostMessageW(window, 0x10, 0, 0)
            # Never press Install. A live process on failure is identified for
            # follow-up rather than silently relabeled as clean exit.
            try:
                code = process.wait(timeout=10)
            except subprocess.TimeoutExpired as error:
                raise RuntimeError('Installer UI remains live: PID ' + str(process.pid)) from error
        self.assertEqual(code, 1)  # Closed before any installation was requested.


if __name__ == '__main__':
    unittest.main()
