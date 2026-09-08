"""Parsing reuse must never reuse filesystem authorization decisions."""
from pathlib import Path
import unittest
from unittest.mock import patch
from scripts import windows_install_trust as trust
from scripts import verify_windows_package as v


class PureParseTests(unittest.TestCase):
    def test_acl_is_read_again_and_changed_permissions_reject(self):
        good = "O:BAD:P(A;;FA;;;SY)(A;;FA;;;BA)(A;;0x1200a9;;;BU)"
        bad = good.replace("0x1200a9", "FA")
        trust._parts.cache_clear()
        with patch.object(trust, 'security_descriptor', side_effect=[good, good, bad]) as read:
            trust.assert_protected(Path('test-only'))
            trust.assert_protected(Path('test-only'))
            with self.assertRaises(trust.RepairRequired):
                trust.assert_protected(Path('test-only'))
        self.assertEqual(read.call_count, 3)
        self.assertGreater(trust._parts.cache_info().hits, 0)
        self.assertEqual(trust._parts.cache_info().maxsize, 128)

    def test_parsed_entries_are_immutable_and_malformed_acl_rejects(self):
        good = "O:BAD:P(A;;FA;;;SY)(A;;FA;;;BA)(A;;0x1200a9;;;BU)"
        entries = trust._parts(good)[2]
        self.assertIsInstance(entries, tuple)
        self.assertTrue(all(isinstance(row, tuple) for row in entries))
        with self.assertRaises(trust.RepairRequired):
            trust._parts(good.replace('(A;', '(D;', 1))

    def test_normalized_rule_matches_public_path_helper(self):
        for path in ('content/private/a', '_internal/torch/_vendor/cache/a', '_internal/CONTENT/a', '_internal', 'file.txt'):
            parts = tuple(part.casefold() for part in Path(path).parts)
            self.assertEqual(v._application_managed_parts(parts), v._is_application_managed_path(path))


if __name__ == '__main__':
    unittest.main()
