"""Drive only our synthetic installer process, with explicit isolated targets."""
import ctypes as c
from ctypes import wintypes as w
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest
import uuid
from scripts.build_protected_bootstrap import build
from scripts import verify_windows_package as v
from scripts import windows_install_trust as trust
from scripts.test_windows_install_trust import TEST_ROOT


class BootstrapWindowInstallTests(unittest.TestCase):
    def drive(self, executable, root, shortcut, expected_code):
        user = c.WinDLL('user32', use_last_error=True)
        cb = c.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
        user.EnumWindows.argtypes = [cb, w.LPARAM]
        user.EnumChildWindows.argtypes = [w.HWND, cb, w.LPARAM]
        user.GetWindowThreadProcessId.argtypes = [w.HWND, c.POINTER(w.DWORD)]
        user.GetWindowTextW.argtypes = [w.HWND, w.LPWSTR, c.c_int]
        user.GetClassNameW.argtypes = [w.HWND, w.LPWSTR, c.c_int]
        user.IsWindowVisible.argtypes = [w.HWND]
        user.PostMessageW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
        user.SendMessageW.argtypes = [w.HWND, w.UINT, w.WPARAM, c.c_void_p]
        process = subprocess.Popen([executable], creationflags=subprocess.CREATE_NO_WINDOW)
        window = None
        def collect(parent=None):
            found = []
            @cb
            def inspect(hwnd, _):
                pid = w.DWORD()
                user.GetWindowThreadProcessId(hwnd, c.byref(pid))
                if pid.value == process.pid:
                    text, kind = c.create_unicode_buffer(2048), c.create_unicode_buffer(256)
                    # Cross-process GetWindowText uses cached captions, not edit
                    # contents. WM_GETTEXT reads the live control value.
                    user.SendMessageW(hwnd, 0x0d, len(text), c.cast(text, c.c_void_p))
                    user.GetClassNameW(hwnd, kind, len(kind))
                    found.append((hwnd, text.value, kind.value))
                return True
            if parent is None:
                user.EnumWindows(inspect, 0)
            else:
                user.EnumChildWindows(parent, inspect, 0)
            return found
        try:
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline and process.poll() is None:
                matches = [h for h, text, _ in collect() if text.startswith('MentorFlow 安装') and user.IsWindowVisible(h)]
                if matches:
                    window = matches[0]
                    break
                time.sleep(0.05)
            self.assertIsNotNone(window)
            children = collect(window)
            edits = [(h, text) for h, text, kind in children if kind.startswith('WindowsForms10.EDIT')]
            self.assertEqual(len(edits), 2)
            link_edit = [h for h, text in edits if text.lower().endswith('.lnk')]
            self.assertEqual(len(link_edit), 1)
            root_edit = next(h for h, _ in edits if h != link_edit[0])
            for hwnd, value in ((root_edit, str(root)), (link_edit[0], str(shortcut))):
                buffer = c.create_unicode_buffer(value)
                self.assertNotEqual(user.SendMessageW(hwnd, 0x0c, 0, c.cast(buffer, c.c_void_p)), 0)
            # Confirm BOTH targets before any mutation-triggering click.
            actual = {h: text for h, text, _ in collect(window)}
            self.assertEqual(actual[root_edit], str(root))
            self.assertEqual(actual[link_edit[0]], str(shortcut))
            buttons = [h for h, text, kind in children if text == '安装' and kind.startswith('WindowsForms10.BUTTON')]
            self.assertEqual(len(buttons), 1)
            user.PostMessageW(buttons[0], 0xf5, 0, 0)  # BM_CLICK on our own Install button.
            deadline = time.monotonic() + 60
            observed = False
            while time.monotonic() < deadline and process.poll() is None:
                labels = [text for _, text, _ in collect(window)]
                observed = any(text.startswith('安装完成。') if expected_code == 0 else text.startswith('安装未完成：') for text in labels)
                if observed:
                    break
                time.sleep(0.05)
            self.assertTrue(observed, 'No expected visible terminal installation status')
        finally:
            if window:
                user.PostMessageW(window, 0x10, 0, 0)
            try:
                code = process.wait(timeout=15)
            except subprocess.TimeoutExpired as error:
                raise RuntimeError('Installer remains live: PID ' + str(process.pid)) from error
        self.assertEqual(code, expected_code)

    def test_actual_window_install_and_visible_existing_target_error(self):
        from scripts.test_native_install_shortcut import NativeShortcutTests
        fixture = NativeShortcutTests()
        fixture.setUp()
        native = Path('E:/MentorFlow-TrustTest-20260906/native-acl-batches')
        self.assertEqual(v.sha256_file(native / 'MentorFlowInstallGate.exe'), '8c989e232048df073b3bbeeb942d43658f7b7f48c92281f2f531b7bdb6a117fd')
        payload = fixture.root / 'App'
        shutil.copytree(native, payload, dirs_exist_ok=True)
        ids = fixture.fixture.kw | {'manifest_sha256': v.sha256_file(v.build_manifest(payload))}
        trust.protect_installation(fixture.root)
        scratch = Path(tempfile.mkdtemp(prefix='e107-window-install-', dir='E:/Codex/Temp'))
        prepared = build(payload, scratch / 'build', **ids)
        root = TEST_ROOT / ('ui installed ' + uuid.uuid4().hex)
        shortcut = fixture.desktop / 'UI MentorFlow.lnk'
        self.drive(prepared['executable'], root, shortcut, 0)
        self.assertEqual(trust.verify_installation(root, **ids)['mode'], 'FAST')
        trust.assert_protected(shortcut)
        receipt = v.sha256_file(root / trust.RECEIPT)
        link = v.sha256_file(shortcut)
        self.drive(prepared['executable'], root, shortcut, 3)
        self.assertEqual(v.sha256_file(root / trust.RECEIPT), receipt)
        self.assertEqual(v.sha256_file(shortcut), link)


if __name__ == '__main__':
    unittest.main()
