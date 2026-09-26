"""Account-owned originals and transactional course sources; no network or UI.

Import returns the stored source payload. Source/chunk IDs are deterministic and
source-scoped (extract_chunk_id retains the extractor's ID). block_fragments in
each chunk preserve every TextBlock without putting a whole document into one
256 KiB sync record. Originals are content-addressed local cache: a complete
copy saved before extraction can remain after failure/cancellation for retry;
source/chunk/outbox records are committed together or not at all.
"""
from __future__ import annotations

from dataclasses import asdict
import hashlib
import os
from pathlib import Path
import sqlite3
from typing import TYPE_CHECKING

from ..file_io import assert_no_reparse_ancestors, open_resource_source, PlainFileCopyError
from .extract import ExtractError, TextBlock, MAX_BYTES, _check, extract_bytes
from .store import StoreError

if TYPE_CHECKING:
    from .research_pipeline import VerifiedSource

MISSING_ORIGINAL = '原文件在另一台电脑上'


def _read_file(path, cancel=None):
    """Hash exactly the same bounded bytes that will be stored and extracted."""
    _check(cancel)
    path = Path(path).expanduser().absolute()
    data, digest = bytearray(), hashlib.sha256()
    try:
        with open_resource_source(path) as (_, stream):
            if os.fstat(stream.fileno()).st_size > MAX_BYTES:
                raise ExtractError('TOO_LARGE')
            while part := stream.read(min(64 * 1024, MAX_BYTES - len(data) + 1)):
                _check(cancel)
                data.extend(part); digest.update(part)
                if len(data) > MAX_BYTES:
                    raise ExtractError('TOO_LARGE')
        _check(cancel)
        return bytes(data), digest.hexdigest()
    except ExtractError:
        raise
    except (OSError, ValueError, PlainFileCopyError) as exc:
        raise ExtractError('CORRUPT', '无法读取此原文件，请确认文件存在、可以访问且不是链接。') from exc


def _storage_error(exc):
    code = 'TOO_LARGE' if isinstance(exc, StoreError) and exc.code == 'PAYLOAD_TOO_LARGE' else 'CORRUPT'
    return ExtractError(code, '资料未能完整入库，请检查账号、计划、磁盘空间及正文大小后重试。')


def _source(store, plan_id, source_id):
    store.get_plan(plan_id)
    for source in store.records('sources', plan_id):
        if source['id'] == source_id:
            return source
    raise StoreError('NOT_FOUND', '找不到当前计划中的资料。')


def _find(store, plan_id, *, kind, sha256, url=None):
    return next((s for s in store.records('sources', plan_id)
                 if s['kind'] == kind and s.get('sha256') == sha256
                 and (kind == 'file' or s.get('final_url', s.get('url')) == url)), None)


def _document_chunks(document, source_id, cancel=None):
    spans, position = [], 0
    for index, block in enumerate(document.blocks):
        _check(cancel)
        spans.append((position, position + len(block.text), index, block))
        position += len(block.text) + 2
    cursor = 0
    for number, chunk in enumerate(document.chunks):
        _check(cancel)
        # Include whitespace gaps up to the next chunk, so blocks round-trip
        # exactly even when extract intentionally leaves separators unchunked.
        left = chunk.char_start if number else 0
        right = document.chunks[number + 1].char_start if number + 1 < len(document.chunks) else position - 2
        fragments = []
        while cursor < len(spans) and spans[cursor][1] <= left:
            cursor += 1
        scan = cursor
        while scan < len(spans) and spans[scan][0] < right:
            _check(cancel)
            start, end, index, block = spans[scan]
            a, b = max(start, left), min(end, right)
            if a < b:
                fragments.append(dict(index=index, offset=a-start, length=end-start,
                    text=block.text[a-start:b-start], kind=block.kind, page=block.page, section=list(block.section)))
            scan += 1
        payload = asdict(chunk)
        payload['extract_chunk_id'] = payload['chunk_id']
        payload['chunk_id'] = f'{source_id}:{chunk.chunk_id}'
        payload['block_fragments'] = fragments
        yield payload


def stored_blocks(chunks):
    """Reassemble imported blocks; retain compatibility with earlier chunk-only sources."""
    chunks = sorted(chunks, key=lambda c: (c['position'], c['id']))
    if not any('block_fragments' in c for c in chunks):
        return tuple(TextBlock(c['text'], 'paragraph', c.get('page_start'), tuple(c.get('section', ()))) for c in chunks)
    groups = {}
    for chunk in chunks:
        for part in chunk['block_fragments']:
            groups.setdefault(part['index'], []).append(part)
    blocks = []
    if sorted(groups) != list(range(len(groups))):
        raise StoreError('INCOMPLETE_SOURCE', '资料正文块不完整，请等待同步完成后重试。')
    for index in sorted(groups):
        parts = sorted(groups[index], key=lambda p: p['offset'])
        first, end, text = parts[0], 0, []
        for part in parts:
            if (part['offset'] != end or any(part[k] != first[k] for k in ('length', 'kind', 'page', 'section'))):
                raise StoreError('INCOMPLETE_SOURCE', '资料正文块不完整，请等待同步完成后重试。')
            text.append(part['text']); end += len(part['text'])
        if end != first['length']:
            raise StoreError('INCOMPLETE_SOURCE', '资料正文块不完整，请等待同步完成后重试。')
        blocks.append(TextBlock(''.join(text), first['kind'], first['page'], tuple(first['section'])))
    return tuple(blocks)


def _save(store, plan_id, payload, document, cancel=None):
    with store.transaction():
        _check(cancel)
        existing = _find(store, plan_id, kind=payload['kind'], sha256=payload['sha256'], url=payload.get('final_url'))
        if existing is not None:
            return existing
        payload = dict(payload, media_type=document.media_type, char_count=document.char_count,
                       block_count=len(document.blocks), chunk_count=len(document.chunks),
                       extraction_schema=1, warnings=list(document.warnings), adopted=True)
        store.add_sources(plan_id, [dict(payload, chunks=_document_chunks(document, payload['id'], cancel))])
        _check(cancel)
        return _source(store, plan_id, payload['id'])


def import_file(store, plan_id, path, *, ocr=False, progress=None, cancel=None):
    """progress(page, total) is forwarded to PDF extraction; ocr is a boolean."""
    _check(cancel)
    if type(ocr) is not bool:
        raise ValueError('ocr 必须是布尔值')
    try:
        store.get_plan(plan_id)
        data, digest = _read_file(path, cancel)
        assert_no_reparse_ancestors(store.root, Path(store.root.anchor))
        with store.transaction():
            store.get_plan(plan_id)
            store.put_original(data, digest)
            _check(cancel)
            existing = _find(store, plan_id, kind='file', sha256=digest)
            if existing is not None:
                return existing
        recognizer = None
        if ocr:
            from .ocr import PdfOCR
            recognizer = PdfOCR()
        document = extract_bytes(data, Path(path).name, ocr=recognizer, progress=progress, cancel=cancel)
        payload = dict(id=f'file-{digest}', kind='file', filename=Path(path).name,
                       size_bytes=len(data), sha256=digest, title=document.title)
        return _save(store, plan_id, payload, document, cancel)
    except ExtractError as exc:
        if exc.code == 'SCANNED_NO_TEXT':
            raise ExtractError(exc.code, '资料可能是扫描件，可开启文字识别（离线 OCR）后重新导入。') from exc
        raise
    except (OSError, sqlite3.Error, StoreError, PlainFileCopyError) as exc:
        raise _storage_error(exc) from exc


def open_original(store, source):
    """Return a local Path, or the exact missing-original notice. Does not launch apps."""
    if source.get('kind') != 'file':
        raise ExtractError('UNSUPPORTED_TYPE', '网页资料请通过记录中的原链接查看。')
    try:
        assert_no_reparse_ancestors(store.root, Path(store.root.anchor))
        data = store.get_original(source.get('sha256'))
        if data is None:
            return MISSING_ORIGINAL
        if source.get('size_bytes') is not None and len(data) != source['size_bytes']:
            raise StoreError('CORRUPT_ORIGINAL')
        return store.root / 'files' / source['sha256']
    except (OSError, sqlite3.Error, StoreError, PlainFileCopyError) as exc:
        raise ExtractError('CORRUPT', '本机原件校验失败，未打开文件；请重新关联正确的原文件。') from exc


def relink_original(store, plan_id, source_id, path):
    """Only matching bytes are copied; source metadata/adoption/outbox stay untouched."""
    try:
        with store.transaction():
            source = _source(store, plan_id, source_id)
            if source['kind'] != 'file':
                raise ExtractError('UNSUPPORTED_TYPE', '网页资料不需要关联本机原件。')
        data, digest = _read_file(path)
        if digest != source['sha256']:
            raise ExtractError('HASH_MISMATCH', '所选文件与资料记录的 SHA256 不一致，未覆盖或关联原件。')
        with store.transaction():
            current = _source(store, plan_id, source_id)
            if current['sha256'] != digest or current['kind'] != 'file':
                raise ExtractError('HASH_MISMATCH', '资料记录已变化，请重新选择资料后关联。')
            assert_no_reparse_ancestors(store.root, Path(store.root.anchor))
            store.put_original(data, digest)
        return open_original(store, current)
    except ExtractError:
        raise
    except (OSError, sqlite3.Error, StoreError, PlainFileCopyError) as exc:
        raise _storage_error(exc) from exc


def import_verified_source(store, plan_id, verified: VerifiedSource):
    """Persist MF-04's already-verified document; never fetch/verify a URL again."""
    document = verified.document
    if verified.content_sha256 != document.sha256 or not document.blocks or not document.chunks:
        raise ExtractError('CORRUPT', '已验证资料的正文或摘要校验信息不一致，未入库。')
    identity = hashlib.sha256((verified.final_url + '\0' + document.sha256).encode('utf-8')).hexdigest()
    payload = dict(id=f'url-{identity}', kind='url', url=verified.url, final_url=verified.final_url,
        sha256=document.sha256, content_sha256=verified.content_sha256, title=verified.title or document.title,
        site=verified.site, tier=verified.tier, category=verified.category, reasons=list(verified.reasons),
        summary=verified.summary, retrieved_at=verified.retrieved_at)
    try:
        return _save(store, plan_id, payload, document)
    except (OSError, sqlite3.Error, StoreError) as exc:
        raise _storage_error(exc) from exc
