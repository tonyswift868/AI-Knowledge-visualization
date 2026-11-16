"""Offline safety checks for the gated 401 account E2E script."""
from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import e2e_sync_cleanup401 as e2e


class _PathText:
    def read_text(self, encoding):
        return "测试账号\n用户名：learner@example.invalid\n密码：secret-fixture\n"


class _Response:
    def __init__(self, value):
        self.value = json.dumps(value).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return self.value


class E2E401OfflineTests(unittest.TestCase):
    def test_default_mode_does_not_read_credentials_or_call_out(self):
        with patch.object(e2e, "ACCOUNT_FILE", object()), \
             patch.object(e2e.urllib.request, "urlopen", side_effect=AssertionError), \
             patch.object(e2e.subprocess, "run", side_effect=AssertionError):
            self.assertEqual(e2e.main([]), 0)

    def test_account_parser_requires_unique_labeled_credentials(self):
        self.assertEqual(e2e.account_candidates(
            "- username: hidden@example.invalid\n- password: dont-print\n"
            "- username: second@example.invalid\n- password: dont-print-two\n"),
            [("hidden@example.invalid", "dont-print"), ("second@example.invalid", "dont-print-two")])
        with self.assertRaises(e2e.SafeFailure):
            e2e.account_candidates("username: one@example.invalid\npassword: x\nusername: second@example.invalid\n")

    def test_remote_sql_uses_lf_bytes_and_explicit_key(self):
        with patch.object(e2e.subprocess, "run", return_value=SimpleNamespace(stdout=b"0|0|1\n")) as run:
            self.assertEqual(e2e._sql_counts("aabb", "issuer"), (0, 0, 1))
        stdin = run.call_args.kwargs["input"]
        self.assertIsInstance(stdin, bytes)
        self.assertNotIn(b"\r", stdin)
        self.assertIn("-i", run.call_args.args[0])
        self.assertIn(r"C:\Users\21533\.ssh\mentorflow_server", run.call_args.args[0])

    def test_execute_uses_course_sync_shapes_and_cleans_only_fresh_ids(self):
        calls = []
        rows = {}

        def open_request(request, timeout):
            path = request.full_url.removeprefix(e2e.API)
            body = json.loads(request.data) if request.data else None
            calls.append((request.method, path, body))
            if path == e2e.LOGIN:
                return _Response({"access_token": "private-token",
                                  "identity": {"issuer": "i", "subject": "s"}})
            if path == e2e.WORKSPACES:
                return _Response({"workspaces": [{"organization_id": "org", "id": "ws"}],
                                  "initialization_required": False})
            if path.startswith(f"{e2e.BASE}/changes?"):
                return _Response({"entities": list(rows.values()), "next_cursor": "4", "has_more": False})
            if request.method == "PUT":
                entity_id = path.rsplit("/", 1)[-1]
                rows[entity_id] = {"kind": "learning_run", "entity_id": entity_id,
                    "revision": 1, "deleted": False, "payload": body["payload"]}
                return _Response({"revision": 1, "deleted": False, "payload": body["payload"]})
            if request.method == "POST" and path.endswith("/delete"):
                entity_id = path.rsplit("/", 2)[-2]
                rows[entity_id] = {"kind": "learning_run", "entity_id": entity_id,
                    "revision": 2, "deleted": True, "payload": {}}
            return _Response({})

        sql_calls = []
        def count_side_effect(marker, issuer):
            sql_calls.append((marker, issuer))
            if marker.startswith("mf401"):
                target_checks = sum(value.startswith("mf401") for value, _ in sql_calls)
                return {1: (0, 0, 8), 2: (3, 0, 8), 3: (0, 0, 8), 4: (0, 0, 8)}[target_checks]
            return (0, 0, 8)

        with patch.object(e2e, "ACCOUNT_FILE", _PathText()), \
             patch.object(e2e.urllib.request, "urlopen", side_effect=open_request), \
             patch.object(e2e, "_sql_counts", side_effect=count_side_effect), \
             patch("builtins.print"):
            e2e.execute()

        writes = [body["payload"] for method, path, body in calls if method == "PUT"]
        put_paths = {path for method, path, _ in calls if method == "PUT"}
        self.assertEqual({item["values"]["entity"] for item in writes},
                         {"chat_sessions", "chat_messages", "account_settings"})
        self.assertEqual(len(writes), 4)
        preset = next(item for item in writes if item["values"]["entity"] == "account_settings")
        self.assertEqual(preset["values"]["payload"]["id"], "teacher_presets")
        self.assertEqual(len({item["record_id"] for item in writes}), 4)
        control_id = next(item["values"]["entity_id"] for item in writes
                          if item["values"]["entity"] == "chat_sessions"
                          and item["values"]["payload"]["title"].startswith("keep401"))
        for item in writes:
            values = item["values"]
            self.assertEqual(item["record_id"], json.dumps(
                [values["entity"], values["entity_id"]], ensure_ascii=False, separators=(",", ":")))
        self.assertEqual(put_paths, {f"{e2e.BASE}/entities/learning_run/"
                                     f"{e2e._wire_id(item['values']['entity'], item['values']['entity_id'])}"
                                     for item in writes})
        deletes = [(index, path) for index, (method, path, _) in enumerate(calls)
                   if method == "POST" and path.endswith("/delete")]
        control_put_index = next(index for index, (method, path, body) in enumerate(calls)
                                 if method == "PUT" and body["payload"]["values"]["entity_id"] == control_id)
        changes_reads = [index for index, (method, path, _) in enumerate(calls)
                         if method == "GET" and path.startswith(f"{e2e.BASE}/changes?")]
        self.assertEqual(len(deletes), 4)
        self.assertEqual(len(changes_reads), 1)
        self.assertLess(control_put_index, deletes[0][0])
        self.assertLess(max(index for index, _ in deletes[:3]), changes_reads[0])
        self.assertLess(changes_reads[0], deletes[3][0])
        self.assertEqual(len({marker for marker, _ in sql_calls}), 2)
        self.assertNotIn("private-token", repr(calls))


if __name__ == "__main__":
    unittest.main()
