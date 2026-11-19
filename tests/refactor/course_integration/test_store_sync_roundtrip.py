"""Two real CourseStore devices exchanging data through CourseSync.

MF-03's store and MF-07's sync client were each tested against stand-ins; this
joins them through MF-07's protocol-faithful fake server (CAS revisions,
idempotent request IDs, tombstones).
"""
from datetime import date, datetime, timedelta, timezone
import importlib.util
from pathlib import Path
import time

import pytest

from tests.refactor.mastery70.support import seed_assessment
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
@pytest.fixture(params=[False, True], ids=['snapshot-fallback', 'incremental'])
def devices(connection, request, tmp_path):
    server, transport, login = connection
    server.incremental = request.param
    server.page_limit = 3
    account = login.identity.account_id
    first, second = CourseStore(account, tmp_path / 'device-a'), CourseStore(account, tmp_path / 'device-b')
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
    seed_assessment(store, 'plan')
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
    b.append_judgement('plan', Judgement('j2', 2, NOW, 'module_test', 'm1', 'att1', (('p1', True),),question_id='p1'))
    b.append_judgement('plan', Judgement('j3', 3, NOW, 'module_test', 'm1', 'att1', (('p2', True),),question_id='p2'))
    b.append_attempt_closed('plan', AttemptClosed('ac1', 4, 'm1', 'att1', 'module_test'))
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


def test_concurrent_plan_fields_merge_without_losing_offline_edit(devices):
    _, transport, a, b = devices
    build_plan(a)
    CourseSync(a, transport).sync()
    CourseSync(b, transport).sync()
    a.rename_plan('plan', 'A 的新标题')
    b.archive_plan('plan')
    CourseSync(a, transport).sync()
    result = CourseSync(b, transport).sync()
    CourseSync(a, transport).sync()
    for store in (a, b):
        plan = store.get_plan('plan')
        assert plan['title'] == 'A 的新标题'
        assert plan['archived'] is True
        assert not store.pending_records(100)
    assert result['merged_conflicts'] >= 1


def test_concurrent_same_field_keeps_later_edit_and_reports_conflict(devices):
    _, transport, a, b = devices
    build_plan(a)
    CourseSync(a, transport).sync()
    CourseSync(b, transport).sync()
    a.rename_plan('plan', '先改')
    time.sleep(.003)
    b.rename_plan('plan', '后改')
    CourseSync(a, transport).sync()
    result = CourseSync(b, transport).sync()
    CourseSync(a, transport).sync()
    assert a.get_plan('plan')['title'] == b.get_plan('plan')['title'] == '后改'
    assert result['merged_conflicts'] >= 1


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


@pytest.fixture(scope='module')
def chat_app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def chat_bridge(store):
    import json
    from types import SimpleNamespace as NS
    from refactor.learning.app.bridge import AppBridge
    from refactor.learning.app.chat import register
    from refactor.learning.app.chat_store import CourseChatStore
    bridge, sent = AppBridge(), []
    bridge.event.connect(lambda text: sent.append(json.loads(text)))
    def complete(purpose, messages, *, system, on_text, cancel):
        assert purpose == 'free_chat' and system is None
        on_text('跨设备回复')
        return NS(text='跨设备回复')
    services = NS(chat=CourseChatStore(store), settings=NS(load=lambda: {'chat_persona': ''}),
                  gateway=NS(complete=complete))
    register(bridge, lambda: services)
    return bridge, sent


def chat_call(app, bridge, sent, method, **params):
    import json
    import time
    rid = 'request-' + str(len(sent))
    bridge.call(json.dumps(dict(id=rid, method=method, params=params)))
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        app.processEvents()
        reply = next((m for m in sent if m.get('id') == rid and m['type'] == 'response'), None)
        if reply:
            assert reply['ok'], reply
            return reply['result']
        time.sleep(.005)
    raise AssertionError('chat bridge did not complete')


def test_chat_bridge_send_sync_read_and_remote_delete(devices, chat_app):
    server, transport, a, b = devices
    bridge_a, sent_a = chat_bridge(a)
    bridge_b, sent_b = chat_bridge(b)
    try:
        first = chat_call(chat_app, bridge_a, sent_a, 'chat.create')['session']
        sid = first['id']
        first = chat_call(chat_app, bridge_a, sent_a, 'chat.send', session_id=sid, text='跨设备问题')['session']
        assert CourseSync(a, transport).sync()['uploaded'] == 3
        CourseSync(b, transport).sync()
        listed = chat_call(chat_app, bridge_b, sent_b, 'chat.list')['sessions']
        assert [(s['id'], s['title'], s['count']) for s in listed] == [(sid, '跨设备问题', 2)]
        assert chat_call(chat_app, bridge_b, sent_b, 'chat.get', session_id=sid)['session'] == first
        assert chat_call(chat_app, bridge_b, sent_b, 'chat.delete', session_id=sid)['deleted']
        CourseSync(b, transport).sync()
        CourseSync(a, transport).sync()
        assert chat_call(chat_app, bridge_a, sent_a, 'chat.list')['sessions'] == []
        assert not a.pending_records(100) and not b.pending_records(100)
        assert all(row['deleted'] for row in server.rows.values())
    finally:
        bridge_a.close(); bridge_b.close()


def test_two_devices_import_same_legacy_file_then_converge(devices):
    from refactor.learning.app.chat_store import CourseChatStore
    from refactor.learning.chat.storage import ScopedChatStore
    server, transport, a, b = devices
    for store in (a, b):
        old = ScopedChatStore(store.root / 'tools' / 'chat')
        old.create(session_id='same-old-session', created_at=NOW)
        old.commit_turn('same-old-session', expected_version=0, user_content='同一个文件',
                        assistant_content='只导入一次', committed_at=NOW)
    first, second = CourseChatStore(a), CourseChatStore(b)
    assert first.list_sessions() == second.list_sessions()
    assert a.pending_records(100) == b.pending_records(100)
    assert CourseSync(a, transport).sync()['uploaded'] == 3
    assert CourseSync(b, transport).sync()['uploaded'] == 0
    assert len(server.rows) == 3 and len(server.requests) == 3
    assert first.list_sessions() == second.list_sessions()
    assert len(second.list_sessions()[0].messages) == 2
    assert not a.pending_records(100) and not b.pending_records(100)
