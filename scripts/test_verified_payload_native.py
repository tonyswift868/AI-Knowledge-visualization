"""Existing .NET compiler; only embedded synthetic archives, never product code."""
import hashlib
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest
import uuid
import zipfile
from scripts import windows_install_trust as trust
from scripts.test_windows_install_trust import InstallTrustTests, TEST_ROOT


class VerifiedPayloadNativeTests(unittest.TestCase):
    def run_fixture(self, entries, *, wrong_hash=False):
        InstallTrustTests.setUpClass()
        scratch = Path(tempfile.mkdtemp(prefix='e107-embedded-payload-', dir='E:/Codex/Temp'))
        archive = scratch / 'payload.zip'
        with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED) as stream:
            for name, data, mode in entries:
                info = zipfile.ZipInfo(name)
                info.external_attr = mode << 16
                stream.writestr(info, data)
        parent = TEST_ROOT / ('payload-parent-' + uuid.uuid4().hex)
        trust._create_protected(parent, directory=True)
        installer = Path(__file__).resolve().parents[1] / 'installer'
        exe = scratch / 'PayloadChecks.exe'
        compile_result = subprocess.run([
            'C:/Windows/Microsoft.NET/Framework64/v4.0.30319/csc.exe',
            '/nologo', '/target:exe', '/platform:x64', '/reference:System.IO.Compression.dll',
            '/out:' + str(exe), '/resource:' + str(archive) + ',MentorFlow.Payload',
            str(installer / 'ProtectedStage.cs'), str(installer / 'VerifiedPayload.cs'),
            str(installer / 'VerifiedPayloadChecks.cs'),
        ], capture_output=True, timeout=30)
        self.assertEqual(compile_result.returncode, 0, compile_result.stdout + compile_result.stderr)
        pin = '0' * 64 if wrong_hash else hashlib.sha256(archive.read_bytes()).hexdigest()
        result = subprocess.run([str(exe), pin, str(parent)], capture_output=True, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)
        return parent, result

    def test_embedded_valid_files_and_empty_directories_are_protected(self):
        parent, result = self.run_fixture([
            ('MentorFlow.exe', b'non-executable fixture', stat.S_IFREG | 0o644),
            ('_internal/runtime.dll', b'not a DLL', stat.S_IFREG | 0o644),
            ('empty/', b'', stat.S_IFDIR | 0o755),
        ])
        self.assertEqual(result.returncode, 0, result.stderr)
        root = Path(result.stdout.decode().strip())
        self.assertEqual(root.parent, parent)
        self.assertEqual((root / 'MentorFlow.exe').read_bytes(), b'non-executable fixture')
        self.assertTrue((root / 'empty').is_dir())
        for path in [root, *root.rglob('*')]:
            trust.assert_protected(path)

    def test_wrong_anchor_rejected_before_staging(self):
        parent, result = self.run_fixture([('a', b'data', stat.S_IFREG)], wrong_hash=True)
        self.assertEqual(result.returncode, 3)
        self.assertIn(b'SHA-256 mismatch', result.stderr)
        self.assertEqual(list(parent.iterdir()), [])

    def test_unsafe_archive_metadata_rejected_before_staging(self):
        for entries in (
            [('../escape', b'x', stat.S_IFREG)],
            [('a', b'x', stat.S_IFREG), ('A', b'y', stat.S_IFREG)],
            [('a', b'x', stat.S_IFREG), ('a/b', b'y', stat.S_IFREG)],
            [('link', b'target', stat.S_IFLNK | 0o777)],
            [('folder/', b'not empty', stat.S_IFDIR)],
        ):
            with self.subTest(entries=entries):
                parent, result = self.run_fixture(entries)
                self.assertEqual(result.returncode, 3, result.stdout)
                self.assertEqual(list(parent.iterdir()), [])


if __name__ == '__main__':
    unittest.main()
