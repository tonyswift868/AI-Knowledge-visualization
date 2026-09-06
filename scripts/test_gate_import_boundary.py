import json
from contextlib import closing
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from scripts import verify_windows_package as verifier


class GateImportBoundaryTests(unittest.TestCase):
    def test_fresh_verifier_import_does_not_load_database_or_update_network(self):
        result = subprocess.run([sys.executable, '-c',
            "import sys,json; import scripts.verify_windows_package; "
            "print(json.dumps([name for name in ('sqlite3','_sqlite3','urllib.request','ssl') if name in sys.modules]))"],
            check=True, capture_output=True, text=True, timeout=10)
        self.assertEqual(json.loads(result.stdout), [])

    def test_explicit_database_check_still_works_without_modifying_database(self):
        with tempfile.TemporaryDirectory(prefix='e107-import-db-', dir='E:/Codex/Temp') as directory:
            root = Path(directory)
            database = root / 'fixture.db'
            with closing(sqlite3.connect(database)) as connection:
                connection.execute('create table fixture(value integer)')
                connection.execute('insert into fixture values(7)')
                connection.commit()
            original = database.read_bytes()
            result = verifier.check_sqlite_root(root)
            self.assertEqual(result['databases'], 1)
            self.assertEqual(result['errors'], [])
            self.assertEqual(database.read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
