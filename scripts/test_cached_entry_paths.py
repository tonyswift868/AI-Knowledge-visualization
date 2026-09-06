"""Cached entry paths must retain metadata and namespace checks."""
from pathlib import Path
import stat
from types import SimpleNamespace
import unittest
from scripts import verify_windows_package as v


class CachedEntryPathTests(unittest.TestCase):
    root = Path('E:/isolated-package')

    def check(self, name, *, cached=True, candidate=None, mode=stat.S_IFREG, links=1, attrs=0, directory=False):
        v._validate_package_entry(
            candidate or self.root / name, self.root, directory=directory,
            resolve_path=False,
            cached_relative=name if cached else None,
            cached_stat=SimpleNamespace(st_mode=mode, st_nlink=links, st_file_attributes=attrs),
        )

    def test_canonical_paths_match_generic_validation(self):
        names = ['MentorFlow.exe', '_internal/runtime.dll', 'content/theme.png',
                 '_internal/torch/_vendor/cache/a.txt', 'folder/.hidden', 'folder/a.PYC',
                 'folder/__pycache__/a', 'content/private/a', 'data/account.json',
                 '..pyc', '...pyo', 'a.', '.pyc', 'foo..pyc']
        for name in names:
            with self.subTest(name=name):
                outcomes = []
                for cached in (False, True):
                    try:
                        self.check(name, cached=cached)
                        outcomes.append('PASS')
                    except v.PackageError:
                        outcomes.append('REJECT')
                self.assertEqual(*outcomes)

    def test_traversal_and_mismatched_names_reject(self):
        for name in ('../outside', '/outside', '\\outside', 'C:/outside', 'C:outside', 'a/../b'):
            with self.subTest(name=name), self.assertRaises(v.PackageError):
                self.check(name)
        with self.assertRaises(v.PackageError):
            self.check('a', candidate=self.root.parent / 'a')

    def test_stale_suffixes_fail_closed(self):
        for name in ('a.pyc', '..pyc', '...pyo', 'foo..pyc', 'folder/a.PYC'):
            with self.subTest(name=name), self.assertRaises(v.PackageError):
                self.check(name)

    def test_metadata_checks_are_not_skipped(self):
        for changes in ({'mode': stat.S_IFLNK}, {'mode': stat.S_IFIFO},
                        {'links': 2}, {'attrs': 0x400}, {'directory': True}):
            with self.subTest(changes=changes), self.assertRaises(v.PackageError):
                self.check('a', **changes)
        self.check('folder', mode=stat.S_IFDIR, directory=True)


if __name__ == '__main__':
    unittest.main()
