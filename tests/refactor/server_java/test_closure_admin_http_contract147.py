"""Run one HTTP-only account/admin trace against Python and Java on local PG."""
from __future__ import annotations

import json
import re

import httpx
import psycopg
import pytest

from contract_support144 import OLD_ISSUER
from contract_support147 import ORG, WORKSPACE, SUBJECTS

def _login(client, username):
    response = client.post("/api/enterprise/auth/native/login",
        json={"username": username, "password": "correct"}, headers={"X-Request-ID": "closure147-login"})
    assert response.status_code == 200, response.text
    value = response.json()
    assert value["access_token"].startswith("mfn1_a_")
    assert value["refresh_token"].startswith("mfn1_r_")
    return value


def _closure_headers(token):
    return {"X-MentorFlow-Closure-Token": token, "X-Request-ID": "closure147"}


def _prepare(client, access):
    response = client.post("/api/enterprise/account/closure/prepare",
        json={"current_password": "correct"}, headers={
            "Authorization": "Bearer " + access, "X-Request-ID": "closure147"})
    assert response.status_code == 200, response.text
    value = response.json()
    assert value["state"] == "PREPARED"
    assert re.fullmatch(r"[0-9a-f]{32}", value["operation_id"])
    assert re.fullmatch(r"mfa1_[0-9a-f]{64}", value["account_id"])
    assert re.fullmatch(r"mfc1_[A-Za-z0-9_-]{43}", value["recovery_token"])
    return response, value


def _status(client, operation, token):
    return client.get("/api/enterprise/account/closure/" + operation, headers=_closure_headers(token))


def _commit(client, operation, token):
    return client.post("/api/enterprise/account/closure/" + operation + "/commit",
        json={"confirmed": True}, headers=_closure_headers(token))


def _normalize_http(response):
    """Keep raw JSON ordering/UTF-8 bytes while replacing only random credentials."""
    body = response.content.decode("utf-8")
    try:
        value = response.json()
        if isinstance(value, dict):
            for field in ("operation_id", "recovery_token"):
                if field in value and isinstance(value[field], str):
                    body = body.replace(value[field], "<" + field + ">")
    except ValueError:
        pass
    return (response.status_code, response.headers.get("content-type"),
        response.headers.get("cache-control"), response.headers.get("x-request-id"), body)


def _normalize_admin(payload):
    """Compare original UTF-8 JSON bytes, replacing only validated clock fields."""
    body = payload.decode("utf-8")
    value = json.loads(body)
    assert value["schema"] == "mentorflow.admin-overview.v1"
    observed, refreshed = value["observed_at"], value["refreshed_at"]
    timestamp = r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{6}\+00:00"
    assert re.fullmatch(timestamp, observed)
    assert re.fullmatch(timestamp, refreshed)
    assert refreshed == observed
    return body.replace(observed, "<timestamp>").replace(refreshed, "<timestamp>")


def _snapshot(admin_url, subjects):
    result = {"rows": {}, "closure": [], "native": []}
    with psycopg.connect(admin_url.replace("postgresql+psycopg:", "postgresql:")) as conn:
        for table, columns in {
            "organization_memberships": "subject_id, role, status",
            "workspace_memberships": "subject_id, status",
            "workspace_role_bindings": "subject_id, role, status",
            "rehydration_manifests": "subject_id, scope_version, initialized, generation",
            "structured_sync_entities": "subject_id, kind, entity_id, revision, deleted, payload::text",
            "structured_sync_requests": "subject_id, request_id, digest, response::text",
        }.items():
            rows = conn.execute(f"SELECT {columns} FROM public.{table} WHERE subject_id = ANY(%s) ORDER BY subject_id", (list(subjects),)).fetchall()
            result["rows"][table] = [list(row) for row in rows]
        records = conn.execute("""SELECT operation_id,issuer,subject_id,account_id,recovery_digest,state,code,
            data_cleaned,identity_deleted,created_at::text,committed_at::text,completed_at::text
            FROM account_closure.operations WHERE subject_id = ANY(%s) ORDER BY subject_id""", (list(subjects),)).fetchall()
        normalized = []
        for row in records:
            value = list(row)
            assert re.fullmatch(r"[0-9a-f]{32}", value[0])
            assert re.fullmatch(r"[0-9a-f]{64}", value[4])
            value[0] = "<operation_id>"; value[4] = "<recovery_digest>"
            value[9:12] = ["<timestamp>" if item is not None else None for item in value[9:12]]
            normalized.append(value)
        result["closure"] = sorted(normalized, key=lambda row: tuple(str(item) for item in row[1:9]))
        native = conn.execute("""SELECT issuer,subject,revoked_reason,account_binding,principal->>'actor_kind'
            FROM native_session_families WHERE subject = ANY(%s) ORDER BY subject,revoked_reason""", (list(subjects),)).fetchall()
        result["native"] = sorted([list(row) for row in native], key=lambda row: tuple(str(item) for item in row))
    return result


def _run_backend_trace(name, fixture):
    client = fixture["clients"][name]
    identity = fixture["identities"][name]
    admin_url = fixture[name + "_admin_url"]
    result = {}
    # Management endpoint: same role projection and canonical aggregate on both applications.
    admin = _login(client, "admin")
    learner = _login(client, "alice")
    org_headers = {"X-MentorFlow-Organization-ID": ORG, "X-MentorFlow-Workspace-ID": WORKSPACE}
    method_results = []
    for method in ("HEAD", "POST", "PUT", "OPTIONS"):
        response = client.request(method, "/api/enterprise/auth/admin/overview", headers={
            **org_headers, "Authorization": "Bearer " + admin["access_token"],
            "X-Request-ID": "admin-method-147"})
        assert response.status_code == 405, (method, response.text)
        assert response.headers.get("allow") == "GET"
        method_results.append((method, response.status_code, response.headers.get("allow"),
            response.headers.get("content-type"), response.headers.get("content-length"), response.content))
    denied = client.get("/api/enterprise/auth/admin/overview", headers={**org_headers,
        "Authorization": "Bearer " + learner["access_token"], "X-Request-ID": "closure147-admin"})
    allowed = client.get("/api/enterprise/auth/admin/overview", headers={**org_headers,
        "Authorization": "Bearer " + admin["access_token"], "X-Request-ID": "closure147-admin"})
    assert denied.status_code == 403 and denied.json()["error"]["code"] == "APPLICATION_SUPER_ADMIN_REQUIRED", denied.text
    assert allowed.status_code == 200, allowed.text
    overview = allowed.json()
    assert overview["schema"] == "mentorflow.admin-overview.v1"
    assert overview["registered_human_users"] == 2
    assert overview["backend_account_count"] == 3
    assert overview["workspace_member_count"] == 3
    # The frozen 0040 function's UNION/EXCEPT operators are left-associative;
    # its current output for this fixture is 0 and parity must preserve it.
    assert overview["identity_account_mismatch_count"] == 0
    result["admin"] = {"denied": _normalize_http(denied),
        "allowed_status": allowed.status_code,
        "content_type": allowed.headers.get("content-type"),
        "cache_control": allowed.headers.get("cache-control"),
        "request_id": allowed.headers.get("x-request-id"),
        "content_length": allowed.headers.get("content-length"),
        "payload": _normalize_admin(allowed.content)}
    result["admin_methods"] = method_results

    # Recovery credentials are account- and operation-scoped; a replacement invalidates the former pair.
    alice, first = _prepare(client, learner["access_token"])
    bob = _login(client, "bob")
    _, bob_closure = _prepare(client, bob["access_token"])
    foreign = _status(client, first["operation_id"], bob_closure["recovery_token"])
    assert foreign.status_code == 403 and foreign.json()["code"] == "CLOSURE_CREDENTIAL_INVALID", foreign.text
    wrong_id = _status(client, "f" * 32, first["recovery_token"])
    assert wrong_id.status_code == 403 and wrong_id.json()["code"] == "CLOSURE_CREDENTIAL_INVALID", wrong_id.text
    alice_old_op, alice_current = first["operation_id"], first
    alice, alice_current = _prepare(client, learner["access_token"])
    stale = _status(client, alice_old_op, alice_old_op and first["recovery_token"])
    assert stale.status_code == 403 and stale.json()["code"] == "CLOSURE_CREDENTIAL_INVALID", stale.text
    before = _status(client, alice_current["operation_id"], alice_current["recovery_token"])
    assert before.status_code == 200 and before.json()["state"] == "PREPARED"
    result["prepare"] = _normalize_http(alice)
    result["status"] = _normalize_http(before)

    # Mark a live lease expired in the isolated PostgreSQL fixture; the normal HTTP retry reclaims it.
    with psycopg.connect(admin_url.replace("postgresql+psycopg:", "postgresql:"), autocommit=True) as conn:
        conn.execute("UPDATE account_closure.operations SET state='IN_PROGRESS',committed_at=clock_timestamp(),claim=%s,lease_expires_at=clock_timestamp()-interval '1 second',code='CLOSURE_IN_PROGRESS' WHERE operation_id=%s",
                     ("b" * 32, alice_current["operation_id"]))
    expired_lease = _status(client, alice_current["operation_id"], alice_current["recovery_token"])
    assert expired_lease.status_code == 200 and expired_lease.json()["state"] == "RETRYABLE_FAILURE"
    assert expired_lease.json()["code"] == "CLOSURE_LEASE_EXPIRED"
    result["expired_lease"] = _normalize_http(expired_lease)

    calls_before = len(identity.calls)
    success = _commit(client, alice_current["operation_id"], alice_current["recovery_token"])
    assert success.status_code == 200 and success.json()["state"] == "COMPLETED", success.text
    replay = _commit(client, alice_current["operation_id"], alice_current["recovery_token"])
    assert replay.status_code == 200 and replay.json() == success.json(), replay.text
    provider_calls = [call for call in identity.calls[calls_before:] if "/admin/realms/mentorflow/users/" in call[1]]
    assert [method for method, _path in provider_calls] == ["GET", "DELETE", "GET"], provider_calls
    access_after = client.get("/api/enterprise/auth/native/session", headers={"Authorization": "Bearer " + learner["access_token"]})
    refresh_after = client.post("/api/enterprise/auth/native/refresh", json={"refresh_token": learner["refresh_token"]})
    assert access_after.status_code == refresh_after.status_code == 401
    result["commit"] = _normalize_http(success)
    result["replay"] = _normalize_http(replay)
    result["old_access"] = (access_after.status_code, access_after.json().get("code"))
    result["old_refresh"] = (refresh_after.status_code, refresh_after.json().get("code"))
    result["rows_after_alice"] = _snapshot(admin_url, SUBJECTS.values())
    # The unclosed accounts retain their scoped data; all Alice-owned rows are removed.
    for table, rows in result["rows_after_alice"]["rows"].items():
        assert all(row[0] != SUBJECTS["alice"] for row in rows), (table, rows)
        assert any(row[0] == SUBJECTS["bob"] for row in rows), (table, rows)
    assert any(row[5] == "COMPLETED" and row[7] is True and row[8] is True for row in result["rows_after_alice"]["closure"])
    assert any(row[1] == SUBJECTS["alice"] and row[2] is not None for row in result["rows_after_alice"]["native"])

    # Simulate a Keycloak outage after the SQL cleanup checkpoint; state must remain retryable and identical.
    admin_new = _login(client, "admin")
    _, admin_op = _prepare(client, admin_new["access_token"])
    identity.fail_delete = True
    fail = _commit(client, admin_op["operation_id"], admin_op["recovery_token"])
    identity.fail_delete = False
    assert fail.status_code == 503 and fail.json()["state"] == "RETRYABLE_FAILURE", fail.text
    assert fail.json()["code"] == "CLOSURE_PROVIDER_UNAVAILABLE"
    result["provider_failure"] = _normalize_http(fail)
    result["rows_after_failure"] = _snapshot(admin_url, SUBJECTS.values())
    failures = [row for row in result["rows_after_failure"]["closure"] if row[2] == SUBJECTS["admin"]]
    assert len(failures) == 1 and failures[0][6:9] == ["CLOSURE_PROVIDER_UNAVAILABLE", True, False], failures
    retry_status = _status(client, admin_op["operation_id"], admin_op["recovery_token"])
    assert retry_status.status_code == 200 and retry_status.json()["state"] == "RETRYABLE_FAILURE"
    identity.fail_delete = False
    retry = _commit(client, admin_op["operation_id"], admin_op["recovery_token"])
    assert retry.status_code == 200 and retry.json()["state"] == "COMPLETED", retry.text
    result["provider_retry"] = _normalize_http(retry)
    result["rows_after_retry"] = _snapshot(admin_url, SUBJECTS.values())
    retried = [row for row in result["rows_after_retry"]["closure"] if row[2] == SUBJECTS["admin"]]
    assert len(retried) == 1 and retried[0][5] == "COMPLETED" and retried[0][7:9] == [True, True]
    assert not any(row[0] == SUBJECTS["admin"] for row in result["rows_after_failure"]["rows"]["rehydration_manifests"])
    assert SUBJECTS["admin"] in identity.deleted
    return result


def test_paired_closure_admin_http_and_rows147(closure147_backends):
    python = _run_backend_trace("python", closure147_backends)
    java = _run_backend_trace("java", closure147_backends)
    for key in ("admin", "admin_methods", "prepare", "status", "expired_lease", "commit", "replay", "old_access", "old_refresh", "provider_failure", "provider_retry"):
        assert python[key] == java[key], (key, python[key], java[key])
    assert python["rows_after_alice"] == java["rows_after_alice"]
    assert python["rows_after_failure"] == java["rows_after_failure"]
    assert python["rows_after_retry"] == java["rows_after_retry"]
