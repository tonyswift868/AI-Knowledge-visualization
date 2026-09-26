from dataclasses import replace
import hashlib
import json
from pathlib import Path
import socket
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from refactor.learning.course import materials
from refactor.learning.course.extract import ExtractError, extract_bytes, extract_html
from refactor.learning.course.materials import (import_file, import_verified_source,
    open_original, relink_original, stored_blocks, MISSING_ORIGINAL)
from refactor.learning.course.research_pipeline import VerifiedSource
from refactor.learning.course.store import CourseStore, MAX_PAYLOAD
from tests.refactor.extract.test_extract import pdf_bytes, word_bytes
from tests.refactor.extract.test_more_formats import pptx_bytes, epub_bytes, xlsx_bytes
from tests.refactor.ocr.test_ocr import scanned_pdf


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('资料入库不得联网')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    monkeypatch.setattr(socket, 'getaddrinfo', forbidden)


@pytest.fixture
def store(tmp_path):
    with CourseStore('synthetic-account', tmp_path / 'device-one') as store:
        store.create_plan('plan')
        yield store


def write(tmp_path, raw, name='资料.md'):
    file = tmp_path / name; file.write_bytes(raw)
    return file


def chunks(store, source, plan='plan'):
    return [c for c in store.records('source_chunks', plan) if c['source_id'] == source['id']]


def verified(url='https://example.test/lesson', raw='<h1>模型基础</h1><p>这是已经验证的资料正文。</p>'):
    doc = extract_html(raw, url)
    return VerifiedSource(url, url, doc.title, 'example.test', 'whitelist', 'academic',
                          ('listed',), doc, '已经验证', '2026-09-27T00:00:00+00:00', doc.sha256)


@pytest.mark.parametrize('filename,factory', [
    ('text.txt', lambda: '中文资料正文。'.encode('gb18030')),
    ('notes.md', lambda: '# 标题\n\n正文\n\n```python\n  x = 1\n```'.encode()),
    ('page.html', lambda: '<h1>网页标题</h1><p>正文</p>'.encode()),
    ('data.csv', lambda: '名称,值\n甲,1'.encode()),
    ('data.json', lambda: '{"标题":"中文内容"}'.encode()),
    ('paper.pdf', lambda: pdf_bytes(['第一章中文正文，讲解模型以及使用方法。' * 3, '第二页文字与学习方法。' * 4])),
    ('word.docx', word_bytes), ('slides.pptx', pptx_bytes),
    ('book.epub', epub_bytes), ('table.xlsx', xlsx_bytes),
])
def test_import_formats_original_and_every_block(store, tmp_path, filename, factory):
    raw = factory(); doc = extract_bytes(raw, filename)
    source = import_file(store, 'plan', write(tmp_path, raw, filename))
    assert source['kind'] == 'file' and source['adopted'] is True
    assert source['sha256'] == hashlib.sha256(raw).hexdigest()
    assert source['filename'] == filename and source['size_bytes'] == len(raw)
    assert source['title'] == doc.title
    assert open_original(store, source).read_bytes() == raw
    persisted = chunks(store, source)
    assert stored_blocks(persisted) == doc.blocks
    assert [c['extract_chunk_id'] for c in persisted] == [c.chunk_id for c in doc.chunks]
    assert [c['text'] for c in persisted] == [c.text for c in doc.chunks]
    assert all(c['source_id'] == source['id'] for c in persisted)
    assert str(tmp_path) not in json.dumps([r.payload for r in store.pending_records(999)], ensure_ascii=False)


def test_scan_disabled_then_real_ocr_enabled(store, tmp_path):
    path = write(tmp_path, scanned_pdf(), '扫描.pdf')
    with pytest.raises(ExtractError) as caught:
        import_file(store, 'plan', path)
    assert caught.value.code == 'SCANNED_NO_TEXT' and '可开启文字识别' in str(caught.value)
    assert store.records('sources', 'plan') == []
    progress = []
    source = import_file(store, 'plan', path, ocr=True, progress=lambda *p: progress.append(p))
    assert progress == [(1, 1)] and source['warnings']
    assert '人工智能' in ''.join(b.text for b in stored_blocks(chunks(store, source)))
    assert {b.page for b in stored_blocks(chunks(store, source))} == {1}


def test_repeat_deduplicates_without_resetting_adoption_or_reextracting(store, tmp_path, monkeypatch):
    path = write(tmp_path, b'# lesson\n\nbody')
    source = import_file(store, 'plan', path)
    store.set_source_adopted('plan', source['id'], False)
    before = store.pending_records(999)
    monkeypatch.setattr(materials, 'extract_bytes', lambda *a, **k: pytest.fail('duplicate extracted again'))
    repeated = import_file(store, 'plan', path)
    assert repeated['id'] == source['id'] and repeated['adopted'] is False
    assert store.pending_records(999) == before
    assert len(store.records('sources', 'plan')) == 1
    assert list((store.root / 'files').iterdir()) == [store.root / 'files' / source['sha256']]


def test_originals_deduplicate_across_plans_and_accounts_stay_separate(store, tmp_path):
    path = write(tmp_path, b'# lesson\n\nbody')
    a = import_file(store, 'plan', path)
    store.create_plan('second')
    b = import_file(store, 'second', path)
    assert a['sha256'] == b['sha256'] and len(list((store.root / 'files').iterdir())) == 1
    with CourseStore('another-account', tmp_path / 'device-one') as other:
        assert open_original(other, a) == MISSING_ORIGINAL


def test_other_device_has_synced_body_then_relinks_without_outbox_change(store, tmp_path):
    path = write(tmp_path, '# 第1章\n\n中文正文。'.encode())
    source = import_file(store, 'plan', path)
    with CourseStore(store.account_id, tmp_path / 'device-two') as other:
        other.apply_remote([(record, 1) for record in store.pending_records(999)])
        remote = other.records('sources', 'plan')[0]
        assert stored_blocks(chunks(other, remote)) == stored_blocks(chunks(store, source))
        assert open_original(other, remote) == '原文件在另一台电脑上'
        before = other.pending_records(999)
        wrong = write(tmp_path, b'different', 'wrong.md')
        with pytest.raises(ExtractError) as caught:
            relink_original(other, 'plan', remote['id'], wrong)
        assert caught.value.code == 'HASH_MISMATCH' and '不一致' in str(caught.value)
        assert open_original(other, remote) == MISSING_ORIGINAL
        linked = relink_original(other, 'plan', remote['id'], path)
        assert linked == open_original(other, remote) and linked.read_bytes() == path.read_bytes()
        assert other.pending_records(999) == before and other.records('sources', 'plan')[0] == remote
        with pytest.raises(ExtractError):
            relink_original(other, 'other-plan', remote['id'], path)


def test_verified_source_real_contract_and_identical_content_at_distinct_urls(store, tmp_path):
    item = verified()
    source = import_verified_source(store, 'plan', item)
    assert source['kind'] == 'url' and source['adopted'] is True
    assert source['final_url'] == item.final_url and source['tier'] == 'whitelist'
    assert source['reasons'] == ['listed'] and source['retrieved_at'] == item.retrieved_at
    assert stored_blocks(chunks(store, source)) == item.document.blocks
    assert not (store.root / 'files').exists()
    before = store.pending_records(999)
    assert import_verified_source(store, 'plan', item) == source
    assert store.pending_records(999) == before
    second = import_verified_source(store, 'plan', replace(item, url='https://example.test/two', final_url='https://example.test/two'))
    local = import_file(store, 'plan', write(tmp_path, '<h1>模型基础</h1><p>这是已经验证的资料正文。</p>'.encode(), 'same.html'))
    assert len({source['id'], second['id'], local['id']}) == 3
    all_chunks = store.records('source_chunks', 'plan')
    assert len({c['id'] for c in all_chunks}) == len(all_chunks)
    with pytest.raises(ExtractError):
        import_verified_source(store, 'plan', replace(item, content_sha256='a' * 64))


def test_big_paragraph_blocks_roundtrip_below_sync_payload_limit(store, tmp_path):
    raw = ('# 长文\n\n' + '正文位置需要完整保留。' * 15_000).encode()
    source = import_file(store, 'plan', write(tmp_path, raw))
    assert stored_blocks(chunks(store, source)) == extract_bytes(raw, '资料.md').blocks
    assert len(chunks(store, source)) > 50
    assert all(len(json.dumps(r.payload, ensure_ascii=False).encode()) < MAX_PAYLOAD for r in store.pending_records(999))


def test_pre_cancel_and_cancel_inside_db_write_rollback_every_record(store, tmp_path, monkeypatch):
    path = write(tmp_path, ('# lesson\n\n' + 'paragraph. ' * 800).encode())
    event = threading.Event(); event.set()
    before = store.pending_records(999)
    with pytest.raises(ExtractError) as caught:
        import_file(store, 'plan', path, cancel=event)
    assert caught.value.code == 'CANCELLED' and not (store.root / 'files').exists()
    event.clear(); original = store._put
    def put(entity, *args, **kwargs):
        result = original(entity, *args, **kwargs)
        if entity == 'source_chunks': event.set()
        return result
    monkeypatch.setattr(store, '_put', put)
    with pytest.raises(ExtractError) as caught:
        import_file(store, 'plan', path, cancel=event)
    assert caught.value.code == 'CANCELLED'
    assert store.records('sources', 'plan') == store.records('source_chunks', 'plan') == []
    assert store.pending_records(999) == before
    # A complete hashed cache object can remain for retry, never a partial file.
    assert all(p.name == hashlib.sha256(p.read_bytes()).hexdigest() for p in (store.root / 'files').iterdir())


def test_disk_and_database_errors_are_not_success(store, tmp_path, monkeypatch):
    path = write(tmp_path, b'# lesson\n\nbody')
    before = store.pending_records(999)
    def fail(*args, **kwargs): raise OSError('synthetic disk failure')
    monkeypatch.setattr(store, 'put_original', fail)
    with pytest.raises(ExtractError) as caught:
        import_file(store, 'plan', path)
    assert caught.value.code == 'CORRUPT' and store.pending_records(999) == before
    monkeypatch.undo()
    def db_fail(*args, **kwargs): raise sqlite3.OperationalError('database or disk is full')
    monkeypatch.setattr(store, 'add_sources', db_fail)
    with pytest.raises(ExtractError): import_file(store, 'plan', path)
    assert store.records('sources', 'plan') == [] and store.pending_records(999) == before


def test_parallel_import_same_file_creates_one_source(store, tmp_path):
    path = write(tmp_path, b'# lesson\n\nbody')
    with ThreadPoolExecutor(max_workers=2) as pool:
        sources = list(pool.map(lambda _: import_file(store, 'plan', path), range(2)))
    assert sources[0] == sources[1] and len(store.records('sources', 'plan')) == 1


def test_corrupt_original_not_opened(store, tmp_path):
    source = import_file(store, 'plan', write(tmp_path, b'body'))
    (store.root / 'files' / source['sha256']).write_bytes(b'corrupt')
    with pytest.raises(ExtractError) as caught: open_original(store, source)
    assert caught.value.code == 'CORRUPT' and '校验失败' in str(caught.value)
