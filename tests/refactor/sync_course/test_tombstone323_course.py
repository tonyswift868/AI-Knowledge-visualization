"""BUG-222 course tombstone recovery using a real temporary CourseStore."""
from dataclasses import replace
import importlib.util
import json
from pathlib import Path
from threading import Event

import httpx
import pytest

from refactor.learning.course.store import CourseStore, StoreError
from refactor.learning.course.sync_course import CourseSync, PREFIX
from refactor.learning.sync_identity import PlanError

_HARNESS_PATH = Path(__file__).resolve().parents[1] / "sync_course" / "test_sync_course.py"
_spec = importlib.util.spec_from_file_location("sync_course_tombstone_harness323", _HARNESS_PATH)
harness = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(harness)
connection = harness.connection


def _deleted_plan(store, plan_id):
    store.create_plan(plan_id, goal="temporary tombstone fixture")
    store.delete_plan(plan_id)
    record = store.pending_record("plans", plan_id)
    assert record is not None and record.deleted
    assert record.payload == {"id": plan_id}
    return record


def _write_calls(server):
    return [(method, path, body) for method, path, body, _ in server.calls
            if method in {"PUT", "POST"}]


@pytest.fixture
def course_store(connection, tmp_path):
    _, _, login = connection
    store = CourseStore(login.identity.account_id, tmp_path / "course-store")
    try:
        yield store
    finally:
        store.close()


def test_unknown_local_delete_uses_only_atomic_tombstone(connection, course_store):
    server, transport, _ = connection
    _deleted_plan(course_store, "plan323-unknown")

    result = CourseSync(course_store, transport).sync()

    writes = _write_calls(server)
    assert len(writes) == 1
    method, path, body = writes[0]
    assert method == "POST" and path.endswith("/tombstone")
    assert set(body) == {"request_id", "expected_revision"}
    assert body["expected_revision"] == 0
    remote = next(iter(server.rows.values()))
    assert remote["deleted"] is True and remote["revision"] == 1
    assert result["ok"] is True
    assert course_store.pending_records(10) == []


@pytest.mark.parametrize('incremental', [False, True])
def test_old_id_only_live_record_recovers_with_exact_local_delete_and_small_upload_limit(
        connection, course_store, incremental):
    server, transport, _ = connection
    server.incremental = incremental
    first_id, deleted_id = "plan323-first", "plan323-half-published"
    course_store.create_plan(first_id, goal="unrelated pending record")
    pending_delete = _deleted_plan(course_store, deleted_id)
    server.put_remote(replace(pending_delete, payload={"id": deleted_id}, deleted=False), revision=1)
    remote_wire_id = next(iter(server.rows))

    # The matching deletion is not in pending_records(limit=1); identity lookup
    # must still find it without treating another record in this plan as a match.
    first = CourseSync(course_store, transport).sync(limit=1)
    assert first["ok"] is False
    assert course_store.pending_record("plans", deleted_id).deleted
    assert server.rows[remote_wire_id]["deleted"] is False
    assert course_store.get_plan(first_id)["goal"] == "unrelated pending record"
    assert not any(method == "PUT" and path.rstrip("/").endswith(remote_wire_id)
                   for method, path, _ in _write_calls(server))
    cursor = json.loads(course_store.sync_cursor())
    assert cursor["records"][remote_wire_id]["unapplied"] is True

    second = CourseSync(course_store, transport).sync(limit=1)
    assert second["ok"] is True
    assert server.rows[remote_wire_id]["deleted"] is True
    assert server.rows[remote_wire_id]["revision"] == 2
    assert course_store.pending_records(10) == []
    assert not any(path.endswith("/tombstone") for _, path, _ in _write_calls(server))
    assert any(path.endswith("/delete") for _, path, _ in _write_calls(server))
    with pytest.raises(StoreError):
        course_store.get_plan(deleted_id)


@pytest.mark.parametrize("mutation", ["extra", "wrong_id", "wrong_schema", "wrong_plan", "wrong_hash"])
def test_pending_delete_only_exempts_exact_id_only_live_identity(connection, course_store, mutation):
    server, transport, _ = connection
    plan_id = "plan323-strict-identity"
    pending_delete = _deleted_plan(course_store, plan_id)
    bad = replace(pending_delete, deleted=False)
    if mutation == "extra":
        bad = replace(bad, payload={"id": plan_id, "unexpected": True})
    elif mutation == "wrong_id":
        bad = replace(bad, payload={"id": plan_id + "-other"})
    elif mutation == "wrong_schema":
        bad = replace(bad, schema="mentorflow.course.tasks.v1")
    elif mutation == "wrong_plan":
        bad = replace(bad, plan_key="plan323-other")
    server.put_remote(bad, revision=1)

    if mutation == "wrong_hash":
        original = next(iter(server.rows))
        row = server.rows.pop(original)
        server.sequences.pop(original)
        wrong_wire_id = PREFIX + "f" * 64
        server.rows[wrong_wire_id] = dict(row, entity_id=wrong_wire_id)
        server.sequences[wrong_wire_id] = 1

    with pytest.raises((PlanError, StoreError, KeyError)):
        CourseSync(course_store, transport).sync()

    remaining = course_store.pending_record("plans", plan_id)
    assert remaining is not None and remaining.deleted
    raw_cursor = course_store.sync_cursor()
    if raw_cursor:
        assert json.loads(raw_cursor).get("since", "0") == "0"
    with pytest.raises(StoreError):
        course_store.get_plan(plan_id)


def test_same_plan_different_wire_identity_is_not_treated_as_deleted(connection, course_store):
    server, transport, _ = connection
    plan_id = "plan323-other-wire"
    pending_delete = _deleted_plan(course_store, plan_id)
    module_id = "module323-other"
    module_wire_id = f"{len(plan_id)}:{plan_id}{module_id}"
    other = harness.Record(
        entity="modules", entity_id=module_wire_id, plan_key=plan_id,
        schema="mentorflow.course.modules.v1", payload={"id": module_id},
        deleted=False, local_revision=1,
    )
    server.put_remote(other, revision=1)

    with pytest.raises((PlanError, StoreError, KeyError)):
        CourseSync(course_store, transport).sync()

    assert course_store.pending_record("plans", plan_id) == pending_delete
    assert json.loads(course_store.sync_cursor()).get("since", "0") == "0"
    assert not any(path.endswith("/tombstone") for _, path, _ in _write_calls(server))


def test_id_only_live_without_local_delete_intent_is_rejected(connection, course_store):
    server, transport, _ = connection
    plan_id = "plan323-no-delete-intent"
    bad = harness.Record(
        entity="plans", entity_id=plan_id, plan_key=plan_id,
        schema="mentorflow.course.plans.v1", payload={"id": plan_id},
        deleted=False, local_revision=1,
    )
    server.put_remote(bad, revision=1)

    with pytest.raises((PlanError, StoreError, KeyError)):
        CourseSync(course_store, transport).sync()

    assert course_store.pending_records(10) == []
    assert json.loads(course_store.sync_cursor()).get("since", "0") == "0"
    with pytest.raises(StoreError):
        course_store.get_plan(plan_id)


@pytest.mark.parametrize("status", [404, 405])
def test_unsupported_tombstone_backend_preserves_local_queue(connection, course_store, status):
    server, transport, _ = connection
    plan_id = "plan323-old-backend"
    _deleted_plan(course_store, plan_id)

    def old_backend(request):
        if request.url.path.endswith("/tombstone"):
            return httpx.Response(status)
        return None

    server.before = old_backend
    with pytest.raises(PlanError) as error:
        CourseSync(course_store, transport).sync()

    assert error.value.code == "SYNC_TOMBSTONE_UNSUPPORTED"
    assert course_store.pending_record("plans", plan_id).deleted
    assert len(_write_calls(server)) == 1
    assert _write_calls(server)[0][1].endswith("/tombstone")


def test_transport_without_tombstone_capability_does_not_fallback_or_ack(connection, course_store):
    server, transport, _ = connection
    plan_id = "plan323-no-capability"
    _deleted_plan(course_store, plan_id)

    class LegacyTransport:
        """Old transport contract with no atomic tombstone method."""
        def __init__(self, inner):
            self.inner = inner
            self.write_calls = []
            self.delete_calls = []

        def require_current(self):
            return self.inner.require_current()

        def read_changes(self, **kwargs):
            return self.inner.read_changes(**kwargs)

        def read_snapshot(self):
            return self.inner.read_snapshot()

        def write(self, *args, **kwargs):
            self.write_calls.append((args, kwargs))
            raise AssertionError("missing tombstone support must not fall back to live PUT")

        def delete(self, *args, **kwargs):
            self.delete_calls.append((args, kwargs))
            raise AssertionError("missing tombstone support must not synthesize create/delete")

    legacy = LegacyTransport(transport)
    with pytest.raises(PlanError) as error:
        CourseSync(course_store, legacy).sync()

    assert error.value.code == "SYNC_TOMBSTONE_UNSUPPORTED"
    assert legacy.write_calls == legacy.delete_calls == []
    assert course_store.pending_record("plans", plan_id).deleted
    assert _write_calls(server) == []


def test_tombstone_conflict_does_not_acknowledge_local_delete(connection, course_store):
    server, transport, _ = connection
    plan_id = "plan323-conflict"
    _deleted_plan(course_store, plan_id)

    def conflict(request):
        if request.url.path.endswith("/tombstone"):
            return httpx.Response(409, json={"code": "SYNC_STALE_REVISION"})
        return None

    server.before = conflict
    with pytest.raises(PlanError) as error:
        CourseSync(course_store, transport).sync()

    assert error.value.code == "SYNC_CONFLICT"
    assert course_store.pending_record("plans", plan_id).deleted
    assert len(_write_calls(server)) >= 1
    assert all(path.endswith("/tombstone") for _, path, _ in _write_calls(server))


def test_cancel_after_remote_tombstone_receipt_does_not_ack_local_delete(connection, course_store):
    server, transport, _ = connection
    plan_id = "plan323-cancel"
    _deleted_plan(course_store, plan_id)
    cancel = Event()
    server.after = lambda request: cancel.set() if request.url.path.endswith("/tombstone") else None

    with pytest.raises(PlanError) as error:
        CourseSync(course_store, transport).sync(cancel=cancel)

    assert error.value.code == "CANCELLED"
    assert course_store.pending_record("plans", plan_id).deleted
    remote = next(iter(server.rows.values()))
    assert remote["deleted"] is True
    cancel.clear()
    assert CourseSync(course_store, transport).sync()['ok']
    assert course_store.pending_record('plans', plan_id) is None
    assert len(_write_calls(server)) == 1
