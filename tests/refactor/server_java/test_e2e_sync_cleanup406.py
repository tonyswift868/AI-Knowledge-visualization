"""Offline contract checks for the gated real-account 406 E2E tool."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import e2e_sync_cleanup406 as e2e


class E2E406OfflineTests(unittest.TestCase):
    def test_default_mode_does_not_read_credentials_or_call_out(self):
        with patch.object(e2e, "ACCOUNT_FILE", object()), \
             patch("e2e_sync_cleanup401.urllib.request.urlopen", side_effect=AssertionError), \
             patch("e2e_sync_cleanup401.subprocess.run", side_effect=AssertionError), \
             patch("builtins.print") as output:
            self.assertEqual(e2e.main([]), 0)
        self.assertIn("DRY PLAN", output.call_args_list[0].args[0])

    def test_changes_uses_entities_and_advances_next_cursor_until_has_more_is_false(self):
        pages = [{"entities": [], "next_cursor": "7", "has_more": True},
                 {"entities": [{"entity_id": "control-id", "revision": 1,
                                "deleted": False, "payload": {}}],
                  "next_cursor": "12", "has_more": False}]
        with patch.object(e2e, "_request", side_effect=pages) as request:
            row = e2e._find_change("token", "org", "ws", "control-id")
        self.assertEqual(row["entity_id"], "control-id")
        self.assertIn("since=0", request.call_args_list[0].args[1])
        self.assertIn("since=7", request.call_args_list[1].args[1])

    def test_script_path_imports_course_store_without_pythonpath(self):
        scratch = Path(r"D:\MentorFlow\.local\test-tmp\406-MF01\e2e\script-path")
        scratch.mkdir(parents=True, exist_ok=True)
        script = Path(e2e.__file__).resolve()
        code = (
            "import runpy,sys,uuid; from pathlib import Path; "
            "sys.path[0]=str(Path(sys.argv[1]).parent); "
            "module=runpy.run_path(sys.argv[1]); "
            "store=module['_course_store']('plan-'+uuid.uuid4().hex,'offline406-marker',Path(sys.argv[2])); "
            "rows=store.pending_records(10000); "
            "assert any(row.entity=='question_answers' for row in rows); "
            "assert any(row.entity=='judgements' for row in rows); "
            "print('CourseStore outbox ready'); store.close()"
        )
        environment = os.environ.copy()
        environment.pop("PYTHONPATH", None)
        result = subprocess.run([sys.executable, "-c", code, str(script), str(scratch)],
            cwd=scratch, env=environment, capture_output=True, text=True, timeout=30, check=True)
        self.assertEqual(result.stdout.strip(), "CourseStore outbox ready")

    def test_uses_course_store_wire_payloads_and_plan_tombstone(self):
        from refactor.learning.course.mastery import Judgement
        from refactor.learning.course.ledger import PlanWindow
        from refactor.learning.course.store import CourseStore

        marker = "answer406-特殊"
        store = CourseStore("offline406-" + uuid.uuid4().hex, Path(self._temporary_directory()))
        try:
            today = date.today()
            store.create_plan("fresh-plan", goal=marker,
                              window=PlanWindow(today, today + timedelta(days=2), 60))
            store.add_sources("fresh-plan", [dict(id="fresh-source", kind="url",
                url="https://example.invalid", title=marker, site="example.invalid",
                tier="official", credibility=1.0, adopted=True,
                chunks=[dict(chunk_id="fresh-chunk", text=marker, page_start=1,
                             page_end=1, section=["body"])])])
            store.save_outline("fresh-plan", [{"id": "fresh-stage", "title": marker}],
                               [{"id": "fresh-module", "stage_id": "fresh-stage", "title": marker,
                                 "prerequisites": []}])
            store.save_generated_module("fresh-plan", "fresh-module",
                [{"id": "fresh-point", "summary": marker, "minutes": 1,
                  "chunk_ids": ["fresh-chunk"]}],
                check_minutes=1, today=datetime.now(timezone.utc).date())
            store.append_judgement("fresh-plan", Judgement(
                "fresh-judgement", 1, datetime.now(timezone.utc), "casual", "fresh-module",
                results=(("fresh-point", True),)), details={"answer": marker, "reason": marker})
            store.append_question_answer("fresh-plan", "fresh-answer", module_id="fresh-module",
                context="check", attempt_id=None, question_id="fresh-question",
                point_ids=["fresh-point"], angle="recall", correct=True,
                answer=marker, reason=marker)
            created = store.pending_records(100)
            answer = next(row for row in created if row.entity == "question_answers")
            judgement = next(row for row in created if row.entity == "judgements")
            self.assertEqual(answer.schema, "mentorflow.course.question_answers.v1")
            self.assertEqual(judgement.schema, "mentorflow.course.judgements.v1")
            self.assertIn(marker, json.dumps(answer.payload, ensure_ascii=False))
            captured = []
            with patch.object(e2e, "_request", side_effect=lambda method, path, **kwargs:
                              captured.append((method, path, kwargs)) or {"revision": 1,
                              "deleted": False, "payload": kwargs["body"]["payload"]}):
                e2e._upload_record("token", "org", "ws", answer)
                e2e._upload_record("token", "org", "ws", judgement)
            method, path, kwargs = captured[0]
            self.assertEqual(method, "PUT")
            self.assertEqual(path, f"{e2e.BASE}/entities/learning_run/"+
                             e2e._wire_id(answer.entity, answer.entity_id))
            self.assertEqual(kwargs["body"]["payload"], {
                "schema": "mentorflow.refactor-sync.v1", "version": 1,
                "record_type": "course_record",
                "record_id": json.dumps([answer.entity, answer.entity_id], ensure_ascii=False,
                                         separators=(",", ":")),
                "values": {"schema": answer.schema, "plan_key": answer.plan_key,
                           "entity": answer.entity, "entity_id": answer.entity_id,
                           "payload": answer.payload}})
            self.assertEqual(captured[1][2]["body"]["payload"]["values"]["schema"],
                             "mentorflow.course.judgements.v1")
            store.delete_plan("fresh-plan")
            tombstones = store.pending_records(100)
            plan_tombstone = next(row for row in tombstones if row.entity == "plans" and row.deleted)
            self.assertFalse(any(row.plan_key == "fresh-plan" and row.entity in
                                 {"judgements", "question_answers"} for row in tombstones))
            with patch.object(e2e, "_request", return_value={"revision": 2}) as request:
                e2e._upload_record("token", "org", "ws", plan_tombstone)
            self.assertEqual(request.call_args.args[:2], (
                "POST", f"{e2e.BASE}/entities/learning_run/"+
                e2e._wire_id("plans", "fresh-plan")+"/delete"))
            self.assertEqual(request.call_args.kwargs["body"]["expected_revision"], 1)
        finally:
            store.close()

    def test_mocked_execute_checks_both_tables_and_untouched_control(self):
        calls = []
        entities = {}

        def request(method, path, *, token=None, body=None, headers=None):
            calls.append((method, path, body))
            if path == e2e.LOGIN:
                return {"access_token": "mock-token", "identity":
                        {"issuer": "https://issuer.invalid", "subject": "test-subject"}}
            if path == e2e.WORKSPACES:
                return {"workspaces": [{"organization_id": "org", "id": "ws"}],
                        "initialization_required": False}
            if path.startswith(f"{e2e.BASE}/changes?"):
                return {"entities": list(entities.values()), "next_cursor": "9", "has_more": False}
            entity_id = path.split("/")[-2] if path.endswith("/delete") else path.rsplit("/", 1)[-1]
            if method == "POST":
                previous = entities.get(entity_id, {})
                values = previous.get("payload", {}).get("values", {})
                if values.get("entity") == "plans":
                    plan_id = values.get("entity_id")
                    for key, row in list(entities.items()):
                        row_values = row.get("payload", {}).get("values", {})
                        if row_values.get("plan_key") == plan_id:
                            entities[key] = {**row, "revision": row["revision"] + 1,
                                             "deleted": True, "payload": {}}
                entities[entity_id] = {"kind": "learning_run", "entity_id": entity_id,
                    "revision": 2, "deleted": True, "payload": {}}
                return {"revision": 2, "deleted": True, "payload": {}}
            payload = body["payload"]
            entities[entity_id] = {"kind": "learning_run", "entity_id": entity_id,
                "revision": 1, "deleted": False, "payload": payload}
            return {"revision": 1, "deleted": False, "payload": payload}

        def counts(marker, issuer):
            matching = [row for row in entities.values()
                        if marker in json.dumps(row, ensure_ascii=False)]
            return len(matching), len(matching), 2

        with patch.object(e2e, "ACCOUNT_FILE", type("Account", (), {
                 "read_text": lambda self, encoding: "username: learner@example.invalid\npassword: fixture\n"})()), \
             patch.object(e2e, "_request", side_effect=request), \
             patch("e2e_sync_cleanup401._request", side_effect=request), \
             patch.object(e2e, "_sql_counts", side_effect=counts), \
             patch("e2e_sync_cleanup401._sql_counts", side_effect=counts), \
             patch("builtins.print"):
            e2e.execute()

        self.assertTrue(any(method == "POST" and path.endswith("/delete") for method, path, _ in calls))
        self.assertEqual(sum(method == "PUT" and body["payload"]["values"]["entity"] == "judgements"
                             for method, _, body in calls if body), 1)
        self.assertEqual(sum(method == "PUT" and body["payload"]["values"]["entity"] == "question_answers"
                             for method, _, body in calls if body), 1)
        self.assertNotIn("mock-token", repr(calls))

    def test_partial_upload_timeout_is_resolved_and_cleaned(self):
        entities = {}
        calls = []
        fail_once = {"pending": True}

        def request(method, path, *, token=None, body=None, headers=None):
            calls.append((method, path, body))
            if path == e2e.LOGIN:
                return {"access_token": "mock-token", "identity":
                        {"issuer": "https://issuer.invalid", "subject": "partial-subject"}}
            if path == e2e.WORKSPACES:
                return {"workspaces": [{"organization_id": "org", "id": "ws"}],
                        "initialization_required": False}
            if path.startswith(f"{e2e.BASE}/changes?"):
                return {"entities": list(entities.values()), "next_cursor": "20", "has_more": False}
            entity_id = path.split("/")[-2] if path.endswith("/delete") else path.rsplit("/", 1)[-1]
            if method == "POST":
                old = entities.get(entity_id)
                self.assertIsNotNone(old)
                self.assertEqual(body["expected_revision"], old["revision"])
                entities[entity_id] = {**old, "revision": old["revision"] + 1,
                                       "deleted": True, "payload": {}}
                return {"revision": old["revision"] + 1, "deleted": True, "payload": {}}
            payload = body["payload"]
            entities[entity_id] = {"kind": "learning_run", "entity_id": entity_id,
                "revision": 1, "deleted": False, "payload": payload}
            if (fail_once["pending"] and payload.get("values", {}).get("entity") != "chat_sessions"):
                fail_once["pending"] = False
                raise e2e.SafeFailure("mocked write response lost after commit")
            return {"revision": 1, "deleted": False, "payload": payload}

        def counts(marker, issuer):
            live = [row for row in entities.values() if not row["deleted"]
                    and marker in json.dumps(row, ensure_ascii=False)]
            return len(live), len(live), 3

        account = type("Account", (), {
            "read_text": lambda self, encoding: "username: learner@example.invalid\npassword: fixture\n"})()
        with patch.object(e2e, "ACCOUNT_FILE", account), \
             patch.object(e2e, "_request", side_effect=request), \
             patch("e2e_sync_cleanup401._request", side_effect=request), \
             patch.object(e2e, "_sql_counts", side_effect=counts), \
             patch("e2e_sync_cleanup401._sql_counts", side_effect=counts), \
             patch("builtins.print"):
            with self.assertRaises(e2e.SafeFailure):
                e2e.execute()

        self.assertFalse(fail_once["pending"])
        self.assertTrue(entities)
        self.assertTrue(all(row["deleted"] and row["payload"] == {} for row in entities.values()))
        self.assertTrue(any(method == "POST" and body["expected_revision"] == 1
                            for method, path, body in calls if path.endswith("/delete")))
        self.assertNotIn("mock-token", repr(calls))

    @staticmethod
    def _temporary_directory():
        scratch = Path(r"D:\MentorFlow\.local\test-tmp\406-MF01\e2e\offline")
        scratch.mkdir(parents=True, exist_ok=True)
        return scratch


if __name__ == "__main__":
    unittest.main()
