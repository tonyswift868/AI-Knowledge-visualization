"""The same real-HTTP operation stream against Python and Java on isolated PostgreSQL."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
import json
import os
import re
from threading import Barrier

import pytest
from sqlalchemy import create_engine, text

from contract_support145 import ORG, WORKSPACE, sync145_backends


BASE = "/api/enterprise/sync/v1"
WIRE_SCHEMA = "mentorflow.refactor-sync.v1"
HEADERS = ("cache-control", "content-type", "content-length", "x-request-id",
           "x-mentorflow-runtime-profile")
_PG_SNAPSHOT = re.compile(rb'"pg-snapshot:[0-9]+:[0-9]+:[0-9,]*"')
_SERVER_TIME = re.compile(
    rb'"server_confirmed_at":"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}'
    rb'(?:\.[0-9]{6})?[+-][0-9]{2}:[0-9]{2}"')


def _entity_id(record_id):
    digest = hashlib.sha256(record_id.encode("utf-8")).hexdigest()
    return "refactor.v1:course_record:" + digest


def _envelope(record_id, *, title="知识点", padding=0):
    value = {"schema": "mentorflow.course.module.v1", "plan_key": "plan:one",
             "entity": "module", "entity_id": record_id,
             "payload": {"title": title, "nodes": [{"id": "point:one", "mastered": False}]}}
    if padding:
        value["payload"]["padding"] = "x" * padding
    return {"schema": WIRE_SCHEMA, "version": 1, "record_type": "course_record",
            "record_id": record_id, "values": value}


def _headers(token, request_id):
    return {"Authorization": "Bearer " + token,
            "X-MentorFlow-Organization-ID": ORG,
            "X-MentorFlow-Workspace-ID": WORKSPACE,
            "X-Request-ID": request_id,
            "Content-Type": "application/json"}


def _wire_json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _signature(response, *, snapshot=False):
    headers = {name: response.headers[name] for name in HEADERS if name in response.headers}
    body = response.content
    if snapshot:
        # Each DB runs its own MVCC snapshot and statement timestamp. Compare
        # exact response bytes after replacing only correctly shaped volatile
        # marker values. Content-Length necessarily reflects those original
        # volatile byte lengths, so it is the one omitted response header here.
        assert len(_PG_SNAPSHOT.findall(body)) >= 1, "snapshot marker format changed"
        assert len(_SERVER_TIME.findall(body)) >= 1, "snapshot time format changed"
        body = _PG_SNAPSHOT.sub(b'"<PG-SNAPSHOT>"', body)
        body = _SERVER_TIME.sub(b'"server_confirmed_at":"<SERVER-TIME>"', body)
        headers.pop("content-length", None)
    return response.status_code, headers, body


def _paired(clients, method, path, *, headers=None, body=None, status=None):
    responses = [clients[name].request(method, path, headers=headers, content=body)
                 for name in ("python", "java")]
    py, java = responses
    volatile_snapshot = path.startswith(f"{BASE}/snapshot") and py.status_code == 200
    assert _signature(py, snapshot=volatile_snapshot) == _signature(java, snapshot=volatile_snapshot), (
        f"{method} {path} diverged\nPython={_signature(py, snapshot=volatile_snapshot)!r}\n"
        f"Java={_signature(java, snapshot=volatile_snapshot)!r}")
    if status is not None:
        assert py.status_code == status, py.content
    return py


def _put_body(payload, *, request_id, revision):
    return _wire_json({"request_id": request_id, "expected_revision": revision, "payload": payload})


def _put(clients, token, record_id, payload, *, request_id, revision, status=200, body=None):
    data = body if body is not None else _put_body(payload, request_id=request_id, revision=revision)
    return _paired(clients, "PUT", f"{BASE}/entities/learning_run/{_entity_id(record_id)}",
                   headers=_headers(token, request_id), body=data, status=status)


def _read_database(url):
    engine = create_engine(url, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            connection.execute(text("SET row_security = off"))
            rows = {}
            rows["structured_sync_entities"] = connection.execute(text("""
                SELECT issuer,subject_id,organization_id,workspace_id,kind,entity_id,
                       revision,deleted,payload,change_seq
                FROM public.structured_sync_entities
                ORDER BY issuer,subject_id,organization_id,workspace_id,kind,entity_id
            """)).mappings().all()
            rows["structured_sync_requests"] = connection.execute(text("""
                SELECT issuer,subject_id,organization_id,workspace_id,request_id,digest,response
                FROM public.structured_sync_requests
                ORDER BY issuer,subject_id,organization_id,workspace_id,request_id
            """)).mappings().all()
            rows["rehydration_manifests"] = connection.execute(text("""
                SELECT issuer,subject_id,organization_id,workspace_id,scope_version,initialized,
                       generation
                FROM public.rehydration_manifests
                ORDER BY issuer,subject_id,organization_id,workspace_id
            """)).mappings().all()
            rows["rls"] = connection.execute(text("""
                SELECT c.relname,c.relrowsecurity,c.relforcerowsecurity,
                       pg_get_userbyid(c.relowner) AS owner
                FROM pg_class c
                WHERE c.oid IN ('public.structured_sync_entities'::regclass,
                                'public.structured_sync_requests'::regclass,
                                'public.rehydration_manifests'::regclass)
                ORDER BY c.relname
            """)).mappings().all()
            return {key: [dict(row) for row in value] for key, value in rows.items()}
    finally:
        engine.dispose()


def test_sync145_contract_stream_and_postgres_rows_match(sync145_backends):
    clients = sync145_backends
    token = clients["jwks"].token(subject="user-145-a")
    headers = _headers(token, "read145")

    # Single create, replay, update and stale-revision error.
    first = _envelope("module:alpha")
    created = _put(clients, token, "module:alpha", first,
                   request_id="create-alpha", revision=0)
    assert created.json()["revision"] == 1
    _put(clients, token, "module:alpha", first, request_id="create-alpha", revision=0)
    reused = _put(clients, token, "module:alpha", _envelope("module:alpha", title="不同内容"),
                  request_id="create-alpha", revision=0, status=409)
    assert reused.json()["code"] == "SYNC_IDEMPOTENCY_KEY_REUSED"
    changed = _envelope("module:alpha", title="更新后的知识点")
    updated = _put(clients, token, "module:alpha", changed,
                   request_id="update-alpha", revision=1)
    assert updated.json()["revision"] == 2
    stale = _put(clients, token, "module:alpha", first,
                 request_id="stale-alpha", revision=1, status=409)
    assert stale.json()["code"] == "SYNC_STALE_REVISION"

    # One outer bulk request returns a per-row success, conflict and oversize result.
    second = _envelope("module:beta", title="批量成功")
    too_large = _envelope("module:too-large", padding=256 * 1024)
    rows = [
        {"kind": "learning_run", "entity_id": _entity_id("module:beta"),
         "request_id": "bulk-good", "expected_revision": 0, "payload": second},
        {"kind": "learning_run", "entity_id": _entity_id("module:alpha"),
         "request_id": "bulk-conflict", "expected_revision": 1, "payload": first},
        {"kind": "learning_run", "entity_id": _entity_id("module:too-large"),
         "request_id": "bulk-large", "expected_revision": 0, "payload": too_large},
    ]
    bulk = _paired(clients, "POST", f"{BASE}/entities/bulk",
                   headers=_headers(token, "bulk145"), body=_wire_json({"records": rows}), status=200)
    assert [row["status"] for row in bulk.json()["results"]] == [200, 409, 422]
    assert bulk.json()["results"][2]["code"] == "SYNC_PAYLOAD_TOO_LARGE"
    invalid = _envelope("module:invalid")
    invalid["values"]["entity"] = "other"
    invalid_row = {"kind": "learning_run", "entity_id": _entity_id("module:invalid"),
                   "request_id": "invalid-payload", "expected_revision": 0, "payload": invalid}
    invalid_bulk = _paired(clients, "POST", f"{BASE}/entities/bulk",
                           headers=_headers(token, "invalidbulk145"),
                           body=_wire_json({"records": [invalid_row]}), status=200)
    assert invalid_bulk.json()["results"][0]["status"] == 422
    assert invalid_bulk.json()["results"][0]["code"] == "SYNC_PAYLOAD_INVALID"

    # Delete is a tombstone; snapshot retains it and changes pages by its cursor.
    deleted = _paired(clients, "POST",
        f"{BASE}/entities/learning_run/{_entity_id('module:beta')}/delete",
        headers=_headers(token, "delete145"),
        body=_wire_json({"request_id": "delete-beta", "expected_revision": 1}), status=200)
    assert deleted.json()["deleted"] is True and deleted.json()["revision"] == 2
    tombstone = _paired(clients, "PUT", f"{BASE}/entities/learning_run/{_entity_id('module:beta')}",
        headers=_headers(token, "tombstone145"),
        body=_put_body(second, request_id="after-delete", revision=2), status=410)
    assert tombstone.json()["code"] == "SYNC_ENTITY_TOMBSTONED"

    snapshot = _paired(clients, "GET", f"{BASE}/snapshot", headers=headers)
    beta = next(item for item in snapshot.json()["entities"] if item["entity_id"] == _entity_id("module:beta"))
    assert beta["deleted"] is True
    first_page = _paired(clients, "GET", f"{BASE}/changes?since=0&kind=learning_run&limit=1",
                         headers=headers)
    assert first_page.json()["has_more"] is True
    cursor = first_page.json()["next_cursor"]
    next_page = _paired(clients, "GET", f"{BASE}/changes?since={cursor}&kind=learning_run&limit=500",
                        headers=headers)
    assert any(item["deleted"] for item in next_page.json()["entities"])
    assert _paired(clients, "GET", f"{BASE}/changes?since=0&kind=learning_run&limit=501",
                   headers=headers, status=422).status_code == 422
    _paired(clients, "GET", f"{BASE}/changes", headers={"X-Request-ID": "unauth145"}, status=401)
    wrong_tenant = _headers(token, "wrong-tenant145") | {"X-MentorFlow-Organization-ID": "org-outside"}
    wrong_scope = _paired(clients, "GET", f"{BASE}/snapshot", headers=wrong_tenant, status=403)
    assert wrong_scope.json()["error"]["code"] == "MEMBERSHIP_NOT_ACTIVE"

    # A second authenticated actor can write the same entity key without seeing A's row.
    other_token = clients["jwks"].token(subject="user-145-b")
    missing_owner_row = _put(clients, other_token, "module:alpha",
                             _envelope("module:alpha", title="跨账号尝试"),
                             request_id="other-stale", revision=1, status=409)
    assert missing_owner_row.json()["code"] == "SYNC_STALE_REVISION"
    other = _envelope("module:alpha", title="另一账号")
    _put(clients, other_token, "module:alpha", other,
         request_id="other-create", revision=0)
    own_snapshot = _paired(clients, "GET", f"{BASE}/snapshot", headers=headers)
    other_snapshot = _paired(clients, "GET", f"{BASE}/snapshot",
                              headers=_headers(other_token, "other-read"))
    assert all(item["payload"] != other for item in own_snapshot.json()["entities"])
    assert any(item["payload"] == other for item in other_snapshot.json()["entities"])

    py_rows = _read_database(clients["python_admin_url"])
    java_rows = _read_database(clients["java_admin_url"])
    assert py_rows == java_rows
    assert all(item["relrowsecurity"] and item["relforcerowsecurity"]
               and item["owner"] == "mentorflow_migrator" for item in py_rows["rls"])


def test_sync145_tombstone_can_create_missing_entity_without_changing_delete(sync145_backends):
    token = sync145_backends["jwks"].token(subject="user-145-a")
    entity_id = _entity_id("first-contact:plan")
    path = f"{BASE}/entities/learning_run/{entity_id}"
    missing_delete = _paired(sync145_backends, "POST", path + "/delete",
        headers=_headers(token, "legacy-missing-delete145"),
        body=_wire_json({"request_id": "legacy-missing-delete", "expected_revision": 1}), status=404)
    assert missing_delete.json()["code"] == "SYNC_ENTITY_NOT_FOUND"
    stale_tombstone = _paired(sync145_backends, "POST", path + "/tombstone",
        headers=_headers(token, "stale-missing-tombstone145"),
        body=_wire_json({"request_id": "stale-missing-tombstone", "expected_revision": 1}), status=409)
    assert stale_tombstone.json()["code"] == "SYNC_STALE_REVISION"

    request = {"request_id": "create-tombstone145", "expected_revision": 0}
    created = _paired(sync145_backends, "POST", path + "/tombstone",
        headers=_headers(token, "create-tombstone145"), body=_wire_json(request), status=200)
    expected = {"kind": "learning_run", "entity_id": entity_id, "revision": 1,
                "deleted": True, "payload": {}}
    assert created.json() == expected
    assert _paired(sync145_backends, "POST", path + "/tombstone",
        headers=_headers(token, "replay-tombstone145"), body=_wire_json(request), status=200).json() == expected
    # The old delete still validates revision >= 1 before reading any receipt.
    _paired(sync145_backends, "POST", path + "/delete",
        headers=_headers(token, "intent-mismatch145"), body=_wire_json(request), status=422)

    existing_id = _entity_id("existing-tombstone:plan")
    existing_path = f"{BASE}/entities/learning_run/{existing_id}"
    _put(sync145_backends, token, "existing-tombstone:plan", _envelope("existing-tombstone:plan"),
         request_id="create-live-for-tombstone", revision=0)
    existing_delete = {"request_id": "delete-existing-tombstone145", "expected_revision": 1}
    existing = _paired(sync145_backends, "POST", existing_path + "/tombstone",
        headers=_headers(token, "existing-tombstone145"),
        body=_wire_json(existing_delete), status=200)
    assert existing.json()["revision"] == 2 and existing.json()["deleted"] is True
    old_intent = _paired(sync145_backends, "POST", existing_path + "/delete",
        headers=_headers(token, "existing-old-intent145"), body=_wire_json(existing_delete), status=409)
    assert old_intent.json()["code"] == "SYNC_IDEMPOTENCY_KEY_REUSED"


@pytest.mark.parametrize("method,route", [
    ("PUT", f"{BASE}/entities/learning_run/" + _entity_id("limit145-single")),
    ("POST", f"{BASE}/entities/bulk"),
])
def test_sync145_http_body_size_boundary_is_measured_and_compared(sync145_backends, method, route):
    """Exercise the 9 MiB boundary and Java's early application rejection."""
    clients = sync145_backends
    token = clients["jwks"].token(subject="user-145-a")
    if method == "PUT":
        base = _put_body(_envelope("limit145-single"), request_id="single-limit", revision=0)
    else:
        row = {"kind": "learning_run", "entity_id": _entity_id("limit145-bulk"),
               "request_id": "bulk-limit", "expected_revision": 0,
               "payload": _envelope("limit145-bulk")}
        base = _wire_json({"records": [row]})
    boundary = 9 * 1024 * 1024
    assert len(base) < boundary
    outcomes = []
    for size in (boundary, boundary + 1):
        body = base + b" " * (size - len(base))
        request_headers = _headers(token, f"limit-{method}-{size}")
        responses = {
            name: clients[name].request(method, route, headers=request_headers, content=body)
            for name in ("python", "java")
        }
        outcomes.append(responses)
    assert outcomes[0]["python"].status_code == 200, f"9 MiB should be accepted: {outcomes[0]!r}"
    assert outcomes[0]["java"].status_code == 200, f"9 MiB should be accepted: {outcomes[0]!r}"
    # Python's direct reference app has no application ingress cap. Java must
    # reject before reading/parsing the 9 MiB + 1 byte request.
    assert outcomes[1]["python"].status_code == 200
    java_oversized = outcomes[1]["java"]
    assert java_oversized.status_code == 413
    assert java_oversized.json()["error"]["code"] == "SYNC_REQUEST_TOO_LARGE"
    assert java_oversized.json()["error"]["message"] == "Request must not exceed 9 MiB (9437184 bytes)"


def test_sync145_java_chunked_body_is_rejected_while_reading(sync145_backends):
    """A chunked request has no length header, so Java must count the stream."""
    clients = sync145_backends
    token = clients["jwks"].token(subject="user-145-chunked-limit")
    route = f"{BASE}/entities/learning_run/" + _entity_id("chunked-limit145")
    base = _put_body(_envelope("chunked-limit145"), request_id="chunked-limit", revision=0)
    body = base + b" " * (9 * 1024 * 1024 + 1 - len(base))

    def chunks():
        for offset in range(0, len(body), 64 * 1024):
            yield body[offset:offset + 64 * 1024]

    request = clients["java"].build_request(
        "PUT", route, headers=_headers(token, "chunked-limit"), content=chunks()
    )
    assert "content-length" not in request.headers
    response = clients["java"].send(request)
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "SYNC_REQUEST_TOO_LARGE"


def test_sync145_membership_database_failure_is_identical_503(sync145_backends):
    clients = sync145_backends
    token = clients["jwks"].token(subject="user-145-db-failure")
    response = _paired(clients, "GET", f"{BASE}/snapshot?kind=learning_run",
                       headers=_headers(token, "membership-db-failure145"), status=503)
    assert response.json()["code"] == "SYNC_QUERY_FAILED"


def test_sync145_fifty_concurrent_same_record_cas_writes_per_backend(sync145_backends):
    clients = sync145_backends
    token = clients["jwks"].token(subject="user-145-a")
    record_id = "module:race145"
    for round_number in range(50):
        barrier = Barrier(2)

        def send(index):
            barrier.wait()
            # Both requests start at the barrier; the winner is intentionally
            # nondeterministic, as it is for two devices racing on the CAS.
            payload = _envelope(record_id, title=f"round-{round_number}")
            body = _put_body(payload, request_id=f"race-{round_number}-{index}",
                             revision=round_number)
            return client.put(
                f"{BASE}/entities/learning_run/{_entity_id(record_id)}",
                headers=_headers(token, f"race-{round_number}-{index}"), content=body)

        for backend_name in ("python", "java"):
            client = clients[backend_name]
            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(send, (0, 1)))
            assert sorted(response.status_code for response in results) == [200, 409]
            loser = next(response for response in results if response.status_code == 409)
            assert loser.json()["code"] == "SYNC_STALE_REVISION"
    py_rows = _read_database(clients["python_admin_url"])
    java_rows = _read_database(clients["java_admin_url"])
    py_row = next(row for row in py_rows["structured_sync_entities"]
                  if row["entity_id"] == _entity_id(record_id))
    java_row = next(row for row in java_rows["structured_sync_entities"]
                    if row["entity_id"] == _entity_id(record_id))
    assert py_row["revision"] == java_row["revision"] == 50
    assert py_row["payload"] == java_row["payload"]
