"""Real PowerShell bootstrap tests using a harmless native marker fixture.

No desktop shortcut or production application is started; all links go to Temp.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from scripts.test_windows_install_trust import InstallTrustTests
from scripts import windows_install_trust as trust
from scripts import verify_windows_package as v


class ShortcutBootstrapTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        InstallTrustTests.setUpClass()
        cls.scratch = Path(tempfile.mkdtemp(prefix="e107-bootstrap-", dir="E:/Codex/Temp"))
        source = cls.scratch / "fixture.cs"
        source.write_text('using System; using System.IO; class Fixture { static int Main() { File.WriteAllText(Environment.GetEnvironmentVariable("E107_BOOTSTRAP_MARKER"), "executed"); return 0; } }', encoding="utf-8")
        cls.exe = cls.scratch / "Fixture.exe"
        compiler = Path(os.environ["SystemRoot"]) / "Microsoft.NET/Framework64/v4.0.30319/csc.exe"
        subprocess.run([str(compiler), "/nologo", "/target:winexe", "/out:" + str(cls.exe), str(source)], check=True, capture_output=True, timeout=30)
        cls.powershell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
        cls.script = Path(__file__).with_name("create_protected_install_shortcut.ps1")

    def setUp(self):
        self.fixture = InstallTrustTests()
        self.fixture.setUp()
        self.root = self.fixture.root
        shutil.copyfile(self.exe, self.root / "App/MentorFlowInstallGate.exe")
        anchor = v.sha256_file(v.build_manifest(self.root / "App"))
        self.fixture.kw["manifest_sha256"] = anchor
        trust.protect_installation(self.root)
        self.fixture.verify(activate=True)
        self.marker = self.scratch / (self.root.name + ".marker")
        self.link = self.scratch / (self.root.name + ".lnk")

    def tearDown(self):
        trust.protect_installation(self.root)

    def publish(self):
        env = os.environ.copy()
        env["E107_BOOTSTRAP_MARKER"] = str(self.marker)
        kw = self.fixture.kw
        return subprocess.run([str(self.powershell), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(self.script), "-InstallRoot", str(self.root), "-ManifestSha256", kw["manifest_sha256"], "-SourceSha", kw["source_sha"], "-TreeSha", kw["tree_sha"], "-ShortcutPath", str(self.link)], env=env, capture_output=True, timeout=30)

    def test_protected_manifest_bound_fixture_publishes_only_test_link(self):
        result = self.publish()
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        self.assertTrue(self.marker.exists())
        self.assertTrue(self.link.exists())

    def test_writable_root_never_executes_gate(self):
        with patch.object(trust, "SDDL", trust.SDDL.replace("0x1200a9", "FA")):
            with self.assertRaises(trust.RepairRequired):
                trust._set_protected(self.root)
        result = self.publish()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b'Bootstrap installation write policy rejected', result.stderr)
        self.assertFalse(self.marker.exists())
        self.assertFalse(self.link.exists())

    def test_gate_hash_mismatch_never_executes_gate(self):
        path = self.root / "App/MentorFlowInstallGate.exe"
        path.write_bytes(path.read_bytes() + b"tamper")
        result = self.publish()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b'Bootstrap payload hash mismatch', result.stderr)
        self.assertFalse(self.marker.exists())
        self.assertFalse(self.link.exists())

    def test_renameable_parent_never_executes_gate(self):
        parent = self.root
        nested = parent / "nested-install"
        trust.install_package(parent / "App", nested, **self.fixture.kw)
        self.root = nested
        try:
            with patch.object(trust, "SDDL", trust.SDDL.replace("0x1200a9", "0x1300a9")):
                with self.assertRaises(trust.RepairRequired):
                    trust._set_protected(parent)
            result = self.publish()
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(b'Bootstrap parent permits installation replacement', result.stderr)
            self.assertFalse(self.marker.exists())
            self.assertFalse(self.link.exists())
        finally:
            trust._set_protected(parent)


if __name__ == "__main__":
    unittest.main()
