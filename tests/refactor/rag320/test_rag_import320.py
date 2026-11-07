"""Regression coverage for recoverable RAG file import batches."""
from hashlib import sha256
import threading
import time

import httpx
import pytest

from refactor.learning.app.bridge import BridgeError
from refactor.learning.app.rag import RagService
from refactor.learning.course.store import CourseStore
from tests.refactor.rag155.test_rag155_backend import CA, FakeRagServer, FakeGateway, rag155_session


def make_service(tmp_path):
    state = rag155_session("rag320-import")
    store = CourseStore(state.identity.account_id, tmp_path / "data")
    server = FakeRagServer()
    service = RagService(store=store, account_id=state.identity.account_id,
        session_provider=lambda: state, api_base_url="https://account.example.test",
        ca_bundle=CA, gateway=FakeGateway(), http_transport=httpx.MockTransport(server))
    return service, server


def select(service, *paths):
    service._picked_paths.update(str(path.resolve()) for path in paths)


def close_service(service):
    service.close()
    deadline = time.monotonic() + 7
    while not service.local._closed and time.monotonic() < deadline:
        time.sleep(.01)
    assert service.local._closed
    service.store.close()


def test_rag320_mixed_batch_persists_valid_text_and_html_and_reports_blank(tmp_path):
    service, _ = make_service(tmp_path)
    txt = tmp_path / "有效.txt"
    blank = tmp_path / "空白.txt"
    html = tmp_path / "有效.html"
    txt.write_text("第一份真实资料内容。", encoding="utf-8")
    blank.write_text("\ufeff　 \t\r\n", encoding="utf-8")
    html.write_text("<html><head><title>网页资料</title></head><body><h1>章节</h1><p>第二份真实内容。</p></body></html>", encoding="utf-8")
    select(service, txt, blank, html)
    try:
        result = service.import_files([str(txt), str(blank), str(html)])

        assert len(result["documents"]) == 2
        assert [error["filename"] for error in result["errors"]] == [blank.name]
        assert result["errors"][0]["code"] == "RAG_EXTRACT_EMPTY"
        assert result["errors"][0]["message"]
        for path in (txt, html):
            document_id = sha256(path.read_bytes()).hexdigest()
            assert service.local.document(document_id) is not None
        html_doc = service.local.document(sha256(html.read_bytes()).hexdigest())
        assert "第二份真实内容" in "\n".join(chunk["text"] for chunk in html_doc["chunks"])
    finally:
        close_service(service)


def test_rag320_all_valid_documents_field_and_retry_do_not_duplicate_local_rows(tmp_path):
    service, _ = make_service(tmp_path)
    path = tmp_path / "valid.txt"
    path.write_text("可重复导入的真实内容。", encoding="utf-8")
    document_id = sha256(path.read_bytes()).hexdigest()
    try:
        select(service, path)
        first = service.import_files([str(path)])
        assert "documents" in first and len(first["documents"]) == 1
        assert first["errors"] == []
        select(service, path)
        retry = service.import_files([str(path)])
        assert retry["documents"][0]["id"] == document_id
        assert len(service.local.documents()) == 1
    finally:
        close_service(service)


def test_rag320_first_failed_file_does_not_block_later_valid_file(tmp_path):
    service, _ = make_service(tmp_path)
    missing = tmp_path / "missing.txt"
    valid = tmp_path / "later.txt"
    valid.write_text("后续文件仍能入库。", encoding="utf-8")
    select(service, valid)
    try:
        result = service.import_files([str(missing), str(valid)])
        assert [error["filename"] for error in result["errors"]] == [missing.name]
        assert result["errors"][0]["code"] == "FILE_UNAVAILABLE"
        assert len(result["documents"]) == 1
        assert service.local.document(sha256(valid.read_bytes()).hexdigest()) is not None
    finally:
        close_service(service)


def test_rag320_first_blank_file_does_not_block_later_valid_file(tmp_path):
    service, _ = make_service(tmp_path)
    blank = tmp_path / "first-blank.txt"
    valid = tmp_path / "later-valid.txt"
    blank.write_text("\ufeff　 \t\r\n", encoding="utf-8")
    valid.write_text("空白失败后的有效文件。", encoding="utf-8")
    select(service, blank, valid)
    try:
        result = service.import_files([str(blank), str(valid)])
        assert [error["filename"] for error in result["errors"]] == [blank.name]
        assert result["errors"][0]["code"] == "RAG_EXTRACT_EMPTY"
        assert len(result["documents"]) == 1
        assert service.local.document(sha256(valid.read_bytes()).hexdigest()) is not None
    finally:
        close_service(service)


@pytest.mark.parametrize("cancel_after_first", [False, True], ids=["before-first", "between-files"])
def test_rag320_cancel_propagates_and_never_imports_later_file(tmp_path, cancel_after_first):
    service, _ = make_service(tmp_path)
    first, later = tmp_path / "first.txt", tmp_path / "later.txt"
    first.write_text("第一份内容。", encoding="utf-8")
    later.write_text("不应处理的后续内容。", encoding="utf-8")
    cancel = threading.Event()
    select(service, first, later)
    if not cancel_after_first:
        cancel.set()
    try:
        with pytest.raises(BridgeError) as caught:
            service.import_files([str(first), str(later)], cancel=cancel,
                on_progress=(lambda _row: cancel.set()) if cancel_after_first else None)
        assert caught.value.code == "CANCELLED"
        assert service.local.document(sha256(later.read_bytes()).hexdigest()) is None
        assert (service.local.document(sha256(first.read_bytes()).hexdigest()) is not None) == cancel_after_first
    finally:
        close_service(service)


def test_rag320_unsafe_path_and_pending_deletion_remain_global_errors(tmp_path):
    service, _ = make_service(tmp_path)
    unsafe = tmp_path / "not-picked.txt"
    unsafe.write_text("未经选择的文件。", encoding="utf-8")
    pending = tmp_path / "pending.txt"
    pending.write_text("正在删除的文件。", encoding="utf-8")
    pending_id = sha256(pending.read_bytes()).hexdigest()
    service._pending_deletion_ids.add(pending_id)
    select(service, pending)
    try:
        with pytest.raises(BridgeError) as unsafe_error:
            service.import_files([str(unsafe)])
        assert unsafe_error.value.code == "FILE_NOT_SELECTED"
        select(service, pending)
        with pytest.raises(BridgeError) as pending_error:
            service.import_files([str(pending)])
        assert pending_error.value.code == "RAG_DELETE_PENDING"
        assert service.local.document(pending_id) is None
    finally:
        close_service(service)


def test_rag320_unauthorized_later_path_preflights_before_any_save(tmp_path):
    service, _ = make_service(tmp_path)
    selected = tmp_path / "selected-first.txt"
    unauthorized = tmp_path / "not-selected-second.txt"
    selected.write_text("即使排在前面也不能提前落盘。", encoding="utf-8")
    unauthorized.write_text("未授权路径。", encoding="utf-8")
    select(service, selected)
    try:
        with pytest.raises(BridgeError) as caught:
            service.import_files([str(selected), str(unauthorized)])
        assert caught.value.code == "FILE_NOT_SELECTED"
        assert service.local.documents() == []
    finally:
        close_service(service)


def test_rag320_extractor_cancellation_propagates_as_global_error(tmp_path, monkeypatch):
    import refactor.learning.app.rag as rag_module

    service, _ = make_service(tmp_path)
    path = tmp_path / "cancel-during-extract.txt"
    path.write_text("提取器开始前设置取消标记。", encoding="utf-8")
    select(service, path)
    cancel = threading.Event()
    original_extract_file = rag_module.extract_file

    def cancel_then_extract(*args, **kwargs):
        cancel.set()
        return original_extract_file(*args, **kwargs)

    monkeypatch.setattr(rag_module, "extract_file", cancel_then_extract)
    try:
        with pytest.raises(BridgeError) as caught:
            service.import_files([str(path)], cancel=cancel)
        assert caught.value.code == "CANCELLED"
        assert service.local.document(sha256(path.read_bytes()).hexdigest()) is None
    finally:
        close_service(service)
