"""Pre-Qt gate logic regression; synthetic files and mocked ACL adapter only."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from scripts import gui_bootstrap_trust as gui
from scripts import windows_install_trust as trust
from scripts import verify_windows_package as v

SCRATCH = Path(tempfile.gettempdir())


class GuiBootstrapTrustTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(dir=SCRATCH, prefix='trust-')
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.package = self.root / 'App'
        keys = sorted(gui.REQUIRED)
        for key in keys:
            path = self.package / key
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'non-executable synthetic bootstrap input')
        closure = self.package / gui.CLOSURE
        closure.parent.mkdir(parents=True, exist_ok=True)
        closure.write_text(json.dumps({'schema':'mentorflow.gui-bootstrap-closure.v1', 'files':keys}), encoding='utf-8')
        keys.append(gui.CLOSURE)
        hashes = {key:hashlib.sha256((self.package/key).read_bytes()).hexdigest() for key in keys}
        manifest = self.package / v.MANIFEST_RELATIVE
        manifest.write_text(json.dumps({'schema':v.MANIFEST_SCHEMA,'product':'MentorFlow',
            'files':[{'path':key,'sha256':digest} for key,digest in hashes.items()]}), encoding='utf-8')
        self.identities = dict(manifest_sha256=v.sha256_file(manifest), source_sha='a'*40, tree_sha='b'*40)
        receipt = {'binding':dict(self.identities, schema=trust.LEGACY_SCHEMA, root=str(self.root),
            root_identity=[self.root.stat().st_dev,self.root.stat().st_ino]),
            'critical_hashes':hashes,'full_verified':True}
        (self.root/trust.RECEIPT).write_text(json.dumps(receipt), encoding='utf-8')
        for name, replacement in (('_root',lambda path:self.root),('assert_safe_parents',lambda path:None),('assert_protected',lambda path:None)):
            p = patch.object(trust,name,replacement)
            p.start()
            self.addCleanup(p.stop)

    def verify(self):
        return gui.verify_gui_bootstrap(self.root,executable=self.package/'MentorFlow.exe',**self.identities)

    def test_exact_synthetic_closure_accepted_without_loading_qt(self):
        self.assertEqual(self.verify(),self.package)

    def test_prepaint_location_does_not_hash_or_enumerate_package(self):
        with patch.object(v, 'sha256_file', side_effect=AssertionError('prepaint hashing')):
            self.assertEqual(gui.protected_gui_location(self.root,
                executable=self.package/'MentorFlow.exe'), self.package)

    def test_daily_entry_never_calls_full_inventory_verifier(self):
        import ast
        source = Path(__file__).resolve().parents[1] / 'desktop_gui_entry.py'
        tree = ast.parse(source.read_text(encoding='utf-8'))
        imported = {alias.name for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
                    for alias in node.names}
        self.assertNotIn('verify_installation', imported)
        self.assertIn('verify_gui_bootstrap', imported)

    def test_exe_and_qt_tampering_rejected(self):
        for key in ('MentorFlow.exe','_internal/PySide6/Qt6Core.dll'):
            path = self.package/key
            original = path.read_bytes()
            path.write_bytes(b'tampered')
            with self.assertRaises(trust.RepairRequired): self.verify()
            path.write_bytes(original)

    def test_manifest_receipt_closure_and_unknown_plugin_rejected(self):
        for path in (self.package/v.MANIFEST_RELATIVE,self.package/gui.CLOSURE,self.root/trust.RECEIPT):
            original=path.read_bytes()
            path.write_bytes(b'{}')
            with self.assertRaises((trust.RepairRequired,KeyError)): self.verify()
            path.write_bytes(original)
        (self.package/'_internal/PySide6/plugins/platforms/unknown.dll').write_bytes(b'unknown')
        with self.assertRaises(trust.RepairRequired): self.verify()

    def test_acl_failure_never_accepts_bootstrap(self):
        with patch.object(trust,'assert_protected',side_effect=trust.RepairRequired('unprotected')):
            with self.assertRaises(trust.RepairRequired): self.verify()

    def test_closure_path_namespace_and_minimum_set(self):
        for name in ('../escape','/outside','C:/outside','a\\b','a//b'):
            with self.assertRaises(ValueError):
                gui.closure_paths({'schema':'mentorflow.gui-bootstrap-closure.v1','files':sorted(gui.REQUIRED)+[name]})
        with self.assertRaises(ValueError):
            gui.closure_paths({'schema':'mentorflow.gui-bootstrap-closure.v1','files':['MentorFlow.exe']})


if __name__ == '__main__': unittest.main()
