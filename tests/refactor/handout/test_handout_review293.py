from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))
from scripts.handout.check import check_handout  # noqa: E402
from scripts.handout.generate import AUTO_END, AUTO_START, TODO_END, TODO_START, digest  # noqa: E402


class HandoutReview293Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.out = Path(self.temp.name)
        page = self.out / "01-files" / "_root.md"
        page.parent.mkdir(parents=True)
        self.page = page
        self.auto_body = "## Folder purpose\n"
        self.todo_id = "S00001"
        page.write_text(
            AUTO_START.format("folder") + "\n" + self.auto_body + AUTO_END + "\n\n"
            + TODO_START.format(self.todo_id) + "\n\n" + TODO_END + "\n",
            encoding="utf-8",
        )
        immutable = self.out / "02-calls.json"
        immutable.write_text("{}\n", encoding="utf-8")
        manifest = {
            "automatic": [{"file": "01-files/_root.md", "id": "folder", "sha256": digest(self.auto_body)}],
            "slots": [{"id": self.todo_id, "file": "01-files/_root.md", "label": "folder purpose", "kind": "folder"}],
            "immutable_files": [{"file": immutable.name, "sha256": self._sha(immutable)}],
        }
        (self.out / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def _sha(path):
        import hashlib
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def test_missing_marker_and_auto_edit_are_reported(self):
        self.page.write_text("## Folder purpose\n", encoding="utf-8")
        result = check_handout(self.out, allow_unfilled=True)
        self.assertFalse(result["ok"])
        self.assertTrue(any("缺少自动块" in item or "标记不完整" in item for item in result["errors"]))

    def test_annotations_can_appear_outside_auto_blocks(self):
        text = self.page.read_text(encoding="utf-8")
        text = text.replace(TODO_START.format(self.todo_id) + "\n\n", TODO_START.format(self.todo_id) + "\nFolder intro.\n")
        text += "\n> 【dots 批注｜2026-11-02｜abcdef0】这里是批注\n"
        self.page.write_text(text, encoding="utf-8")
        result = check_handout(self.out)
        self.assertTrue(result["ok"], result["errors"])
        self.assertEqual(1, result["filled"])

    def test_invalid_annotation_prefix_and_changed_auto_are_reported(self):
        text = self.page.read_text(encoding="utf-8")
        text = text.replace(self.auto_body, "## Edited folder purpose\n")
        text = text.replace(TODO_START.format(self.todo_id) + "\n\n", TODO_START.format(self.todo_id) + "\nDescription\n")
        text += "\n> 【dots 批注｜yesterday｜abcdef0】bad format\n"
        self.page.write_text(text, encoding="utf-8")
        result = check_handout(self.out)
        self.assertFalse(result["ok"])
        self.assertTrue(any("哈希改变" in item for item in result["errors"]))
        self.assertTrue(any("未按格式" in item for item in result["errors"]))

    def test_manifest_slot_points_to_the_exact_marked_page(self):
        text = self.page.read_text(encoding="utf-8")
        text = text.replace(TODO_START.format(self.todo_id) + "\n\n", TODO_START.format(self.todo_id) + "\nFilled description\n")
        self.page.write_text(text, encoding="utf-8")
        result = check_handout(self.out)
        self.assertTrue(result["ok"], result["errors"])
        self.assertEqual(self.page.relative_to(self.out).as_posix(), "01-files/_root.md")
        self.assertEqual(1, result["slots"])


if __name__ == "__main__":
    unittest.main()
