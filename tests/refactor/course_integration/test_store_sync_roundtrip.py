"""Two real CourseStore devices exchanging data through CourseSync.

MF-03's store and MF-07's sync client were each tested against stand-ins; this
joins them through MF-07's protocol-faithful fake server (CAS revisions,
idempotent request IDs, tombstones).
"""
from datetime import date, datetime, timedelta, timezone
import importlib.util
from pathlib import Path
import tempfile

import pytest

from refactor.learning.course.ledger import PlanWindow
from refactor.learning.course.mastery import AttemptClosed, Judgement
from refactor.learning.course.store import CourseStore
from refactor.learning.course.sync_course import CourseSync

_HARNESS = Path(__file__).resolve().parents[1] / 'sync_course' / 'test_sync_course.py'
_spec = importlib.util.spec_from_file_location('sync_course_harness', _HARNESS)
harness = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(harness)
connection = harness.connection  # reuse the fake server + SyncTransport fixture

TODAY = date(2026, 9, 28)
NOW = datetime(2026, 9, 28, 12, tzinfo=timezone.utc)
WINDOW = PlanWindow(TODAY, TODAY + timedelta(days=6), 120)
SCRATCH = Path('E:/Codex/Temp/MentorFlowTests/course-integration')


@pytest.fixture(params=[False, True], ids=['snapshot-fallback', 'incremental'])
def devices(connection, request):
    server, transport, login = connection
    server.incremental = request.param
    server.page_limit = 3
    account = login.identity.account_id
    SCRATCH.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=SCRATCH) as a, tempfile.TemporaryDirectory(dir=SCRATCH) as b:
        first, second = CourseStore(account, Path(a)), CourseStore(account, Path(b))
        try:
            yield server, transport, first, second
        finally:
            first.close()
            second.close()


def build_plan(store):
    store.create_plan('plan', goal='学习 AI 智能体', final_goal='能独立搭建智能体', level='入门',
                      notes='', window=WINDOW)
    store.add_sources('plan', [dict(id='src', kind='url', url='https://arxiv.org/abs/2210.03629',
                                    title='ReAct', site='arxiv.org', tier='whitelist', credibility=0.95,
                                    adopted=True, chunks=[dict(chunk_id='c1', text='ReAct 交替推理与行动。',
                                                               page_start=1, page_end=1, section=['摘要'])])])
    store.save_outline('plan', [dict(id='s1', title='基础')],
                       [dict(id='m1', stage_id='s1', title='ReAct 范式', prerequisites=[])])
    store.save_generated_module('plan', 'm1', [dict(id='p1', summary='思考-行动-观察循环', minutes=30, chunk_ids=['c1']),
                                               dict(id='p2', summary='与 CoT 的区别', minutes=30, chunk_ids=['c1'])],
                                check_minutes=10, today=TODAY)
    store.start_plan('plan')


def state(store):
    mastery = store.load_mastery('plan')
    ledger = store.load_ledger('plan')
    return (sorted((m['id'], m['title']) for m in store.records('modules', 'plan')),
            sorted((p['id'], p['summary']) for p in store.records('points', 'plan')),
            {pid: (mastery.point(pid).casual_errors, mastery.point(pid).module_errors) for pid in ('p1', 'p2')},
            ledger.status(TODAY))


def test_plan_and_progress_round_trip_between_two_devices(devices):
    server, transport, a, b = devices
    build_plan(a)
    a.append_judgement('plan', Judgement('j1', 1, NOW, 'casual', 'm1', results=(('p1', False),)))
    a.add_credit('plan', 'cr1', 'p1', 30, TODAY, 'tentative')

    assert CourseSync(a, transport).sync()['uploaded'] > 0
    CourseSync(b, transport).sync()
    assert state(b) == state(a)
    assert b.load_mastery('plan').point('p1').casual_errors == 1

    # Device B continues: a module test on B must reach A without rewriting A's events.
    b.append_judgement('plan', Judgement('j2', 2, NOW, 'module_test', 'm1', 'att1', (('p1', True), ('p2', True))))
    b.append_attempt_closed('plan', AttemptClosed('ac1', 3, 'm1', 'att1', 'module_test'))
    b.confirm_credits('plan', 'p1')
    CourseSync(b, transport).sync()
    CourseSync(a, transport).sync()

    assert state(a) == state(b)
    assert a.load_mastery('plan').module('m1').state == 'passed'
    assert a.load_mastery('plan').point('p1').casual_errors == 1   # A's original event kept
    # Nothing left to upload on either side, and a further round is a no-op.
    assert not a.pending_records(100) and not b.pending_records(100)
    writes = len(server.requests)
    CourseSync(a, transport).sync()
    CourseSync(b, transport).sync()
    assert len(server.requests) == writes


def test_delete_and_reopen_with_real_store_and_old_cursor(devices):
    import json
    server, transport, a, b = devices
    a.create_chat_session('同步后删除', session_id='chat1')
    a.append_chat_message('chat1', 'user', '消息', message_id='msg1')
    CourseSync(a, transport).sync()
    CourseSync(b, transport).sync()
    # A real pre-upgrade cursor has no sequence or cached payload business ID.
    cursor = json.loads(b.sync_cursor())
    cursor.pop('since', None)
    for meta in cursor['records'].values():
        meta.pop('payload_id', None)
    b.set_sync_cursor(json.dumps(cursor))
    a.delete_chat_session('chat1')
    CourseSync(a, transport).sync()
    CourseSync(b, transport).sync()
    assert b.records('chat_sessions') == []
    assert b.records('chat_messages') == []
    # Open a second SQLite connection after persistence, without sharing objects.
    with CourseStore(b.account_id, b.root.parents[2]) as reopened:
        assert reopened.sync_cursor() == b.sync_cursor()
        assert reopened.records('chat_sessions') == []
        assert CourseSync(reopened, transport).sync()['pulled'] == 0
    assert CourseSync(b, transport).sync()['pulled'] == 0


def test_deletion_after_cursor_save_failure_cannot_orphan_remote_row(devices, monkeypatch):
    server, transport, a, b = devices
    a.create_chat_session('稍后删除', session_id='chat1')
    CourseSync(a, transport).sync()
    original = b.set_sync_cursor
    calls = 0
    def save(cursor):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError('synthetic cursor save failure after remote apply')
        original(cursor)
    monkeypatch.setattr(b, 'set_sync_cursor', save)
    with pytest.raises(OSError):
        CourseSync(b, transport).sync()
    assert len(b.records('chat_sessions')) == 1
    monkeypatch.setattr(b, 'set_sync_cursor', original)
    a.delete_chat_session('chat1')
    CourseSync(a, transport).sync()
    CourseSync(b, transport).sync()
    assert b.records('chat_sessions') == []


def test_upload_cursor_failure_keeps_pending_until_identity_is_durable(devices, monkeypatch):
    server, transport, a, b = devices
    a.create_chat_session('上传回执保存失败', session_id='chat1')
    original = a.set_sync_cursor
    calls = 0
    def save(cursor):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError('synthetic upload cursor persistence failure')
        original(cursor)
    monkeypatch.setattr(a, 'set_sync_cursor', save)
    with pytest.raises(OSError):
        CourseSync(a, transport).sync()
    assert a.pending_records(100)
    CourseSync(b, transport).sync()
    b.delete_chat_session('chat1')
    CourseSync(b, transport).sync()
    monkeypatch.setattr(a, 'set_sync_cursor', original)
    CourseSync(a, transport).sync()
    assert a.records('chat_sessions') == [] and not a.pending_records(100)
