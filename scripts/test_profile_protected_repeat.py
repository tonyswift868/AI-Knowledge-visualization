import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

from scripts import profile_protected_repeat as profile


class RepeatProfileTests(unittest.TestCase):
    def test_real_wrapped_functions_are_called_and_restored(self):
        trust = profile.trust
        with patch.object(trust, '_root', return_value='root') as root, \
             patch.object(trust, '_check_entry_acls') as acl, \
             patch.object(trust, '_identity', return_value='digest') as identity:
            def snapshot(*args):
                trust._check_entry_acls(['file'])
            with patch.object(trust, '_snapshot', side_effect=snapshot) as snap:
                def verify(path, **kwargs):
                    trust._root(path)
                    trust._snapshot(path)
                    trust._identity({})
                    return dict(total_seconds=1, hash_seconds=0.5, verification='PASS')
                with patch.object(trust, 'verify_installation', side_effect=verify):
                    result = profile.measure('isolated', {})
                self.assertIs(trust._snapshot, snap)
            self.assertIs(trust._root, root)
            root.assert_called_once_with('isolated')
            acl.assert_called_once_with(['file'])
            identity.assert_called_once_with({})
            self.assertEqual(result['verification'], 'PASS')
            self.assertGreaterEqual(result['phases_seconds']['snapshot_except_entry_acl'], 0)

    def test_rejection_propagates_and_wrappers_are_restored(self):
        original = profile.trust._snapshot
        with patch.object(profile.trust, 'verify_installation', side_effect=profile.trust.RepairRequired('tamper')):
            with self.assertRaises(profile.trust.RepairRequired):
                profile.measure('isolated', {})
        self.assertIs(profile.trust._snapshot, original)

    def test_hash_detail_preserves_digest_prefix_and_restores_hash(self):
        original = profile.verifier.sha256_file
        with tempfile.TemporaryDirectory(dir='E:/Codex/Temp') as directory:
            root = Path(directory)
            path = root / 'synthetic.bin'
            path.write_bytes(b'isolated synthetic bytes')
            expected = original(path)
            prefix = bytearray()
            def verify(*args, **kwargs):
                profile.trust._root(root)
                profile.trust._snapshot(root)
                profile.trust._check_entry_acls([])
                profile.trust._identity({})
                self.assertEqual(profile.verifier.sha256_file(path, prefix=prefix), expected)
                return dict(total_seconds=1, hash_seconds=0.5)
            with patch.object(profile.trust, '_root'), patch.object(profile.trust, '_snapshot'), \
                 patch.object(profile.trust, '_check_entry_acls'), patch.object(profile.trust, '_identity'), \
                 patch.object(profile.trust, 'verify_installation', side_effect=verify):
                result = profile.measure(root, {}, hash_detail=True)
            self.assertEqual(prefix, b'isolated synthet')
            self.assertEqual(result['hash_calls'], 1)
            self.assertEqual(result['slowest_hash_calls'][0]['bytes'], path.stat().st_size)
            self.assertIs(profile.verifier.sha256_file, original)


if __name__ == '__main__':
    unittest.main()
