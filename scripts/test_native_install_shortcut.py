"""Native COM links in protected synthetic directories, never the desktop."""
import ctypes as c
from ctypes import wintypes as w
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import unittest
from unittest.mock import patch
import uuid
from scripts import windows_install_shortcut as link
from scripts import windows_install_trust as trust
from scripts import verify_windows_package as v
from scripts.test_windows_install_trust import InstallTrustTests, TEST_ROOT, normal_token


class NativeShortcutTests(unittest.TestCase):
    def setUp(self):
        InstallTrustTests.setUpClass()
        self.fixture = InstallTrustTests()
        self.fixture.setUp()
        self.root = self.fixture.root
        (self.root / 'App/MentorFlowInstallGate.exe').write_bytes(b'non-executable fixture')
        self.fixture.kw['manifest_sha256'] = v.sha256_file(v.build_manifest(self.root / 'App'))
        trust.protect_installation(self.root)
        self.fixture.verify(activate=True)
        self.desktop = TEST_ROOT / ('links-' + uuid.uuid4().hex)
        trust._create_protected(self.desktop, directory=True)
        self.destination = self.desktop / 'MentorFlow.lnk'

    def test_real_native_link_points_to_gate_not_product_or_shell(self):
        result = link.publish_shortcut(self.root, self.destination, **self.fixture.kw)
        self.assertEqual(result, str(self.destination))
        trust.assert_protected(self.destination)
        self.assertEqual(self.destination.stat().st_nlink, 1)
        self.assertEqual(list(self.desktop.iterdir()), [self.destination])
        # Read back through the Shell interface, without Resolve/Execute.
        ole = c.WinDLL('ole32')
        link._check(ole.CoInitializeEx(None, 2))
        obj, persist = c.c_void_p(), c.c_void_p()
        try:
            clsid = link.Guid.parse('00021401-0000-0000-c000-000000000046')
            iid = link.Guid.parse('000214f9-0000-0000-c000-000000000046')
            link._check(ole.CoCreateInstance(c.byref(clsid), None, 1, c.byref(iid), c.byref(obj)))
            iid_file = link.Guid.parse('0000010b-0000-0000-c000-000000000046')
            link._check(link._method(obj, 0, c.POINTER(link.Guid), c.POINTER(c.c_void_p))(obj, c.byref(iid_file), c.byref(persist)))
            link._check(link._method(persist, 5, w.LPCWSTR, w.DWORD)(persist, str(self.destination), 0))
            target, arguments = c.create_unicode_buffer(4096), c.create_unicode_buffer(4096)
            link._check(link._method(obj, 3, w.LPWSTR, c.c_int, c.c_void_p, w.DWORD)(obj, target, len(target), None, 4))
            link._check(link._method(obj, 10, w.LPWSTR, c.c_int)(obj, arguments, len(arguments)))
            self.assertEqual(Path(target.value), self.root / 'App/MentorFlowInstallGate.exe')
            self.assertTrue(arguments.value.startswith('launch --install-root '))
            for value in self.fixture.kw.values():
                self.assertIn(value, arguments.value)
            self.assertNotIn('powershell', arguments.value.lower())
        finally:
            if persist: link._method(persist, 2)(persist)
            if obj: link._method(obj, 2)(obj)
            ole.CoUninitialize()

    def test_invalid_package_never_reaches_shell_link_publication(self):
        (self.root / 'App/Qt6Core.dll').write_bytes(b'tamper')
        with patch.object(link, '_save_link') as save:
            with self.assertRaises(trust.RepairRequired):
                link.publish_shortcut(self.root, self.destination, **self.fixture.kw)
            save.assert_not_called()
        self.assertFalse(self.destination.exists())

    def test_existing_entry_is_preserved(self):
        self.destination.write_bytes(b'existing user entry')
        with self.assertRaisesRegex(trust.RepairRequired, 'existing shortcut'):
            link.publish_shortcut(self.root, self.destination, **self.fixture.kw)
        self.assertEqual(self.destination.read_bytes(), b'existing user entry')

    def test_install_cli_activates_before_publishing_native_link(self):
        destination = TEST_ROOT / ('installed-' + uuid.uuid4().hex)
        arguments = ['install', '--install-root', str(destination), '--payload-source', str(self.root / 'App'), '--shortcut-path', str(self.destination), '--quiet-install']
        for key, value in self.fixture.kw.items():
            arguments.extend(['--' + key.replace('_', '-'), value])
        with redirect_stdout(StringIO()):
            self.assertEqual(trust.main(arguments), 0)
        self.assertTrue((destination / trust.RECEIPT).exists())
        self.assertTrue(self.destination.exists())
        self.assertEqual(trust.verify_installation(destination, **self.fixture.kw)['mode'], 'FAST')

    def test_normal_token_cannot_publish(self):
        with normal_token(), self.assertRaisesRegex(trust.RepairRequired, 'requires elevation'):
            link.publish_shortcut(self.root, self.destination, **self.fixture.kw)
        self.assertFalse(self.destination.exists())

    def test_entry_created_during_publication_is_not_overwritten(self):
        save = link._save_link
        def competing_entry(*args):
            save(*args)
            self.destination.write_bytes(b'concurrently published entry')
        with patch.object(link, '_save_link', side_effect=competing_entry):
            with self.assertRaises(FileExistsError):
                link.publish_shortcut(self.root, self.destination, **self.fixture.kw)
        self.assertEqual(self.destination.read_bytes(), b'concurrently published entry')
        self.assertEqual(list(self.desktop.iterdir()), [self.destination])

    def test_non_install_command_cannot_request_shortcut_mutation(self):
        with redirect_stdout(StringIO()):
            self.assertEqual(trust.main(['gate', '--install-root', str(self.root), '--shortcut-path', str(self.destination)]), 3)
        self.assertFalse(self.destination.exists())


if __name__ == '__main__':
    unittest.main()
