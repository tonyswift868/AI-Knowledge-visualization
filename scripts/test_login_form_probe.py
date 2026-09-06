import ctypes
from pathlib import Path
import subprocess
import tempfile
import sys
import time
import unittest
from unittest.mock import patch
from types import SimpleNamespace


class FormProbeTests(unittest.TestCase):
    def test_oidc_probe_requires_tls_success_and_exact_issuer(self):
        from scripts.measure_installed_login_gate import probe_oidc
        from scripts.windows_installed_launch import ISSUER
        import json
        for code, body, expected in ((0, {'issuer': ISSUER}, True),
                (22, {'issuer': ISSUER}, False), (0, {'issuer': 'https://other.invalid'}, False),
                (0, [], False), (0, 'not json', False)):
            raw = body if isinstance(body, str) else json.dumps(body)
            with patch('scripts.measure_installed_login_gate.subprocess.run',
                       return_value=SimpleNamespace(returncode=code, stdout=raw)) as run:
                self.assertEqual(probe_oidc(Path('E:/isolated')), (code, expected))
                command = run.call_args.args[0]
                self.assertIn('--cacert', command)
                self.assertNotIn('--insecure', command)
                self.assertNotIn('-k', command)

    def test_invalid_dwell_rejects_before_fixture_access(self):
        for options in (['--observe-seconds', '-1'], ['--observe-seconds', '31'],
                        ['--observe-seconds', 'nan'],
                        ['--observe-seconds', '1', '--form-probe', 'unused.exe']):
            result = subprocess.run([sys.executable, '-m', 'scripts.measure_installed_login_gate',
                '--helper', 'unused.exe', *options], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 2)
            self.assertIn('error:', result.stderr)

    def test_actual_synthetic_controls_and_wrong_process_rejection(self):
        scratch = Path(tempfile.mkdtemp(prefix='e107-form-probe-', dir='E:/Codex/Temp'))
        framework = Path('C:/Windows/Microsoft.NET/Framework64/v4.0.30319')
        compiler = framework / 'csc.exe'
        sources = Path(__file__).parent
        probe = scratch / 'probe.exe'
        fixture = scratch / 'fixture.exe'
        renderer = scratch / 'renderer.exe'
        subprocess.run([str(compiler), '/nologo', '/out:' + str(renderer),
            '/reference:System.Drawing.dll', str(sources / 'OwnedWindowRender.cs')],
            check=True, capture_output=True)
        subprocess.run([str(compiler), '/nologo', '/out:' + str(probe),
            *['/reference:' + str(framework / 'WPF' / name) for name in (
                'UIAutomationClient.dll', 'UIAutomationTypes.dll', 'WindowsBase.dll')],
            str(sources / 'LoginFormProbe.cs')], check=True, capture_output=True)
        subprocess.run([str(compiler), '/nologo', '/out:' + str(fixture),
            '/reference:System.Windows.Forms.dll', str(sources / 'LoginFormProbeFixture.cs')],
            check=True, capture_output=True)
        process = subprocess.Popen([str(fixture)], stdout=subprocess.PIPE, text=True,
            creationflags=subprocess.CREATE_NO_WINDOW)
        handle = int(process.stdout.readline().strip())
        try:
            from ctypes import wintypes as w
            from scripts.measure_installed_login_gate import login_window_state
            user = ctypes.WinDLL('user32')
            user.IsWindowVisible.argtypes = [w.HWND]
            user.IsIconic.argtypes = [w.HWND]
            user.GetClientRect.argtypes = [w.HWND, ctypes.POINTER(w.RECT)]
            user.ShowWindowAsync.argtypes = [w.HWND, ctypes.c_int]
            user.GetWindowRect.argtypes = [w.HWND, ctypes.POINTER(w.RECT)]
            user.SetWindowPos.argtypes = [w.HWND, w.HWND, ctypes.c_int, ctypes.c_int,
                                         ctypes.c_int, ctypes.c_int, w.UINT]
            def expect_state(expected):
                deadline = time.monotonic() + 3
                while login_window_state(user, handle) != expected and time.monotonic() < deadline:
                    time.sleep(0.02)
                self.assertEqual(login_window_state(user, handle), expected)
            expect_state('visible')
            user.ShowWindowAsync(handle, 6)
            expect_state('minimized')
            user.ShowWindowAsync(handle, 0)
            expect_state('hidden')
            user.ShowWindowAsync(handle, 9)
            expect_state('visible')
            original_area = w.RECT()
            self.assertTrue(user.GetWindowRect(handle, ctypes.byref(original_area)))
            user.SetWindowPos(handle, None, 0, 0, 150, 150, 0x16)
            expect_state('small')
            user.SetWindowPos(handle, None, 0, 0, original_area.right - original_area.left,
                              original_area.bottom - original_area.top, 0x16)
            expect_state('visible')
            picture = scratch / 'render.png'
            rejected = subprocess.run([str(renderer), str(handle), str(process.pid + 1), str(picture)],
                timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
            self.assertEqual(rejected.returncode, 3)
            self.assertFalse(picture.exists())
            rendered = subprocess.run([str(renderer), str(handle), str(process.pid), str(picture)],
                timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
            self.assertEqual(rendered.returncode, 0)
            from PIL import Image
            with Image.open(picture) as image:
                self.assertGreater(image.width, 100)
                self.assertGreater(len(image.convert('RGB').getcolors(maxcolors=1000000)), 1)
            wrong = subprocess.run([str(probe), str(handle), str(process.pid + 1)],
                capture_output=True, text=True, timeout=35, creationflags=subprocess.CREATE_NO_WINDOW)
            self.assertEqual(wrong.returncode, 3)
            result = subprocess.run([str(probe), str(handle), str(process.pid)],
                capture_output=True, text=True, timeout=35, creationflags=subprocess.CREATE_NO_WINDOW)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(result.stdout.strip(), 'FORM_CONTROLS_FOCUS_READY')
        finally:
            user = ctypes.WinDLL('user32')
            user.PostMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t]
            user.PostMessageW(handle, 0x10, 0, 0)
            process.wait(timeout=10)
            process.stdout.close()


if __name__ == '__main__':
    unittest.main()
