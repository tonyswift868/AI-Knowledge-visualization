"""Visible native status and cancellation tests; synthetic installs only."""
import ctypes as c
from ctypes import wintypes as w
from pathlib import Path
import unittest
from scripts.windows_install_progress import InstallProgress, InstallCancelled
from scripts.test_windows_install_trust import InstallTrustTests
from scripts import windows_install_trust as trust


class InstallProgressTests(unittest.TestCase):
    def test_activation_command_uses_installation_status(self):
        from unittest.mock import patch
        from contextlib import redirect_stdout
        from io import StringIO
        InstallTrustTests.setUpClass()
        fixture = InstallTrustTests()
        fixture.setUp()
        args = ['activate', '--install-root', str(fixture.root)]
        for key, value in fixture.kw.items():
            args.extend(['--' + key.replace('_', '-'), value])
        with patch('scripts.windows_install_progress.InstallProgress') as progress, redirect_stdout(StringIO()):
            self.assertEqual(trust.main(args), 0)
            self.assertGreaterEqual(progress.return_value.__enter__.return_value.update.call_count, 3)

    def test_visible_nonblank_status_and_close_cancellation(self):
        with InstallProgress() as status:
            self.assertTrue(status.api.IsWindowVisible(status.window))
            self.assertTrue(status.api.IsWindowVisible(status.label))
            status.update("正在执行完整验证，请稍候。")
            status.api.GetWindowTextW.argtypes = [w.HWND, w.LPWSTR, c.c_int]
            message = c.create_unicode_buffer(256)
            self.assertGreater(status.api.GetWindowTextW(status.label, message, len(message)), 0)
            self.assertEqual(message.value, "正在执行完整验证，请稍候。")
            status.api.DestroyWindow(status.window)
            with self.assertRaises(InstallCancelled):
                status.update("must not continue")
        self.assertFalse(status.api.IsWindow(status.window))

    def test_cancel_during_full_hash_never_publishes_receipt(self):
        InstallTrustTests.setUpClass()
        fixture = InstallTrustTests()
        fixture.setUp()
        destination = fixture.root / "cancelled-install"
        messages = []
        def cancel(message):
            messages.append(message)
            if "SHA-256" in message:
                raise InstallCancelled("test cancellation")
        with self.assertRaises(InstallCancelled):
            trust.install_package(fixture.root / "App", destination, progress=cancel, **fixture.kw)
        self.assertTrue(any("复制" in message for message in messages))
        self.assertFalse((destination / trust.RECEIPT).exists())
        trust.assert_protected(destination)
        with self.assertRaises(trust.RepairRequired):
            trust.verify_installation(destination, **fixture.kw)


if __name__ == "__main__":
    unittest.main()
