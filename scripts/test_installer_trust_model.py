"""Reject protected-gate payloads before legacy compiler/output side effects."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


class InstallerTrustModelTests(unittest.TestCase):
    def test_native_payload_refused_before_compiler_or_output(self):
        scratch = Path(tempfile.mkdtemp(prefix='e107-installer-boundary-', dir='E:/Codex/Temp'))
        payload = scratch / 'payload'
        payload.mkdir()
        (payload / 'MentorFlow.exe').write_bytes(b'non-executable fixture')
        gate = payload / 'MentorFlowInstallGate.exe'
        gate.write_bytes(b'non-executable gate fixture')
        output = scratch / 'must-not-be-created'
        compiler = scratch / 'missing-compiler.exe'
        shell = Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
        script = Path(__file__).with_name('build_windows_installer.ps1')
        command = [str(shell), '-NoProfile', '-NonInteractive', '-File', str(script),
                   '-PayloadRoot', str(payload), '-OutputRoot', str(output),
                   '-NsisPath', str(compiler)]
        result = subprocess.run(command, capture_output=True, timeout=30)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b'PROTECTED_INSTALLER_REQUIRED', result.stdout + result.stderr)
        self.assertFalse(output.exists())
        self.assertEqual(gate.read_bytes(), b'non-executable gate fixture')

        installer_source = script.parent.parent / 'installer/MentorFlow.nsi'
        direct_output = scratch / 'must-not-exist.exe'
        direct = subprocess.run([
            'E:/Codex/App/NSIS-3.12/makensis.exe', '/V2',
            '/DPAYLOAD_DIR=' + str(payload), '/DOUTPUT_FILE=' + str(direct_output),
            str(installer_source),
        ], capture_output=True, timeout=30)
        self.assertNotEqual(direct.returncode, 0)
        self.assertIn(b'PROTECTED_INSTALLER_REQUIRED', direct.stdout + direct.stderr)
        self.assertFalse(direct_output.exists())

        # Only this test-owned placeholder is removed, not a real package file.
        gate.unlink()
        legacy = subprocess.run(command, capture_output=True, timeout=30)
        self.assertNotEqual(legacy.returncode, 0)
        self.assertNotIn(b'PROTECTED_INSTALLER_REQUIRED', legacy.stdout + legacy.stderr)
        self.assertIn(b'NSIS 3.12 compiler is missing', legacy.stdout + legacy.stderr)
        self.assertFalse(output.exists())


if __name__ == '__main__':
    unittest.main()
