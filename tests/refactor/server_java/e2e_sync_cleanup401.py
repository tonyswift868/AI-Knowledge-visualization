"""Gated real-account sync cleanup exercise for 401; dry-plan is offline."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

API = "https://89.45.45.28"
ACCOUNT_FILE = Path(r"D:\MentorFlow\.local\server\test-account.txt")
BASE = "/api/enterprise/sync/v1"
LOGIN = "/api/enterprise/auth/native/login"
WORKSPACES = "/api/enterprise/account/workspaces"
SSH = ("ssh", "-i", r"C:\Users\21533\.ssh\mentorflow_server", "-o", "IdentitiesOnly=yes",
       "-o", "BatchMode=yes", "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=3",
       "mentorflow-admin@89.45.45.28")


class SafeFailure(RuntimeError):
    pass


def account_candidates(raw: str) -> list[tuple[str, str]]:
    """Parse markdown-style field rows; return secrets only to the caller."""
    usernames: list[str] = []
    passwords: list[str] = []
    for source_line in raw.splitlines():
        line = re.sub(r"^\s*(?:[-*+]\s+|\d+[.)、]\s*)", "", source_line).strip().strip("| ")
        match = re.search(r"(?i)(username|email|用户名|邮箱|password|密码)", line)
        if not match:
            continue
        label = match.group(1)
        key = "password" if label.lower() == "password" or label == "密码" else "username"
        value = line[match.end():].strip().strip("`*| :：=\t")
        if value:
            (passwords if key == "password" else usernames).append(value)
    if not usernames or len(usernames) != len(passwords):
        raise SafeFailure("account file fields are ambiguous")
    candidates = list(zip(usernames, passwords))
    if len(candidates) != len(set(candidates)):
        raise SafeFailure("account file fields are ambiguous")
    return candidates


def _request(method: str, path: str, *, token: str | None = None, body: dict | None = None,
             headers: dict[str, str] | None = None) -> dict:
    request_headers = {"Content-Type": "application/json"} | (headers or {})
    if token:
        request_headers["Authorization"] = "Bearer " + token
    data = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode() if body is not None else None
    request = urllib.request.Request(API + path, data=data, headers=request_headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            result = json.loads(response.read())
            if not isinstance(result, dict):
                raise ValueError
            return result
    except (OSError, urllib.error.URLError, ValueError):
        raise SafeFailure("HTTP request failed") from None


def _course_values(record_id: str, entity: str, payload: dict) -> dict:
    return {"schema": "mentorflow.refactor-sync.v1", "version": 1,
            "record_type": "course_record", "record_id": json.dumps([entity, record_id],
                                                                        ensure_ascii=False, separators=(",", ":")),
            "values": {"schema": "mentorflow.course." + entity + ".v1",
                       "plan_key": "", "entity": entity,
                       "entity_id": record_id, "payload": payload}}


def _wire_id(entity: str, record_id: str) -> str:
    identity = json.dumps([entity, record_id], ensure_ascii=False, separators=(",", ":"))
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    return "refactor.v1:course_record:" + digest


def _sync(token: str, org: str, workspace: str, entity: str, record_id: str,
          payload: dict, revision: int = 0) -> dict:
    request_id = uuid.uuid4().hex
    body = {"request_id": request_id, "expected_revision": revision,
            "payload": _course_values(record_id, entity, payload)}
    headers = {"X-MentorFlow-Organization-ID": org,
               "X-MentorFlow-Workspace-ID": workspace,
               "X-Request-ID": request_id}
    return _request("PUT", f"{BASE}/entities/learning_run/{_wire_id(entity, record_id)}",
                    token=token, body=body, headers=headers)


def _delete(token: str, org: str, workspace: str, entity: str, record_id: str) -> None:
    request_id = uuid.uuid4().hex
    body = {"request_id": request_id, "expected_revision": 1}
    headers = {"X-MentorFlow-Organization-ID": org,
               "X-MentorFlow-Workspace-ID": workspace,
               "X-Request-ID": request_id}
    _request("POST", f"{BASE}/entities/learning_run/{_wire_id(entity, record_id)}/delete",
             token=token, body=body, headers=headers)


def _sql_counts(marker: str, issuer: str) -> tuple[int, int, int]:
    # The marker is hex-only. Issuer is quoted as a SQL literal before embedding.
    issuer_literal = "'" + issuer.replace("'", "''") + "'"
    sql = ("BEGIN READ ONLY; "
           "SELECT (SELECT count(*) FROM public.structured_sync_entities t "
           f"WHERE row_to_json(t)::text LIKE '%{marker}%'), "
           "(SELECT count(*) FROM public.structured_sync_requests t "
           f"WHERE row_to_json(t)::text LIKE '%{marker}%'), "
           "(SELECT count(DISTINCT subject_id) FROM public.rehydration_manifests "
           f"WHERE issuer={issuer_literal} AND initialized IS TRUE); COMMIT;")
    remote = ("set -eu\n"
              "docker exec -i mentorflow-e1-preview-postgres-1 psql -XqAt "
              "-v ON_ERROR_STOP=1 -U postgres -d mentorflow <<'MF401_SQL'\n"
              + sql + "\nMF401_SQL\n")
    try:
        result = subprocess.run([*SSH, "sudo -n sh -s"], input=remote.encode("utf-8"),
                                capture_output=True, timeout=30, check=True)
        numbers = tuple(int(value) for value in result.stdout.decode("utf-8").strip().split("|"))
        if len(numbers) != 3:
            raise ValueError
        return numbers
    except (OSError, subprocess.SubprocessError, ValueError):
        raise SafeFailure("read-only SQL count failed") from None


def _find_change(token: str, org: str, workspace: str, entity_id: str) -> dict:
    """Find one known ID through the deployed opaque-cursor changes contract."""
    since = "0"
    while True:
        query = urllib.parse.urlencode({"since": since, "kind": "learning_run", "limit": 500})
        response = _request("GET", f"{BASE}/changes?{query}", token=token,
            headers={"X-MentorFlow-Organization-ID": org,
                     "X-MentorFlow-Workspace-ID": workspace,
                     "X-Request-ID": uuid.uuid4().hex})
        rows = response.get("entities")
        next_cursor = response.get("next_cursor")
        has_more = response.get("has_more")
        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
            raise SafeFailure("changes response shape invalid")
        found = next((row for row in rows if row.get("entity_id") == entity_id), None)
        if found is not None:
            return found
        if type(has_more) is not bool or not isinstance(next_cursor, str) or not re.fullmatch(r"0|[1-9][0-9]*", next_cursor):
            raise SafeFailure("changes cursor shape invalid")
        if not has_more:
            raise SafeFailure("control record missing from changes feed")
        if next_cursor == since:
            raise SafeFailure("changes cursor did not advance")
        since = next_cursor


def execute(account_index: int = 0) -> None:
    accounts = account_candidates(ACCOUNT_FILE.read_text(encoding="utf-8"))
    if account_index < 0 or account_index >= len(accounts):
        raise SafeFailure("account index is unavailable")
    username, password = accounts[account_index]
    login = _request("POST", LOGIN, body={"username": username, "password": password})
    token = login.get("access_token")
    identity = login.get("identity") or {}
    issuer = identity.get("issuer") if isinstance(identity, dict) else None
    subject = identity.get("subject") if isinstance(identity, dict) else None
    if not isinstance(token, str) or not isinstance(issuer, str) or not issuer or not isinstance(subject, str) or not subject:
        raise SafeFailure("login response shape invalid")
    workspaces = _request("GET", WORKSPACES, token=token)
    rows = workspaces.get("workspaces") if isinstance(workspaces, dict) else None
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
        raise SafeFailure("workspace selection is ambiguous; no records changed")
    context = rows[0]
    org, workspace = context.get("organization_id"), context.get("id")
    if not isinstance(org, str) or not isinstance(workspace, str):
        raise SafeFailure("workspace context is incomplete; no records changed")
    if workspaces.get("initialization_required") is True:
        raise SafeFailure("workspace initialization is required; no records changed")

    marker = "mf401" + uuid.uuid4().hex
    keep_marker = "keep401" + uuid.uuid4().hex
    # Every entity uses a fresh ID; none targets an existing account record.
    session_id, message_id, presets_id, control_id = ("mf401-" + uuid.uuid4().hex for _ in range(4))
    before = _sql_counts(marker, issuer.rstrip("/"))
    keep_before = _sql_counts(keep_marker, issuer.rstrip("/"))
    if before[:2] != (0, 0) or keep_before[:2] != (0, 0):
        raise SafeFailure("random marker unexpectedly exists; no records changed")
    records = [
        ("chat_sessions", session_id, {"id": session_id, "title": marker, "archived": False}),
        ("chat_messages", message_id, {"id": message_id, "session_id": session_id,
                                        "role": "user", "content": marker, "kind": "text",
                                        "at": "2026-11-16T00:00:00Z", "compressed": False, "summary_of": []}),
        ("account_settings", presets_id,
         {"id": "teacher_presets", "value": {"presets": [{"id": presets_id, "name": marker,
              "persona": "", "style": "warm", "detail": "brief", "examples": "normal",
              "analogies": ["life"]}], "default_id": presets_id}}),
    ]
    control_record = ("chat_sessions", control_id,
                      {"id": control_id, "title": keep_marker, "archived": False})
    created: list[tuple[str, str]] = []
    deleted: set[tuple[str, str]] = set()
    try:
        created.append((control_record[0], control_record[1]))
        initial = _sync(token, org, workspace, *control_record)
        expected_control_payload = _course_values(control_record[1], control_record[0], control_record[2])
        if (initial.get("revision") != 1 or initial.get("deleted") is not False
                or initial.get("payload") != expected_control_payload):
            raise SafeFailure("control record create response invariant failed")
        for entity, record_id, payload in records:
            created.append((entity, record_id))
            _sync(token, org, workspace, entity, record_id, payload)
        if _sql_counts(marker, issuer.rstrip("/"))[0] < 3:
            raise SafeFailure("sync rows were not observed")
        for entity, record_id, _ in records:
            _delete(token, org, workspace, entity, record_id)
            deleted.add((entity, record_id))
        target_deleted_counts = _sql_counts(marker, issuer.rstrip("/"))
        if target_deleted_counts[:2] != (0, 0):
            raise SafeFailure("target marker remained after deletion")
        control_entity_id = _wire_id(control_record[0], control_record[1])
        control_row = _find_change(token, org, workspace, control_entity_id)
        if (control_row.get("revision") != 1 or control_row.get("deleted") is not False
                or control_row.get("payload") != expected_control_payload):
            raise SafeFailure("control record changed while deleting targets")
    finally:
        # Cleanup only the fresh IDs created by this run, including its control record.
        cleanup_failed: list[tuple[str, str]] = []
        for entity, record_id in created:
            if (entity, record_id) not in deleted:
                try:
                    _delete(token, org, workspace, entity, record_id)
                except SafeFailure:
                    cleanup_failed.append((entity, record_id))
        if cleanup_failed:
            safe_ids = ", ".join(f"{entity}:{record_id}" for entity, record_id in cleanup_failed)
            print(f"Manual cleanup required for fresh E2E IDs: {safe_ids}", file=sys.stderr)
            raise SafeFailure("cleanup incomplete; use listed fresh IDs for manual cleanup")
        target_counts = _sql_counts(marker, issuer.rstrip("/"))
        control_counts = _sql_counts(keep_marker, issuer.rstrip("/"))
        if (target_counts[:2] != (0, 0) or control_counts[:2] != (0, 0)
                or target_counts[2] != before[2] or control_counts[2] != before[2]):
            print("Final cleanup counts did not return to baseline.", file=sys.stderr)
            raise SafeFailure("final marker/account count invariant failed")
    print("E2E passed: both marker counts=0, fresh control unchanged before cleanup, canonical backend account count unchanged")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", help="perform gated real HTTP/SSH E2E")
    parser.add_argument("--account-index", type=int, choices=(0, 1), default=0,
                        help="select one of the two labeled test accounts (default: 0)")
    args = parser.parse_args(argv)
    if not args.execute:
        print("DRY PLAN: no account file, network, SSH, or server data accessed")
        print("Would preserve a fresh control session while syncing/deleting chat and teacher presets, "
              "then delete control and verify both marker counts.")
        return 0
    try:
        execute(args.account_index)
    except (SafeFailure, OSError):
        print("E2E stopped safely; inspect operator state before any rerun.", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
