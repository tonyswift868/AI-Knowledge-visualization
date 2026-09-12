"""Offline negative checks for knowledge input and handoff boundaries."""
import json
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

    def test_current_state_disagreement_rejected(self):
        self.board["baseline_sha"] = "0" * 40
        self.assert_rejected()

    def test_unknown_is_not_inferred_running(self):
        output = handoff.build(handoff.ROOT).decode()
        self.assertIn("UNKNOWN_WORKERS = 01,02,03,04,B", output)
        self.assertNotIn("01:RUNNING", output)

    def test_dependency_cycle_and_false_ready_rejected(self):
        self.queue["tasks"][0]["dependencies"] = ["COMPACT_REPO_HANDOFF"]
        self.assert_rejected()
        self.queue["tasks"][0]["dependencies"] = []
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


if __name__ == "__main__":
    unittest.main()
