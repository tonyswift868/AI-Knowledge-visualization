"""Real frozen install command inside an embedded synthetic product payload."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import uuid
from scripts import prepare_bootstrap_payload as prep
from scripts import windows_install_trust as trust
from scripts import verify_windows_package as v
from scripts.test_windows_install_trust import TEST_ROOT


class EmbeddedInstallNativeTests(unittest.TestCase):
    def test_compiled_pins_to_real_native_install_and_existing_target_refusal(self):
        from scripts.test_native_install_shortcut import NativeShortcutTests
        fixture = NativeShortcutTests()
        fixture.setUp()
        native = Path('E:/MentorFlow-TrustTest-20260906/native-acl-batches')
        self.assertEqual(v.sha256_file(native / 'MentorFlowInstallGate.exe'), '8c989e232048df073b3bbeeb942d43658f7b7f48c92281f2f531b7bdb6a117fd')
        payload = fixture.root / 'App'
        shutil.copytree(native, payload, dirs_exist_ok=True)
        ids = fixture.fixture.kw | {'manifest_sha256': v.sha256_file(v.build_manifest(payload))}
        trust.protect_installation(fixture.root)
        scratch = Path(tempfile.mkdtemp(prefix='e107-embedded-install-', dir='E:/Codex/Temp'))
        prepared = prep.prepare(payload, scratch / 'prepared', **ids)
        source = Path(__file__).resolve().parents[1] / 'installer'
        exe = scratch / 'EmbeddedInstallChecks.exe'
        command = ['C:/Windows/Microsoft.NET/Framework64/v4.0.30319/csc.exe', '/nologo', '/target:exe', '/platform:x64',
                   '/reference:System.IO.Compression.dll', '/out:' + str(exe),
                   '/resource:' + prepared['archive'] + ',MentorFlow.Payload', prepared['identity_source']]
        command += [str(source / name) for name in ('ProtectedStage.cs', 'VerifiedPayload.cs', 'InstallFromEmbedded.cs', 'EmbeddedInstallChecks.cs')]
        compiled = subprocess.run(command, capture_output=True, timeout=30)
        self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)
        root = TEST_ROOT / ('embedded-installed-' + uuid.uuid4().hex)
        # Space-containing destinations also exercise exact Windows argument quoting.
        shortcut = fixture.desktop / 'MentorFlow isolated.lnk'
        command = [str(exe), str(root), str(shortcut), str(TEST_ROOT)]
        result = subprocess.run(command, capture_output=True, timeout=60, creationflags=subprocess.CREATE_NO_WINDOW)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(trust.verify_installation(root, **ids)['mode'], 'FAST')
        trust.assert_protected(shortcut)
        receipt_hash = v.sha256_file(root / trust.RECEIPT)
        again = subprocess.run(command, capture_output=True, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)
        self.assertEqual(again.returncode, 3)
        self.assertEqual(v.sha256_file(root / trust.RECEIPT), receipt_hash)


if __name__ == '__main__':
    unittest.main()
