"""Exact HTTP payload projection parity for every current sync record family."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from contract_support145 import ORG, WORKSPACE, sync145_backends
DEFINITIONS = json.loads((Path(__file__).resolve().parents[3] /
    "refactor/server-java/src/main/resources/sync/SyncDefinitions.json").read_text(encoding="utf-8"))
_REFACTOR_FIELDS = DEFINITIONS["refactor_fields"]
_REFACTOR_FIELD_VARIANTS = DEFINITIONS["refactor_field_variants"]
_REFACTOR_KIND_BY_RECORD = DEFINITIONS["refactor_kind_by_record"]
RECORDS = DEFINITIONS["product_records"]


BASE = "/api/enterprise/sync/v1"
WIRE_SCHEMA = "mentorflow.refactor-sync.v1"
HEADERS = ("cache-control", "content-type", "content-length", "x-request-id",
           "x-mentorflow-runtime-profile")
RUN_ID = uuid.uuid4().hex[:10]


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _entity(record_type, record_id):
    digest = hashlib.sha256(record_id.encode("utf-8")).hexdigest()
    return "refactor.v1:" + record_type + ":" + digest


def _headers(token, request_id):
    return {"Authorization": "Bearer " + token,
            "X-MentorFlow-Organization-ID": ORG,
            "X-MentorFlow-Workspace-ID": WORKSPACE,
            "X-Request-ID": request_id,
            "Content-Type": "application/json"}


def _paired(clients, method, path, *, token, request_id, body, expected=None):
    response = clients["java"].request(method, path, headers=_headers(token, request_id), content=body)
    if expected is not None:
        assert response.status_code == expected, response.content
    return response


def _put(clients, token, record_type, record_id, payload, *, request_id, status=200):
    payload = deepcopy(payload)
    # Keep the envelope record ID aligned with the URL entity digest. Tests
    # mutate only values; a stale envelope ID would fail before projection.
    payload["record_id"] = record_id
    body = _json({"request_id": request_id, "expected_revision": 0, "payload": payload})
    path = f"{BASE}/entities/{_REFACTOR_KIND_BY_RECORD[record_type]}/{_entity(record_type, record_id)}"
    return _paired(clients, "PUT", path, token=token, request_id=request_id, body=body, expected=status)


def _refactor_values(record_type, record_id):
    if record_type == "course_record":
        return {"schema": "mentorflow.course.module.v1", "plan_key": "plan:one",
                "entity": "module", "entity_id": "module:one", "payload": {"title": "课程模块"}}
    if record_type == "resource":
        return {"material_id": record_id, "filename": "notes.txt", "media_type": "text/plain",
                "sha256": "a" * 64, "size": 12, "product_classification": "USER_FILE",
                "text": "sample text"}
    if record_type == "draft_plan":
        return {"plan_key": "", "draft_id": "draft:one", "revision": 1, "status": "NEEDS_INPUT",
                "snapshot": {}, "confirmed": False, "updated_at": None, "idempotency_key": "key:one"}
    if record_type == "note":
        return {"plan_key": "", "task_id": "task:one", "text": "note"}
    values = {key: None for key in _REFACTOR_FIELDS[record_type]}
    if record_type in _REFACTOR_FIELD_VARIANTS:
        values = {key: None for key in next(iter(_REFACTOR_FIELD_VARIANTS[record_type]))}
    return values


def _envelope(record_type, record_id):
    return {"schema": WIRE_SCHEMA, "version": 1, "record_type": record_type,
            "record_id": record_id, "values": _refactor_values(record_type, record_id)}


def test_sync_payload145_all_refactor_record_types_http_roundtrip(sync145_backends):
    clients = sync145_backends
    token = clients["jwks"].token(subject="user-145-payload-refactor-" + RUN_ID)
    for index, record_type in enumerate(_REFACTOR_KIND_BY_RECORD):
        record_id = f"refactor:{record_type}:145"
        result = _put(clients, token, record_type, record_id, _envelope(record_type, record_id),
                      request_id=f"rf145-{index}")
        assert result.status_code == 200, (record_type, result.content)
        if record_type == "resource":
            assert result.json()["payload"]["refactor_record_id"] == record_id
        else:
            assert result.json()["payload"]["record_type"] == record_type


def _product_row(table, spec):
    row = {column: None for column in spec["columns"]}
    for key in spec["keys"]:
        row[key] = f"{table}-{key}-145"
    for key, value in {
        "original_filename": "source.txt", "filename": "source.txt", "title": "Test source",
        "media_type": "text/plain", "sha256": "b" * 64, "content_hash": "b" * 64,
        "size_bytes": 12, "classification": "USER_FILE", "receipt": "{}", "state": "{}",
        "payload": "{}", "source_type": "text", "mastery": 0.4,
        "completed_task_count": 2, "score": 0.4,
    }.items():
        if key in row:
            row[key] = value
    return row


def test_sync_payload145_all_product_record_types_from_java_definitions(sync145_backends):
    clients = sync145_backends
    token = clients["jwks"].token(subject="user-145-payload-product-" + RUN_ID)
    index = 0
    for table, spec in RECORDS.items():
        row = _product_row(table, spec)
        payload = {"product_record": {"schema": DEFINITIONS["product_schema"],
                                     "record_type": table, "values": row}}
        if spec.get("auxiliary"):
            payload["product_auxiliary"] = True
        if spec["kind"] == "resource":
            payload.update(product_classification=row.get("classification") or "USER_FILE",
                filename=row.get("original_filename") or row.get("title") or "resource",
                media_type=row.get("media_type") or "application/octet-stream",
                sha256=row.get("sha256") or row.get("content_hash"), size_bytes=row.get("size_bytes") or 0)
        elif spec["kind"] == "plan":
            payload.update(title=row.get("title") or row.get("id"), resource_ids=[])
        elif spec["kind"] == "progress":
            payload.update(plan_id=None, mastery=float(row.get("mastery") or row.get("score") or 0),
                           completed_tasks=int(row.get("completed_task_count") or 0))
        keys = [row[key] for key in spec["keys"]]
        entity_id = str(keys[0]) if table == "active_materials" else table + ":" + hashlib.sha256(
            json.dumps(keys, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
        path = f"{BASE}/entities/{spec['kind']}/{entity_id}"
        request_id = f"product145-{index}"
        response = _paired(clients, "PUT", path, token=token, request_id=request_id,
                           body=_json({"request_id": request_id, "expected_revision": 0,
                                       "payload": payload}), expected=200)
        assert response.json()["revision"] == 1, (table, response.content)
        index += 1


def test_sync_payload219_retired_aggregate_is_row_failure_not_batch_500(sync145_backends):
    clients = sync145_backends
    token = clients["jwks"].token(subject="user-145-retired-aggregate-219-" + RUN_ID)
    good_id = "course:bulk-219"
    good_entity = _entity("course_record", good_id)
    good_payload = _envelope("course_record", good_id)
    retired_values = {"id": "retired-product-plan-state", "state": "{}"}
    retired_key = json.dumps([retired_values["id"]], ensure_ascii=False, separators=(",", ":"))
    retired_entity = "product_plan_state:" + hashlib.sha256(retired_key.encode()).hexdigest()
    retired_payload = {"product_record": {"schema": "mentorflow.product-record.v1",
                        "record_type": "product_plan_state", "values": retired_values}}
    rows = [
        {"kind": "learning_run", "entity_id": good_entity, "request_id": "bulk-course219",
         "expected_revision": 0, "payload": good_payload},
        {"kind": "plan", "entity_id": retired_entity, "request_id": "bulk-retired219",
         "expected_revision": 0, "payload": retired_payload},
    ]
    batch = _paired(clients, "POST", f"{BASE}/entities/bulk", token=token,
                    request_id="bulk-mixed219", body=_json({"records": rows}), expected=200)
    results = batch.json()["results"]
    assert [(item["status"], item.get("code")) for item in results] == [
        (200, None), (422, "PRODUCT_SYNC_RECORD_FIELDS")]
    for backend in ("java",):
        snapshot = clients[backend].get(f"{BASE}/snapshot", headers=_headers(
            token, "bulk-mixed-snapshot219"))
        assert snapshot.status_code == 200
        assert any(item["entity_id"] == good_entity for item in snapshot.json()["entities"])
        assert not any(item["entity_id"] == retired_entity for item in snapshot.json()["entities"])


def test_sync_payload145_course_fields_secret_depth_and_size(sync145_backends):
    clients = sync145_backends
    token = clients["jwks"].token(subject="user-145-payload-course-" + RUN_ID)
    base = _envelope("course_record", "course:field-test")
    bad_values = [
        ("schema", "mentorflow.course.Module.v1"), ("schema", "mentorflow.course.module.v1000"),
        ("plan_key", "/invalid"), ("plan_key", None), ("entity", "other"),
        ("entity_id", "../bad"), ("entity_id", 7), ("payload", []), ("payload", None),
    ]
    for index, (field, value) in enumerate(bad_values):
        payload = deepcopy(base)
        payload["values"][field] = value
        response = _put(clients, token, "course_record", f"course:field:{index}", payload,
                        request_id=f"field145-{index}", status=422)
        assert response.json()["code"] == "SYNC_PAYLOAD_INVALID"
    for index, edit in enumerate(("missing", "extra")):
        payload = deepcopy(base)
        if edit == "missing":
            payload["values"].pop("entity")
        else:
            payload["values"]["extra"] = True
        response = _put(clients, token, "course_record", f"course:shape:{index}", payload,
                        request_id=f"shape145-{index}", status=422)
        assert response.json()["code"] == "SYNC_PAYLOAD_INVALID"
    secret = deepcopy(base)
    secret["values"]["payload"] = {"nested": [{"provider_secret": "never"}]}
    rejected_secret = _put(clients, token, "course_record", "course:secret", secret,
                           request_id="secret145", status=422)
    assert rejected_secret.json()["code"] == "SYNC_SECRET_FIELD_REJECTED"

    for depth, expected in ((32, 200), (33, 422)):
        payload = deepcopy(base)
        nested = {}
        for _ in range(depth - 1):
            nested = {"child": nested}
        payload["values"]["payload"] = nested
        response = _put(clients, token, "course_record", f"course:depth:{depth}", payload,
                        request_id=f"depth145-{depth}", status=expected)
        if expected == 422:
            assert response.json()["code"] == "SYNC_PAYLOAD_INVALID"

    for extra, expected in ((0, 200), (1, 422)):
        payload = deepcopy(base)
        payload["values"]["payload"] = {"text": "x" * (262_144 - len(b'{"text":""}') + extra)}
        size = len(json.dumps(payload["values"]["payload"], ensure_ascii=False, sort_keys=True,
                              separators=(",", ":")).encode("utf-8"))
        assert size == 262_144 + extra
        response = _put(clients, token, "course_record", f"course:size:{extra}", payload,
                        request_id=f"size145-{extra}", status=expected)
        if extra:
            assert response.json()["code"] == "SYNC_PAYLOAD_TOO_LARGE"


def test_sync_payload145_raw_all_kinds_and_legacy_references(sync145_backends):
    clients = sync145_backends
    token = clients["jwks"].token(subject="user-145-payload-raw-" + RUN_ID)
    # Legacy Pydantic payloads retain their historic shape and omitted defaults.
    resource_id = "legacy-resource145"
    resource = {"filename": "notes.txt", "media_type": "text/plain", "sha256": "c" * 64,
                "size_bytes": 4, "product_classification": "USER_FILE"}
    r = _paired(clients, "PUT", f"{BASE}/entities/resource/{resource_id}", token=token,
                request_id="raw-resource145", body=_json({"request_id": "raw-resource145",
                "expected_revision": 0, "payload": resource}), expected=200)
    assert r.json()["payload"]["filename"] == "notes.txt"
    plan_id = "legacy-plan145"
    plan = {"title": "Plan", "resource_ids": [resource_id]}
    p = _paired(clients, "PUT", f"{BASE}/entities/plan/{plan_id}", token=token,
                request_id="raw-plan145", body=_json({"request_id": "raw-plan145",
                "expected_revision": 0, "payload": plan}), expected=200)
    assert p.json()["payload"]["resource_ids"] == [resource_id]
    progress = {"plan_id": plan_id, "mastery": 0.5, "completed_tasks": 3}
    pr = _paired(clients, "PUT", f"{BASE}/entities/progress/progress-145", token=token,
                 request_id="raw-progress145", body=_json({"request_id": "raw-progress145",
                 "expected_revision": 0, "payload": progress}), expected=200)
    assert pr.json()["payload"]["plan_id"] == plan_id

    kinds = ("plan", "progress", "resource", "task", "calendar", "review", "learner_profile",
             "learning_run", "history", "setting")
    for kind in kinds:
        payload = {"raw": "原样保留"}
        if kind == "plan": payload = {"title": "Raw", "resource_ids": []}
        elif kind == "progress": payload = {"plan_id": plan_id, "mastery": 0.2, "completed_tasks": 0}
        elif kind == "resource": payload = {"filename": "raw.txt", "media_type": "text/plain",
                                               "sha256": "d" * 64, "size_bytes": 1}
        request_id = "kind145-" + kind
        response = _paired(clients, "PUT", f"{BASE}/entities/{kind}/raw-{kind}-145", token=token,
                           request_id=request_id,
                           body=_json({"request_id": request_id, "expected_revision": 0,
                                       "payload": payload}), expected=200)
        assert response.json()["payload"].get("raw") == "原样保留" if kind not in ("plan", "progress", "resource") else True


def test_sync_payload145_unicode_numeric_canonical_digest(sync145_backends):
    clients = sync145_backends
    subject = "user-145-payload-digest-" + RUN_ID
    token = clients["jwks"].token(subject=subject)
    record_id = "numeric:unicode:145"
    payload = _envelope("course_record", record_id)
    payload["values"]["payload"] = {"😀": 1.0, "\ue000": -0.0, "中文": {"z": True, "a": 3},
                                      "subnormal": 5e-324, "large": 1e23, "tiny": 1e-5}
    req = "canonical-digest145"
    response = _put(clients, token, "course_record", record_id, payload, request_id=req)
    assert response.json()["revision"] == 1
    fingerprint = ["learning_run", _entity("course_record", record_id), 0, False, payload]
    expected = hashlib.sha256(json.dumps(fingerprint, sort_keys=True, separators=(",", ":"),
                                         allow_nan=False).encode()).hexdigest()
    assert expected
    for name in ("java",):
        engine = create_engine(clients[name + "_admin_url"])
        try:
            with engine.connect() as connection:
                digest = connection.execute(text("SELECT digest FROM structured_sync_requests WHERE subject_id=:subject AND request_id=:id"),
                                            {"subject": subject, "id": req}).scalar_one()
                assert digest == expected
        finally:
            engine.dispose()
