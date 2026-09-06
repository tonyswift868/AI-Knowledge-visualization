"""Offline installed-launch contract checks; no account/backend imports."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from scripts import windows_installed_launch as launch


class InstalledLaunchTests(unittest.TestCase):
    def test_external_profile_schema(self):
        with tempfile.TemporaryDirectory(dir="E:/Codex/Temp") as folder:
            path = Path(folder) / "launch.json"
            self.assertEqual(launch.read_profile(path), "personal")
            path.write_text('{"schema":"mentorflow.desktop-runtime.v1","data_profile":"device-one"}', encoding="utf-8")
            self.assertEqual(launch.read_profile(path), "device-one")
            for raw in [
                '{"schema":"mentorflow.desktop-runtime.v1","issuer":"http://evil"}',
                '{"schema":"mentorflow.desktop-runtime.v1","data_profile":"../other"}',
                '{"data_profile":"one","data_profile":"two"}',
                '{"schema":"mentorflow.desktop-runtime.v1","data_profile":"one","token":"secret"}',
            ]:
                path.write_text(raw, encoding="utf-8")
                with self.assertRaises(ValueError):
                    launch.read_profile(path)

    def test_child_environment_removes_account_and_loader_injection(self):
        with patch.dict(os.environ, {"PYTHONPATH": "untrusted", "QT_PLUGIN_PATH": "untrusted", "MENTORFLOW_WORKSPACE_ID": "test-workspace", "SSL_CERT_FILE": "untrusted"}):
            env = launch.clean_environment(Path("E:/Codex/Temp"))
        for name in ("PYTHONPATH", "QT_PLUGIN_PATH", "MENTORFLOW_WORKSPACE_ID", "SSL_CERT_FILE"):
            self.assertNotIn(name, env)

    def test_launcher_source_has_no_powershell_or_elevation(self):
        import inspect
        source = inspect.getsource(launch.launch)
        self.assertNotIn("powershell", source.lower())
        self.assertNotIn("runas", source.lower())
        self.assertLess(source.index("verify()"), source.index("return subprocess.call"))
        self.assertIn("--cacert", source)
        self.assertIn("StrictHostKeyChecking=yes", source)


if __name__ == "__main__":
    unittest.main()
