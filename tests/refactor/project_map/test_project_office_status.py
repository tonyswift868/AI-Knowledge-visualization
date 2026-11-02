from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "project_map" / "office_status.py"
TASK_TMP = Path("D:/MentorFlow/.local/test-tmp/map288/office-status")
TASK_TMP.mkdir(parents=True, exist_ok=True)
spec = importlib.util.spec_from_file_location("project_map_office_status", SCRIPT)
office_status = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(office_status)


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True)
    return result.stdout.strip()


class OfficeStatusTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="office-status-", dir=TASK_TMP)
        self.root = Path(self.temp.name)
        self.main = self.root / "main repo"
        self.office = self.root / "office root"
        self.main.mkdir()
        git(self.main, "init", "-b", "main")
        git(self.main, "config", "user.name", "Test")
        git(self.main, "config", "user.email", "test@example.invalid")
        (self.main / "base.txt").write_text("base\n", encoding="utf-8")
        git(self.main, "add", "base.txt")
        git(self.main, "commit", "-m", "main base")
        self.main_oid = git(self.main, "rev-parse", "HEAD")
        for name in office_status.STATIONS:
            clone = self.office / name / "repo"
            clone.parent.mkdir(parents=True)
            subprocess.run(["git", "clone", "-q", str(self.main), str(clone)], check=True)
            git(clone, "config", "user.name", "Test")
            git(clone, "config", "user.email", "test@example.invalid")
            git(clone, "checkout", "-q", "-b", f"office/{name}")

    def tearDown(self):
        self.temp.cleanup()

    def test_default_roots_and_output_follow_script_repository(self):
        expected_repo = SCRIPT.parents[2]
        self.assertEqual(office_status.DEFAULT_MAIN, expected_repo)
        self.assertEqual(office_status.DEFAULT_OUT, expected_repo / ".local" / "project-map")
        self.assertEqual(office_status.DEFAULT_OFFICE, Path("D:/MentorFlow-office"))

    def test_porcelain_parser_keeps_spaces_and_both_rename_paths(self):
        raw = b"R  new name.txt\0old name.txt\0?? untracked file.txt\0"
        self.assertEqual(office_status._dirty_paths(raw), ["new name.txt", "old name.txt", "untracked file.txt"])

    def test_collect_reports_seven_branches_and_dirty_files_using_readonly_git(self):
        repo = self.office / "classroom" / "repo"
        (repo / "rename old.txt").write_text("same body\n", encoding="utf-8")
        git(repo, "add", "rename old.txt")
        git(repo, "commit", "-m", "station-only commit")
        os.rename(repo / "rename old.txt", repo / "rename new.txt")
        git(repo, "add", "-A")
        (repo / "untracked file.txt").write_text("local\n", encoding="utf-8")

        commands = []
        real_run = office_status.subprocess.run

        def observe(command, *args, **kwargs):
            if command and command[0] == "git":
                commands.append(command)
            if command and command[0] == "git":
                env = kwargs["env"]
                self.assertEqual(env["GIT_OPTIONAL_LOCKS"], "0")
                self.assertIn("--no-optional-locks", command)
                self.assertIn("core.fsmonitor=false", command)
            return real_run(command, *args, **kwargs)

        with patch.object(office_status.subprocess, "run", side_effect=observe):
            result = office_status.collect(self.main, self.office)
        self.assertEqual(len(result["stations"]), 7)
        self.assertTrue(all(row["available"] for row in result["stations"]))
        self.assertTrue(all(row["comparison"]["available"] for row in result["stations"]))
        self.assertEqual(result["stations"][0]["comparison"]["ahead"], 1)
        self.assertEqual(result["stations"][0]["comparison"]["behind"], 0)
        self.assertIn("rename old.txt", result["stations"][0]["dirty_files"])
        self.assertIn("rename new.txt", result["stations"][0]["dirty_files"])
        self.assertIn("untracked file.txt", result["stations"][0]["dirty_files"])
        self.assertTrue(any("status" in command for command in commands))
        self.assertFalse(any("fetch" in command for command in commands))

    def test_absent_main_object_is_unavailable_without_fetch(self):
        other_main = self.root / "other main"
        other_main.mkdir()
        git(other_main, "init", "-b", "main")
        result = office_status._station_status("client", self.office / "client" / "repo", "f" * 40)
        self.assertFalse(result["comparison"]["available"])
        self.assertIn("未 fetch", result["comparison"]["reason"])

    def test_output_can_be_changed_but_reserved_roots_are_rejected(self):
        target = self.root / "custom output"
        self.assertEqual(office_status._ensure_out(target, office_root=self.office), target)
        for forbidden in (Path("D:/MentorFlowApp/cache"), Path("D:/MentorFlowData/account-shell"),
                          Path("D:/MentorFlow/.local/interview"),
                          self.office / "client" / "repo" / ".local"):
            with self.subTest(forbidden=forbidden):
                with self.assertRaises(ValueError):
                    office_status._ensure_out(forbidden, office_root=self.office)

    def test_main_writes_json_and_markdown_to_custom_directory(self):
        output = self.root / "sample output"
        code = office_status.main(["--repo", str(self.main), "--main-repo", str(self.main),
                                   "--office-root", str(self.office), "--out", str(output)])
        self.assertEqual(code, 0)
        self.assertTrue((output / "office-status.json").is_file())
        self.assertIn("七个工位", (output / "office-status.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
