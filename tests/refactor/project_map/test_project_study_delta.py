import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).parents[3] / "scripts" / "project_map" / "study_delta.py"
TEST_TMP = Path("D:/MentorFlow/.local/test-tmp/map288")
SPEC = importlib.util.spec_from_file_location("study_delta286", SCRIPT)
study_delta = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(study_delta)


def git(repo: Path, *args: str) -> str:
    env = os.environ.copy()
    env.update({"GIT_AUTHOR_NAME": "delta test", "GIT_AUTHOR_EMAIL": "delta@example.invalid",
                "GIT_COMMITTER_NAME": "delta test", "GIT_COMMITTER_EMAIL": "delta@example.invalid"})
    return subprocess.run(["git", "-C", str(repo), *args], check=True, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, env=env, text=True).stdout.strip()


class StudyDeltaTests(unittest.TestCase):
    def setUp(self) -> None:
        TEST_TMP.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="study-delta-", dir=TEST_TMP)
        self.repo = Path(self.temp.name) / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-b", "main")
        (self.repo / "refactor/x").mkdir(parents=True)
        (self.repo / "refactor/known").mkdir(parents=True)
        (self.repo / "docs/learn").mkdir(parents=True)
        (self.repo / "refactor/x/a.py").write_text("def alpha():\n    return 1\n\n\ndef beta():\n    return 2\n", encoding="utf-8")
        (self.repo / "refactor/x/sample.bin").write_bytes(b"old-binary\x00\x01")
        (self.repo / "refactor/known/rag.py").write_text("rag = 1\n", encoding="utf-8")
        (self.repo / "docs/learn/one.md").write_text("lesson one\n", encoding="utf-8")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-m", "initial module")
        self.base = git(self.repo, "rev-parse", "HEAD")

        (self.repo / "refactor/x/a.py").unlink()
        (self.repo / "refactor/x/b.py").write_text("def alpha():\n    return 3\n\n\ndef beta():\n    return 2\n", encoding="utf-8")
        (self.repo / "refactor/x/sample.bin").write_bytes(b"new-binary\x00\x02")
        (self.repo / "refactor/known/rag.py").write_text("rag = 2\n", encoding="utf-8")
        (self.repo / "docs/learn/one.md").unlink()
        (self.repo / "docs/learn/two.md").write_text("lesson two\n", encoding="utf-8")
        (self.repo / "refactor/unmapped.py").write_text("x = 1\n", encoding="utf-8")
        (self.repo / "tests/refactor").mkdir(parents=True)
        (self.repo / "tests/refactor/test_unmapped.py").write_text("assert True\n", encoding="utf-8")
        (self.repo / ".github/workflows").mkdir(parents=True)
        (self.repo / ".github/workflows/check.yml").write_text("name: check\n", encoding="utf-8")
        (self.repo / "misc").mkdir()
        (self.repo / "misc/metadata.json").write_text("{}\n", encoding="utf-8")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-m", "BUG-286 update module")
        self.head = git(self.repo, "rev-parse", "HEAD")
        self.modules = {"modules": [
            {"id": "M01", "title": "code", "paths": ["refactor/x"]},
            {"id": "M02", "title": "docs", "paths": ["docs/learn"]},
            {"id": "M03", "title": "shared code map", "paths": ["refactor/x"]},
            {"id": "M07", "title": "RAG", "paths": ["refactor/known"]},
        ], "learned_modules": ["M01", "M02"]}

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_summarizes_rename_delete_add_functions_and_commit_clue(self) -> None:
        result = study_delta.analyze(self.repo, self.base, self.head, self.modules)
        code, docs = result["modules"]
        self.assertEqual(code["changes"][0]["status"], "R")
        self.assertEqual(code["changes"][0]["old_path"], "refactor/x/a.py")
        self.assertEqual(code["changes"][0]["new_path"], "refactor/x/b.py")
        self.assertGreater(code["stats"]["insertions"], 0)
        self.assertEqual(code["stats"]["binary_files"], 1)
        self.assertIn("alpha", [item["name"] for item in code["function_contexts"]["names"]])
        self.assertNotIn("return", [item["name"] for item in code["function_contexts"]["names"]])
        self.assertEqual(code["commit_summaries"]["shown"][0]["issue_ids"], ["BUG-286"])
        self.assertEqual({c["status"] for c in docs["changes"]}, {"D", "A"})
        unassigned = result["change_coverage"]["unassigned_changes"]
        self.assertEqual({row["new_path"] for row in unassigned}, {
            "refactor/unmapped.py", "tests/refactor/test_unmapped.py",
            ".github/workflows/check.yml", "misc/metadata.json",
        })
        self.assertTrue(all(row["insertions"] == 1 and row["deletions"] == 0 for row in unassigned))
        self.assertTrue(all(row["related_commits"]["count"] == 1 for row in unassigned))
        self.assertTrue(all(row["related_commits"]["count"] == 1
                            for row in result["change_coverage"]["selected_module_paths"]))
        self.assertTrue(result["change_coverage"]["accounting_reconciles"])
        self.assertEqual(result["change_coverage"]["changed_file_records"],
                         result["change_coverage"]["selected_module_records"]
                         + result["change_coverage"]["known_unlearned_records"]
                         + result["change_coverage"]["unassigned_records"])
        self.assertEqual(result["summary"]["known_but_unlearned_paths"][0]["new_path"], "refactor/known/rag.py")

    def test_module_learned_at_overrides_default_base_per_module(self) -> None:
        modules = json.loads(json.dumps(self.modules))
        modules["modules"][0]["learned_at"] = self.head
        result = study_delta.analyze(self.repo, self.base, self.head, modules)
        code, docs = result["modules"]
        self.assertEqual(code["comparison"]["from_input"], self.head)
        self.assertEqual(code["comparison"]["from_commit"], self.head)
        self.assertEqual(code["changes"], [])
        self.assertEqual(docs["comparison"]["from_input"], self.base)
        self.assertTrue(docs["changes"])

    def test_shared_module_details_deduplicate_global_paths_and_cli_override(self) -> None:
        result = study_delta.analyze(self.repo, self.base, self.head, self.modules, learned_override=["M01", "M03"])
        self.assertEqual(result["selected_modules"], ["M01", "M03"])
        self.assertEqual(len(result["modules"][0]["changes"]), len(result["modules"][1]["changes"]))
        self.assertEqual(result["summary"]["selected_unique_changed_file_paths"], 3)

    def test_move_outside_old_mapping_keeps_rename_and_new_location(self) -> None:
        target = self.repo / "refactor/relocated.py"
        git(self.repo, "mv", "refactor/x/b.py", "refactor/relocated.py")
        git(self.repo, "commit", "-m", "move implementation without rewriting it")
        moved = git(self.repo, "rev-parse", "HEAD")
        mapping = {"modules": [{"id": "M01", "paths": ["refactor/x/b.py"]}]}
        module = study_delta.analyze(self.repo, self.head, moved, mapping)["modules"][0]
        self.assertEqual(module["changes"], [{"status": "R", "old_path": "refactor/x/b.py", "new_path": "refactor/relocated.py", "rename_score": "100"}])
        self.assertEqual(module["stats"]["insertions"], 0)
        self.assertEqual(module["stats"]["deletions"], 0)
        self.assertEqual(module["change_share_estimate"]["changed_existing_path_count"], 1)
        self.assertTrue(target.is_file())

    def test_binary_and_rename_numstat_keep_a_record_for_each_file(self) -> None:
        result = study_delta.analyze(self.repo, self.base, self.head, self.modules)
        coverage = result["change_coverage"]
        self.assertEqual(coverage["changed_file_records"], len(coverage["selected_module_paths"])
                         + len(coverage["known_but_unlearned_paths"])
                         + len(coverage["unassigned_changes"]))
        rename = next(row for row in coverage["selected_module_paths"] if row["status"] == "R")
        self.assertEqual(rename["insertions"], 1)
        self.assertEqual(rename["deletions"], 1)
        binary = next(row for row in coverage["selected_module_paths"] if row["binary"])
        self.assertIsNone(binary["insertions"])
        self.assertIsNone(binary["deletions"])

    def test_cli_writes_markdown_and_json_to_selected_output_dir(self) -> None:
        out = Path(self.temp.name) / "out"
        command = [sys.executable, "-B", str(SCRIPT), "--repo", str(self.repo),
                   "--from", self.base, "--to", self.head,
                   "--modules", json.dumps(self.modules), "--out", str(out)]
        result = subprocess.run(command, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.assertTrue((out / "study-delta.json").is_file())
        self.assertTrue((out / "study-delta.md").is_file())
        data = json.loads((out / "study-delta.json").read_text(encoding="utf-8"))
        self.assertEqual(data["change_coverage"]["unassigned_records"], 4)

    def test_module_map_covers_primary_directory_prefixes(self) -> None:
        module_file = SCRIPT.with_name("study_modules.json")
        modules, _ = study_delta._load_modules(module_file)
        cases = [
            ("M02", "refactor/learning/course/new_question.py"),
            ("M04", "refactor/server/migrations/versions/0047_new.py"),
            ("M04", "scripts/release/sqlite_runtime.py"),
            ("M06", "refactor/server-java/src/main/java/example/Contract.java"),
            ("M07", "refactor/server-java/src/main/java/win/clannad/mentorflow/rag/RagService.java"),
            ("M08", "refactor/learning/connections/providers/new_provider.py"),
            ("M09", "refactor/server-java/deploy/new.yml"),
            ("M10", "refactor/desktop/packaging/new.iss"),
            ("M10", "refactor/learning/web/src/updates/new.tsx"),
        ]
        by_id = {module["id"]: module for module in modules}
        for module_id, path in cases:
            self.assertTrue(study_delta._matches(path, by_id[module_id]["paths"]),
                            f"{path} should be mapped to {module_id}")

    def test_module_input_size_and_output_boundaries(self) -> None:
        too_large = Path(self.temp.name) / "too-large.json"
        too_large.write_bytes(b" " * (study_delta.MAX_MODULE_FILE_BYTES + 1))
        with self.assertRaisesRegex(study_delta.DeltaError, "1 MiB"):
            study_delta._load_modules(too_large)
        with self.assertRaisesRegex(study_delta.DeltaError, "user data"):
            study_delta._validated_out(Path("D:/MentorFlowApp/MentorFlow/Data"), self.repo)
        with self.assertRaisesRegex(study_delta.DeltaError, "sensitive"):
            study_delta._validated_out(Path("D:/MentorFlow/.local/release-keys"), self.repo)

    def test_modified_existing_file_counts_toward_review_hint(self) -> None:
        mapping = {"modules": [{"id": "M01", "paths": ["refactor/x/sample.bin"]}]}
        module = study_delta.analyze(self.repo, self.base, self.head, mapping)["modules"][0]
        self.assertEqual(module["change_share_estimate"]["changed_existing_path_count"], 1)
        self.assertEqual(module["change_share_estimate"]["ratio"], 1.0)
        self.assertTrue(module["change_share_estimate"]["suggest_relearning_review"])

    def test_dirty_worktree_is_reported_but_not_mixed_into_diff(self) -> None:
        baseline = study_delta.analyze(self.repo, self.base, self.head, self.modules)
        (self.repo / "refactor/x/b.py").write_text("def alpha():\n    return 900\n", encoding="utf-8")
        result = study_delta.analyze(self.repo, self.base, self.head, self.modules)
        self.assertTrue(result["working_tree"]["dirty"])
        self.assertEqual(result["modules"], baseline["modules"])
        self.assertEqual(result["summary"], baseline["summary"])

    def test_root_sentinel_and_non_ancestor_report(self) -> None:
        result = study_delta.analyze(self.repo, "ROOT", self.head, self.modules)
        self.assertTrue(result["from"]["root_empty_tree"])
        self.assertTrue(result["history"]["is_ancestor"])
        git(self.repo, "checkout", "--orphan", "other")
        (self.repo / "isolated.txt").write_text("isolated\n", encoding="utf-8")
        git(self.repo, "add", "isolated.txt")
        git(self.repo, "commit", "-m", "independent root")
        other = git(self.repo, "rev-parse", "HEAD")
        non_ancestor = study_delta.analyze(self.repo, self.base, other, self.modules)
        self.assertFalse(non_ancestor["history"]["is_ancestor"])

    def test_invalid_revision_and_path_escape_fail_clearly(self) -> None:
        with self.assertRaisesRegex(study_delta.DeltaError, "revision not found"):
            study_delta.analyze(self.repo, "not-a-real-ref", self.head, self.modules)
        bad_map = {"modules": [{"id": "M01", "paths": ["../outside"]}]}
        with self.assertRaisesRegex(study_delta.DeltaError, "stay inside"):
            study_delta.analyze(self.repo, self.base, self.head, bad_map)


if __name__ == "__main__":
    unittest.main()
