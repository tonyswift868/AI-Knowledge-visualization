import ctypes
from pathlib import Path
import subprocess
import tempfile
import sys
import unittest


class FormProbeTests(unittest.TestCase):
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
