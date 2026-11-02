from __future__ import annotations

import importlib.util
import shutil
import tempfile
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[3]


def load_annex():
    path = REPO / "scripts" / "handout" / "annex.py"
    spec = importlib.util.spec_from_file_location("annex293_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Annex293Tests(unittest.TestCase):
    def test_generates_structure_without_reading_json_or_sqlite_content(self):
        annex = load_annex()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            snapshot = root / "snapshot"
            project_map = snapshot / "scripts" / "project_map"
            project_map.mkdir(parents=True)
            source_map = REPO / "scripts" / "project_map"
            shutil.copyfile(source_map / "data_map.py", project_map / "data_map.py")
            shutil.copyfile(source_map / "office_status.py", project_map / "office_status.py")
            app_root = root / "app"
            data_root = root / "data"
            app_root.mkdir()
            data_root.mkdir()
            (app_root / "config.json").write_text('{"private_value":"DO_NOT_READ_293"}', encoding="utf-8")
            (data_root / "records.sqlite3").write_bytes(b"sqlite-private-payload-293")
            output = root / "out"

            artifacts = annex.generate_annex(snapshot, output, {
                "app_root": app_root,
                "data_root": data_root,
                "office_root": root / "office",
                "main_repo": root / "main",
                "snapshot_commit": "abc123",
            })

            self.assertEqual(4, len(artifacts))
            markdown = (output / "data-directory-map.md").read_text(encoding="utf-8")
            serialized = (output / "data-directory-map.json").read_text(encoding="utf-8")
            self.assertIn("records.sqlite3", markdown)
            self.assertNotIn("DO_NOT_READ_293", markdown + serialized)
            self.assertNotIn("sqlite-private-payload-293", markdown + serialized)


if __name__ == "__main__":
    unittest.main()
