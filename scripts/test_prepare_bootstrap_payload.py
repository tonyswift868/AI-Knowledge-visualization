from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import uuid
import zipfile
from scripts import prepare_bootstrap_payload as prep
from scripts import windows_install_trust as trust
from scripts.test_windows_install_trust import TEST_ROOT


class PrepareBootstrapTests(unittest.TestCase):
    def setUp(self):
        from scripts.test_native_install_shortcut import NativeShortcutTests
        fixture = NativeShortcutTests()
        fixture.setUp()
        self.payload = fixture.root / 'App'
        self.ids = fixture.fixture.kw
        self.scratch = Path(tempfile.mkdtemp(prefix='e107-prepare-bootstrap-', dir='E:/Codex/Temp'))
        self.output = self.scratch / 'prepared'

    def compile_and_run(self, prepared):
        source = Path(__file__).resolve().parents[1] / 'installer'
        exe = self.scratch / 'PreparedChecks.exe'
        command = ['C:/Windows/Microsoft.NET/Framework64/v4.0.30319/csc.exe',
                   '/nologo', '/target:exe', '/platform:x64', '/reference:System.IO.Compression.dll',
                   '/out:' + str(exe), '/resource:' + prepared['archive'] + ',MentorFlow.Payload',
                   prepared['identity_source']]
        command += [str(source / name) for name in ('ProtectedStage.cs', 'VerifiedPayload.cs', 'PreparedPayloadChecks.cs')]
        compiled = subprocess.run(command, capture_output=True, timeout=30)
        self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)
        parent = TEST_ROOT / ('prepared-parent-' + uuid.uuid4().hex)
        trust._create_protected(parent, directory=True)
        result = subprocess.run([str(exe), str(parent)], capture_output=True, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)
        return parent, result

    def test_prepared_archive_and_compiled_identity_round_trip(self):
        prepared = prep.prepare(self.payload, self.output, **self.ids)
        self.assertEqual(prepared['identities']['ManifestSha256'], self.ids['manifest_sha256'])
        self.assertEqual(prepared['identities']['SourceSha'], self.ids['source_sha'])
        self.assertEqual(prepared['identities']['TreeSha'], self.ids['tree_sha'])
        with zipfile.ZipFile(prepared['archive']) as archive:
            for entry in archive.infolist():
                if not entry.is_dir():
                    self.assertEqual(archive.read(entry), (self.payload / entry.filename).read_bytes())
        parent, result = self.compile_and_run(prepared)
        self.assertEqual(result.returncode, 0, result.stderr)
        root = Path(result.stdout.decode().strip())
        self.assertEqual(root.parent, parent)
        trust.assert_protected(root / 'MentorFlowInstallGate.exe')

    def test_changed_embedded_archive_cannot_change_compiled_pin(self):
        prepared = prep.prepare(self.payload, self.output, **self.ids)
        with Path(prepared['archive']).open('ab') as stream:
            stream.write(b'tampered test archive')
        parent, result = self.compile_and_run(prepared)
        self.assertEqual(result.returncode, 3)
        self.assertIn(b'SHA-256 mismatch', result.stderr)
        self.assertEqual(list(parent.iterdir()), [])

    def test_mutation_after_preflight_never_emits_identity(self):
        verify = prep.v.verify_package
        def after_check(*args, **kwargs):
            result = verify(*args, **kwargs)
            (self.payload / 'Qt6Core.dll').write_bytes(b'test mutation after initial check')
            return result
        with patch.object(prep.v, 'verify_package', side_effect=after_check):
            with self.assertRaises(prep.v.PackageError):
                prep.prepare(self.payload, self.output, **self.ids)
        self.assertFalse((self.output / 'ReleaseIdentity.cs').exists())

    def test_bad_identity_rejected_before_output(self):
        with self.assertRaises(ValueError):
            prep.prepare(self.payload, self.output, **(self.ids | {'source_sha': 'not a literal'}))
        self.assertFalse(self.output.exists())
        with self.assertRaises(prep.v.PackageError):
            prep.prepare(self.payload, self.output, **(self.ids | {'manifest_sha256': '0' * 64}))
        self.assertFalse(self.output.exists())


if __name__ == '__main__':
    unittest.main()
