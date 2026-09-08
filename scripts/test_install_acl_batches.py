"""Batched ACL reads cannot skip paths, cache authorization or swallow failure."""
from pathlib import Path
import threading
import unittest
from unittest.mock import patch
from scripts import windows_install_trust as trust


class AclBatchTests(unittest.TestCase):
    def test_every_path_read_again_including_tail_batch(self):
        paths = [Path(f'entry-{i}') for i in range(193)]
        lock = threading.Lock()
        seen = []
        def read(path):
            with lock:
                seen.append(path)
        with patch.object(trust, 'assert_protected', side_effect=read):
            trust._check_entry_acls(paths)
            self.assertCountEqual(seen, paths)
            seen.clear()
            trust._check_entry_acls(paths)
            self.assertCountEqual(seen, paths)

    def test_failure_from_any_batch_propagates(self):
        paths = [Path(f'entry-{i}') for i in range(193)]
        for bad in (paths[0], paths[64], paths[-1]):
            def read(path):
                if path == bad:
                    raise trust.RepairRequired('changed ACL')
            with self.subTest(path=bad), patch.object(trust, 'assert_protected', side_effect=read):
                with self.assertRaisesRegex(trust.RepairRequired, 'changed ACL'):
                    trust._check_entry_acls(paths)

    def test_small_installation_stays_on_calling_thread(self):
        calling = threading.get_ident()
        paths = [Path('entry')]
        def read(path):
            self.assertEqual(threading.get_ident(), calling)
        with patch.object(trust, 'assert_protected', side_effect=read) as check:
            trust._check_entry_acls(paths)
            check.assert_called_once_with(paths[0])

    def test_real_parallel_acl_drift_stops_before_payload_hash(self):
        from scripts.test_windows_install_trust import InstallTrustTests
        from scripts import verify_windows_package as v
        InstallTrustTests.setUpClass()
        fixture = InstallTrustTests()
        fixture.setUp()
        app = fixture.root / 'App'
        for index in range(130):
            (app / f'extra-{index}.txt').write_bytes(b'isolated non-executable fixture')
        fixture.kw['manifest_sha256'] = v.sha256_file(v.build_manifest(app))
        trust.protect_installation(fixture.root)
        fixture.verify(activate=True)
        self.assertEqual(fixture.verify()['mode'], 'FAST')
        try:
            with patch.object(trust, 'SDDL', trust.SDDL.replace('0x1200a9', 'FA')):
                with self.assertRaises(trust.RepairRequired):
                    trust._set_protected(app / 'extra-129.txt')
            with patch.object(v, '_hash_gate_entries') as hash_payload:
                with self.assertRaisesRegex(trust.RepairRequired, 'write policy'):
                    fixture.verify()
                hash_payload.assert_not_called()
        finally:
            trust.protect_installation(fixture.root)


if __name__ == '__main__':
    unittest.main()
