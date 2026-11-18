"""456: invisible NUL characters are removed at shared text and wire boundaries."""

import json
from types import SimpleNamespace
import pytest

from refactor.learning.course import extract
from refactor.learning.course.extract import ExtractError, TextBlock
from refactor.learning.course.sync_course import CourseSync, _json
from refactor.learning.course.sync_course import _values
from refactor.learning.course.sync_contract import SyncRecord
from refactor.learning.app import rag


def test_document_removes_nul_from_title_blocks_and_chunks():
    document = extract._document(
        b"source",
        "教材\x00标题",
        "text/plain",
        [TextBlock("第一段\x00正文", "paragraph", None, ("章\x00一",))],
        [],
        None,
    )

    assert document.title == "教材标题"
    assert all("\x00" not in block.text + "".join(block.section) for block in document.blocks)
    assert all("\x00" not in chunk.text + "".join(chunk.section) for chunk in document.chunks)
    assert document.char_count == len("\n\n".join(block.text for block in document.blocks))


def test_plain_text_nul_remains_corrupt_file():
    with pytest.raises(ExtractError) as caught:
        extract.extract_bytes("正文\x00仍在".encode("utf-8"), "notes.txt")
    assert caught.value.code == "CORRUPT"


def test_wire_json_removes_nested_nul_without_touching_other_fields():
    payload = {"text": "坏\x00字", "nested": {"title": "好\x00标题"}, "count": 3}
    encoded = _json(payload)
    decoded = json.loads(encoded)

    assert "\x00" not in encoded
    assert decoded == {"text": "坏字", "nested": {"title": "好标题"}, "count": 3}


def test_course_sync_values_clean_payload_but_keep_record_identity():
    record = SyncRecord(
        "source_chunks", "chunk-1", "plan-1", "mentorflow.course.source_chunks.v1",
        {"id": "chunk-1", "text": "正文\x00内容", "section": "章\x00一"}, False, 4,
    )

    values = _values(record)

    assert values["entity_id"] == "chunk-1"
    assert values["plan_key"] == "plan-1"
    assert values["payload"] == {"id": "chunk-1", "text": "正文内容", "section": "章一"}


def test_course_sync_request_cleans_old_payload_and_acknowledges_once():
    record = SyncRecord(
        "messages", "message-1", "plan-1", "mentorflow.course.messages.v1",
        {"id": "message-1", "content": "坏\x00字", "kind": "note", "count": 2}, False, 7,
    )
    scope = SimpleNamespace(snapshot_scope=lambda: {"account_id": "account", "workspace_id": "workspace"})
    sent = []

    class Transport:
        def require_current(self):
            return scope

        def write(self, kind, entity_id, *, payload, **kwargs):
            sent.append(payload)
            return {"kind": kind, "entity_id": entity_id, "revision": 1,
                    "deleted": False, "payload": payload}

    class Store:
        def __init__(self):
            self.cursors = []
            self.acks = []

        def set_sync_cursor(self, cursor):
            self.cursors.append(cursor)

        def mark_synced(self, *ack):
            self.acks.append(ack)

    store = Store()
    sync = object.__new__(CourseSync)
    sync.store, sync.transport, sync._scope = store, Transport(), scope
    sync._closed = SimpleNamespace(is_set=lambda: False)
    sync._cancel = SimpleNamespace(is_set=lambda: False)

    sync._send(record, {"records": {}}, None, deleting=False)

    assert len(sent) == len(store.acks) == 1
    encoded = json.dumps(sent[0], ensure_ascii=False)
    assert "\x00" not in encoded and "\\u0000" not in encoded
    assert sent[0]["values"]["payload"] == {
        "id": "message-1", "content": "坏字", "kind": "note", "count": 2,
    }


class _RagLocal:
    def __init__(self, row):
        self.row = dict(row)
        self.remote_state = None
        self.uploaded = None

    def set_remote_state(self, document_id, canonical_id, status, uploaded, total):
        self.remote_state = (document_id, canonical_id, status, uploaded, total)
        self.row.update(canonical_id=canonical_id, status=status, uploaded_chunks=uploaded,
                        canonical_total_chunks=total)

    def document(self, document_id):
        return dict(self.row)

    def set_uploaded_content(self, document_id, digest):
        self.uploaded = digest
        self.row["uploaded_content_sha256"] = digest


def test_existing_rag_nul_is_cleaned_before_upload_and_does_not_repeat():
    row = {
        "id": "doc-1", "title": "标题\x00", "source": "source\x00",
        "status": "not_uploaded", "uploaded_chunks": 0,
        "current_content_sha256": "old-dirty-digest", "uploaded_content_sha256": "",
        "chunks": [{"seq": 0, "text": "正文\x00内容", "page": 1, "section": "章\x00一"}],
    }
    local = _RagLocal(row)
    service = object.__new__(rag.RagService)
    service.local = local
    service._remote_capabilities = set()
    service._worker_local = SimpleNamespace(batch_index=0)
    sent = []

    def send(path, payload, *, cancel, on_receipt):
        sent.append(payload)
        on_receipt({"document_id": "doc-1", "requested_document_id": "doc-1",
                    "status": "ready", "received_chunks": 1, "total_chunks": 1})

    service._send_upload_batch = send
    result = service._send_batches(row)

    wire = json.dumps(sent, ensure_ascii=False)
    assert "\x00" not in wire and "\\u0000" not in wire
    assert result["status"] == "ready"
    assert local.uploaded == rag._source_content_digest(local.row["chunks"])
    assert rag._upload_is_current(local.row)
