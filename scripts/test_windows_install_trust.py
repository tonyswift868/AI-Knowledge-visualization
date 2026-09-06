"""Standalone unittest: synthetic protected installs, no backend conftest/DB."""
import ctypes as c
from ctypes import wintypes as w
from contextlib import contextmanager
import json
import os
from pathlib import Path
import tempfile
import unittest

from scripts import verify_windows_package as v
from scripts import windows_install_trust as trust

TEST_ROOT = Path("E:/MentorFlow-TrustTest-20260906")
ICON = "_internal/PySide6/qml/QtCharts/designer/images/test.png"


@contextmanager
def normal_token():
    """Exercise filesystem operations with this user's normal desktop token."""
    adv, kernel = trust._api()
    token, linked, impersonation = w.HANDLE(), w.HANDLE(), w.HANDLE()
    size = w.DWORD()
    kernel.GetCurrentProcess.restype = w.HANDLE
    kernel.CloseHandle.argtypes = [w.HANDLE]
    adv.OpenProcessToken.argtypes = [w.HANDLE, w.DWORD, c.POINTER(w.HANDLE)]
    adv.GetTokenInformation.argtypes = [w.HANDLE, c.c_int, c.c_void_p, w.DWORD, c.POINTER(w.DWORD)]
    adv.ImpersonateLoggedOnUser.argtypes = [w.HANDLE]
    adv.DuplicateTokenEx.argtypes = [w.HANDLE, w.DWORD, c.c_void_p, c.c_int, c.c_int, c.POINTER(w.HANDLE)]
    if not adv.OpenProcessToken(kernel.GetCurrentProcess(), 0x0008, c.byref(token)):
        raise RuntimeError("cannot query current token")
    impersonated = False
    try:
        # TokenLinkedToken is identification-only on this host (1346). Use the
        # existing shell token, verifying the same user before impersonation.
        user32 = c.WinDLL("user32", use_last_error=True)
        user32.GetShellWindow.restype = w.HWND
        user32.GetWindowThreadProcessId.argtypes = [w.HWND, c.POINTER(w.DWORD)]
        pid = w.DWORD()
        user32.GetWindowThreadProcessId(user32.GetShellWindow(), c.byref(pid))
        kernel.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
        kernel.OpenProcess.restype = w.HANDLE
        process = kernel.OpenProcess(0x1000, False, pid.value)
        if not process:
            raise RuntimeError("normal desktop shell unavailable")
        try:
            if not adv.OpenProcessToken(process, 0xA, c.byref(linked)):
                raise RuntimeError("normal shell token unavailable")
        finally:
            kernel.CloseHandle(process)
        identities = []
        for handle in (token, linked):
            buf = c.create_string_buffer(1024)
            if not adv.GetTokenInformation(handle, 1, buf, len(buf), c.byref(size)):
                raise RuntimeError("cannot verify token user")
            identities.append(buf)
        adv.EqualSid.argtypes = [c.c_void_p, c.c_void_p]
        if not adv.EqualSid(c.c_void_p.from_buffer(identities[0]), c.c_void_p.from_buffer(identities[1])):
            raise RuntimeError("shell token belongs to another user")
        if not adv.DuplicateTokenEx(linked, 0xE, None, 2, 2, c.byref(impersonation)):
            raise RuntimeError(f"cannot duplicate linked token: {c.get_last_error()}")
        if not adv.ImpersonateLoggedOnUser(impersonation):
            raise RuntimeError(f"cannot impersonate desktop token: {c.get_last_error()}")
        impersonated = True
        if c.WinDLL("shell32").IsUserAnAdmin():
            raise RuntimeError("write test token is still elevated")
        yield impersonation
    finally:
        if impersonated:
            adv.RevertToSelf()
        if linked:
            kernel.CloseHandle(linked)
        if impersonation:
            kernel.CloseHandle(impersonation)
        kernel.CloseHandle(token)


class InstallTrustTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not TEST_ROOT.exists():
            TEST_ROOT.mkdir()
            trust.protect_installation(TEST_ROOT)
        trust.assert_protected(TEST_ROOT)

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="synthetic-", dir=TEST_ROOT))
        app = self.root / "App"
        app.mkdir()
        for name in ["MentorFlow.exe", "Qt6Core.dll", "native.pyd", "SELF-CHECK/MentorFlowSelfCheck.exe", "SELF-CHECK/verify_windows_package.py", "SELF-CHECK/mentorflow-check.bat", "SELF-CHECK/mentorflow-update.bat", ICON]:
            path = app / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"synthetic non-executable fixture")
        (app / v.VERSION_FILE_NAME).write_text(json.dumps({"schema": "mentorflow.version.v1", "product": "MentorFlow", "version": "1.2.3"}), encoding="utf-8")
        self.anchor = v.sha256_file(v.build_manifest(app))
        trust.protect_installation(self.root)
        self.kw = dict(manifest_sha256=self.anchor, source_sha="1" * 40, tree_sha="2" * 40)

    def verify(self, **kwargs):
        return trust.verify_installation(self.root, **(self.kw | kwargs))

    def test_install_then_fast(self):
        self.assertEqual(self.verify(activate=True)["mode"], "INSTALL_FULL")
        result = self.verify()
        self.assertEqual(result["mode"], "FAST")
        self.assertFalse(result["full_hash"])
        self.assertLess(result["files_hashed"], result["files_total"])

    def test_atomic_new_install_driver_and_existing_target_refusal(self):
        destination = self.root / "new-version"
        result = trust.install_package(self.root / "App", destination, **self.kw)
        self.assertEqual(result["mode"], "INSTALL_FULL")
        self.assertEqual(trust.verify_installation(destination, **self.kw)["mode"], "FAST")
        with normal_token():
            trust.assert_protected(destination / "App/MentorFlow.exe")
        with self.assertRaisesRegex(trust.RepairRequired, "new dedicated"):
            trust.install_package(self.root / "App", destination, **self.kw)

    def test_normal_token_cannot_write_replace_delete_or_activate(self):
        self.verify(activate=True)
        with normal_token():
            kernel = trust._api()[1]
            kernel.CreateFileW.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD, c.c_void_p, w.DWORD, w.DWORD, w.HANDLE]
            kernel.CreateFileW.restype = w.HANDLE
            kernel.DeleteFileW.argtypes = [w.LPCWSTR]
            for path in [self.root / trust.RECEIPT, self.root / "App/MentorFlow.exe", self.root / "App" / ICON, self.root / "App" / v.MANIFEST_RELATIVE]:
                handle = kernel.CreateFileW(str(path), 0x40000000, 7, None, 3, 0, None)
                error = c.get_last_error()
                if handle != c.c_void_p(-1).value:
                    kernel.CloseHandle(handle)
                    self.fail("normal token obtained a write handle")
                self.assertEqual(error, 5, str(path))
                self.assertFalse(kernel.DeleteFileW(str(path)))
                self.assertEqual(c.get_last_error(), 5)
            handle = kernel.CreateFileW(str(self.root / "App/unknown.py"), 0x40000000, 7, None, 1, 0, None)
            error = c.get_last_error()
            if handle != c.c_void_p(-1).value:
                kernel.CloseHandle(handle)
                self.fail("normal token created an unknown file")
            self.assertEqual(error, 5)
            with self.assertRaises(trust.RepairRequired):
                self.verify(activate=True)
            self.assertEqual(self.verify()["mode"], "FAST")

    def test_exe_dll_pyd_rejected(self):
        self.verify(activate=True)
        for name in ["MentorFlow.exe", "Qt6Core.dll", "native.pyd"]:
            path = self.root / "App" / name
            original = path.read_bytes()
            path.write_bytes(b"X" * len(original))
            with self.assertRaises(trust.RepairRequired):
                self.verify()
            with self.assertRaisesRegex(trust.RepairRequired, "tampered"):
                self.verify(activate=True)
            path.write_bytes(original)
            self.verify(activate=True)

    def test_static_elevated_damage_invalidates_then_full_rejects(self):
        self.verify(activate=True)
        path = self.root / "App" / ICON
        before = path.stat()
        path.write_bytes(b"X" * before.st_size)
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
        with self.assertRaisesRegex(trust.RepairRequired, "receipt invalid"):
            self.verify()
        with self.assertRaisesRegex(trust.RepairRequired, "tampered"):
            self.verify(activate=True)

    def test_critical_hash_rejects_restored_mtime(self):
        self.verify(activate=True)
        path = self.root / "App/Qt6Core.dll"
        before = path.stat()
        path.write_bytes(b"X" * before.st_size)
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
        with self.assertRaisesRegex(trust.RepairRequired, "tampered"):
            self.verify()

    def test_manifest_tamper(self):
        self.verify(activate=True)
        path = self.root / "App" / v.MANIFEST_RELATIVE
        path.write_bytes(path.read_bytes() + b" ")
        with self.assertRaisesRegex(trust.RepairRequired, "manifest changed"):
            self.verify()

    def test_receipt_forgery_and_deletion(self):
        self.verify(activate=True)
        path = self.root / trust.RECEIPT
        path.write_text('{"full_verified":true}', encoding="utf-8")
        with self.assertRaisesRegex(trust.RepairRequired, "receipt invalid"):
            self.verify()
        self.verify(activate=True)
        path.unlink()
        with self.assertRaisesRegex(trust.RepairRequired, "receipt invalid"):
            self.verify()

    def test_acl_drift_rejected(self):
        self.verify(activate=True)
        from unittest.mock import patch
        weak = trust.SDDL.replace("0x1200a9", "FA")
        with patch.object(trust, "SDDL", weak):
            with self.assertRaises(trust.RepairRequired):
                trust._set_protected(self.root / "App/MentorFlow.exe")
        with self.assertRaisesRegex(trust.RepairRequired, "write policy"):
            self.verify()
        trust.protect_installation(self.root)

    def test_root_acl_drift_rejected(self):
        self.verify(activate=True)
        from unittest.mock import patch
        with patch.object(trust, "SDDL", trust.SDDL.replace("0x1200a9", "FA")):
            with self.assertRaises(trust.RepairRequired):
                trust._set_protected(self.root)
        with self.assertRaisesRegex(trust.RepairRequired, "write policy"):
            self.verify()
        trust.protect_installation(self.root)

    def test_parent_delete_permission_rejected(self):
        from unittest.mock import patch
        with patch.object(trust, "SDDL", trust.SDDL.replace("0x1200a9", "0x1300a9")):
            with self.assertRaises(trust.RepairRequired):
                trust._set_protected(self.root)
        with self.assertRaisesRegex(trust.RepairRequired, "replace installation"):
            trust.assert_safe_parents(self.root / "App")
        trust.protect_installation(self.root)

    def test_real_new_version_needs_new_full_activation(self):
        self.verify(activate=True)
        app = self.root / "App"
        version_path = app / v.VERSION_FILE_NAME
        version = json.loads(version_path.read_bytes())
        version["version"] = "1.2.4"
        version_path.write_text(json.dumps(version), encoding="utf-8")
        anchor = v.sha256_file(v.build_manifest(app))
        trust.protect_installation(self.root)
        with self.assertRaisesRegex(trust.RepairRequired, "receipt invalid"):
            self.verify(manifest_sha256=anchor)
        self.assertEqual(self.verify(manifest_sha256=anchor, activate=True)["mode"], "INSTALL_FULL")
        self.assertEqual(self.verify(manifest_sha256=anchor)["mode"], "FAST")

    def test_unknown_and_new_identity(self):
        self.verify(activate=True)
        for name in ["unknown.exe", "unknown.dll", "unknown.py", "unknown.pyd", "config.json", "qmldir"]:
            path = self.root / "App" / name
            path.write_text("unknown", encoding="utf-8")
            trust._set_protected(path)
            with self.assertRaises(trust.RepairRequired):
                self.verify()
            path.unlink()
        with self.assertRaisesRegex(trust.RepairRequired, "receipt invalid"):
            self.verify(source_sha="3" * 40)
        with self.assertRaisesRegex(trust.RepairRequired, "manifest changed"):
            self.verify(manifest_sha256="a" * 64)

    def test_classification_is_contextual_and_fail_closed(self):
        self.assertEqual(trust.category(ICON), "STATIC_RESOURCE")
        for name in ["icon.png", "prompt.txt", "_internal/story_packs/icon.png", "module.pyd", "security.json", "qmldir", "ui.qml", "plugin.dll", "module.py", ICON.replace(".png", ".svg")]:
            self.assertEqual(trust.category(name), "CRITICAL")

    def test_cached_stat_never_authorizes_lexical_escape(self):
        app = self.root / "App"
        outside = self.root / "outside.bin"
        outside.write_bytes(b"outside")
        for candidate in [outside, app / ".." / "outside.bin"]:
            with self.assertRaises(v.PackageError):
                v._validate_package_entry(candidate, app, directory=False, resolve_path=False, cached_stat=outside.stat())
        for relative in ["MentorFlow.exe", "../outside.bin", str(outside)]:
            with self.assertRaises(v.PackageError):
                v._validate_package_entry(outside, app, directory=False, resolve_path=False, cached_stat=outside.stat(), cached_relative=relative)


if __name__ == "__main__":
    unittest.main()
