"""Gated real-account plan/progress deletion exercise; dry-plan is offline."""
from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone
import json
import sys
import tempfile
import re
import urllib.parse
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from e2e_sync_cleanup401 import (  # reuse the established login, SSH, count and protocol helpers
    ACCOUNT_FILE, BASE, LOGIN, WORKSPACES, SafeFailure,
    account_candidates, _request, _sql_counts, _wire_id,
)


class SafeFailure406(SafeFailure):
    pass


def _course_store(plan_id: str, marker: str, root: Path):
    from refactor.learning.course.mastery import Judgement
    from refactor.learning.course.ledger import PlanWindow
    from refactor.learning.course.store import CourseStore

    store = CourseStore("e2e406-" + uuid.uuid4().hex, root)
    today = date.today()
    store.create_plan(plan_id, goal=marker, final_goal="E2E deletion", level="beginner", notes="",
                      window=PlanWindow(today, today + timedelta(days=2), 60))
    source_id, chunk_id = ("e2e406-" + uuid.uuid4().hex for _ in range(2))
    store.add_sources(plan_id, [dict(id=source_id, kind="url", url="https://example.invalid",
        title=marker, site="example.invalid", tier="official", credibility=1.0,
        adopted=True, chunks=[dict(chunk_id=chunk_id, text=marker, page_start=1,
                                   page_end=1, section=["body"])])])
    stage_id, module_id, point_id = ("e2e406-" + uuid.uuid4().hex for _ in range(3))
    store.save_outline(plan_id, [{"id": stage_id, "title": marker}],
                       [{"id": module_id, "stage_id": stage_id, "title": marker, "prerequisites": []}])
    store.save_generated_module(plan_id, module_id,
        [{"id": point_id, "summary": marker, "minutes": 1, "chunk_ids": [chunk_id]}],
        check_minutes=1, today=datetime.now(timezone.utc).date())
    store.append_question_answer(plan_id, "answer-" + uuid.uuid4().hex,
        module_id=module_id, context="check", attempt_id=None,
        question_id="question-" + uuid.uuid4().hex, point_ids=[point_id], angle="recall",
        correct=True, answer=marker, reason=marker)
    store.append_judgement(plan_id, Judgement(
        "judgement-" + uuid.uuid4().hex, 1, datetime.now(timezone.utc), "casual",
        module_id, results=((point_id, True),)),
        details={"answer": marker, "reason": marker})
    return store


def _upload_record(token: str, org: str, workspace: str, record) -> dict:
    from refactor.learning.course.sync_course import _envelope, _values
    values = _values(record)
    wire_id = _wire_id(record.entity, record.entity_id)
    request_id = "e2e406-" + uuid.uuid4().hex
    headers = {"X-MentorFlow-Organization-ID": org,
               "X-MentorFlow-Workspace-ID": workspace,
               "X-Request-ID": request_id}
    if record.deleted:
        path = f"{BASE}/entities/learning_run/{wire_id}/delete"
        body = {"request_id": request_id, "expected_revision": 1}
        return _request("POST", path, token=token, body=body, headers=headers)
    body = {"request_id": request_id, "expected_revision": 0, "payload": _envelope(values)}
    return _request("PUT", f"{BASE}/entities/learning_run/{wire_id}",
                    token=token, body=body, headers=headers)


def _sync_records(token: str, org: str, workspace: str, store, records) -> None:
    for record in records:
        result = _upload_record(token, org, workspace, record)
        if result.get("revision") != (2 if record.deleted else 1):
            raise SafeFailure406("course record sync response invariant failed")
        store.mark_synced(record.entity, record.entity_id, record.local_revision,
                          result["revision"])


def _read_change(token: str, org: str, workspace: str, entity_id: str,
                 *, missing_ok: bool = False) -> dict | None:
    since = "0"
    while True:
        query = urllib.parse.urlencode({"since": since, "kind": "learning_run", "limit": 500})
        response = _request("GET", f"{BASE}/changes?{query}", token=token,
            headers={"X-MentorFlow-Organization-ID": org,
                     "X-MentorFlow-Workspace-ID": workspace,
                     "X-Request-ID": uuid.uuid4().hex})
        rows = response.get("entities")
        next_cursor, has_more = response.get("next_cursor"), response.get("has_more")
        if (not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows)
                or not isinstance(next_cursor, str)
                or not re.fullmatch(r"0|[1-9][0-9]*", next_cursor)
                or type(has_more) is not bool):
            raise SafeFailure406("changes response shape invalid")
        found = next((row for row in rows if row.get("entity_id") == entity_id), None)
        if found is not None:
            if (type(found.get("revision")) is not int or found["revision"] < 1
                    or type(found.get("deleted")) is not bool
                    or not isinstance(found.get("payload"), dict)):
                raise SafeFailure406("changes entity shape invalid")
            return found
        if not has_more:
            if missing_ok:
                return None
            raise SafeFailure406("control record missing from changes feed")
        if next_cursor == since:
            raise SafeFailure406("changes cursor did not advance")
        since = next_cursor


def _find_change(token: str, org: str, workspace: str, entity_id: str) -> dict:
    row = _read_change(token, org, workspace, entity_id)
    assert row is not None
    return row


def _delete_current(token: str, org: str, workspace: str, entity: str,
                    record_id: str) -> None:
    wire_id = _wire_id(entity, record_id)
    current = _read_change(token, org, workspace, wire_id, missing_ok=True)
    if current is None or current["deleted"]:
        return
    request_id = "e2e406-cleanup-" + uuid.uuid4().hex
    headers = {"X-MentorFlow-Organization-ID": org,
               "X-MentorFlow-Workspace-ID": workspace,
               "X-Request-ID": request_id}
    try:
        response = _request("POST", f"{BASE}/entities/learning_run/{wire_id}/delete",
            token=token, body={"request_id": request_id,
                               "expected_revision": current["revision"]}, headers=headers)
    except SafeFailure:
        latest = _read_change(token, org, workspace, wire_id, missing_ok=True)
        if latest is not None and latest["deleted"]:
            return
        raise
    if (response.get("revision") != current["revision"] + 1
            or response.get("deleted") is not True or response.get("payload") != {}):
        latest = _read_change(token, org, workspace, wire_id, missing_ok=True)
        if latest is not None and latest["deleted"]:
            return
        raise SafeFailure406("cleanup delete response invariant failed")


def execute(account_index: int = 0) -> None:
    accounts = account_candidates(ACCOUNT_FILE.read_text(encoding="utf-8"))
    if account_index < 0 or account_index >= len(accounts):
        raise SafeFailure406("account index is unavailable")
    username, password = accounts[account_index]
    login = _request("POST", LOGIN, body={"username": username, "password": password})
    token, identity = login.get("access_token"), login.get("identity")
    issuer = identity.get("issuer") if isinstance(identity, dict) else None
    subject = identity.get("subject") if isinstance(identity, dict) else None
    if not all(isinstance(value, str) and value for value in (token, issuer, subject)):
        raise SafeFailure406("login response shape invalid")
    workspaces = _request("GET", WORKSPACES, token=token)
    rows = workspaces.get("workspaces") if isinstance(workspaces, dict) else None
    if (not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict)
            or workspaces.get("initialization_required") is True):
        raise SafeFailure406("workspace selection is ambiguous or uninitialized")
    org, workspace = rows[0].get("organization_id"), rows[0].get("id")
    if not all(isinstance(value, str) and value for value in (org, workspace)):
        raise SafeFailure406("workspace context is incomplete")

    marker = "mf406" + uuid.uuid4().hex
    keep_marker = "keep406" + uuid.uuid4().hex
    plan_id = "plan406-" + uuid.uuid4().hex
    control_id = "control406-" + uuid.uuid4().hex
    before = _sql_counts(marker, issuer.rstrip("/"))
    if before[:2] != (0, 0):
        raise SafeFailure406("random marker unexpectedly exists")
    from refactor.learning.course.store import CourseStore
    created: list[tuple[str, str]] = []
    target_records = []
    with tempfile.TemporaryDirectory(prefix="e2e406-", dir=Path(r"D:\MentorFlow\.local\test-tmp\406-MF01\e2e")) as temp:
        store = None
        try:
            store = _course_store(plan_id, marker, Path(temp))
            records = store.pending_records(10000)
            # Register every fresh outbox ID before sending; a lost response may have committed.
            target_records = [(row.entity, row.entity_id) for row in records]
            if not any(row.entity == "question_answers" for row in records) or not any(row.entity == "judgements" for row in records):
                raise SafeFailure406("CourseStore did not produce answer and judgement records")
            control = {"schema": "mentorflow.refactor-sync.v1", "version": 1,
                       "record_type": "course_record", "record_id": json.dumps(
                           ["chat_sessions", control_id], separators=(",", ":")),
                       "values": {"schema": "mentorflow.course.chat_sessions.v1", "plan_key": "",
                                  "entity": "chat_sessions", "entity_id": control_id,
                                  "payload": {"id": control_id, "title": keep_marker, "archived": False}}}
            created.append(("chat_sessions", control_id))
            from e2e_sync_cleanup401 import _sync
            initial = _sync(token, org, workspace, "chat_sessions", control_id, control["values"]["payload"])
            if initial.get("revision") != 1 or initial.get("deleted") is not False:
                raise SafeFailure406("control record create response invariant failed")
            _sync_records(token, org, workspace, store, records)
            if _sql_counts(marker, issuer.rstrip("/"))[:2] == (0, 0):
                raise SafeFailure406("course answer/judgement rows were not observed")
            store.delete_plan(plan_id)
            tombstones = store.pending_records(10000)
            _sync_records(token, org, workspace, store, tombstones)
            if _sql_counts(marker, issuer.rstrip("/"))[:2] != (0, 0):
                raise SafeFailure406("target marker remained after plan deletion")
            control_row = _find_change(token, org, workspace,
                                       _wire_id("chat_sessions", control_id))
            if (control_row.get("revision") != 1 or control_row.get("deleted") is not False
                    or control_row.get("payload") != control):
                raise SafeFailure406("control record changed while deleting target plan")
        finally:
            if store is not None:
                store.close()
            # Resolve each fresh ID's current state because a write or plan purge may have
            # committed even if its response was lost.
            cleanup_failed = []
            cleanup_ids = list(dict.fromkeys([*target_records, *created]))
            for entity, record_id in cleanup_ids:
                try:
                    _delete_current(token, org, workspace, entity, record_id)
                except (SafeFailure, OSError):
                    cleanup_failed.append((entity, record_id))
            if cleanup_failed:
                safe_ids = ", ".join(f"{entity}:{record_id}" for entity, record_id in cleanup_failed)
                print(f"Manual cleanup required for fresh E2E IDs: {safe_ids}", file=sys.stderr)
                raise SafeFailure406("cleanup incomplete; use listed fresh IDs for manual cleanup")
            target_counts = _sql_counts(marker, issuer.rstrip("/"))
            control_counts = _sql_counts(keep_marker, issuer.rstrip("/"))
            if (target_counts[:2] != (0, 0) or control_counts[:2] != (0, 0)
                    or target_counts[2] != before[2] or control_counts[2] != before[2]):
                raise SafeFailure406("final cleanup marker/account count invariant failed")
    print("E2E passed: real CourseStore answer/judgement sync, plan deletion, both marker counts=0, control unchanged, account count unchanged")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", help="perform gated real HTTP/SSH E2E")
    parser.add_argument("--account-index", type=int, choices=(0, 1), default=0)
    args = parser.parse_args(argv)
    if not args.execute:
        print("DRY PLAN: no account file, network, SSH, or server data accessed")
        print("Would create a fresh CourseStore plan with answer and judgement, sync, delete the plan, verify both marker counts, then clean only this run's IDs.")
        return 0
    try:
        execute(args.account_index)
    except (SafeFailure, OSError):
        print("E2E stopped safely; inspect operator state before any rerun.", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
