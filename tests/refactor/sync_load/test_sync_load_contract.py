"""Small regression checks for the load-path client optimizations."""
from copy import deepcopy
import hashlib

from refactor.learning.course.sync_contract import SyncRecord
from refactor.learning.course.sync_course import CourseSync, DEFAULT_BATCH_SIZE, _wire_id


def account_id():
    hashes = ":".join(hashlib.sha256(value.encode()).hexdigest()
                        for value in ("https://identity.invalid/realm", "load-store-test"))
    return "mfa1_" + hashlib.sha256(("mentorflow.account.v1:" + hashes).encode()).hexdigest()


class Scope:
    def snapshot_scope(self):
        return {"issuer": "https://identity.invalid/realm", "subject_id": "load-test",
                "organization_id": "org", "workspace_id": "workspace"}


class Store:
    def __init__(self, record):
        self.record = record
        self.pending = True
        self.cursor = ""
        self.bulk = []
        self.individual = []

    def pending_records(self, limit):
        assert limit == DEFAULT_BATCH_SIZE
        return [deepcopy(self.record)] if self.pending else []

    def mark_synced(self, *ack):
        self.individual.append(ack)
        raise AssertionError("the bulk CourseStore path should be selected")

    def mark_synced_many(self, acknowledgements):
        self.bulk.append(list(acknowledgements))
        self.pending = False

    def apply_remote(self, records):
        raise AssertionError("the fixture has no remote rows")

    def sync_cursor(self):
        return self.cursor

    def set_sync_cursor(self, cursor):
        self.cursor = cursor


class Transport:
    def __init__(self):
        self.scope = Scope()
        self.pull_limits = []
        self.writes = []

    def require_current(self):
        return self.scope

    def read_changes(self, *, since, limit):
        self.pull_limits.append(limit)
        return {"schema": "mentorflow.structured-sync-changes.v1", "contract_version": 1,
                "scope": self.scope.snapshot_scope(), "kind": "learning_run", "since": since,
                "next_cursor": since, "has_more": False, "entities": []}

    def write(self, kind, entity_id, *, request_id, expected_revision, payload):
        self.writes.append((kind, entity_id, request_id, expected_revision, payload))
        return {"kind": kind, "entity_id": entity_id, "revision": expected_revision + 1,
                "deleted": False, "payload": payload}

    def delete(self, *args, **kwargs):
        raise AssertionError("not a delete fixture")


def test_load_path_uses_server_page_limit_and_one_outbox_ack():
    record = SyncRecord("messages", "message-1", "plan-1", "mentorflow.course.messages.v1",
                        {"id": "message-1", "content": "课堂消息"}, False, 1)
    store, transport = Store(record), Transport()
    result = CourseSync(store, transport).sync()
    assert result == {"uploaded": 1, "pulled": 0, "conflicts": 0, "pending": 0, "ok": True}
    assert transport.pull_limits == [DEFAULT_BATCH_SIZE] == [500]
    assert len(transport.writes) == 1 and len(store.bulk) == 1
    assert store.bulk[0][0] == ("messages", "message-1", 1, 1)


def test_wire_id_remains_unchanged_by_batching():
    values = {"schema": "mentorflow.course.messages.v1", "plan_key": "plan-1",
              "entity": "messages", "entity_id": "message-1", "payload": {"id": "message-1"}}
    assert _wire_id(values).startswith("refactor.v1:course_record:")


def test_course_store_bulk_ack_keeps_a_newer_local_revision_pending(tmp_path):
    from refactor.learning.course.store import CourseStore

    with CourseStore(account_id(), tmp_path) as store:
        store.create_plan("plan-1")
        original = store.pending_records(10)[0]
        store.mark_synced_many([(original.entity, original.entity_id, original.local_revision, 3)])
        assert not store.pending_records(10)

        store.save_intake("plan-1", goal="本机新编辑")
        newer = store.pending_records(10)[0]
        store.mark_synced_many([(original.entity, original.entity_id, original.local_revision, 4)])
        assert store.pending_records(10)[0].local_revision == newer.local_revision
        store.mark_synced_many([(newer.entity, newer.entity_id, newer.local_revision, 5)])
        assert not store.pending_records(10)
