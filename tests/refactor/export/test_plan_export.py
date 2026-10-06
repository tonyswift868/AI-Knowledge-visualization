from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import socket
import threading
import zipfile

from lxml import html
import pytest

from tests.refactor.mastery70.support import seed_assessment
from refactor.learning.course import export
from refactor.learning.course.export import export_plan, ExportResult, ExportStatus, ExportCancellation
from refactor.learning.course.ledger import PlanWindow
from refactor.learning.course.mastery import Judgement, AttemptClosed
from refactor.learning.course.materials import import_file, import_verified_source
from refactor.learning.course.store import CourseStore
from tests.refactor.materials.test_materials import verified

TODAY = date(2026, 9, 27)
NOW = datetime(2026, 9, 27, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs): raise AssertionError('导出不得访问网络')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    monkeypatch.setattr(socket, 'getaddrinfo', forbidden)


@pytest.fixture
def course(tmp_path):
    with CourseStore('account', tmp_path / 'device') as store:
        store.create_plan('plan', goal='课程 <script>not executable</script>', final_goal='掌握模型基础',
            window=PlanWindow(TODAY, TODAY + timedelta(days=4), 100))
        file = tmp_path / '资料.md'; file.write_text('# 第一章\n\n正文内容。\n\n## 细节\n\n- 学习列表', encoding='utf-8')
        source = import_file(store, 'plan', file)
        url_source = import_verified_source(store, 'plan', verified())
        chunk_id = next(c['id'] for c in store.records('source_chunks', 'plan') if c['source_id'] == source['id'])
        store.save_outline('plan', [dict(id='stage', title='第一阶段')],
            [dict(id='module', stage_id='stage', title='模型模块', prerequisites=[])])
        store.save_generated_module('plan', 'module', [dict(id='point', title='基础知识', summary='理解模型',
            minutes=20, chunk_ids=[chunk_id])], check_minutes=5, today=TODAY)
        store.save_teacher_profile('plan', {'progress': '熟悉基础'})
        store.append_message('plan', 'module', 'user', '如何理解？', message_id='message', at=NOW)
        seed_assessment(store, 'plan')
        store.start_plan('plan')
        store.append_judgement('plan', Judgement('j', 1, NOW, 'module_test', 'module', 'attempt', (('point', True),),question_id='point'),
                               details={'question': '问题', 'answer': '回答', 'chunk_ids': [chunk_id]})
        store.append_attempt_closed('plan', AttemptClosed('a', 2, 'module', 'attempt', 'module_test'))
        store.start_checking('plan')
        store.append_judgement('plan', Judgement('check', 3, NOW, 'check', 'module', results=(('point', True),), question_id='point'))
        store.add_credit('plan', 'credit', 'module', 25, TODAY)
        store.confirm_credits('plan', 'module', event_id='confirm')
        store.complete_plan('plan', today=TODAY)
        store.save_report('plan', {'summary': '学习完成', 'strengths': ['基础概念']})
        store.create_plan('other-plan', goal='OTHER-PLAN-PRIVATE-CONTENT')
        store.save_teacher_profile('other-plan', {'secret': 'not this plan'})
        yield store, source, file


def read_json(archive, name):
    return json.loads(archive.read(name))


def test_complete_zip_matches_database_events_and_projections(course, tmp_path):
    store, source, file = course
    target = tmp_path / 'complete.zip'
    calls, before = [], store.pending_records(999)
    result = export_plan(store, 'plan', target, progress=lambda *p: calls.append(p))
    assert isinstance(result, ExportResult) and result.succeeded and result.status is ExportStatus.SUCCEEDED
    assert result.output_path == target and result.account_id == store.account_id and result.plan_id == 'plan'
    assert store.pending_records(999) == before
    assert [n for n, total in calls] == list(range(1, calls[-1][1] + 1))
    with zipfile.ZipFile(target) as archive:
        plan = read_json(archive, 'plan.json'); state = read_json(archive, 'progress.json')
        assert plan['plan'] == store.get_plan('plan')
        for key in ('stages', 'modules', 'points', 'sources', 'source_chunks', 'teacher_profile', 'messages'):
            assert plan[key] == store.records(key, 'plan')
        for key in ('judgements', 'attempt_closed', 'credits', 'ledger_events'):
            assert state[key] == store.records(key, 'plan')
        book, ledger = store.load_mastery('plan'), store.load_ledger('plan')
        assert state['mastery']['points'] == [json.loads(json.dumps(asdict(book.point('point'))))]
        assert state['mastery']['modules'] == [json.loads(json.dumps(asdict(book.module('module'))))]
        assert state['mastery']['stats'] == book.stats()
        assert state['ledger'] == ledger.to_dict()
        assert state['ledger_status'] == asdict(ledger.status(date.fromisoformat(state['as_of_date'])))
        assert read_json(archive, 'report.json')['reports'] == store.records('reports', 'plan')
        material = next(m for m in plan['materials'] if m['source_id'] == source['id'])
        assert archive.read(material['original_path']) == file.read_bytes()
        text = archive.read(material['text_path']).decode()
        assert '[第一章 · heading]' in text and '第一章 / 细节' in text and '正文内容。' in text
        assert all(b'OTHER-PLAN-PRIVATE-CONTENT' not in archive.read(n) for n in archive.namelist())


def test_manifest_and_checksum_file_cover_every_archive_member(course, tmp_path):
    target = tmp_path / 'hashes.zip'
    assert export_plan(course[0], 'plan', target).succeeded
    with zipfile.ZipFile(target) as archive:
        manifest = read_json(archive, 'manifest.json')
        listed = {m['path'] for m in manifest['members']}
        assert listed == set(archive.namelist()) - {'manifest.json', 'SHA256SUMS'}
        for member in manifest['members']:
            raw = archive.read(member['path'])
            assert hashlib.sha256(raw).hexdigest() == member['sha256']
            assert len(raw) == member['size_bytes']
        checks = dict(line.split('  ', 1)[::-1] for line in archive.read('SHA256SUMS').decode().splitlines())
        assert set(checks) == set(archive.namelist()) - {'SHA256SUMS'}
        assert all(hashlib.sha256(archive.read(n)).hexdigest() == digest for n, digest in checks.items())


def test_index_is_offline_escaped_and_all_links_resolve(course, tmp_path):
    target = tmp_path / 'offline.zip'; assert export_plan(course[0], 'plan', target).succeeded
    with zipfile.ZipFile(target) as archive:
        page = archive.read('index.html').decode()
        assert 'http://' not in page.lower() and 'https://' not in page.lower()
        tree = html.fromstring(page)
        assert tree.xpath('//script|//iframe|//base|//*[@src]|//*[@onclick]') == []
        assert "default-src 'none'" in page
        assert '正文内容。' in tree.text_content() and '理解模型' in tree.text_content()
        from urllib.parse import unquote
        for link in tree.xpath('//@href'):
            assert unquote(link) in archive.namelist()


def test_missing_originals_publishes_explicit_incomplete_manifest_not_success(course, tmp_path):
    store, source, file = course
    with CourseStore(store.account_id, tmp_path / 'device-two') as remote:
        remote.apply_remote([(record, 1) for record in store.pending_records(999)])
        target = tmp_path / 'missing.zip'
        result = export_plan(remote, 'plan', target)
        assert result.status is ExportStatus.MISSING_ORIGINALS and not result.succeeded
        assert result.missing == ('资料.md',) and result.output_path == target
        with zipfile.ZipFile(target) as archive:
            missing = read_json(archive, 'manifest.json')['missing_originals']
            assert missing == [dict(source_id=source['id'], filename='资料.md', sha256=source['sha256'])]
            assert any(n.startswith('sources/') for n in archive.namelist())
            assert not any(n.startswith('originals/') for n in archive.namelist())
        text_only = tmp_path / 'text-only.zip'
        result = export_plan(remote, 'plan', text_only, include_originals=False)
        assert result.succeeded
        with zipfile.ZipFile(text_only) as archive:
            manifest = read_json(archive, 'manifest.json')
            assert manifest['include_originals'] is False and manifest['missing_originals'] == []


@pytest.mark.parametrize('inside', ['self', 'files', 'other-account'])
def test_destination_in_any_account_tree_rejected(course, inside):
    store = course[0]
    root = {'self': store.root, 'files': store.root / 'files', 'other-account': store.root.parent / 'other'}[inside]
    root.mkdir(exist_ok=True)
    target = root / 'forbidden.zip'
    result = export_plan(store, 'plan', target)
    assert result.status is ExportStatus.FAILED and result.code == 'DESTINATION_IN_ACCOUNT'
    assert not target.exists()


def test_existing_destination_and_publication_race_never_overwrite(course, tmp_path):
    target = tmp_path / 'exists.zip'; target.write_bytes(b'keep existing')
    assert export_plan(course[0], 'plan', target).code == 'DESTINATION_EXISTS'
    assert target.read_bytes() == b'keep existing'
    target = tmp_path / 'race.zip'
    def competing_writer(current, total):
        if current == total: target.write_bytes(b'concurrent file')
    result = export_plan(course[0], 'plan', target, progress=competing_writer)
    assert result.status is ExportStatus.FAILED and target.read_bytes() == b'concurrent file'
    assert not list(tmp_path.glob('*.part'))


@pytest.mark.parametrize('when', ['before', 'first-member', 'last-member'])
def test_cancellation_removes_staging_and_leaves_no_zip(course, tmp_path, when):
    event = ExportCancellation()
    if when == 'before': event.set()
    def progress(current, total):
        if when == 'first-member' and current == 1 or when == 'last-member' and current == total: event.set()
    target = tmp_path / 'cancel.zip'
    result = export_plan(course[0], 'plan', target, cancel=event, progress=progress)
    assert result.status is ExportStatus.CANCELLED and not target.exists()
    assert not list(tmp_path.glob('*.part')) and event.published_result is None


def test_publication_result_survives_late_cancel(course, tmp_path):
    event = ExportCancellation(); target = tmp_path / 'saved.zip'
    result = export_plan(course[0], 'plan', target, cancel=event)
    event.set()
    assert result.succeeded and event.published_result == result and target.exists()


def test_write_failure_cleans_staging(course, tmp_path, monkeypatch):
    original = zipfile.ZipFile.writestr
    def fail(archive, name, data, *args, **kwargs):
        if name == 'progress.json': raise OSError('synthetic disk full')
        return original(archive, name, data, *args, **kwargs)
    monkeypatch.setattr(zipfile.ZipFile, 'writestr', fail)
    target = tmp_path / 'failure.zip'
    result = export_plan(course[0], 'plan', target)
    assert result.status is ExportStatus.FAILED and not target.exists()
    assert not list(tmp_path.glob('*.part'))


def test_original_changes_during_export_never_publish(course, tmp_path):
    store, source, _ = course
    path = store.root / 'files' / source['sha256']
    def progress(current, total):
        if current == 1: path.write_bytes(b'changed in the middle')
    target = tmp_path / 'changed.zip'
    result = export_plan(store, 'plan', target, progress=progress)
    assert result.code == 'ORIGINAL_CHANGED' and result.status is ExportStatus.FAILED
    assert not target.exists() and not list(tmp_path.glob('*.part'))


def test_missing_plan_and_creating_plan_without_ledger(course, tmp_path):
    store = course[0]
    assert export_plan(store, 'missing', tmp_path / 'none.zip').status is ExportStatus.NO_CURRENT_PLAN
    assert not (tmp_path / 'none.zip').exists()
    target = tmp_path / 'draft.zip'
    assert export_plan(store, 'other-plan', target).succeeded
    with zipfile.ZipFile(target) as archive:
        assert read_json(archive, 'progress.json')['ledger'] is None
        assert 'report.json' not in archive.namelist()


def test_partly_synced_body_cannot_claim_success(course, tmp_path):
    store, source, _ = course
    store._db.execute('DELETE FROM source_chunks WHERE plan_key=? AND source_id=? AND id NOT IN (SELECT chunk_id FROM point_sources)',
                      ('plan', source['id']))
    target = tmp_path / 'incomplete.zip'
    assert export_plan(store, 'plan', target).code == 'SOURCE_INCOMPLETE'
    assert not target.exists()


def test_export_uses_one_snapshot_even_if_records_change_later(course, tmp_path):
    store, source, _ = course
    before = store.records('sources', 'plan')
    def progress(current, total):
        if current == 1: store.set_source_adopted('plan', source['id'], False)
    target = tmp_path / 'snapshot.zip'
    assert export_plan(store, 'plan', target, progress=progress).succeeded
    with zipfile.ZipFile(target) as archive:
        assert read_json(archive, 'plan.json')['sources'] == before
    assert next(s for s in store.records('sources', 'plan') if s['id'] == source['id'])['adopted'] is False


def test_pdf_source_text_keeps_page_numbers(course, tmp_path):
    from tests.refactor.extract.test_extract import pdf_bytes
    store = course[0]
    file = tmp_path / 'paper.pdf'
    file.write_bytes(pdf_bytes(['第一页介绍学习概念及应用。' * 3, '第二页讲解例子与练习方法。' * 3]))
    source = import_file(store, 'plan', file)
    target = tmp_path / 'pages.zip'
    assert export_plan(store, 'plan', target).succeeded
    with zipfile.ZipFile(target) as archive:
        item = next(m for m in read_json(archive, 'manifest.json')['materials'] if m['source_id'] == source['id'])
        text = archive.read(item['text_path']).decode()
        assert '[第 1 页 · paragraph]' in text and '[第 2 页 · paragraph]' in text
