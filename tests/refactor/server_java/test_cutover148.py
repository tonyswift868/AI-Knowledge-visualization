"""Real HTTP cutover rehearsal between Python and Java on the same local PG."""
from __future__ import annotations

import hashlib
import json

import psycopg

from contract_support147 import ORG, WORKSPACE
from cutover_support148 import ARTIFACTS, cutover148_backends


NATIVE = "/api/enterprise/auth/native"
LIFECYCLE = "/api/enterprise/auth/lifecycle"
CLOSURE = "/api/enterprise/account/closure"
SYNC = "/api/enterprise/sync/v1"


def _login(client, username, password="correct"):
    response = client.post(NATIVE + "/login", json={"username": username, "password": password},
                          headers={"X-Request-ID": "cutover148-login"})
    assert response.status_code == 200, response.status_code
    tokens = response.json()
    assert tokens["access_token"].startswith("mfn1_a_")
    assert tokens["refresh_token"].startswith("mfn1_r_")
    return tokens


def _refresh(client, refresh_token):
    response = client.post(NATIVE + "/refresh", json={"refresh_token": refresh_token},
                           headers={"X-Request-ID": "cutover148-refresh"})
    assert response.status_code == 200, response.status_code
    tokens = response.json()
    assert tokens["access_token"].startswith("mfn1_a_")
    assert tokens["refresh_token"].startswith("mfn1_r_")
    return tokens


def _auth(access, request_id):
    return {"Authorization": "Bearer " + access,
            "X-MentorFlow-Organization-ID": ORG,
            "X-MentorFlow-Workspace-ID": WORKSPACE,
            "X-Request-ID": request_id,
            "Content-Type": "application/json"}


def _sync_put(client, access, marker):
    record_id = "cutover148:" + marker
    entity_id = "refactor.v1:course_record:" + hashlib.sha256(record_id.encode()).hexdigest()
    value = {"schema": "mentorflow.course.module.v1", "plan_key": "cutover148",
             "entity": "module", "entity_id": record_id,
             "payload": {"title": marker, "nodes": []}}
    body = {"schema": "mentorflow.refactor-sync.v1", "version": 1,
            "record_type": "course_record", "record_id": record_id, "values": value}
    response = client.put(f"{SYNC}/entities/learning_run/{entity_id}",
        headers=_auth(access, "cutover148-put-" + marker),
        content=json.dumps({"request_id": "cutover148-" + marker, "expected_revision": 0,
                            "payload": body}, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    assert response.status_code == 200, response.status_code
    return entity_id


def _snapshot_contains(client, access, entity_id, marker):
    response = client.get(SYNC + "/snapshot?kind=learning_run",
                          headers=_auth(access, "cutover148-snapshot-" + marker))
    assert response.status_code == 200, response.status_code
    assert any(item.get("entity_id") == entity_id for item in response.json().get("entities", []))


def _old_access_projection(client, access):
    response = client.get(NATIVE + "/session", headers={
        "Authorization": "Bearer " + access, "X-Request-ID": "cutover148-old-access"})
    # Keep exact response body evidence without ever persisting the bearer token.
    return {"status": response.status_code, "body": response.content.decode("utf-8")}


def _old_sync_access_projection(client, access):
    response = client.get(SYNC + "/snapshot?kind=learning_run",
        headers=_auth(access, "cutover148-old-sync-access"))
    assert response.status_code == 401
    return {"status": response.status_code, "body": response.content.decode("utf-8")}


def _change_password(client, access, old, new):
    response = client.post(LIFECYCLE + "/change-password",
        json={"current_password": old, "new_password": new},
        headers=_auth(access, "cutover148-password"))
    assert response.status_code == 200, response.status_code
    return {"status": response.status_code,
            "state": response.json().get("state"), "code": response.json().get("code")}


def _close_account(client, access):
    prepared = client.post(CLOSURE + "/prepare", json={"current_password": "replacement148"},
        headers={"Authorization": "Bearer " + access, "X-Request-ID": "cutover148-close-prepare"})
    assert prepared.status_code == 200, prepared.status_code
    prep = prepared.json()
    assert prep.get("state") == "PREPARED"
    # Recovery material stays in memory and is intentionally excluded from evidence.
    closed = client.post(CLOSURE + "/" + prep["operation_id"] + "/commit",
        json={"confirmed": True}, headers={"X-MentorFlow-Closure-Token": prep["recovery_token"],
                                         "X-Request-ID": "cutover148-close-commit"})
    assert closed.status_code == 200, closed.status_code
    assert closed.json().get("state") == "COMPLETED"
    return {"prepare_status": prepared.status_code, "commit_status": closed.status_code,
            "state": closed.json().get("state")}


def _rows_after_closure(admin_url, subject):
    with psycopg.connect(admin_url.replace("postgresql+psycopg:", "postgresql:")) as conn:
        sync_count = conn.execute("SELECT count(*) FROM structured_sync_entities WHERE subject_id=%s", (subject,)).fetchone()[0]
        manifest_count = conn.execute("SELECT count(*) FROM rehydration_manifests WHERE subject_id=%s", (subject,)).fetchone()[0]
        native = conn.execute("SELECT revoked_reason FROM native_session_families WHERE subject=%s", (subject,)).fetchall()
    return {"sync_rows": sync_count, "manifest_rows": manifest_count,
            "native_families": len(native), "native_revoked": bool(native) and all(row[0] for row in native)}


def test_cutover148_python_java_same_database_bidirectional(cutover148_backends):
    fixture = cutover148_backends
    clients = fixture["clients"]
    evidence = {"same_database": True, "phases": []}

    # First direction: Python creates the native family and sync row, then the
    # existing refresh family is consumed by Java without copying session data.
    py_tokens = _login(clients["python"], "alice148")
    entity_a = _sync_put(clients["python"], py_tokens["access_token"], "from-python-a")
    old_access = _old_access_projection(clients["java"], py_tokens["access_token"])
    assert old_access["status"] == 401
    old_sync_access = _old_sync_access_projection(clients["java"], py_tokens["access_token"])
    java_tokens = _refresh(clients["java"], py_tokens["refresh_token"])
    assert clients["java"].get(NATIVE + "/session", headers={
        "Authorization": "Bearer " + java_tokens["access_token"]}).status_code == 200
    _snapshot_contains(clients["java"], java_tokens["access_token"], entity_a, "from-python-a")
    entity_a_java = _sync_put(clients["java"], java_tokens["access_token"], "from-java-a")
    py_roundtrip = _refresh(clients["python"], java_tokens["refresh_token"])
    _snapshot_contains(clients["python"], py_roundtrip["access_token"], entity_a_java, "from-java-a")
    entity_a_python = _sync_put(clients["python"], py_roundtrip["access_token"], "from-python-a2")
    java_tokens = _refresh(clients["java"], py_roundtrip["refresh_token"])
    _snapshot_contains(clients["java"], java_tokens["access_token"], entity_a_python, "from-python-a2")

    # Java mutation must persist into the same native session table and the
    # shared identity/Admin authority; the former refresh token is revoked.
    password_a = _change_password(clients["java"], java_tokens["access_token"],
                                  "correct", "replacement148")
    stale_a = clients["python"].post(NATIVE + "/refresh",
        json={"refresh_token": java_tokens["refresh_token"]},
        headers={"X-Request-ID": "cutover148-stale-refresh-a"})
    assert stale_a.status_code == 401
    fresh_a = _login(clients["java"], "alice148", "replacement148")
    closure_a = _close_account(clients["java"], fresh_a["access_token"])
    closed_refresh_a = clients["python"].post(NATIVE + "/refresh",
        json={"refresh_token": fresh_a["refresh_token"]},
        headers={"X-Request-ID": "cutover148-closed-refresh-a"})
    assert closed_refresh_a.status_code == 401
    cleanup_a = _rows_after_closure(fixture["admin_url"], fixture["subjects"]["alice148"])
    assert cleanup_a["sync_rows"] == cleanup_a["manifest_rows"] == 0
    assert cleanup_a["native_families"] >= 1 and cleanup_a["native_revoked"]
    evidence["phases"].append({"direction": "Python→Java", "sync_cross_read": True,
        "old_python_access_rejected_by_java": old_access,
        "old_python_sync_access_rejected_by_java": old_sync_access,
        "password_change": password_a, "old_refresh_status": stale_a.status_code,
        "closure": closure_a, "closed_refresh_status": closed_refresh_a.status_code,
        "cleanup": cleanup_a})

    # Reverse direction on a separate test identity because account closure is
    # intentionally destructive. Java starts the family; Python refreshes it,
    # sees Java's sync write, changes the password, and closes it.
    java_b = _login(clients["java"], "bob148")
    entity_b = _sync_put(clients["java"], java_b["access_token"], "from-java-b")
    old_java_access = _old_access_projection(clients["python"], java_b["access_token"])
    assert old_java_access["status"] == 401
    old_java_sync_access = _old_sync_access_projection(clients["python"], java_b["access_token"])
    py_b = _refresh(clients["python"], java_b["refresh_token"])
    _snapshot_contains(clients["python"], py_b["access_token"], entity_b, "from-java-b")
    entity_b_python = _sync_put(clients["python"], py_b["access_token"], "from-python-b")
    java_b = _refresh(clients["java"], py_b["refresh_token"])
    _snapshot_contains(clients["java"], java_b["access_token"], entity_b_python, "from-python-b")
    py_b = _refresh(clients["python"], java_b["refresh_token"])
    password_b = _change_password(clients["python"], py_b["access_token"],
                                  "correct", "replacement148")
    stale_b = clients["java"].post(NATIVE + "/refresh", json={"refresh_token": py_b["refresh_token"]},
        headers={"X-Request-ID": "cutover148-stale-refresh-b"})
    assert stale_b.status_code == 401
    fresh_b = _login(clients["python"], "bob148", "replacement148")
    closure_b = _close_account(clients["python"], fresh_b["access_token"])
    closed_refresh_b = clients["java"].post(NATIVE + "/refresh",
        json={"refresh_token": fresh_b["refresh_token"]},
        headers={"X-Request-ID": "cutover148-closed-refresh-b"})
    assert closed_refresh_b.status_code == 401
    cleanup_b = _rows_after_closure(fixture["admin_url"], fixture["subjects"]["bob148"])
    assert cleanup_b["sync_rows"] == cleanup_b["manifest_rows"] == 0
    assert cleanup_b["native_families"] >= 1 and cleanup_b["native_revoked"]
    evidence["phases"].append({"direction": "Java→Python", "sync_cross_read": True,
        "old_java_access_rejected_by_python": old_java_access,
        "old_java_sync_access_rejected_by_python": old_java_sync_access,
        "password_change": password_b, "old_refresh_status": stale_b.status_code,
        "closure": closure_b, "closed_refresh_status": closed_refresh_b.status_code,
        "cleanup": cleanup_b})

    # Evidence is deliberately limited to statuses, response body for the old
    # access-token boundary, and cleanup counts. No access/refresh/recovery token.
    evidence_path = ARTIFACTS / "cutover-evidence.json"
    evidence_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    serialized = evidence_path.read_text(encoding="utf-8")
    for token in (py_tokens["access_token"], py_tokens["refresh_token"],
                  java_tokens["access_token"], java_tokens["refresh_token"],
                  fresh_a["access_token"], fresh_a["refresh_token"],
                  java_b["access_token"], java_b["refresh_token"],
                  py_b["access_token"], py_b["refresh_token"],
                  fresh_b["access_token"], fresh_b["refresh_token"]):
        assert token not in serialized
