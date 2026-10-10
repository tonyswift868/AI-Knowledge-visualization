"""Per-record failures, idempotent retries and shared transport ownership."""
from collections import Counter
import hashlib
import httpx
import pytest

from refactor.learning.course.sync_course import CourseSync
from refactor.learning.sync_identity import PlanError
from refactor.learning.course.store import CourseStore
from test_sync_course import connection, record, wire, MemoryStore


def test_sync95_failed_rows_do_not_block_later_rows_and_recover(connection):
    server, transport, _ = connection
    records = [record(str(i)) for i in range(8)]
    store = MemoryStore(*records)
    failures = {wire(records[i])[0] for i in (1, 4)}
    attempts = Counter()
    def reject(request):
        if request.method == 'PUT':
            key = request.url.path.rsplit('/', 1)[-1]
            attempts[key] += 1
            if key in failures:
                return httpx.Response(503)
    server.before = reject
    sync = CourseSync(store, transport)
    progress = []
    sync.on_progress = lambda values, cancel: progress.append(values)
    result = sync.sync()
    assert result['uploaded'] == 6 and result['failed'] == 2 and result['pending'] == 2
    assert len(server.requests) == 6 and all(attempts[k] == 2 for k in failures)
    assert progress[-1] == dict(phase='upload', completed=6, total=8, failed=2)
    server.before = None
    assert sync.sync()['ok']
    assert not store.pending and len(server.requests) == 8


def test_sync95_every_upload_failed_is_a_round_failure(connection):
    server, transport, _ = connection
    store = MemoryStore(record('a'), record('b'))
    server.before = lambda r: httpx.Response(503) if r.method == 'PUT' else None
    with pytest.raises(PlanError) as exc:
        CourseSync(store, transport).sync()
    assert exc.value.code == 'SYNC_UNAVAILABLE'
    assert len(store.pending) == 2
    assert len([r for r in server.calls if r[0] == 'PUT']) == 4


def test_sync95_network_disconnection_does_not_spend_500_timeouts(connection):
    server, transport, _ = connection
    store = MemoryStore(*(record(str(i)) for i in range(20)))
    def disconnected(request):
        if request.method == 'PUT':
            raise httpx.ConnectError('synthetic disconnected local fixture')
    server.before = disconnected
    with pytest.raises(PlanError):
        CourseSync(store, transport).sync()
    assert len([r for r in server.calls if r[0] == 'PUT']) == 4
    assert len(store.pending) == 20


def test_sync95_field_merge_notice_names_plan_and_actual_field_winners(connection, tmp_path):
    _, transport, login = connection
    with CourseStore(login.identity.account_id, tmp_path/'local') as local, CourseStore(login.identity.account_id, tmp_path/'remote') as remote:
        local.create_plan('named-plan', goal='冲突测试计划')
        assert CourseSync(local, transport).sync()['ok']
        assert CourseSync(remote, transport).sync()['ok']
        remote.save_intake('named-plan', notes='另一端备注')
        assert CourseSync(remote, transport).sync()['ok']
        local.save_intake('named-plan', final_goal='本机验收目标')
        result = CourseSync(local, transport).sync()
        assert result['merged_conflicts'] == 1
        message = result['merge_messages'][0]
        assert '冲突测试计划' in message and '本机的验收目标' in message and '云端的备注' in message
        assert '另一端备注' not in message and '本机验收目标' not in message
        plan = local.get_plan('named-plan')
        assert plan['notes'] == '另一端备注' and plan['final_goal'] == '本机验收目标'


def test_sync95_client_reused_for_delta_snapshot_mutations_and_objects(connection):
    server, transport, login = connection
    client = transport._client
    assert client.timeout.read == 20 and not client.follow_redirects
    assert 'Authorization' not in client.headers
    transport.read_snapshot()
    assert CourseSync(MemoryStore(record('a')), transport).sync()['ok']
    data = b'{"synthetic":"history"}'; digest = hashlib.sha256(data).hexdigest()
    def objects(request):
        if '/objects/' in request.url.path:
            assert request.extensions['timeout']['read'] == 60
            if request.method == 'PUT':
                return httpx.Response(200, json=dict(sha256=digest, size=len(data)))
            return httpx.Response(200, content=data)
    server.before = objects
    transport.upload_history_blob(digest, data)
    assert transport.download_history_blob(digest) == data
    assert transport._client is client and not client.is_closed
    login.access_token = 'fresh-synthetic'
    transport.read_snapshot()
    assert server.calls[-1][3] == 'Bearer fresh-synthetic'
    transport.close()
    assert client.is_closed
    with pytest.raises(PlanError) as exc:
        transport.read_snapshot()
    assert exc.value.code == 'CANCELLED'
