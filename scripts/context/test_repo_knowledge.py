"""Offline negative checks for knowledge input and handoff boundaries."""
import json
import contextlib
import io
from pathlib import Path
import re
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import build_compact_handoff as handoff
import check_repo_knowledge as lint


class KnowledgeBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.original_read = handoff.read
        self.board = json.loads(handoff.read(handoff.ROOT, handoff.READ_FIRST[4]))
        self.queue = json.loads(handoff.read(handoff.ROOT, handoff.READ_FIRST[5]))

    def patched_read(self, root, name):
        if name == handoff.READ_FIRST[4]:
            return json.dumps(self.board)
        if name == handoff.READ_FIRST[5]:
            return json.dumps(self.queue)
        return self.original_read(root, name)

    def assert_rejected(self):
        with patch.object(handoff, "read", self.patched_read):
            with self.assertRaises(ValueError):
                handoff.build(handoff.ROOT)

    def test_missing_fields_fail_closed(self):
        del self.board["workers"]["A"]["dispatch_id"]
        self.assert_rejected()

    def test_accepted_delivery_cannot_omit_pending_acceptance(self):
        del self.board['workers']['04']['acceptance_state']
        self.assert_rejected()

    def test_accepted_delivery_requires_exact_integration_pointer(self):
        self.board['workers']['01']['integrated_commit'] = 'short-sha'
        self.assert_rejected()

    def test_current_state_disagreement_rejected(self):
        self.board["baseline_sha"] = "0" * 40
        self.assert_rejected()

    def test_unknown_is_not_inferred_running(self):
        for slot in ("01", "02", "03", "04", "B"):
            self.board["workers"][slot]["status"] = "UNKNOWN"
        with patch.object(handoff, "read", self.patched_read):
            output = handoff.build(handoff.ROOT).decode()
        self.assertIn("UNKNOWN_WORKERS = 01,02,03,04,B", output)
        self.assertNotIn("01:RUNNING", output)

    def test_dependency_cycle_and_false_ready_rejected(self):
        self.queue["tasks"][0]["dependencies"] = ["COMPACT_REPO_HANDOFF"]
        self.assert_rejected()
        self.queue["tasks"][0]["dependencies"] = []
        self.queue["tasks"][0]["state"] = "BLOCKED"
        self.queue["tasks"][1]["state"] = "READY"
        self.assert_rejected()

    def test_duplicate_json_key_rejected(self):
        with self.assertRaises(ValueError):
            json.loads('{"generation": "MAIN3", "generation": "MAIN2"}', object_pairs_hook=handoff.no_duplicate_keys)

    def test_sensitive_inputs_rejected_without_echo(self):
        samples = ["-----BEGIN " + "PRIVATE KEY-----", "pass" + "word=synthetic", "Authorization: " + "Bearer synthetic", "SMTP_" + "KEY=synthetic", '"api_key"' + ': "synthetic"', "xkeysib-" + "synthetic"]
        for value in samples:
            self.assertGreater(handoff.secret_count(value), 0)
            with self.assertRaises(ValueError):
                handoff.build(handoff.ROOT, value)

    def test_overflow_not_truncated(self):
        for worker in self.board["workers"].values():
            worker.update(status="RUNNING", dispatch_id="synthetic-dispatch", task_id="x" * 512)
        self.assert_rejected()

    def test_missing_and_traversing_links_rejected(self):
        root = handoff.ROOT
        text = "[valid](ARCHITECTURE.md) [missing](missing-knowledge-target.md) [outside](../outside.md)"
        with patch.object(lint, "read", return_value=text):
            failures, count = lint.link_failures(root, [root / "AGENTS.md"])
        self.assertEqual(count, 3)
        self.assertEqual(len(failures), 2)
        with self.assertRaises(ValueError):
            handoff.safe_path(root, "../outside.md")


class WebHandoffBoundaryTests(unittest.TestCase):
    """Real disposable Git histories; no network, worker refs or product runtime."""

    def git(self, *args):
        result = subprocess.run(
            ["git", "-C", str(self.root), "-c", "user.name=Knowledge Fixture",
             "-c", "user.email=fixture@example.invalid", "-c", "commit.gpgSign=false",
             "-c", "core.hooksPath=.fixture-no-hooks", *args],
            capture_output=True, text=True, encoding="utf-8", check=True, timeout=15,
        )
        return result.stdout.strip()

    def commit(self):
        self.git("add", ".")
        self.git("commit", "-qm", "synthetic knowledge boundary fixture")
        return self.git("rev-parse", "HEAD")

    def write(self, path, value):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(value, encoding="utf-8")

    def setUp(self):
        scratch = handoff.safe_path(handoff.ROOT, "artifacts/handoff")
        scratch.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="knowledge-test-", dir=scratch)
        self.assertTrue(Path(self.temp.name).resolve().is_relative_to(scratch.resolve()))
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.git("init", "-q", "--initial-branch=integration/main3")
        self.write("seed.txt", "synthetic baseline\n")
        baseline = self.commit()
        baseline_tree = self.git("rev-parse", "HEAD^{tree}")
        self.write("knowledge.txt", "synthetic knowledge delivery\n")
        knowledge = self.commit()
        knowledge_tree = self.git("rev-parse", "HEAD^{tree}")
        for path in handoff.WEB_INPUTS:
            self.write(path, handoff.read(handoff.ROOT, path))
        state_path = self.root / handoff.READ_FIRST[2]
        current = state_path.read_text(encoding="utf-8")
        for name, value in (("MAIN2_CANONICAL_SHA", baseline), ("MAIN2_CANONICAL_TREE", baseline_tree),
                            ("KNOWLEDGE_COMMIT", knowledge), ("KNOWLEDGE_TREE", knowledge_tree)):
            current = re.sub(rf"^{name} = .+$", f"{name} = {value}", current, flags=re.M)
        state_path.write_text(current, encoding="utf-8")
        self.board = json.loads((self.root / handoff.READ_FIRST[4]).read_text(encoding="utf-8"))
        self.board.update(baseline_sha=baseline, baseline_tree=baseline_tree)
        self.board["knowledge_delivery"] = dict(ref=knowledge, commit=knowledge, tree=knowledge_tree)
        self.write(handoff.READ_FIRST[4], json.dumps(self.board))
        self.snapshot = self.commit()

    def generate(self):
        output = handoff.build_web(self.root)
        target = self.root / handoff.WEB_OUTPUT
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(output)
        return output

    def test_wrapper_commit_preserves_exact_source_and_freshness(self):
        output = self.generate()
        publication = self.commit()
        self.assertNotEqual(publication, self.snapshot)
        self.assertIn(f"CURRENT_INTEGRATION_COMMIT = {self.snapshot}".encode(), output)
        self.assertIn(b"DERIVED / NOT SOURCE OF TRUTH", output)
        for slot in ('01', '02', '03', '04'):
            worker = self.board['workers'][slot]
            expected = f"{slot}_DELIVERY = {worker['delivery_state']}; {worker['acceptance_state']}"
            self.assertIn(expected.encode(), output)
        self.assertIn(b"FORMAL_MAIN3 = NOT_CREATED", output)
        self.assertIn(b"BRIDGE = SUPERSEDED / BRIDGE CLOSED", output)
        self.assertNotIn(b"01:UNKNOWN", output)
        self.assertNotIn(b"04:UNKNOWN", output)
        self.assertLessEqual(len(output), 4096)
        self.assertEqual(output, handoff.build_web(self.root))
        handoff.check_output(self.root, handoff.WEB_OUTPUT, handoff.build_web(self.root))
        self.git("checkout", "--detach", "-q")
        self.assertEqual(output, handoff.build_web(self.root))

    def test_dirty_staged_and_untracked_source_rejected(self):
        self.generate()
        self.write("seed.txt", "dirty source\n")
        with self.assertRaises(ValueError):
            handoff.build_web(self.root)
        self.git("add", "seed.txt")
        with self.assertRaises(ValueError):
            handoff.build_web(self.root)
        self.write("seed.txt", "synthetic baseline\n")
        with self.assertRaises(ValueError):
            handoff.build_web(self.root)
        self.write("seed.txt", "dirty source\n")
        self.commit()
        self.write("untracked.txt", "new source\n")
        with self.assertRaises(ValueError):
            handoff.build_web(self.root)

    def test_source_commit_and_tampered_handoff_invalidate_check(self):
        output = self.generate()
        self.commit()
        self.write("seed.txt", "accepted new source\n")
        self.commit()
        with self.assertRaises(ValueError):
            handoff.check_output(self.root, handoff.WEB_OUTPUT, handoff.build_web(self.root))
        fresh = self.generate()
        self.assertNotEqual(output, fresh)
        self.write(handoff.WEB_OUTPUT, fresh.decode() + "tampered\n")
        with self.assertRaises(ValueError):
            handoff.check_output(self.root, handoff.WEB_OUTPUT, handoff.build_web(self.root))

    def test_wrong_knowledge_tree_rejected(self):
        self.board["knowledge_delivery"]["tree"] = "0" * 40
        self.write(handoff.READ_FIRST[4], json.dumps(self.board))
        current = (self.root / handoff.READ_FIRST[2]).read_text(encoding="utf-8")
        self.write(handoff.READ_FIRST[2], re.sub(r"^KNOWLEDGE_TREE = .+$", "KNOWLEDGE_TREE = " + "0" * 40, current, flags=re.M))
        self.commit()
        with self.assertRaises(ValueError):
            handoff.build_web(self.root)

    def test_sensitive_state_fails_without_echoing_or_overwriting(self):
        original = self.generate()
        synthetic = "SMTP_" + "KEY=synthetic"
        self.board["control_plane"]["user_action_required"] = synthetic
        self.write(handoff.READ_FIRST[4], json.dumps(self.board))
        self.commit()
        errors = io.StringIO()
        with patch.object(handoff, "ROOT", self.root), patch("sys.argv", ["handoff", "--web"]), contextlib.redirect_stderr(errors):
            self.assertEqual(handoff.main(), 1)
        self.assertNotIn(synthetic, errors.getvalue())
        self.assertEqual((self.root / handoff.WEB_OUTPUT).read_bytes(), original)

    def test_utf8_overflow_is_rejected_without_truncation(self):
        queue = json.loads((self.root / handoff.READ_FIRST[5]).read_text(encoding="utf-8"))
        for i in range(8):
            queue["tasks"].append(dict(task_id="学习" * 70 + str(i), owner="A", state="BLOCKED",
                dependencies=[], unlocks=[], user_action_required=None, reason="synthetic overflow"))
        self.write(handoff.READ_FIRST[5], json.dumps(queue, ensure_ascii=False))
        self.commit()
        with self.assertRaisesRegex(ValueError, "4096 bytes"):
            handoff.build_web(self.root)

    def test_wrong_branch_and_missing_provenance_input_rejected(self):
        self.git("checkout", "-qb", "wrong-fixture-branch")
        with self.assertRaises(ValueError):
            handoff.build_web(self.root)
        self.git("checkout", "-q", "integration/main3")
        self.git("rm", "-q", "docs/handoff/README.md")
        self.commit()
        with self.assertRaises(ValueError):
            handoff.build_web(self.root)

    def test_queue_user_action_is_preserved(self):
        queue = json.loads((self.root / handoff.READ_FIRST[5]).read_text(encoding="utf-8"))
        task = next(task for task in queue["tasks"] if task["state"] == "BLOCKED")
        task["user_action_required"] = "Review supplied final candidate."
        self.write(handoff.READ_FIRST[5], json.dumps(queue))
        self.commit()
        output = handoff.build_web(self.root).decode()
        self.assertIn("USER_ACTION_REQUIRED = " + task["task_id"] + ": Review supplied final candidate.", output)


if __name__ == "__main__":
    unittest.main()
