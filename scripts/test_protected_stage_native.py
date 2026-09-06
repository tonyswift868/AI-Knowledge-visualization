"""Compile the staging primitive with existing Windows .NET, then inspect ACLs."""
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import uuid
from scripts import windows_install_trust as trust
from scripts.test_windows_install_trust import InstallTrustTests, TEST_ROOT, normal_token


class ProtectedStageNativeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        InstallTrustTests.setUpClass()
        cls.scratch = Path(tempfile.mkdtemp(prefix='e107-native-stage-', dir='E:/Codex/Temp'))
        cls.exe = cls.scratch / 'StageChecks.exe'
        installer = Path(__file__).resolve().parents[1] / 'installer'
        result = subprocess.run([
            'C:/Windows/Microsoft.NET/Framework64/v4.0.30319/csc.exe',
            '/nologo', '/target:exe', '/platform:x64', '/out:' + str(cls.exe),
            str(installer / 'ProtectedStage.cs'), str(installer / 'ProtectedStageChecks.cs'),
        ], capture_output=True, timeout=30)
        if result.returncode:
            raise RuntimeError(result.stdout.decode(errors='replace') + result.stderr.decode(errors='replace'))

    def test_atomic_protection_and_no_overwrite_or_path_alias(self):
        result = subprocess.run([str(self.exe), str(TEST_ROOT)], capture_output=True, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)
        self.assertEqual(result.returncode, 0, result.stderr)
        root = Path(result.stdout.decode().strip())
        self.assertEqual(root.parent, TEST_ROOT)
        self.assertTrue(root.name.startswith('MentorFlow-stage-'))
        trust.assert_safe_parents(root)
        for path in [root, *root.rglob('*')]:
            # Inspect the native extended-path result explicitly. This is not
            # an end-to-end long-path qualification of the Python installer.
            trust.assert_protected(Path('\\\\?\\' + str(path)))
        good = root / 'nested/ok.bin'
        self.assertEqual(good.read_bytes(), b'\x01\x02\x03')
        self.assertEqual(good.stat().st_nlink, 1)
        deep = root / 'deep' / ('a' * 120) / ('b' * 120) / 'ok.bin'
        self.assertGreater(len(str(deep)), 260)
        self.assertEqual(Path('\\\\?\\' + str(deep)).read_bytes(), b'\x01\x02\x03')
        with normal_token(), self.assertRaises(PermissionError):
            good.write_bytes(b'ordinary token write must fail')
        self.assertEqual(good.read_bytes(), b'\x01\x02\x03')

    def test_untrusted_parent_rejected_without_creating_stage(self):
        parent = self.scratch / 'ordinary-owned-parent'
        parent.mkdir()
        result = subprocess.run([str(self.exe), str(parent)], capture_output=True, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)
        self.assertEqual(result.returncode, 3)
        self.assertEqual(list(parent.iterdir()), [])

    def test_parent_delete_right_rejected_even_with_admin_owner(self):
        parent = TEST_ROOT / ('stage-parent-' + uuid.uuid4().hex)
        trust._create_protected(parent, directory=True)
        try:
            with patch.object(trust, 'SDDL', trust.SDDL.replace('0x1200a9', '0x1300a9')):
                with self.assertRaises(trust.RepairRequired):
                    trust._set_protected(parent)
            result = subprocess.run([str(self.exe), str(parent)], capture_output=True, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)
            self.assertEqual(result.returncode, 3)
            self.assertIn(b'ordinary-user replacement', result.stderr)
            self.assertEqual(list(parent.iterdir()), [])
        finally:
            trust._set_protected(parent)


if __name__ == '__main__':
    unittest.main()
