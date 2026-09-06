import unittest
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


if __name__ == '__main__':
    unittest.main()
