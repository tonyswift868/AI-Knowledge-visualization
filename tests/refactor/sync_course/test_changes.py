import json

import httpx
import pytest

from test_sync_course import connection, MemoryStore, CourseSync, PlanError, record


def test_paging_persisted_cursor_and_empty_delta(connection):
    server, transport, _ = connection
    server.incremental = True
    server.page_limit = 2
    for i in range(5):
        server.put_remote(record(str(i)))
    store = MemoryStore()
    assert CourseSync(store, transport).sync()['pulled'] == 5
    assert len(server.pages) == 3
    assert json.loads(store.cursor)['since'] == '5'
    assert CourseSync(store, transport).sync()['pulled'] == 0
    assert server.pages[-1]['entities'] == []
    server.put_remote(record('2', payload={'changed': True}), 2)
    assert CourseSync(store, transport).sync()['pulled'] == 1
    assert len(server.pages[-1]['entities']) == 1
    assert not any(path.endswith('/snapshot') for _, path, _, _ in server.calls)


def test_cursor_only_advances_after_page_is_saved(connection):
    server, transport, _ = connection
    server.incremental, server.page_limit = True, 1
    server.put_remote(record('1'))
    server.put_remote(record('2'))
    store = MemoryStore()
    def fail_second(request):
        if request.url.params.get('since') == '1':
            return httpx.Response(503)
    server.before = fail_second
    with pytest.raises(PlanError):
        CourseSync(store, transport).sync()
    assert len(store.rows) == 1 and json.loads(store.cursor)['since'] == '1'
    server.before = None
    assert CourseSync(store, transport).sync()['pulled'] == 1


def test_upgrade_downgrade_upgrade_reconciles_without_losing_local_queue(connection):
    server, transport, _ = connection
    server.put_remote(record())
    store = MemoryStore()
    CourseSync(store, transport).sync()  # old snapshot server
    server.incremental = True
    assert CourseSync(store, transport).sync()['pulled'] == 0
    assert json.loads(store.cursor)['since'] == '1'
    server.incremental = False
    server.put_remote(record(payload={'old_server_edit': True}), 2)
    CourseSync(store, transport).sync()
    assert json.loads(store.cursor)['since'] == '0'
    server.incremental = True
    assert CourseSync(store, transport).sync()['pulled'] == 0
    assert json.loads(store.cursor)['since'] == '2'


@pytest.mark.parametrize('status', [401, 403, 429, 500, 503])
def test_only_404_falls_back(connection, status):
    server, transport, _ = connection
    server.before = lambda request: httpx.Response(status)
    with pytest.raises(PlanError):
        CourseSync(MemoryStore(record()), transport).sync()
    assert len(server.calls) == 1 and server.calls[0][1].endswith('/changes')


@pytest.mark.parametrize('corrupt', ['scope', 'sequence', 'cursor', 'empty_more', 'kind'])
def test_invalid_changes_page_preserves_cursor(connection, corrupt):
    server, transport, _ = connection
    server.incremental = True
    server.put_remote(record())
    def corrupt_page(request):
        server.before = None
        page = server.handle(request).json()
        if corrupt == 'scope': page['scope']['subject_id'] = 'wrong'
        if corrupt == 'sequence': page['entities'][0]['change_seq'] = 0
        if corrupt == 'cursor': page['next_cursor'] = '999'
        if corrupt == 'empty_more': page.update(entities=[], next_cursor='0', has_more=True)
        if corrupt == 'kind': page['entities'][0]['kind'] = 'history'
        return httpx.Response(200, json=page)
    server.before = corrupt_page
    store = MemoryStore()
    with pytest.raises(PlanError) as exc:
        CourseSync(store, transport).sync()
    assert exc.value.code == 'SYNC_RESPONSE_INVALID'
    assert store.cursor == '' and not store.rows


def test_cancel_between_pages_keeps_last_saved_cursor(connection):
    server, transport, _ = connection
    server.incremental, server.page_limit = True, 1
    server.put_remote(record('1')); server.put_remote(record('2'))
    store = MemoryStore()
    sync = CourseSync(store, transport)
    def cancel_second(request):
        if request.url.params.get('since') == '1': sync.cancel()
    server.before = cancel_second
    with pytest.raises(PlanError) as exc:
        sync.sync()
    assert exc.value.code == 'CANCELLED'
    assert json.loads(store.cursor)['since'] == '1'
    server.before = None
    assert sync.sync()['pulled'] == 1


def test_incremental_conflict_and_edit_during_upload(connection):
    server, transport, _ = connection
    server.incremental = True
    store = MemoryStore(record())
    def conflict(request):
        if request.method == 'PUT':
            server.before = None
            server.put_remote(record(payload={'remote': True}))
    server.before = conflict
    server.after = lambda request: store.edit(record(payload={'newer': True}, local_revision=2))
    result = CourseSync(store, transport).sync()
    assert result['conflicts'] == 1 and result['pending'] == 1
    server.after = None
    assert CourseSync(store, transport).sync()['ok']
    assert list(server.rows.values())[0]['payload']['values']['payload'] == {'newer': True}
