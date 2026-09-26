"""Single-course offline ZIP export from a consistent CourseStore snapshot.

No account/old-page wiring lives here. A valid ZIP with missing originals is
published as MISSING_ORIGINALS (succeeded remains False); missing files are
listed in the manifest and result. include_originals=False is an explicit
text/data-only export. Cancelled/failed exports never replace a destination.
progress(completed_members, total_members) runs before publication.
"""
from __future__ import annotations

from contextlib import nullcontext
from dataclasses import asdict
from datetime import datetime
import hashlib
import html
import json
import os
from pathlib import Path
import re
from urllib.parse import quote
from uuid import uuid4
import zipfile

from ..file_io import (_native_path, assert_no_reparse_ancestors, canonical_io_path,
                       open_resource_source, PlainFileCopyError)
from ..plan_export import ExportResult, ExportStatus, ExportCancellation
from .extract import ExtractError, _check
from .materials import open_original, stored_blocks, MISSING_ORIGINAL
from .store import StoreError


class _ExportError(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False).encode('utf-8')


def _snapshot(store, plan_id, cancel, today):
    entities = ('sources', 'source_chunks', 'stages', 'modules', 'points', 'teacher_profile', 'messages',
                'judgements', 'attempt_closed', 'credits', 'ledger_events', 'reports')
    with store.transaction():
        _check(cancel)
        plan = store.get_plan(plan_id)
        records = {}
        for entity in entities:
            _check(cancel)
            records[entity] = store.records(entity, plan_id)
        if plan['module_count'] != len(records['modules']):
            raise _ExportError('PLAN_INCOMPLETE', '计划模块尚未完整恢复，请等待同步完成后导出。')
        for module in records['modules']:
            if module['point_count'] != sum(p['module_id'] == module['id'] for p in records['points']):
                raise _ExportError('PLAN_INCOMPLETE', '计划知识点尚未完整恢复，请等待同步完成后导出。')
        book = store.load_mastery(plan_id)
        module_ids = {p['module_id'] for p in records['points']}
        mastery = dict(points=[asdict(book.point(p['id'])) for p in records['points']],
            modules=[asdict(book.module(m['id'])) for m in records['modules'] if m['id'] in module_ids],
            stats=book.stats(), check_ready=book.check_ready(), check_complete=book.check_complete())
        ledger = store.load_ledger(plan_id) if plan['initial_window'] is not None else None
        progress = {name: records[name] for name in ('judgements', 'attempt_closed', 'credits', 'ledger_events')}
        progress.update(as_of_date=today.isoformat(), mastery=mastery,
            ledger=ledger.to_dict() if ledger is not None else None,
            ledger_status=asdict(ledger.status(today)) if ledger is not None else None)
        _check(cancel)
        return plan, records, progress


def _source_text(source, chunks, cancel):
    if 'chunk_count' in source and len(chunks) != source['chunk_count']:
        raise _ExportError('SOURCE_INCOMPLETE', '资料正文尚未完整恢复，请等待同步完成后导出。')
    blocks = stored_blocks(chunks)
    if ('block_count' in source and len(blocks) != source['block_count'] or
            'char_count' in source and sum(len(b.text) for b in blocks) + 2 * (len(blocks) - 1) != source['char_count']):
        raise _ExportError('SOURCE_INCOMPLETE', '资料正文块尚未完整恢复，请等待同步完成后导出。')
    if not blocks:
        raise _ExportError('SOURCE_INCOMPLETE', '资料没有正文，请补齐后导出。')
    lines = [source.get('title') or source.get('filename') or '资料']
    for block in blocks:
        _check(cancel)
        context = ' / '.join(block.section)
        location = (f'第 {block.page} 页' if block.page is not None else '')
        lines.extend(['', f'[{" · ".join(v for v in (context, location, block.kind) if v)}]', block.text])
    return '\n'.join(lines).encode('utf-8')


def _offline_html(plan, records, progress, materials, source_texts):
    def escape(value):
        # Original links stay in plan.json. This page only navigates local files.
        value = re.sub(r'https?://', lambda m: m[0].replace(':', '：'), str(value), flags=re.I)
        return html.escape(value, quote=True)
    sections = []
    states = {p['point_id']: p for p in progress['mastery']['points']}
    for stage in records['stages']:
        sections.append('<h2>' + escape(stage.get('title', '阶段')) + '</h2>')
        for module in (m for m in records['modules'] if m['stage_id'] == stage['id']):
            sections.append('<h3>' + escape(module.get('title', '模块')) + '</h3><ul>')
            for point in (p for p in records['points'] if p['module_id'] == module['id']):
                state = states[point['id']]
                sections.append('<li>' + escape(point.get('summary', '')) + ' · ' + escape(point['minutes']) +
                    ' 分钟 · ' + escape(state['state']) + ' · 检验等级 ' + escape(state['level']) + '</li>')
            sections.append('</ul>')
    links = []
    for item in materials:
        link = lambda path, name: f'<a href="{quote(path, safe="/")}">{escape(name)}</a>'
        original = link(item['original_path'], '原件') if item.get('original_path') else escape(item['original_status'])
        body = source_texts[item['source_id']].decode('utf-8')
        links.append('<li>' + link(item['text_path'], item['title']) + ' · ' + original +
                     '<details><summary>展开正文（章节与页码）</summary><pre>' + escape(body) + '</pre></details></li>')
    report_link = '<a href="report.json">学习报告</a> · ' if records['reports'] else ''
    return ('''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'">
<title>MentorFlow 计划导出</title><style>body{font:16px/1.6 system-ui,sans-serif;max-width:1000px;margin:30px auto;padding:0 20px;color:#302632}pre{white-space:pre-wrap;overflow-wrap:anywhere}h1,h2{color:#803658}li{margin:8px 0}a{color:#803658}</style></head><body><h1>'''
        + escape(plan.get('goal') or '学习计划') + '</h1><p>解压整个 ZIP 后打开本页即可离线浏览。'
        '这是导出时的记录，不代表服务器已注销账号。</p><p>最终目标：' + escape(plan.get('final_goal', '')) +
        '</p><p>计划状态：' + escape(plan['state']) + '</p>' + ''.join(sections) +
        '<h2>进度与账本</h2><pre>' + escape(json.dumps(progress['ledger_status'], ensure_ascii=False, indent=2)) +
        '</pre><h2>资料正文与本机原件</h2><ul>' + (''.join(links) or '<li>暂无资料</li>') +
        '</ul><p><a href="plan.json">完整计划与出处</a> · <a href="progress.json">判定事件与进度</a> · ' +
        report_link + '<a href="manifest.json">文件校验与缺失清单</a></p></body></html>').encode('utf-8')


def _destination(store, target_path):
    output = canonical_io_path(target_path)
    # Exclude the entire accounts tree, including sibling accounts.
    if output.is_relative_to(store.root.parent):
        raise _ExportError('DESTINATION_IN_ACCOUNT', '保存位置位于账号数据目录内，请另选目录。')
    assert_no_reparse_ancestors(output, Path(output.anchor))
    if output.suffix.lower() != '.zip' or not _native_path(output.parent).is_dir():
        raise _ExportError('DESTINATION_INVALID', '请选择已有目录中的 .zip 文件作为保存位置。')
    if _native_path(output).exists():
        raise _ExportError('DESTINATION_EXISTS', '目标文件已存在，请选择新文件名；原文件未覆盖。')
    return output


def export_plan(store, plan_id, target_path, *, include_originals=True, progress=None, cancel=None) -> ExportResult:
    """Synchronous worker API. Reuses the account export statuses/cancellation gate."""
    def result(status, code, message, **kwargs):
        return ExportResult(status, code, message, plan_id=plan_id or '', account_id=store.account_id, **kwargs)
    temporary, published = None, False
    try:
        _check(cancel)
        if target_path is None:
            return result(ExportStatus.CANCELLED, 'CANCELLED', '已取消选择保存位置。')
        if type(include_originals) is not bool:
            raise _ExportError('INVALID_OPTION', 'include_originals 必须是布尔值。')
        if not plan_id:
            return result(ExportStatus.NO_CURRENT_PLAN, 'NO_CURRENT_PLAN', '暂无当前计划，未生成导出包。')
        output = _destination(store, target_path)
        now = datetime.now().astimezone()
        try:
            plan, records, state = _snapshot(store, plan_id, cancel, now.date())
        except StoreError as exc:
            if exc.code == 'NOT_FOUND':
                return result(ExportStatus.NO_CURRENT_PLAN, 'NO_CURRENT_PLAN', '找不到当前计划，未生成导出包。')
            raise
        sources = records['sources']
        chunks = records['source_chunks']
        materials, missing, originals, source_texts = [], [], {}, {}
        payloads = {}
        for number, source in enumerate(sources, 1):
            _check(cancel)
            text_path = f'sources/{number:04d}.txt'
            text = _source_text(source, [c for c in chunks if c['source_id'] == source['id']], cancel)
            payloads[text_path] = source_texts[source['id']] = text
            item = dict(source_id=source['id'], title=source.get('title') or source.get('filename') or '资料',
                        text_path=text_path, original_path=None, original_status='网页资料，无本机原件')
            if source['kind'] == 'file':
                item.update(filename=source.get('filename', source['sha256']), sha256=source['sha256'])
                if not include_originals:
                    item['original_status'] = '本次按选择不包含原件'
                else:
                    original = open_original(store, source)
                    if original == MISSING_ORIGINAL:
                        item['original_status'] = MISSING_ORIGINAL
                        missing.append(dict(source_id=source['id'], filename=item['filename'], sha256=source['sha256']))
                    else:
                        digest = source['sha256']
                        if digest not in originals:
                            filename = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', item['filename']).strip(' .')[:100] or 'original'
                            member = f'originals/{number:04d}-{filename}'
                            originals[digest] = (original, member, source.get('size_bytes'))
                        item.update(original_path=originals[digest][1], original_status='已包含原件')
            materials.append(item)
        plan_document = dict(schema='mentorflow.course-export.plan.v1', plan=plan, materials=materials,
            **{key: records[key] for key in ('stages', 'modules', 'points', 'sources', 'source_chunks', 'teacher_profile', 'messages')})
        payloads['plan.json'] = _json(plan_document)
        payloads['progress.json'] = _json(dict(schema='mentorflow.course-export.progress.v1', **state))
        if records['reports']:
            payloads['report.json'] = _json(dict(reports=records['reports']))
        payloads['index.html'] = _offline_html(plan, records, state, materials, source_texts)
        _check(cancel)
        temporary = output.with_name(output.name + '.' + uuid4().hex + '.part')
        total, completed, members = len(payloads) + len(originals) + 2, 0, {}

        def written(name, digest, size):
            nonlocal completed
            members[name] = dict(path=name, sha256=digest, size_bytes=size)
            completed += 1
            if progress is not None:
                progress(completed, total)
            _check(cancel)

        with _native_path(temporary).open('xb') as handle:
            with zipfile.ZipFile(handle, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
                for name, raw in payloads.items():
                    _check(cancel)
                    archive.writestr(name, raw)
                    written(name, hashlib.sha256(raw).hexdigest(), len(raw))
                for expected, (path, member, expected_size) in originals.items():
                    digest, size = hashlib.sha256(), 0
                    with open_resource_source(path) as (_, stream), archive.open(member, 'w', force_zip64=True) as target:
                        while part := stream.read(1024 * 1024):
                            _check(cancel)
                            digest.update(part); size += len(part)
                            target.write(part)
                    if digest.hexdigest() != expected or expected_size is not None and size != expected_size:
                        raise _ExportError('ORIGINAL_CHANGED', '原件在导出期间发生变化，未发布导出包。')
                    written(member, digest.hexdigest(), size)
                manifest = dict(schema='mentorflow.course-export.v1', plan_id=plan_id,
                    created_at=now.isoformat(), include_originals=include_originals,
                    missing_originals=missing, materials=materials,
                    hash_scope='除 manifest.json 和 SHA256SUMS 外的所有 ZIP 成员；SHA256SUMS 另含 manifest.json 摘要。',
                    members=[members[key] for key in sorted(members)])
                raw = _json(manifest); archive.writestr('manifest.json', raw)
                written('manifest.json', hashlib.sha256(raw).hexdigest(), len(raw))
                raw = ''.join(f'{value["sha256"]}  {name}\n' for name, value in sorted(members.items())).encode()
                archive.writestr('SHA256SUMS', raw)
                written('SHA256SUMS', hashlib.sha256(raw).hexdigest(), len(raw))
            handle.flush(); os.fsync(handle.fileno())
        with zipfile.ZipFile(_native_path(temporary)) as archive:
            if set(archive.namelist()) != set(members):
                raise _ExportError('VERIFY_FAILED', '导出包成员核验失败。')
            for name, item in members.items():
                digest, size = hashlib.sha256(), 0
                with archive.open(name) as stream:
                    while part := stream.read(1024 * 1024):
                        _check(cancel)
                        digest.update(part); size += len(part)
                if digest.hexdigest() != item['sha256'] or size != item['size_bytes']:
                    raise _ExportError('VERIFY_FAILED', '导出包完整性核验失败。')
        gate = cancel.publication_lock if isinstance(cancel, ExportCancellation) else nullcontext()
        with gate:
            _check(cancel)
            _destination(store, output)
            if os.name == 'nt':
                os.rename(_native_path(temporary), _native_path(output))
            else:
                os.link(temporary, output); temporary.unlink()
            published = True
            outcome = result(ExportStatus.MISSING_ORIGINALS if missing else ExportStatus.SUCCEEDED,
                'MISSING_ORIGINALS' if missing else 'EXPORTED',
                '正文和计划已导出，但缺少原件；请查看缺失清单。这不代表资料已完整保存。' if missing else
                '计划已导出，解压后打开 index.html 可离线浏览。', output_path=output,
                missing=tuple(item['filename'] for item in missing))
            if isinstance(cancel, ExportCancellation):
                cancel.published_result = outcome
            return outcome
    except (ExtractError, StoreError, _ExportError) as exc:
        return result(ExportStatus.CANCELLED if exc.code == 'CANCELLED' else ExportStatus.FAILED,
                      exc.code, '导出已取消，未发布部分文件。' if exc.code == 'CANCELLED' else str(exc))
    except (ValueError, PlainFileCopyError):
        return result(ExportStatus.FAILED, 'DESTINATION_INVALID', '保存或原件路径不安全，请选择普通文件目录后重试。')
    except Exception:
        return result(ExportStatus.FAILED, 'EXPORT_FAILED', '导出失败，请检查目录权限、磁盘空间和原件状态；未报告保存成功。')
    finally:
        if temporary is not None and not published:
            try:
                _native_path(temporary).unlink(missing_ok=True)
            except OSError:
                return result(ExportStatus.FAILED, 'PARTIAL_CLEANUP_FAILED', f'导出失败，临时文件未能清理：{temporary}')
